from typing import List, Optional

from app.models.product import Product
from app.schemas.scenario import ScenarioCostSummary


def calculate_simulation_costs(
    product: Product,
    daily_ending_stocks: List[int],
    orders_placed_count: int,
    total_unmet_demand: int,
) -> ScenarioCostSummary:
    """Calculates inventory holding, ordering, and shortage costs with missing input safeguards.

    - Computes holding cost from ending daily inventory when unit holding cost is configured.
    - Computes ordering cost per replenishment event when ordering cost is configured.
    - Computes shortage cost when stockout cost per unit is configured.
    - Discloses unavailable cost components rather than fabricating financial estimates.
    """
    unavailable_reasons: List[str] = []

    # 1. Ordering Cost
    ordering_cost_unit: Optional[float] = None
    total_ordering_cost: Optional[float] = None
    if product.ordering_cost is not None and float(product.ordering_cost) > 0:
        ordering_cost_unit = round(float(product.ordering_cost), 2)
        total_ordering_cost = round(orders_placed_count * ordering_cost_unit, 2)
    else:
        unavailable_reasons.append(
            "Ordering cost per order is not configured for this product; ordering costs are excluded."
        )

    # 2. Holding Cost
    holding_cost_unit_year: Optional[float] = None
    total_holding_cost: Optional[float] = None

    if (
        product.holding_cost_per_unit_year is not None
        and float(product.holding_cost_per_unit_year) > 0
    ):
        holding_cost_unit_year = round(float(product.holding_cost_per_unit_year), 4)
    elif (
        product.unit_cost is not None
        and float(product.unit_cost) > 0
        and product.holding_cost_rate is not None
        and float(product.holding_cost_rate) > 0
    ):
        holding_cost_unit_year = round(
            float(product.unit_cost) * float(product.holding_cost_rate), 4
        )

    if holding_cost_unit_year is not None:
        daily_holding_rate = holding_cost_unit_year / 365.0
        sum_daily_stock = sum(max(0, stock) for stock in daily_ending_stocks)
        total_holding_cost = round(sum_daily_stock * daily_holding_rate, 2)
    else:
        unavailable_reasons.append(
            "Holding cost per unit/year (or unit cost * holding rate) is not configured; holding costs are excluded."
        )

    # 3. Stockout Cost
    stockout_cost_unit: Optional[float] = None
    total_stockout_cost: Optional[float] = None
    if product.stockout_cost is not None and float(product.stockout_cost) > 0:
        stockout_cost_unit = round(float(product.stockout_cost), 2)
        total_stockout_cost = round(total_unmet_demand * stockout_cost_unit, 2)
    else:
        unavailable_reasons.append(
            "Stockout cost per unit is not configured; stockout penalty costs are excluded."
        )

    # 4. Total Cost Calculation
    # Incomplete cost totals are not represented as complete financial impact.
    # Total inventory cost requires both core operational components (ordering and holding).
    total_cost: Optional[float] = None
    fully_calculated = False

    if total_ordering_cost is not None and total_holding_cost is not None:
        cost_parts = [total_ordering_cost, total_holding_cost]
        if total_stockout_cost is not None:
            cost_parts.append(total_stockout_cost)
            fully_calculated = True
        else:
            fully_calculated = product.stockout_cost is None
        total_cost = round(sum(cost_parts), 2)
    else:
        total_cost = None
        fully_calculated = False
        available_parts = [
            c
            for c in [total_ordering_cost, total_holding_cost, total_stockout_cost]
            if c is not None
        ]
        if available_parts:
            unavailable_reasons.append(
                "Total inventory cost is unavailable because required cost components are missing; "
                "partial cost totals are not presented as complete financial impact."
            )

    return ScenarioCostSummary(
        ordering_cost_per_order=ordering_cost_unit,
        total_ordering_cost=total_ordering_cost,
        annual_holding_cost_rate=float(product.holding_cost_rate)
        if product.holding_cost_rate is not None
        else None,
        holding_cost_per_unit_year=holding_cost_unit_year,
        total_holding_cost=total_holding_cost,
        stockout_cost_per_unit=stockout_cost_unit,
        total_stockout_cost=total_stockout_cost,
        total_inventory_cost=total_cost,
        costs_fully_calculated=fully_calculated,
        unavailable_reasons=unavailable_reasons,
    )
