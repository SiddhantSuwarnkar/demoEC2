"""StockSense - Statistical Anomaly Detector Service.

Deterministic, explainable anomaly detection operating on historical demand observations.
Uses a robust Median / Median Absolute Deviation (MAD) detector with handling for:
- Zero MAD and sparse demand histories (fallback to Mean Absolute Deviation).
- Constant demand histories (no false positives, zero variance recognized).
- Short histories (strict threshold on minimum required observations).
- Explicit promotion and stockout flags.
- Distinguishing statistical candidate flags from user-confirmed classifications.
- Non-mutation guarantees: Never alters historical observations or source data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Protocol


class DemandObservationProtocol(Protocol):
    """Protocol matching DemandRecord attributes needed for detection."""

    id: int
    date: date
    quantity: float
    stockout_flag: bool
    promotion_flag: bool


@dataclass(frozen=True)
class AnomalyDetectorConfig:
    """Configuration parameters for deterministic statistical anomaly detection."""

    detector_version: str = "median_mad_v1.0"
    threshold_multiplier: float = 3.0  # Modified Z-score cutoff (k * sigma_MAD)
    min_history_records: int = 5  # Minimum observations required for statistical evaluation


@dataclass
class AnomalyCandidate:
    """A detected anomaly candidate awaiting human review."""

    demand_record_id: int
    observation_date: date
    observed_quantity: float
    anomaly_type: str  # promotion_spike, high_demand_spike, low_demand_drop, stockout_affected, potential_data_error, unknown_anomaly
    suggested_classification: str  # 'promotion', 'data_error', 'unknown'
    score: float
    lower_bound: float
    upper_bound: float
    median: float
    mad: float
    std_estimator: float
    explanation: str
    stockout_flag: bool = False
    promotion_flag: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "demand_record_id": self.demand_record_id,
            "observation_date": self.observation_date.isoformat(),
            "observed_quantity": self.observed_quantity,
            "anomaly_type": self.anomaly_type,
            "suggested_classification": self.suggested_classification,
            "score": round(self.score, 4),
            "lower_bound": round(self.lower_bound, 4),
            "upper_bound": round(self.upper_bound, 4),
            "median": round(self.median, 4),
            "mad": round(self.mad, 4),
            "std_estimator": round(self.std_estimator, 4),
            "explanation": self.explanation,
            "stockout_flag": self.stockout_flag,
            "promotion_flag": self.promotion_flag,
        }


@dataclass
class DetectionResult:
    """Complete output of a deterministic anomaly detection run."""

    total_records_analyzed: int
    detector_version: str
    median: float
    mad: float
    std_estimator: float
    lower_bound: float
    upper_bound: float
    candidates: List[AnomalyCandidate] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _compute_median(values: List[float]) -> float:
    """Compute exact median of a list of floats."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 1:
        return float(sorted_vals[mid])
    return float((sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0)


def detect_anomalies(
    observations: List[DemandObservationProtocol],
    config: Optional[AnomalyDetectorConfig] = None,
) -> DetectionResult:
    """Run deterministic statistical anomaly detection across historical demand observations.

    Methodology:
    1. Robust Median and Median Absolute Deviation (MAD):
       M = median(Y)
       MAD = median(|Y - M|)
       sigma_hat = 1.4826022 * MAD (normal-consistent scale estimator)
    2. Zero MAD Handling:
       - If all values are identical (constant demand): variance is 0, bounds = [M, M], no outliers.
       - If MAD is 0 but some variation exists: use Mean Absolute Deviation:
         MeanAD = sum(|Y - M|) / N
         sigma_hat = 1.253314 * MeanAD
    3. Missing calendar dates are NEVER converted into zero-demand records.
       Analysis runs strictly across recorded observations.
    4. Categorization heuristics:
       - Spike (Y > upper_bound) with promotion_flag=True -> 'promotion_spike', suggestion 'promotion'.
       - Spike (Y > upper_bound) without promotion -> 'high_demand_spike', suggestion 'unknown'.
       - Drop (Y < lower_bound) with stockout_flag=True -> 'stockout_affected', suggestion 'unknown'.
       - Drop (Y < lower_bound) without stockout -> 'low_demand_drop', suggestion 'unknown'.
       - Recorded stockout with zero demand for positive-demand product -> 'stockout_affected', suggestion 'unknown'.
       - Negative or non-finite demand -> 'potential_data_error', suggestion 'data_error'.
    """
    cfg = config or AnomalyDetectorConfig()
    total_records = len(observations)

    if total_records < cfg.min_history_records:
        return DetectionResult(
            total_records_analyzed=total_records,
            detector_version=cfg.detector_version,
            median=0.0,
            mad=0.0,
            std_estimator=0.0,
            lower_bound=0.0,
            upper_bound=0.0,
            candidates=[],
            notes=[
                f"Insufficient demand history: {total_records} records found, "
                f"minimum required for statistical detector is {cfg.min_history_records}."
            ],
        )

    # Extract quantities strictly from recorded observations (no date synthesis)
    quantities: List[float] = [float(obs.quantity) for obs in observations]

    # Calculate Median
    med = _compute_median(quantities)

    # Calculate Absolute Deviations from Median
    deviations = [abs(q - med) for q in quantities]
    mad = _compute_median(deviations)

    notes: List[str] = []
    # 1.482602218505602 is the normal-consistency scaling factor for MAD (1 / norm.ppf(0.75))
    normal_mad_scale = 1.482602218505602

    if mad > 1e-9:
        std_est = normal_mad_scale * mad
    else:
        # Zero MAD handling: check for constant demand vs sparse/skewed demand
        min_q = min(quantities)
        max_q = max(quantities)
        if math.isclose(min_q, max_q, rel_tol=1e-9, abs_tol=1e-9):
            # Perfectly constant demand history
            std_est = 0.0
            notes.append("Constant demand history detected (MAD=0, zero variance).")
        else:
            # MAD is 0 because >50% of observations match the median (e.g., zero-heavy sparse demand)
            mean_ad = sum(deviations) / float(total_records)
            if mean_ad > 1e-9:
                # 1.2533141373 is sqrt(pi / 2), the normal-consistency factor for Mean Absolute Deviation
                std_est = 1.2533141373 * mean_ad
                notes.append("Zero MAD resolved using Mean Absolute Deviation fallback.")
            else:
                std_est = 0.0

    k = cfg.threshold_multiplier
    raw_lower_bound = med - (k * std_est)
    if std_est > 1e-9:
        upper_bound = med + (k * std_est)
        lower_bound = max(0.0, raw_lower_bound)
        if raw_lower_bound <= 0.0:
            notes.append(
                f"Lower statistical bound reached zero (median - {k}*std_est = {raw_lower_bound:.2f} <= 0). "
                f"For demand series with CV >= {1.0 / k:.2f}, drops to zero cannot reach the {k}-sigma threshold."
            )
    else:
        upper_bound = med
        lower_bound = med
        raw_lower_bound = med

    candidates: List[AnomalyCandidate] = []

    for obs in observations:
        qty = float(obs.quantity)

        # Check for potential data error (invalid / negative demand)
        if not math.isfinite(qty) or qty < 0.0:
            candidates.append(
                AnomalyCandidate(
                    demand_record_id=obs.id,
                    observation_date=obs.date,
                    observed_quantity=qty,
                    anomaly_type="potential_data_error",
                    suggested_classification="data_error",
                    score=999.0,
                    lower_bound=lower_bound,
                    upper_bound=upper_bound,
                    median=med,
                    mad=mad,
                    std_estimator=std_est,
                    explanation=(
                        f"Potential data error: invalid or negative demand value ({qty}) recorded."
                    ),
                    stockout_flag=bool(obs.stockout_flag),
                    promotion_flag=bool(obs.promotion_flag),
                )
            )
            continue

        if std_est <= 1e-9:
            # Constant demand: no statistical variance, cannot exceed bound unless data error
            continue

        score = abs(qty - med) / std_est

        # Case 1: Unusually High Demand
        if qty > upper_bound and score >= k:
            if obs.promotion_flag:
                anomaly_type = "promotion_spike"
                suggested_classification = "promotion"
                explanation = (
                    f"Observed demand of {qty:.1f} significantly exceeds upper expected threshold "
                    f"({upper_bound:.1f}, score={score:.2f}) and coincided with an active promotion flag."
                )
            else:
                anomaly_type = "high_demand_spike"
                suggested_classification = "unknown"
                explanation = (
                    f"Unusually high demand spike ({qty:.1f}) exceeds upper expected threshold "
                    f"({upper_bound:.1f}, score={score:.2f}) with no active promotion recorded."
                )

            candidates.append(
                AnomalyCandidate(
                    demand_record_id=obs.id,
                    observation_date=obs.date,
                    observed_quantity=qty,
                    anomaly_type=anomaly_type,
                    suggested_classification=suggested_classification,
                    score=score,
                    lower_bound=lower_bound,
                    upper_bound=upper_bound,
                    median=med,
                    mad=mad,
                    std_estimator=std_est,
                    explanation=explanation,
                    stockout_flag=bool(obs.stockout_flag),
                    promotion_flag=bool(obs.promotion_flag),
                )
            )
            continue

        # Case 2: Unusually Low Demand
        # Triggers when observation is at least k sigmas below median (qty <= raw_lower_bound)
        if score >= k and qty < med:
            if obs.stockout_flag:
                anomaly_type = "stockout_affected"
                suggested_classification = "unknown"
                explanation = (
                    f"Demand drop to {qty:.1f} fell below lower expected threshold "
                    f"({lower_bound:.1f}, score={score:.2f}) and coincided with a reported stockout flag "
                    "(potential censored demand)."
                )
            else:
                anomaly_type = "low_demand_drop"
                suggested_classification = "unknown"
                explanation = (
                    f"Unusually low demand drop to {qty:.1f} fell below lower expected threshold "
                    f"({lower_bound:.1f}, score={score:.2f})."
                )

            candidates.append(
                AnomalyCandidate(
                    demand_record_id=obs.id,
                    observation_date=obs.date,
                    observed_quantity=qty,
                    anomaly_type=anomaly_type,
                    suggested_classification=suggested_classification,
                    score=score,
                    lower_bound=lower_bound,
                    upper_bound=upper_bound,
                    median=med,
                    mad=mad,
                    std_estimator=std_est,
                    explanation=explanation,
                    stockout_flag=bool(obs.stockout_flag),
                    promotion_flag=bool(obs.promotion_flag),
                )
            )
            continue

        # Case 3: Stockout-affected demand warning on positive median products
        # If stockout flag is present on a zero/near-zero demand day while median is substantial
        if obs.stockout_flag and qty == 0.0 and med >= 5.0 and score >= 2.0:
            candidates.append(
                AnomalyCandidate(
                    demand_record_id=obs.id,
                    observation_date=obs.date,
                    observed_quantity=qty,
                    anomaly_type="stockout_affected",
                    suggested_classification="unknown",
                    score=score,
                    lower_bound=lower_bound,
                    upper_bound=upper_bound,
                    median=med,
                    mad=mad,
                    std_estimator=std_est,
                    explanation=(
                        f"Zero demand observed during a reported stockout event for a product with "
                        f"positive historical baseline (median {med:.1f}, score={score:.2f}, potential censored demand)."
                    ),
                    stockout_flag=True,
                    promotion_flag=bool(obs.promotion_flag),
                )
            )

    return DetectionResult(
        total_records_analyzed=total_records,
        detector_version=cfg.detector_version,
        median=med,
        mad=mad,
        std_estimator=std_est,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        candidates=candidates,
        notes=notes,
    )
