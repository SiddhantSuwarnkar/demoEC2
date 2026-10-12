import math
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from app.models.product import Product
from app.schemas.scenario import (
    DailySimulationPoint,
    ReplenishmentEvent,
    ScenarioMetrics,
)
from app.services.replenishment_calculator import calculate_eoq
from app.services.simulation.costs import calculate_simulation_costs
from app.services.simulation.demand_provider import SimulationDemandProfile
from app.services.simulation.reorder_point import (
    StressedRopResult,
    calculate_stressed_reorder_point,
)


@dataclass
class ScenarioSimulationResult:
    timeline: List[DailySimulationPoint]
    replenishment_events: List[ReplenishmentEvent]
    metrics: ScenarioMetrics
    stressed_rop: StressedRopResult
    parameters: Dict[str, any]
    assumptions: List[str]


def run_single_simulation(
    product: Product,
    demand_profile: SimulationDemandProfile,
    demand_surge_pct: float = 0.0,
    supplier_delay_days: int = 0,
    horizon_days: int = 30,
    lead_time_days_override: Optional[int] = None,
    target_service_level_override: Optional[float] = None,
) -> ScenarioSimulationResult:
    """Executes a pure, deterministic chronological inventory simulation.

    Daily Event Sequence:
    1. Start of day: physical on-hand inventory carried forward.
    2. Replenishment arrivals: any pending purchase order scheduled for today arrives.
    3. Demand fulfillment: demand occurs; fulfilled up to available stock.
       Unmet demand is logged separately as lost sales; physical stock never goes negative.
    4. End of day: inventory position evaluated (ending_stock + in_transit - committed).
       If inventory position <= reorder point, replenishment order is triggered with
       MOQ, pack-size, and EOQ adjustments.
    """
    assumptions: List[str] = [
        "Daily event sequence: Arrivals at start of day -> Customer demand fulfillment -> End-of-day reorder evaluation.",
        "Unmet demand is treated as lost sales and tracked separately; on-hand physical stock is strictly non-negative.",
        "Simulated replenishment orders are decision-support projections; no real purchase orders are placed.",
        "Open incoming supplier orders prior to Day 1 are not tracked in the schema; starting in-transit quantity is 0.",
    ]
    if demand_profile.warnings:
        assumptions.extend(demand_profile.warnings)

    # 1. Starting Parameters
    current_stock = int(product.current_stock or 0)
    committed_qty = int(product.committed_qty or 0)
    baseline_lead_time = int(lead_time_days_override or product.lead_time_days or 7)
    effective_lead_time = max(1, baseline_lead_time + supplier_delay_days)
    tsl = float(target_service_level_override or product.target_service_level or 0.95)
    moq = product.minimum_order_qty
    pack_size = product.pack_size

    # 2. Reorder point and safety stock under stress
    stressed_rop = calculate_stressed_reorder_point(
        mean_daily_demand=demand_profile.mean_daily_demand,
        daily_demand_stddev=demand_profile.daily_demand_stddev,
        baseline_lead_time_days=baseline_lead_time,
        supplier_delay_days=supplier_delay_days,
        demand_surge_pct=demand_surge_pct,
        target_service_level=tsl,
    )

    reorder_point_threshold = stressed_rop.stressed_reorder_point

    # 3. EOQ evaluation (if economic inputs exist)
    h_cost = product.holding_cost_per_unit_year
    if h_cost is None and product.holding_cost_rate is not None and product.unit_cost is not None:
        h_cost = Decimal(str(product.holding_cost_rate)) * Decimal(str(product.unit_cost))
    elif h_cost is not None:
        h_cost = Decimal(str(h_cost))

    ord_cost = Decimal(str(product.ordering_cost)) if product.ordering_cost is not None else None

    surge_multiplier = 1.0 + (demand_surge_pct / 100.0)
    effective_daily_demand = demand_profile.mean_daily_demand * surge_multiplier

    raw_eoq, eoq_available, _ = calculate_eoq(
        mean_daily_demand=effective_daily_demand,
        ordering_cost=ord_cost,
        holding_cost_per_unit_year=h_cost,
    )

    # 4. Simulation Tracking
    timeline: List[DailySimulationPoint] = []
    events: List[ReplenishmentEvent] = []
    pending_arrivals: Dict[int, int] = {}  # day_index -> order_qty

    event_counter = 1
    total_ordered_units = 0

    for t in range(1, horizon_days + 1):
        demand_item = demand_profile.daily_demands[t - 1]
        sim_date = demand_item.date

        # Step 1: Start of Day Stock
        starting_stock = current_stock

        # Step 2: Replenishment Arrivals
        arrivals_today = pending_arrivals.get(t, 0)
        available_before_demand = starting_stock + arrivals_today

        # Step 3: Demand Fulfillment with Surge
        base_qty = demand_item.baseline_quantity
        surged_float = base_qty * (1.0 + (demand_surge_pct / 100.0))
        simulated_demand = max(0, int(math.ceil(surged_float)))

        fulfilled = min(available_before_demand, simulated_demand)
        unmet = max(0, simulated_demand - available_before_demand)
        ending_stock = available_before_demand - fulfilled

        is_stockout = ending_stock == 0 and simulated_demand > 0 and unmet > 0

        # Step 4: End-of-Day Replenishment Decision
        # In-transit orders: pending arrivals on future days
        in_transit_qty = sum(qty for arr_day, qty in pending_arrivals.items() if arr_day > t)
        inventory_position = ending_stock + in_transit_qty - committed_qty

        reorder_triggered = inventory_position <= reorder_point_threshold
        order_qty_placed = 0
        arrival_day_for_order = None

        if reorder_triggered:
            net_deficit = max(0, reorder_point_threshold - inventory_position)
            if raw_eoq is not None and raw_eoq > 0:
                proposed = max(math.ceil(raw_eoq), net_deficit)
                basis_text = f"max(EOQ={math.ceil(raw_eoq)}, deficit={net_deficit})"
            else:
                proposed = max(reorder_point_threshold, net_deficit)
                basis_text = f"max(ROP={reorder_point_threshold}, deficit={net_deficit})"

            if proposed == 0:
                proposed = max(1, reorder_point_threshold)

            adjustments = []
            # Apply MOQ
            if moq is not None and proposed < moq:
                proposed = moq
                adjustments.append(f"MOQ applied ({moq})")

            # Apply Pack Size
            if pack_size is not None and pack_size > 0:
                adjusted_pack = math.ceil(proposed / pack_size) * pack_size
                if adjusted_pack != proposed:
                    adjustments.append(f"Pack size rounded to {adjusted_pack}")
                proposed = adjusted_pack

            adj_text = f" [{', '.join(adjustments)}]" if adjustments else ""

            order_qty_placed = proposed
            total_ordered_units += proposed
            arrival_day_for_order = t + effective_lead_time

            pending_arrivals[arrival_day_for_order] = (
                pending_arrivals.get(arrival_day_for_order, 0) + proposed
            )

            cal_arrival_date = sim_date + timedelta(days=effective_lead_time)

            events.append(
                ReplenishmentEvent(
                    event_id=event_counter,
                    day_index=t,
                    order_date=sim_date,
                    expected_arrival_date=cal_arrival_date,
                    arrival_day_index=arrival_day_for_order,
                    order_quantity=order_qty_placed,
                    trigger_inventory_position=inventory_position,
                    reorder_point_threshold=reorder_point_threshold,
                    lead_time_days=baseline_lead_time,
                    delay_days_applied=supplier_delay_days,
                    effective_lead_time_days=effective_lead_time,
                    explanation=(
                        f"Inventory position ({inventory_position}) fell to or below ROP threshold "
                        f"({reorder_point_threshold}). Ordered {order_qty_placed} units based on {basis_text}{adj_text} "
                        f"with {effective_lead_time}d effective lead time (baseline: {baseline_lead_time}d, delay: +{supplier_delay_days}d)."
                    ),
                )
            )
            event_counter += 1

        # Advance state to next day
        current_stock = ending_stock

        timeline.append(
            DailySimulationPoint(
                day_index=t,
                date=sim_date,
                starting_stock=starting_stock,
                arrivals_today=arrivals_today,
                available_before_demand=available_before_demand,
                demand=simulated_demand,
                fulfilled_demand=fulfilled,
                unmet_demand=unmet,
                ending_stock=ending_stock,
                is_stockout=is_stockout,
                on_order_qty=in_transit_qty,
                inventory_position=inventory_position,
                reorder_triggered=reorder_triggered,
                order_quantity_placed=order_qty_placed,
                order_arrives_on_day=arrival_day_for_order,
            )
        )

    # 5. Summary Metrics Aggregation & Mathematical Reconciliation
    total_demand = sum(p.demand for p in timeline)
    total_fulfilled = sum(p.fulfilled_demand for p in timeline)
    total_unmet = sum(p.unmet_demand for p in timeline)

    # Reconciliation assertion 1: Demand conservation (fulfilled + unmet == demand)
    assert total_fulfilled + total_unmet == total_demand, (
        f"Demand reconciliation failed: fulfilled ({total_fulfilled}) + unmet ({total_unmet}) != total demand ({total_demand})"
    )

    fulfillment_rate: Optional[float] = None
    fulfillment_rate_reason: Optional[str] = None
    if total_demand > 0:
        fulfillment_rate = round((total_fulfilled / total_demand) * 100.0, 2)
    else:
        fulfillment_rate_reason = (
            "Total simulated demand is 0 units; fulfillment rate ratio is undefined."
        )
        assumptions.append(
            "Zero demand simulated across horizon; fulfillment rate is undefined (null)."
        )

    stockout_days = sum(1 for p in timeline if p.is_stockout)
    stockout_rate = round((stockout_days / horizon_days) * 100.0, 2)

    first_stockout = next((p for p in timeline if p.is_stockout), None)
    first_stockout_date = first_stockout.date if first_stockout else None
    first_stockout_day = first_stockout.day_index if first_stockout else None

    arrivals_in_horizon = sum(p.arrivals_today for p in timeline)

    # Reconciliation assertion 2: Inventory conservation (ending == starting + arrivals - fulfilled)
    starting_inv = timeline[0].starting_stock
    ending_inv = timeline[-1].ending_stock
    assert ending_inv == starting_inv + arrivals_in_horizon - total_fulfilled, (
        f"Inventory conservation reconciliation failed: ending ({ending_inv}) != "
        f"starting ({starting_inv}) + arrivals ({arrivals_in_horizon}) - fulfilled ({total_fulfilled})"
    )

    ending_in_transit = sum(
        qty for arr_day, qty in pending_arrivals.items() if arr_day > horizon_days
    )
    ending_inv_pos = ending_inv + ending_in_transit - committed_qty

    daily_end_stocks = [p.ending_stock for p in timeline]
    costs = calculate_simulation_costs(
        product=product,
        daily_ending_stocks=daily_end_stocks,
        orders_placed_count=len(events),
        total_unmet_demand=total_unmet,
    )

    metrics = ScenarioMetrics(
        starting_inventory=starting_inv,
        ending_inventory=ending_inv,
        total_demand=total_demand,
        total_fulfilled=total_fulfilled,
        total_unmet_demand=total_unmet,
        fulfillment_rate_pct=fulfillment_rate,
        fulfillment_rate_unavailable_reason=fulfillment_rate_reason,
        stockout_days_count=stockout_days,
        stockout_rate_pct=stockout_rate,
        first_stockout_date=first_stockout_date,
        first_stockout_day_index=first_stockout_day,
        replenishment_orders_count=len(events),
        total_units_ordered=total_ordered_units,
        arrivals_within_horizon_count=arrivals_in_horizon,
        ending_inventory_position=ending_inv_pos,
        baseline_reorder_point=stressed_rop.baseline_reorder_point,
        stressed_reorder_point=stressed_rop.stressed_reorder_point,
        baseline_safety_stock=stressed_rop.baseline_safety_stock,
        stressed_safety_stock=stressed_rop.stressed_safety_stock,
        cost_summary=costs,
    )

    parameters = {
        "demand_surge_pct": demand_surge_pct,
        "supplier_delay_days": supplier_delay_days,
        "horizon_days": horizon_days,
        "baseline_lead_time_days": baseline_lead_time,
        "effective_lead_time_days": effective_lead_time,
        "target_service_level": tsl,
        "minimum_order_qty": moq,
        "pack_size": pack_size,
        "eoq_available": eoq_available,
        "raw_eoq": raw_eoq,
    }

    return ScenarioSimulationResult(
        timeline=timeline,
        replenishment_events=events,
        metrics=metrics,
        stressed_rop=stressed_rop,
        parameters=parameters,
        assumptions=assumptions,
    )
