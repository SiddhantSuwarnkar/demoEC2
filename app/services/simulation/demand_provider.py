from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppException
from app.models.demand import DemandRecord
from app.models.forecast import ForecastRun
from app.models.product import Product
from app.services.replenishment_calculator import (
    DemandObservation,
    InsufficientDemandHistoryError,
    calculate_demand_statistics,
)


class DemandSourceError(AppException):
    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="DEMAND_SOURCE_ERROR",
            status_code=422,
        )


@dataclass(frozen=True)
class DailyDemandItem:
    date: date
    quantity: float = 0.0
    day_index: int = 1  # 1 to horizon_days
    baseline_quantity: Optional[float] = None

    def __post_init__(self):
        if self.baseline_quantity is not None and self.quantity == 0.0:
            object.__setattr__(self, "quantity", self.baseline_quantity)
        elif self.baseline_quantity is None:
            object.__setattr__(self, "baseline_quantity", self.quantity)


# Alias for test conveniences
DemandPoint = DailyDemandItem


@dataclass(frozen=True)
class SimulationDemandProfile:
    daily_demands: List[DailyDemandItem]
    mean_daily_demand: float
    daily_demand_stddev: float
    source_type: str  # "forecast_snapshot" or "historical_demand_average"
    forecast_run_id: Optional[int] = None
    forecast_method: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def resolve_simulation_demand(
    db: Session,
    product: Product,
    horizon_days: int,
    forecast_run_id: Optional[int] = None,
    start_date: Optional[date] = None,
) -> SimulationDemandProfile:
    """Resolves an explainable daily demand profile for simulation horizon.

    - If forecast_run_id is specified:
      Validates product ownership, run status, and exact calendar date coverage.
      Since Phase 5 ForecastRun models are historical benchmarks evaluated on past holdout dates,
      they cannot be disguised as forward forecasts. If requested simulation dates do not match
      persisted forecast dates, a clear DemandSourceError is raised.
    - If forecast_run_id is None:
      Uses the defensible historical daily demand rate fallback (average over recorded observation days)
      with full disclosure of calendar span and missing observation dates.
    """
    sim_start = start_date or (date.today() + timedelta(days=1))
    requested_dates = [sim_start + timedelta(days=i) for i in range(horizon_days)]
    warnings: List[str] = []

    # 1. If forecast_run_id is explicitly provided, validate and load it
    if forecast_run_id is not None:
        forecast_run = db.execute(
            select(ForecastRun).where(ForecastRun.id == forecast_run_id)
        ).scalar_one_or_none()

        if not forecast_run:
            raise DemandSourceError(f"Specified forecast run ID {forecast_run_id} not found.")

        if forecast_run.product_id != product.id:
            raise DemandSourceError(
                f"Specified forecast run ID {forecast_run_id} belongs to product ID "
                f"{forecast_run.product_id}, not requested product '{product.sku}' (ID {product.id})."
            )

        if forecast_run.status == "failed":
            raise DemandSourceError(
                f"Specified forecast run ID {forecast_run_id} failed "
                f"({forecast_run.failure_reason or 'benchmark execution failed'}) and cannot be used for simulation."
            )

        if forecast_run.status == "unavailable":
            raise DemandSourceError(
                f"Specified forecast run ID {forecast_run_id} is unavailable "
                f"({forecast_run.failure_reason or 'model unavailable'}) and cannot be used for simulation."
            )

        # Extract predictions from candidate or baseline
        cand_payload = forecast_run.candidate_config or {}
        base_payload = forecast_run.baseline_config or {}

        preds_data = []
        method_name = forecast_run.candidate_method

        if cand_payload.get("status") == "completed" and cand_payload.get("predictions"):
            preds_data = cand_payload["predictions"]
        elif base_payload.get("predictions"):
            preds_data = base_payload["predictions"]
            method_name = forecast_run.baseline_method
            if forecast_run.status == "partial":
                warnings.append(
                    f"Forecast run ID {forecast_run_id} has partial status "
                    f"(candidate model failed: {forecast_run.failure_reason}); using baseline moving average predictions."
                )

        if not preds_data:
            raise DemandSourceError(
                f"Specified forecast run ID {forecast_run_id} has no valid predictions available for simulation."
            )

        # Build map of predictions by exact calendar date
        preds_by_date: Dict[date, float] = {}
        for p in preds_data:
            raw_d = p.get("date")
            if raw_d is not None:
                p_date = date.fromisoformat(str(raw_d)) if isinstance(raw_d, str) else raw_d
                preds_by_date[p_date] = float(p.get("predicted", 0.0))

        # Check alignment with requested simulation dates
        missing_dates = [d for d in requested_dates if d not in preds_by_date]

        if missing_dates:
            holdout_start = forecast_run.holdout_start_date
            holdout_end = forecast_run.holdout_end_date
            if len(missing_dates) == len(requested_dates):
                # None of the requested dates match - typical when simulating future horizon with a historical benchmark
                raise DemandSourceError(
                    f"Forecast run ID {forecast_run_id} is a historical benchmark evaluated on past holdout dates "
                    f"({holdout_start} to {holdout_end}). It cannot supply future forecasts for requested simulation "
                    f"period ({requested_dates[0]} to {requested_dates[-1]}). The existing Phase 5 forecast snapshot is an "
                    "evaluation benchmark on historical holdout observations, not an out-of-sample future forecast."
                )
            else:
                # Partial match (some dates missing within or beyond the holdout span)
                raise DemandSourceError(
                    f"Forecast run ID {forecast_run_id} does not contain forecast predictions for {len(missing_dates)} "
                    f"calendar date(s) in requested horizon (e.g. {missing_dates[0]}). Missing forecast dates cannot be silently imputed."
                )

        # All requested simulation dates have exact point predictions
        daily_items: List[DailyDemandItem] = []
        predicted_vals: List[float] = []
        for i, cur_date in enumerate(requested_dates):
            val = preds_by_date[cur_date]
            predicted_vals.append(val)
            daily_items.append(
                DailyDemandItem(
                    day_index=i + 1,
                    date=cur_date,
                    baseline_quantity=max(0.0, float(val)),
                )
            )

        mean_pred = sum(predicted_vals) / len(predicted_vals) if predicted_vals else 0.0
        std_pred = 0.0
        if len(predicted_vals) > 1:
            var = sum((x - mean_pred) ** 2 for x in predicted_vals) / (len(predicted_vals) - 1)
            std_pred = var**0.5

        return SimulationDemandProfile(
            daily_demands=daily_items,
            mean_daily_demand=round(mean_pred, 2),
            daily_demand_stddev=round(std_pred, 2),
            source_type="forecast_snapshot",
            forecast_run_id=forecast_run.id,
            forecast_method=method_name,
            metadata={
                "forecast_run_id": forecast_run.id,
                "method": method_name,
                "points_count": len(predicted_vals),
                "mae_baseline": forecast_run.mae_baseline,
                "mae_candidate": forecast_run.mae_candidate,
                "holdout_start_date": str(forecast_run.holdout_start_date),
                "holdout_end_date": str(forecast_run.holdout_end_date),
            },
            warnings=warnings,
        )

    # 2. Historical demand fallback (when forecast_run_id is None)
    records = (
        db.execute(
            select(DemandRecord)
            .where(DemandRecord.product_id == product.id)
            .order_by(DemandRecord.date.asc())
        )
        .scalars()
        .all()
    )

    if not records:
        raise DemandSourceError(f"Product {product.sku} has no demand records to simulate demand.")

    observations = [
        DemandObservation(
            date=r.date,
            quantity=float(r.quantity),
            stockout_flag=bool(r.stockout_flag),
            promotion_flag=bool(r.promotion_flag),
        )
        for r in records
    ]

    try:
        stats = calculate_demand_statistics(observations)
    except InsufficientDemandHistoryError as exc:
        raise DemandSourceError(str(exc))

    mean_demand = stats.mean_daily_demand
    std_demand = stats.daily_demand_stddev

    warnings.extend(stats.data_quality_warnings)
    warnings.append(
        "Simulation demand is driven by historical mean daily demand (average over recorded observation days) "
        "because no suitable forecast run was specified or available."
    )

    daily_items = []
    for i, cur_date in enumerate(requested_dates):
        daily_items.append(
            DailyDemandItem(
                day_index=i + 1,
                date=cur_date,
                baseline_quantity=max(0.0, float(mean_demand)),
            )
        )

    return SimulationDemandProfile(
        daily_demands=daily_items,
        mean_daily_demand=round(mean_demand, 2),
        daily_demand_stddev=round(std_demand, 2),
        source_type="historical_demand_average",
        forecast_run_id=None,
        forecast_method=None,
        metadata={
            "demand_rate_basis": stats.demand_rate_basis,
            "recorded_observations_count": stats.recorded_observations_count,
            "calendar_span_days": stats.calendar_span_days,
            "missing_calendar_dates_count": stats.missing_calendar_dates_count,
        },
        warnings=warnings,
    )
