import math
from dataclasses import dataclass
from typing import Dict

from app.services.replenishment_calculator import calculate_z_factor


@dataclass(frozen=True)
class StressedRopResult:
    baseline_safety_stock: int
    baseline_reorder_point: int
    stressed_safety_stock: int
    stressed_reorder_point: int
    effective_lead_time_days: int
    z_factor: float
    explanation: Dict[str, str]


def calculate_stressed_reorder_point(
    mean_daily_demand: float,
    daily_demand_stddev: float,
    baseline_lead_time_days: int,
    supplier_delay_days: int,
    demand_surge_pct: float,
    target_service_level: float = 0.95,
) -> StressedRopResult:
    """Calculates baseline and stressed safety stock and reorder point under stress parameters.

    - Stressed lead time: baseline_lead_time + supplier_delay_days
    - Stressed demand rate: mean_daily_demand * (1 + demand_surge_pct / 100)
    - Stressed demand stddev: daily_demand_stddev * (1 + demand_surge_pct / 100)
    - Uses exact Normal quantile Z-factor mapping.
    """
    z = calculate_z_factor(target_service_level)

    # 1. Baseline
    b_lt = max(1, baseline_lead_time_days)
    b_ss = math.ceil(z * daily_demand_stddev * math.sqrt(b_lt))
    b_lead_demand = mean_daily_demand * b_lt
    b_rop = math.ceil(b_lead_demand + b_ss)

    # 2. Stressed
    s_lt = max(1, baseline_lead_time_days + supplier_delay_days)
    surge_multiplier = 1.0 + (demand_surge_pct / 100.0)
    s_demand = mean_daily_demand * surge_multiplier
    s_stddev = daily_demand_stddev * surge_multiplier

    s_ss = math.ceil(z * s_stddev * math.sqrt(s_lt))
    s_lead_demand = s_demand * s_lt
    s_rop = math.ceil(s_lead_demand + s_ss)

    explanation = {
        "formula_safety_stock": "SS = ceil(Z * stddev_d * sqrt(LeadTime))",
        "formula_reorder_point": "ROP = ceil(DemandRate * LeadTime + SS)",
        "baseline_parameters": f"L={b_lt}d, d={round(mean_daily_demand, 2)}, std={round(daily_demand_stddev, 2)}, Z={round(z, 3)}",
        "stressed_parameters": f"L={s_lt}d (delay=+{supplier_delay_days}d), d={round(s_demand, 2)} (surge=+{demand_surge_pct}%), std={round(s_stddev, 2)}",
    }

    return StressedRopResult(
        baseline_safety_stock=b_ss,
        baseline_reorder_point=b_rop,
        stressed_safety_stock=s_ss,
        stressed_reorder_point=s_rop,
        effective_lead_time_days=s_lt,
        z_factor=z,
        explanation=explanation,
    )
