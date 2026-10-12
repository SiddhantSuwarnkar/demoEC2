import logging
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppException
from app.models.product import Product
from app.models.scenario import ScenarioRun
from app.schemas.scenario import (
    DailySimulationPoint,
    ReplenishmentEvent,
    ScenarioComparisonItem,
    ScenarioComparisonRequest,
    ScenarioComparisonResponse,
    ScenarioListResponse,
    ScenarioMetrics,
    ScenarioRunRead,
    ScenarioRunRequest,
)
from app.services.forecasting.data_prep import ProductNotFoundError
from app.services.simulation.demand_provider import resolve_simulation_demand
from app.services.simulation.engine import run_single_simulation

logger = logging.getLogger("stocksense.simulation.service")


class ScenarioNotFoundError(AppException):
    def __init__(self, scenario_id: int):
        super().__init__(
            message=f"Scenario run with ID {scenario_id} not found.",
            code="SCENARIO_NOT_FOUND",
            status_code=404,
        )


class ScenarioService:
    @staticmethod
    def run_scenario_for_product(
        db: Session,
        product_id: int,
        request: ScenarioRunRequest,
    ) -> ScenarioRunRead:
        """Executes a What-If scenario simulation and persists an immutable snapshot."""
        product = db.execute(select(Product).where(Product.id == product_id)).scalar_one_or_none()
        if not product:
            raise ProductNotFoundError(product_id)

        # 1. Resolve demand profile
        demand_profile = resolve_simulation_demand(
            db=db,
            product=product,
            horizon_days=request.horizon_days,
            forecast_run_id=request.forecast_run_id,
            start_date=request.start_date,
        )

        # 2. Run Baseline simulation (0% surge, 0d delay)
        baseline_sim = run_single_simulation(
            product=product,
            demand_profile=demand_profile,
            demand_surge_pct=0.0,
            supplier_delay_days=0,
            horizon_days=request.horizon_days,
            lead_time_days_override=request.lead_time_days_override,
            target_service_level_override=request.target_service_level_override,
        )

        # 3. Run Stressed simulation (if parameters differ from baseline)
        if request.demand_surge_pct > 0.0 or request.supplier_delay_days > 0:
            stress_sim = run_single_simulation(
                product=product,
                demand_profile=demand_profile,
                demand_surge_pct=request.demand_surge_pct,
                supplier_delay_days=request.supplier_delay_days,
                horizon_days=request.horizon_days,
                lead_time_days_override=request.lead_time_days_override,
                target_service_level_override=request.target_service_level_override,
            )
            active_sim = stress_sim
        else:
            active_sim = baseline_sim

        # 4. Prepare JSON payloads for persistence
        timeline_json = [p.model_dump(mode="json") for p in active_sim.timeline]
        events_json = [e.model_dump(mode="json") for e in active_sim.replenishment_events]
        demand_source_info = {
            "source_type": demand_profile.source_type,
            "forecast_run_id": demand_profile.forecast_run_id,
            "forecast_method": demand_profile.forecast_method,
            "mean_daily_demand": demand_profile.mean_daily_demand,
            "daily_demand_stddev": demand_profile.daily_demand_stddev,
            "metadata": demand_profile.metadata,
        }

        scenario_config = {
            "demand_source_info": demand_source_info,
            "assumptions_and_limitations": active_sim.assumptions,
            "timeline": timeline_json,
            "replenishment_events": events_json,
        }

        results_summary = {
            "unmet_demand_difference": active_sim.metrics.total_unmet_demand
            - baseline_sim.metrics.total_unmet_demand,
            "stockout_days_difference": active_sim.metrics.stockout_days_count
            - baseline_sim.metrics.stockout_days_count,
            "orders_count": len(active_sim.replenishment_events),
            "total_units_ordered": active_sim.metrics.total_units_ordered,
            "cost_summary": active_sim.metrics.cost_summary.model_dump(mode="json"),
        }

        # 5. Persist to scenario_runs table
        scenario_run = ScenarioRun(
            product_id=product_id,
            scenario_name=request.scenario_name,
            demand_surge_pct=request.demand_surge_pct,
            supplier_delay_days=request.supplier_delay_days,
            horizon_days=request.horizon_days,
            baseline_forecast_run_id=demand_profile.forecast_run_id,
            projected_unmet_demand=float(active_sim.metrics.total_unmet_demand),
            projected_total_cost=active_sim.metrics.cost_summary.total_inventory_cost,
            parameters=active_sim.parameters,
            scenario_config=scenario_config,
            baseline_metrics=baseline_sim.metrics.model_dump(mode="json"),
            scenario_metrics=active_sim.metrics.model_dump(mode="json"),
            results_summary=results_summary,
            run_status="completed",
        )

        try:
            db.add(scenario_run)
            db.commit()
            db.refresh(scenario_run)
        except Exception:
            db.rollback()
            raise

        return ScenarioService._map_from_db(scenario_run, product)

    @staticmethod
    def get_scenario(db: Session, scenario_id: int) -> ScenarioRunRead:
        """Retrieves a persisted scenario snapshot by ID without recomputation."""
        run = db.execute(
            select(ScenarioRun).where(ScenarioRun.id == scenario_id)
        ).scalar_one_or_none()
        if not run:
            raise ScenarioNotFoundError(scenario_id)

        product = db.execute(select(Product).where(Product.id == run.product_id)).scalar_one()
        return ScenarioService._map_from_db(run, product)

    @staticmethod
    def list_scenarios(
        db: Session,
        product_id: Optional[int] = None,
        page: int = 1,
        limit: int = 20,
    ) -> ScenarioListResponse:
        """Lists paginated historical scenarios, optionally filtered by product_id."""
        stmt = select(ScenarioRun)
        count_stmt = select(func.count(ScenarioRun.id))

        if product_id is not None:
            product = db.execute(
                select(Product).where(Product.id == product_id)
            ).scalar_one_or_none()
            if not product:
                raise ProductNotFoundError(product_id)
            stmt = stmt.where(ScenarioRun.product_id == product_id)
            count_stmt = count_stmt.where(ScenarioRun.product_id == product_id)

        total = db.scalar(count_stmt) or 0

        runs = (
            db.execute(stmt.order_by(ScenarioRun.id.desc()).offset((page - 1) * limit).limit(limit))
            .scalars()
            .all()
        )

        if not runs:
            return ScenarioListResponse(total=total, page=page, limit=limit, items=[])

        # Batch load products to prevent N+1 queries
        product_ids = {r.product_id for r in runs}
        products = {
            p.id: p
            for p in db.execute(select(Product).where(Product.id.in_(product_ids))).scalars().all()
        }

        items = [
            ScenarioService._map_from_db(r, products[r.product_id])
            for r in runs
            if r.product_id in products
        ]
        return ScenarioListResponse(
            total=total,
            page=page,
            limit=limit,
            items=items,
        )

    @staticmethod
    def list_scenarios_for_product(
        db: Session,
        product_id: int,
        page: int = 1,
        limit: int = 20,
    ) -> ScenarioListResponse:
        """Lists paginated historical scenarios for a product (backward-compatible delegate)."""
        return ScenarioService.list_scenarios(db=db, product_id=product_id, page=page, limit=limit)

    @staticmethod
    def compare_scenarios(
        db: Session,
        product_id: int,
        request: ScenarioComparisonRequest,
    ) -> ScenarioComparisonResponse:
        """Compares a baseline scenario against multiple stress scenarios on identical starting inventory and horizon."""
        product = db.execute(select(Product).where(Product.id == product_id)).scalar_one_or_none()
        if not product:
            raise ProductNotFoundError(product_id)

        demand_profile = resolve_simulation_demand(
            db=db,
            product=product,
            horizon_days=request.horizon_days,
            forecast_run_id=request.forecast_run_id,
            start_date=request.start_date,
        )

        sim_start_date = demand_profile.daily_demands[0].date

        # 1. Baseline Simulation
        baseline_sim = run_single_simulation(
            product=product,
            demand_profile=demand_profile,
            demand_surge_pct=0.0,
            supplier_delay_days=0,
            horizon_days=request.horizon_days,
        )

        b_metrics = baseline_sim.metrics
        b_item = ScenarioComparisonItem(
            scenario_name="Baseline (0% surge, 0d delay)",
            demand_surge_pct=0.0,
            supplier_delay_days=0,
            ending_inventory=b_metrics.ending_inventory,
            total_unmet_demand=b_metrics.total_unmet_demand,
            unmet_demand_difference_vs_baseline=0,
            stockout_days_count=b_metrics.stockout_days_count,
            stockout_days_difference_vs_baseline=0,
            fulfillment_rate_pct=b_metrics.fulfillment_rate_pct,
            total_units_ordered=b_metrics.total_units_ordered,
            total_inventory_cost=b_metrics.cost_summary.total_inventory_cost,
            cost_difference_vs_baseline=0.0
            if b_metrics.cost_summary.total_inventory_cost is not None
            else None,
            first_stockout_date=b_metrics.first_stockout_date,
        )

        comparisons: List[ScenarioComparisonItem] = []
        insights: List[str] = []

        # 2. Stress Scenarios
        for req_scen in request.scenarios:
            if (
                "horizon_days" in req_scen.model_fields_set
                and req_scen.horizon_days != request.horizon_days
            ):
                raise AppException(
                    message=(
                        f"Incompatible comparison: scenario '{req_scen.scenario_name}' specifies horizon "
                        f"({req_scen.horizon_days}d) which conflicts with comparison horizon ({request.horizon_days}d)."
                    ),
                    code="INCOMPATIBLE_COMPARISON_HORIZON",
                    status_code=422,
                )

            if (
                req_scen.forecast_run_id is not None
                and req_scen.forecast_run_id != request.forecast_run_id
            ):
                raise AppException(
                    message=(
                        f"Incompatible comparison: scenario '{req_scen.scenario_name}' specifies forecast_run_id "
                        f"({req_scen.forecast_run_id}) which conflicts with comparison demand basis ({request.forecast_run_id})."
                    ),
                    code="INCOMPATIBLE_DEMAND_BASIS",
                    status_code=422,
                )

            s_sim = run_single_simulation(
                product=product,
                demand_profile=demand_profile,
                demand_surge_pct=req_scen.demand_surge_pct,
                supplier_delay_days=req_scen.supplier_delay_days,
                horizon_days=request.horizon_days,
                lead_time_days_override=req_scen.lead_time_days_override,
                target_service_level_override=req_scen.target_service_level_override,
            )
            s_m = s_sim.metrics

            unmet_diff = s_m.total_unmet_demand - b_metrics.total_unmet_demand
            stockout_diff = s_m.stockout_days_count - b_metrics.stockout_days_count

            cost_diff = None
            if (
                s_m.cost_summary.total_inventory_cost is not None
                and b_metrics.cost_summary.total_inventory_cost is not None
            ):
                cost_diff = round(
                    s_m.cost_summary.total_inventory_cost
                    - b_metrics.cost_summary.total_inventory_cost,
                    2,
                )

            comp_item = ScenarioComparisonItem(
                scenario_name=req_scen.scenario_name,
                demand_surge_pct=req_scen.demand_surge_pct,
                supplier_delay_days=req_scen.supplier_delay_days,
                ending_inventory=s_m.ending_inventory,
                total_unmet_demand=s_m.total_unmet_demand,
                unmet_demand_difference_vs_baseline=unmet_diff,
                stockout_days_count=s_m.stockout_days_count,
                stockout_days_difference_vs_baseline=stockout_diff,
                fulfillment_rate_pct=s_m.fulfillment_rate_pct,
                total_units_ordered=s_m.total_units_ordered,
                total_inventory_cost=s_m.cost_summary.total_inventory_cost,
                cost_difference_vs_baseline=cost_diff,
                first_stockout_date=s_m.first_stockout_date,
            )
            comparisons.append(comp_item)

            # Generate narrative insight
            if unmet_diff > 0:
                insights.append(
                    f"'{req_scen.scenario_name}' (+{req_scen.demand_surge_pct}% surge, +{req_scen.supplier_delay_days}d delay) "
                    f"results in {unmet_diff} additional unmet units and {stockout_diff} additional stockout days vs. baseline."
                )
            elif stockout_diff > 0:
                insights.append(
                    f"'{req_scen.scenario_name}' introduces {stockout_diff} stockout days without increasing total unmet demand."
                )
            else:
                insights.append(
                    f"'{req_scen.scenario_name}' maintained 100% stock availability equivalent to baseline."
                )

        return ScenarioComparisonResponse(
            product_id=product.id,
            sku=product.sku,
            product_name=product.product_name,
            simulation_start_date=sim_start_date,
            horizon_days=request.horizon_days,
            baseline=b_item,
            comparisons=comparisons,
            insights=insights,
        )

    @staticmethod
    def _map_from_db(run: ScenarioRun, product: Product) -> ScenarioRunRead:
        config = run.scenario_config or {}
        b_metrics_data = run.baseline_metrics or {}
        s_metrics_data = run.scenario_metrics or {}

        b_metrics = ScenarioMetrics(**b_metrics_data) if b_metrics_data else None
        s_metrics = ScenarioMetrics(**s_metrics_data) if s_metrics_data else None

        timeline = [DailySimulationPoint(**pt) for pt in config.get("timeline", [])]
        events = [ReplenishmentEvent(**ev) for ev in config.get("replenishment_events", [])]

        return ScenarioRunRead(
            id=run.id,
            product_id=run.product_id,
            sku=product.sku,
            product_name=product.product_name,
            scenario_name=run.scenario_name,
            demand_surge_pct=run.demand_surge_pct,
            supplier_delay_days=run.supplier_delay_days,
            horizon_days=run.horizon_days,
            baseline_recommendation_id=run.baseline_recommendation_id,
            baseline_forecast_run_id=run.baseline_forecast_run_id,
            run_status=run.run_status,
            failure_reason=run.failure_reason,
            projected_unmet_demand=run.projected_unmet_demand,
            projected_total_cost=run.projected_total_cost,
            demand_source_info=config.get("demand_source_info", {}),
            parameters=run.parameters or {},
            baseline_metrics=b_metrics,
            scenario_metrics=s_metrics,
            replenishment_events=events,
            timeline=timeline,
            assumptions_and_limitations=config.get("assumptions_and_limitations", []),
            created_at=run.created_at,
        )
