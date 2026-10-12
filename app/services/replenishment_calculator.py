"""StockSense - Explainable Replenishment Calculation Engine.

This module provides pure, deterministic domain functions and models for
inventory replenishment calculations:
- Demand statistics (average daily demand, standard deviation, missing date analysis, stockout warnings)
- Cycle Service Level to Normal Z-factor mapping
- Safety stock (constant and variable lead-time models)
- Reorder Point (ROP) and expected lead-time demand
- Inventory position calculation under schema constraints
- Economic Order Quantity (EOQ) with missing cost safeguards
- Order quantity proposal with MOQ and pack-size adjustments
- Explainability snapshots, assumptions, and human-readable narrative generation.

These functions are strictly decoupled from HTTP endpoints and ORM transactions.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence


class ReplenishmentError(Exception):
    """Base exception for replenishment calculation failures."""

    pass


class InsufficientDemandHistoryError(ReplenishmentError):
    """Raised when demand history is empty or insufficient to evaluate variability."""

    pass


class MissingSupplierSettingsError(ReplenishmentError):
    """Raised when required supplier parameters (e.g., lead time) are missing."""

    pass


class InvalidReplenishmentParameterError(ReplenishmentError):
    """Raised when input parameters (service level, lead times) violate mathematical constraints."""

    pass


@dataclass(frozen=True)
class DemandObservation:
    """Single daily demand observation."""

    date: date
    quantity: float
    stockout_flag: bool = False
    promotion_flag: bool = False


@dataclass(frozen=True)
class DemandStatistics:
    """Summary statistics for historical daily demand."""

    mean_daily_demand: float
    daily_demand_stddev: float
    recorded_observations_count: int
    calendar_span_days: int
    missing_calendar_dates_count: int
    stockout_observations_count: int
    stockout_ratio: float
    earliest_date: date
    latest_date: date
    demand_rate_basis: str = "average_over_recorded_observations"
    data_quality_warnings: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReplenishmentInputs:
    """All input parameters required for calculating replenishment."""

    product_id: int
    sku: str
    product_name: str
    on_hand_qty: int
    committed_qty: int = 0
    lead_time_days: Optional[int] = None
    lead_time_stddev_days: Optional[float] = None
    target_service_level: float = 0.95
    unit_cost: Optional[Decimal] = None
    ordering_cost: Optional[Decimal] = None
    holding_cost_per_unit_year: Optional[Decimal] = None
    holding_cost_rate: Optional[Decimal] = None
    minimum_order_qty: Optional[int] = None
    pack_size: Optional[int] = None
    formula_version: str = "v1.0"


@dataclass
class ReplenishmentCalculationResult:
    """Comprehensive explainable output of the replenishment engine."""

    product_id: int
    sku: str
    product_name: str
    formula_version: str
    calculation_status: str  # "reorder_now", "healthy"

    # Demand metrics
    mean_daily_demand: float
    daily_demand_stddev: float
    annual_demand: float
    demand_records_count: int
    calendar_span_days: int
    data_quality_warnings: List[str]

    # Service level & Lead time
    target_service_level: float
    z_factor: float
    average_lead_time_days: float
    lead_time_stddev_days: float
    lead_time_model: str  # "constant" or "variable"

    # Core inventory formulas
    expected_lead_time_demand: float
    safety_stock_exact: float
    safety_stock: int
    reorder_point_exact: float
    reorder_point: int

    # Inventory position
    on_hand_qty: int
    committed_qty: int
    inventory_position: int
    reorder_triggered: bool
    urgency: str  # "low", "medium", "high", "critical"

    # Lot sizing & EOQ
    raw_eoq: Optional[float]
    eoq_available: bool
    eoq_unavailable_reason: Optional[str]
    proposed_baseline_quantity: int

    # Order adjustments
    minimum_order_qty: Optional[int]
    pack_size: Optional[int]
    moq_applied: bool
    pack_size_applied: bool
    recommended_order_quantity: int

    # Audit & Explainability
    explanation: str
    assumptions: Dict[str, Any]
    input_snapshot: Dict[str, Any]


def calculate_demand_statistics(
    observations: Sequence[DemandObservation],
) -> DemandStatistics:
    """Calculate average daily demand and sample standard deviation from observations.

    Rules:
    - Never silently fabricate missing calendar dates as 0.
    - Explicit zero records are included in the average and standard deviation.
    - Out-of-stock records are preserved, analyzed, and explicitly disclosed in warnings.
    - Requires at least 2 observations to compute sample standard deviation (Bessel's correction).
    """
    if not observations:
        raise InsufficientDemandHistoryError("No demand records provided for product.")

    if len(observations) < 2:
        raise InsufficientDemandHistoryError(
            f"Insufficient demand history: {len(observations)} observation provided. "
            "At least 2 historical demand observations are required to estimate demand variability."
        )

    # Sort chronologically
    sorted_obs = sorted(observations, key=lambda x: x.date)
    quantities = [float(o.quantity) for o in sorted_obs]
    dates = [o.date for o in sorted_obs]

    earliest_date = dates[0]
    latest_date = dates[-1]
    calendar_span_days = (latest_date - earliest_date).days + 1
    recorded_count = len(sorted_obs)
    missing_dates_count = max(0, calendar_span_days - recorded_count)

    mean_demand = statistics.mean(quantities)
    # Sample standard deviation with Bessel's correction (N - 1)
    demand_stddev = statistics.stdev(quantities)

    stockout_count = sum(1 for o in sorted_obs if o.stockout_flag)
    stockout_ratio = stockout_count / recorded_count

    warnings: List[str] = []
    if missing_dates_count > 0:
        warnings.append(
            f"Demand rate ({round(mean_demand, 2)} units/day) reflects the average over {recorded_count} "
            f"recorded observation day(s), not total calendar days (span: {calendar_span_days} days with "
            f"{missing_dates_count} missing calendar date(s)). Missing dates were not converted to zero demand."
        )

    if stockout_count > 0:
        warnings.append(
            f"{stockout_count} of {recorded_count} observations ({stockout_ratio * 100:.1f}%) occurred "
            "during out-of-stock periods. Observed sales on stockout days may censor/understate true customer demand "
            "(retained as observed without unobserved demand imputation)."
        )

    return DemandStatistics(
        mean_daily_demand=round(mean_demand, 6),
        daily_demand_stddev=round(demand_stddev, 6),
        recorded_observations_count=recorded_count,
        calendar_span_days=calendar_span_days,
        missing_calendar_dates_count=missing_dates_count,
        stockout_observations_count=stockout_count,
        stockout_ratio=round(stockout_ratio, 4),
        earliest_date=earliest_date,
        latest_date=latest_date,
        demand_rate_basis="average_over_recorded_observations",
        data_quality_warnings=warnings,
    )


def calculate_z_factor(target_service_level: float) -> float:
    """Map Cycle Service Level (CSL) to standard normal quantile Z.

    Validates that target_service_level is strictly in (0, 1).
    Note: Cycle Service Level measures the probability of not stocking out during a replenishment cycle;
    it is not the same as Fill Rate (the volumetric fraction of demand satisfied from stock).
    """
    if not (0.0 < target_service_level < 1.0):
        raise InvalidReplenishmentParameterError(
            f"Target service level must be strictly between 0.0 and 1.0 (exclusive). Got {target_service_level}."
        )

    z = statistics.NormalDist(mu=0.0, sigma=1.0).inv_cdf(target_service_level)
    return round(z, 6)


def calculate_safety_stock(
    mean_daily_demand: float,
    daily_demand_stddev: float,
    lead_time_days: float,
    lead_time_stddev_days: float,
    z_factor: float,
) -> tuple[float, int, str]:
    """Calculate safety stock using documented formula.

    Formula:
      Safety stock = Z * sqrt(L * sigma_d^2 + d_bar^2 * sigma_L^2)
    Special case for constant lead time (sigma_L == 0):
      Safety stock = Z * sigma_d * sqrt(L)

    Returns:
      (raw_safety_stock, integer_safety_stock, model_name)
    """
    if lead_time_days <= 0:
        raise InvalidReplenishmentParameterError(
            f"Average supplier lead time must be positive (> 0). Got {lead_time_days} days."
        )

    if lead_time_stddev_days < 0:
        raise InvalidReplenishmentParameterError(
            f"Lead time standard deviation cannot be negative. Got {lead_time_stddev_days} days."
        )

    if lead_time_stddev_days == 0.0:
        # Constant lead time special case
        raw_ss = z_factor * daily_demand_stddev * math.sqrt(lead_time_days)
        model = "constant_lead_time"
    else:
        # Variable lead time general formula
        variance_term = (lead_time_days * (daily_demand_stddev**2)) + (
            (mean_daily_demand**2) * (lead_time_stddev_days**2)
        )
        raw_ss = z_factor * math.sqrt(max(0.0, variance_term))
        model = "variable_lead_time"

    # Non-negative check and ceiling rounding to guarantee conservative cycle service level
    raw_ss = max(0.0, raw_ss)
    int_ss = int(math.ceil(raw_ss))

    return round(raw_ss, 4), int_ss, model


def calculate_reorder_point(
    mean_daily_demand: float,
    lead_time_days: float,
    safety_stock: int,
) -> tuple[float, int]:
    """Calculate reorder point (ROP).

    Formula:
      ROP = expected_lead_time_demand + safety_stock
      expected_lead_time_demand = d_bar * L
    """
    expected_ltd = mean_daily_demand * lead_time_days
    raw_rop = expected_ltd + safety_stock
    int_rop = max(0, int(math.ceil(raw_rop)))
    return round(expected_ltd, 4), int_rop


def calculate_eoq(
    mean_daily_demand: float,
    ordering_cost: Optional[Decimal],
    holding_cost_per_unit_year: Optional[Decimal],
) -> tuple[Optional[float], bool, Optional[str]]:
    """Calculate Economic Order Quantity (EOQ).

    Formula:
      EOQ = sqrt((2 * D * S) / H)
    Where:
      D = annual demand = mean_daily_demand * 365
      S = ordering cost per order
      H = annual holding cost per unit

    Safety check:
      EOQ is only calculated when D, S, and H are all present and strictly positive (> 0).
      Never substitutes arbitrary defaults for missing costs.
    """
    annual_demand = mean_daily_demand * 365.0
    if annual_demand <= 0.0:
        return None, False, f"Annual demand is non-positive ({annual_demand:.2f} units/year)."

    if ordering_cost is None or ordering_cost <= Decimal("0.00"):
        return None, False, "Ordering cost ($S) is missing or non-positive."

    if holding_cost_per_unit_year is None or holding_cost_per_unit_year <= Decimal("0.00"):
        return None, False, "Annual holding cost per unit ($H) is missing or non-positive."

    s_val = float(ordering_cost)
    h_val = float(holding_cost_per_unit_year)

    eoq_val = math.sqrt((2.0 * annual_demand * s_val) / h_val)
    return round(eoq_val, 3), True, None


def calculate_replenishment(
    inputs: ReplenishmentInputs,
    demand_observations: Sequence[DemandObservation],
) -> ReplenishmentCalculationResult:
    """Execute complete deterministic replenishment calculation for a single product."""
    # 1. Validate supplier lead time
    if inputs.lead_time_days is None:
        raise MissingSupplierSettingsError(
            f"Product '{inputs.sku}' has no supplier lead time configured. "
            "Average supplier lead time (lead_time_days) is required to calculate replenishment."
        )

    if inputs.lead_time_days <= 0:
        raise InvalidReplenishmentParameterError(
            f"Invalid lead time for product '{inputs.sku}': lead_time_days must be greater than 0. "
            f"Got {inputs.lead_time_days}."
        )

    # 2. Demand statistics
    demand_stats = calculate_demand_statistics(demand_observations)
    mean_daily_demand = demand_stats.mean_daily_demand
    daily_demand_stddev = demand_stats.daily_demand_stddev
    annual_demand = round(mean_daily_demand * 365.0, 3)

    # 3. Target service level and Z-factor
    target_service_level = inputs.target_service_level
    z_factor = calculate_z_factor(target_service_level)

    # 4. Safety Stock
    lead_time_days = float(inputs.lead_time_days)
    lead_time_stddev_days = float(inputs.lead_time_stddev_days or 0.0)
    raw_ss, int_ss, lead_time_model = calculate_safety_stock(
        mean_daily_demand=mean_daily_demand,
        daily_demand_stddev=daily_demand_stddev,
        lead_time_days=lead_time_days,
        lead_time_stddev_days=lead_time_stddev_days,
        z_factor=z_factor,
    )

    # 5. Reorder Point
    expected_ltd, int_rop = calculate_reorder_point(
        mean_daily_demand=mean_daily_demand,
        lead_time_days=lead_time_days,
        safety_stock=int_ss,
    )

    # 6. Inventory Position
    # Schema limitation: incoming purchase orders are not currently tracked in the database
    on_hand = inputs.on_hand_qty
    committed = inputs.committed_qty
    inventory_position = on_hand - committed

    # 7. Reorder Trigger and Urgency
    reorder_triggered = inventory_position <= int_rop
    calculation_status = "reorder_now" if reorder_triggered else "healthy"

    if inventory_position <= 0:
        urgency = "critical"
    elif inventory_position <= int_ss:
        urgency = "high"
    elif inventory_position <= int_rop:
        urgency = "medium"
    else:
        urgency = "low"

    # 8. EOQ Baseline
    raw_eoq, eoq_available, eoq_reason = calculate_eoq(
        mean_daily_demand=mean_daily_demand,
        ordering_cost=inputs.ordering_cost,
        holding_cost_per_unit_year=inputs.holding_cost_per_unit_year,
    )

    # 9. Proposed Quantity and Adjustments
    explanation_parts: List[str] = []
    moq_applied = False
    pack_size_applied = False
    moq_adjustment_units = 0
    pack_size_adjustment_units = 0

    if not reorder_triggered:
        proposed_baseline_qty = 0
        final_order_qty = 0
        explanation_parts.append(
            f"Inventory position ({inventory_position}) is above the reorder point ({int_rop}). "
            "No replenishment order is currently recommended."
        )
    else:
        # Reorder is triggered: compute baseline quantity using max(ceil(EOQ), net_deficit)
        net_deficit = max(0, int_rop - inventory_position)
        if eoq_available and raw_eoq is not None:
            eoq_int = int(math.ceil(raw_eoq))
            proposed_baseline_qty = max(eoq_int, net_deficit)
            explanation_parts.append(
                f"Reorder triggered: Inventory position ({inventory_position}) is at or below the reorder point ({int_rop}). "
                f"Proposed baseline quantity is {proposed_baseline_qty} units based on max(ceil(EOQ), net_deficit) "
                f"(raw EOQ = {raw_eoq:.1f}, integer EOQ = {eoq_int}, net deficit to ROP = {net_deficit})."
            )
        else:
            proposed_baseline_qty = net_deficit
            if net_deficit > 0:
                explanation_parts.append(
                    f"Reorder triggered: Inventory position ({inventory_position}) is below the reorder point ({int_rop}). "
                    f"EOQ is unavailable ({eoq_reason}). Proposed baseline quantity is set to net deficit of {net_deficit} units "
                    f"to restore stock to reorder point."
                )
            else:
                explanation_parts.append(
                    f"Reorder triggered: Inventory position ({inventory_position}) is at the reorder point ({int_rop}). "
                    f"Net deficit to ROP is 0 units, and EOQ is unavailable ({eoq_reason}). Proposed baseline quantity is 0 units "
                    f"until inventory falls below reorder point or cost inputs are provided."
                )

        working_qty = proposed_baseline_qty

        # Apply MOQ only if a positive order quantity is proposed
        if (
            inputs.minimum_order_qty is not None
            and inputs.minimum_order_qty > 0
            and working_qty > 0
        ):
            if working_qty < inputs.minimum_order_qty:
                moq_applied = True
                moq_adjustment_units = inputs.minimum_order_qty - working_qty
                explanation_parts.append(
                    f"Quantity adjusted from {working_qty} to {inputs.minimum_order_qty} units "
                    f"to satisfy supplier Minimum Order Quantity (MOQ = {inputs.minimum_order_qty})."
                )
                working_qty = inputs.minimum_order_qty

        # Apply Pack Size only if a positive order quantity is proposed
        if inputs.pack_size is not None and inputs.pack_size > 1 and working_qty > 0:
            packs = math.ceil(working_qty / inputs.pack_size)
            rounded_pack_qty = packs * inputs.pack_size
            if rounded_pack_qty != working_qty:
                pack_size_applied = True
                pack_size_adjustment_units = rounded_pack_qty - working_qty
                explanation_parts.append(
                    f"Quantity rounded up from {working_qty} to {rounded_pack_qty} units "
                    f"to match supplier pack size of {inputs.pack_size} ({packs} packs)."
                )
                working_qty = rounded_pack_qty

        final_order_qty = working_qty
        explanation_parts.append(f"Final recommended order quantity: {final_order_qty} units.")

    # Build human-readable narrative explanation
    full_explanation = " ".join(explanation_parts)

    # Build structured assumptions dictionary
    assumptions_dict: Dict[str, Any] = {
        "formula_version": inputs.formula_version,
        "demand_model": {
            "mean_daily_demand": mean_daily_demand,
            "daily_demand_stddev": daily_demand_stddev,
            "annual_demand": annual_demand,
            "observations_count": demand_stats.recorded_observations_count,
            "calendar_span_days": demand_stats.calendar_span_days,
            "missing_calendar_dates": demand_stats.missing_calendar_dates_count,
            "stockout_observations": demand_stats.stockout_observations_count,
            "stockout_ratio": demand_stats.stockout_ratio,
            "demand_rate_basis": "average_over_recorded_observations",
            "stockout_censoring_treatment": (
                "Observed quantities retained as recorded without unobserved demand imputation. "
                "Stockouts are flagged as data quality warnings only and are not treated as corrected demand."
            ),
            "statistical_convention": "Sample standard deviation with Bessel's correction (N - 1)",
            "data_quality_warnings": demand_stats.data_quality_warnings,
        },
        "service_level_model": {
            "target_cycle_service_level": target_service_level,
            "z_factor": z_factor,
            "metric_type": "Cycle Service Level (CSL) - probability of zero stockouts during lead time (distinct from Fill Rate)",
        },
        "lead_time_model": {
            "average_lead_time_days": lead_time_days,
            "lead_time_stddev_days": lead_time_stddev_days,
            "model_type": lead_time_model,
            "formula_used": (
                "Z * sigma_d * sqrt(L)"
                if lead_time_model == "constant_lead_time"
                else "Z * sqrt(L * sigma_d^2 + d_bar^2 * sigma_L^2)"
            ),
        },
        "safety_stock": {
            "exact": raw_ss,
            "rounded": int_ss,
            "rounding_policy": "Ceiling (integer units) for conservative service level coverage",
        },
        "reorder_point": {
            "expected_lead_time_demand": expected_ltd,
            "exact": expected_ltd + raw_ss,
            "rounded": int_rop,
        },
        "inventory_position": {
            "on_hand_qty": on_hand,
            "committed_qty": committed,
            "incoming_open_orders": 0,
            "inventory_position": inventory_position,
            "limitation": (
                "Incoming open purchase orders are not currently tracked in the database (assumed 0). "
                "Inventory position is strictly on_hand_qty - committed_qty."
            ),
        },
        "eoq": {
            "available": eoq_available,
            "raw_eoq": raw_eoq,
            "unavailable_reason": eoq_reason,
            "ordering_cost": float(inputs.ordering_cost)
            if inputs.ordering_cost is not None
            else None,
            "holding_cost_per_unit_year": (
                float(inputs.holding_cost_per_unit_year)
                if inputs.holding_cost_per_unit_year is not None
                else None
            ),
            "holding_cost_rate": (
                float(inputs.holding_cost_rate) if inputs.holding_cost_rate is not None else None
            ),
            "policy": "EOQ used as lot-size baseline when triggered; falls back to net deficit when costs are missing",
        },
        "adjustments": {
            "reorder_triggered": reorder_triggered,
            "proposed_baseline_quantity": proposed_baseline_qty,
            "moq": inputs.minimum_order_qty,
            "moq_applied": moq_applied,
            "moq_adjustment_units": moq_adjustment_units,
            "pack_size": inputs.pack_size,
            "pack_size_applied": pack_size_applied,
            "pack_size_adjustment_units": pack_size_adjustment_units,
            "final_recommended_order_quantity": final_order_qty,
        },
    }

    # Build input snapshot for deterministic auditability
    input_snapshot_dict: Dict[str, Any] = {
        "product_id": inputs.product_id,
        "sku": inputs.sku,
        "product_name": inputs.product_name,
        "on_hand_qty": inputs.on_hand_qty,
        "committed_qty": inputs.committed_qty,
        "lead_time_days": inputs.lead_time_days,
        "lead_time_stddev_days": inputs.lead_time_stddev_days,
        "target_service_level": inputs.target_service_level,
        "unit_cost": str(inputs.unit_cost) if inputs.unit_cost is not None else None,
        "ordering_cost": str(inputs.ordering_cost) if inputs.ordering_cost is not None else None,
        "holding_cost_per_unit_year": (
            str(inputs.holding_cost_per_unit_year)
            if inputs.holding_cost_per_unit_year is not None
            else None
        ),
        "holding_cost_rate": (
            str(inputs.holding_cost_rate) if inputs.holding_cost_rate is not None else None
        ),
        "minimum_order_qty": inputs.minimum_order_qty,
        "pack_size": inputs.pack_size,
        "demand_records_count": len(demand_observations),
    }

    return ReplenishmentCalculationResult(
        product_id=inputs.product_id,
        sku=inputs.sku,
        product_name=inputs.product_name,
        formula_version=inputs.formula_version,
        calculation_status=calculation_status,
        mean_daily_demand=mean_daily_demand,
        daily_demand_stddev=daily_demand_stddev,
        annual_demand=annual_demand,
        demand_records_count=demand_stats.recorded_observations_count,
        calendar_span_days=demand_stats.calendar_span_days,
        data_quality_warnings=demand_stats.data_quality_warnings,
        target_service_level=target_service_level,
        z_factor=z_factor,
        average_lead_time_days=lead_time_days,
        lead_time_stddev_days=lead_time_stddev_days,
        lead_time_model=lead_time_model,
        expected_lead_time_demand=expected_ltd,
        safety_stock_exact=raw_ss,
        safety_stock=int_ss,
        reorder_point_exact=expected_ltd + raw_ss,
        reorder_point=int_rop,
        on_hand_qty=on_hand,
        committed_qty=committed,
        inventory_position=inventory_position,
        reorder_triggered=reorder_triggered,
        urgency=urgency,
        raw_eoq=raw_eoq,
        eoq_available=eoq_available,
        eoq_unavailable_reason=eoq_reason,
        proposed_baseline_quantity=proposed_baseline_qty,
        minimum_order_qty=inputs.minimum_order_qty,
        pack_size=inputs.pack_size,
        moq_applied=moq_applied,
        pack_size_applied=pack_size_applied,
        recommended_order_quantity=final_order_qty,
        explanation=full_explanation,
        assumptions=assumptions_dict,
        input_snapshot=input_snapshot_dict,
    )
