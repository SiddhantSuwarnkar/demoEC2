import datetime as dt
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DailySimulationPoint(BaseModel):
    day_index: int = Field(..., description="Day index in simulation horizon (1-based)")
    date: dt.date = Field(..., description="Simulated calendar date")
    starting_stock: int = Field(..., description="Available physical stock at beginning of day")
    arrivals_today: int = Field(
        ..., description="Replenishment orders arriving and available today"
    )
    available_before_demand: int = Field(..., description="Starting stock plus arrivals today")
    demand: int = Field(..., description="Simulated demand for today (rounded to integer units)")
    fulfilled_demand: int = Field(..., description="Demand units successfully fulfilled")
    unmet_demand: int = Field(..., description="Demand units unfulfilled (lost sales)")
    ending_stock: int = Field(..., ge=0, description="Physical on-hand inventory at end of day")
    is_stockout: bool = Field(..., description="True if ending stock is 0 and demand was unmet")
    on_order_qty: int = Field(..., ge=0, description="Outstanding replenishment orders in transit")
    inventory_position: int = Field(..., description="ending_stock + on_order_qty - committed_qty")
    reorder_triggered: bool = Field(..., description="True if replenishment order was placed today")
    order_quantity_placed: int = Field(
        default=0, ge=0, description="Units ordered today if triggered"
    )
    order_arrives_on_day: Optional[int] = Field(
        default=None, description="Day index when order will arrive"
    )

    @property
    def beginning_inventory(self) -> int:
        return self.starting_stock

    @property
    def incoming_replenishment(self) -> int:
        return self.arrivals_today

    @property
    def simulated_demand(self) -> int:
        return self.demand

    @property
    def ending_inventory(self) -> int:
        return self.ending_stock


class ReplenishmentEvent(BaseModel):
    event_id: int
    day_index: int
    order_date: dt.date
    expected_arrival_date: dt.date
    arrival_day_index: int
    order_quantity: int
    trigger_inventory_position: int
    reorder_point_threshold: int
    lead_time_days: int
    delay_days_applied: int
    effective_lead_time_days: int
    explanation: str

    @property
    def ordered_quantity(self) -> int:
        return self.order_quantity

    @property
    def trigger_reason(self) -> str:
        return self.explanation


class ScenarioCostSummary(BaseModel):
    ordering_cost_per_order: Optional[float] = None
    total_ordering_cost: Optional[float] = None
    annual_holding_cost_rate: Optional[float] = None
    holding_cost_per_unit_year: Optional[float] = None
    total_holding_cost: Optional[float] = None
    stockout_cost_per_unit: Optional[float] = None
    total_stockout_cost: Optional[float] = None
    total_inventory_cost: Optional[float] = None
    costs_fully_calculated: bool = False
    unavailable_reasons: List[str] = Field(default_factory=list)

    @property
    def total_shortage_cost(self) -> Optional[float]:
        return self.total_stockout_cost


class ScenarioMetrics(BaseModel):
    starting_inventory: int
    ending_inventory: int
    total_demand: int
    total_fulfilled: int
    total_unmet_demand: int
    fulfillment_rate_pct: Optional[float] = None
    fulfillment_rate_unavailable_reason: Optional[str] = None
    stockout_days_count: int
    stockout_rate_pct: float
    first_stockout_date: Optional[dt.date] = None
    first_stockout_day_index: Optional[int] = None
    replenishment_orders_count: int
    total_units_ordered: int
    arrivals_within_horizon_count: int
    ending_inventory_position: int
    baseline_reorder_point: int
    stressed_reorder_point: int
    baseline_safety_stock: int
    stressed_safety_stock: int
    cost_summary: ScenarioCostSummary

    @property
    def total_simulated_demand(self) -> int:
        return self.total_demand

    @property
    def total_units_fulfilled(self) -> int:
        return self.total_fulfilled

    @property
    def number_of_replenishment_orders(self) -> int:
        return self.replenishment_orders_count


class ScenarioRunRequest(BaseModel):
    scenario_name: str = Field(default="Custom Scenario", max_length=128)
    demand_surge_pct: float = Field(
        default=0.0,
        ge=0.0,
        le=500.0,
        description="Percentage increase in demand (0.0 = baseline, 25.0 = 25% surge)",
    )
    supplier_delay_days: int = Field(
        default=0,
        ge=0,
        le=90,
        description="Additional supplier delivery delay in days",
    )
    horizon_days: int = Field(
        default=30,
        ge=7,
        le=180,
        description="Simulation horizon in days (7 to 180)",
    )
    start_date: Optional[dt.date] = Field(
        default=None,
        description="Optional simulation start date (defaults to tomorrow)",
    )
    demand_source: Optional[str] = Field(
        default=None,
        description="Demand source: 'forecast', 'historical', or auto-detected if null",
    )
    forecast_run_id: Optional[int] = Field(
        default=None,
        description="Specific forecast run ID to use for demand profile",
    )
    lead_time_days_override: Optional[int] = Field(
        default=None,
        ge=1,
        le=120,
        description="Optional baseline lead time override",
    )
    target_service_level_override: Optional[float] = Field(
        default=None,
        ge=0.50,
        le=0.999,
        description="Optional target cycle service level override",
    )


# Backward-compatibility alias
ScenarioRunCreate = ScenarioRunRequest


class ScenarioRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    sku: Optional[str] = None
    product_name: Optional[str] = None
    scenario_name: str
    demand_surge_pct: float
    supplier_delay_days: int
    horizon_days: int
    baseline_recommendation_id: Optional[int] = None
    baseline_forecast_run_id: Optional[int] = None
    run_status: str = "completed"
    failure_reason: Optional[str] = None
    projected_unmet_demand: Optional[float] = None
    projected_total_cost: Optional[float] = None

    # Detailed simulation results
    demand_source_info: Dict[str, Any] = Field(default_factory=dict)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    baseline_metrics: Optional[ScenarioMetrics] = None
    scenario_metrics: Optional[ScenarioMetrics] = None
    replenishment_events: List[ReplenishmentEvent] = Field(default_factory=list)
    timeline: List[DailySimulationPoint] = Field(default_factory=list)
    assumptions_and_limitations: List[str] = Field(default_factory=list)
    created_at: dt.datetime


class ScenarioListResponse(BaseModel):
    total: int
    page: int
    limit: int
    items: List[ScenarioRunRead]


class ScenarioComparisonItem(BaseModel):
    scenario_name: str
    demand_surge_pct: float
    supplier_delay_days: int
    ending_inventory: int
    total_unmet_demand: int
    unmet_demand_difference_vs_baseline: int
    stockout_days_count: int
    stockout_days_difference_vs_baseline: int
    fulfillment_rate_pct: Optional[float] = None
    total_units_ordered: int
    total_inventory_cost: Optional[float] = None
    cost_difference_vs_baseline: Optional[float] = None
    first_stockout_date: Optional[dt.date] = None


class ScenarioComparisonRequest(BaseModel):
    horizon_days: int = Field(default=30, ge=7, le=180)
    start_date: Optional[dt.date] = Field(
        default=None,
        description="Optional simulation start date (defaults to tomorrow)",
    )
    forecast_run_id: Optional[int] = None
    scenarios: List[ScenarioRunRequest] = Field(
        ...,
        min_length=1,
        max_length=5,
        description="List of stress scenarios to simulate and compare against baseline",
    )


class ScenarioComparisonResponse(BaseModel):
    product_id: int
    sku: str
    product_name: str
    simulation_start_date: dt.date
    horizon_days: int
    baseline: ScenarioComparisonItem
    comparisons: List[ScenarioComparisonItem]
    insights: List[str] = Field(default_factory=list)
