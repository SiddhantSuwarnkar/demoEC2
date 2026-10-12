from datetime import date, timedelta
from typing import List

from sqlalchemy import select

from app.models.demand import DemandRecord
from app.models.forecast import ForecastRun
from app.models.product import Product
from app.services.simulation.costs import calculate_simulation_costs
from app.services.simulation.demand_provider import (
    DemandPoint,
    SimulationDemandProfile,
)
from app.services.simulation.engine import run_single_simulation
from app.services.simulation.reorder_point import calculate_stressed_reorder_point

# --- Helpers / Fixtures ---


def create_test_product(
    db,
    sku: str = "SKU-SIM-TEST",
    on_hand_qty: int = 100,
    safety_stock: int = 20,
    reorder_point: int = 50,
    lead_time_days: int = 5,
    min_order_qty: int = 25,
    pack_size: int = 10,
    unit_cost: float = 10.0,
    holding_cost_rate: float = 0.20,
    fixed_order_cost: float = 50.0,
) -> Product:
    prod = Product(
        sku=sku,
        product_name=f"Test Product {sku}",
        unit_of_measure="piece",
        on_hand_qty=on_hand_qty,
        safety_stock=safety_stock,
        reorder_point=reorder_point,
        lead_time_days=lead_time_days,
        min_order_qty=min_order_qty,
        pack_size=pack_size,
        unit_cost=unit_cost,
        holding_cost_rate=holding_cost_rate,
        fixed_order_cost=fixed_order_cost,
    )
    db.add(prod)
    db.commit()
    db.refresh(prod)
    return prod


def create_uniform_demand_records(
    db,
    product_id: int,
    num_days: int = 14,
    daily_qty: float = 10.0,
    start_date: date = date(2026, 1, 1),
) -> List[DemandRecord]:
    records = []
    for i in range(num_days):
        rec = DemandRecord(
            product_id=product_id,
            date=start_date + timedelta(days=i),
            quantity=daily_qty,
            promotion_flag=False,
            stockout_flag=False,
        )
        db.add(rec)
        records.append(rec)
    db.commit()
    return records


# ==============================================================================
# 9.1 Simulation Engine Unit Tests
# ==============================================================================


def test_simulation_engine_baseline_no_stockout():
    """1. Baseline scenario with high inventory completes with 0 stockouts."""
    prod = Product(
        sku="TEST-SKU",
        product_name="Baseline Item",
        on_hand_qty=200,
        lead_time_days=3,
        min_order_qty=10,
        pack_size=5,
    )
    demands = [
        DemandPoint(date=date(2026, 1, 1) + timedelta(days=i), quantity=5.0) for i in range(10)
    ]
    profile = SimulationDemandProfile(
        source_type="test_mock",
        daily_demands=demands,
        mean_daily_demand=5.0,
        daily_demand_stddev=0.0,
    )

    result = run_single_simulation(
        product=prod,
        demand_profile=profile,
        demand_surge_pct=0.0,
        supplier_delay_days=0,
        horizon_days=10,
    )

    m = result.metrics
    assert m.starting_inventory == 200
    assert m.total_simulated_demand == 50
    assert m.total_units_fulfilled == 50
    assert m.total_unmet_demand == 0
    assert m.stockout_days_count == 0
    assert m.first_stockout_date is None
    assert m.fulfillment_rate_pct == 100.0
    assert m.ending_inventory == 150


def test_simulation_engine_demand_surge_scaling():
    """2. Demand surge increases daily demand according to documented ceiling rounding."""
    prod = Product(
        sku="TEST-SURGE",
        product_name="Surge Item",
        on_hand_qty=500,
        lead_time_days=3,
    )
    # 10 days of 10 units base
    demands = [
        DemandPoint(date=date(2026, 1, 1) + timedelta(days=i), quantity=10.0) for i in range(10)
    ]
    profile = SimulationDemandProfile(
        source_type="test_mock",
        daily_demands=demands,
        mean_daily_demand=10.0,
        daily_demand_stddev=0.0,
    )

    # 25% surge -> ceil(10 * 1.25) = 13 units/day -> total 130
    res_surge = run_single_simulation(
        product=prod,
        demand_profile=profile,
        demand_surge_pct=25.0,
        supplier_delay_days=0,
        horizon_days=10,
    )

    assert res_surge.metrics.total_simulated_demand == 130
    assert res_surge.metrics.total_units_fulfilled == 130
    assert res_surge.timeline[0].simulated_demand == 13


def test_simulation_engine_supplier_delay_shifts_arrivals():
    """3. Supplier delay shifts expected replenishment arrivals."""
    prod = Product(
        sku="TEST-DELAY",
        product_name="Delay Item",
        on_hand_qty=20,
        reorder_point=30,  # Below ROP at start, triggers reorder on Day 1
        lead_time_days=4,
        min_order_qty=50,
        pack_size=10,
    )
    demands = [
        DemandPoint(date=date(2026, 1, 1) + timedelta(days=i), quantity=5.0) for i in range(15)
    ]
    profile = SimulationDemandProfile(
        source_type="test_mock",
        daily_demands=demands,
        mean_daily_demand=5.0,
        daily_demand_stddev=1.0,
    )

    # Baseline: Lead time = 4. Order triggered Day 1 (Jan 1) arrives Jan 5 (1 + 4)
    res_base = run_single_simulation(
        product=prod,
        demand_profile=profile,
        demand_surge_pct=0.0,
        supplier_delay_days=0,
        horizon_days=15,
    )
    assert len(res_base.replenishment_events) >= 1
    assert res_base.replenishment_events[0].expected_arrival_date == date(2026, 1, 5)

    # With +3 days supplier delay: Lead time = 7. Order triggered Day 1 arrives Jan 8 (1 + 7)
    res_delayed = run_single_simulation(
        product=prod,
        demand_profile=profile,
        demand_surge_pct=0.0,
        supplier_delay_days=3,
        horizon_days=15,
    )
    assert len(res_delayed.replenishment_events) >= 1
    assert res_delayed.replenishment_events[0].expected_arrival_date == date(2026, 1, 8)
    assert res_delayed.replenishment_events[0].effective_lead_time_days == 7


def test_simulation_engine_combined_surge_and_delay():
    """4. Combined surge and delay applies both demand escalation and arrival delay."""
    prod = Product(
        sku="TEST-COMBINED",
        product_name="Combined Stress Item",
        on_hand_qty=40,
        reorder_point=50,
        lead_time_days=3,
        min_order_qty=60,
    )
    demands = [
        DemandPoint(date=date(2026, 1, 1) + timedelta(days=i), quantity=10.0) for i in range(10)
    ]
    profile = SimulationDemandProfile(
        source_type="test_mock",
        daily_demands=demands,
        mean_daily_demand=10.0,
        daily_demand_stddev=1.0,
    )

    res = run_single_simulation(
        product=prod,
        demand_profile=profile,
        demand_surge_pct=50.0,  # 10 * 1.5 = 15 units/day
        supplier_delay_days=4,  # lead time = 3 + 4 = 7 days
        horizon_days=10,
    )

    assert res.metrics.total_simulated_demand == 150
    assert len(res.replenishment_events) >= 1
    first_ev = res.replenishment_events[0]
    assert first_ev.order_date == date(2026, 1, 1)
    assert first_ev.expected_arrival_date == date(2026, 1, 8)
    # Day 1: start 40, demand 15, end 25
    # Day 2: start 25, demand 15, end 10
    # Day 3: start 10, demand 15 -> 10 fulfilled, 5 unmet, end 0 (stockout!)
    assert res.metrics.stockout_days_count > 0
    assert res.metrics.total_unmet_demand > 0
    assert res.metrics.first_stockout_date == date(2026, 1, 3)


def test_simulation_engine_no_negative_on_hand_stock():
    """5. Demand exceeding inventory produces unmet demand without negative on-hand stock."""
    prod = Product(
        sku="TEST-NONEG",
        product_name="No Negative Stock",
        on_hand_qty=12,
        lead_time_days=10,
    )
    # Demand is 20 units on day 1
    demands = [DemandPoint(date=date(2026, 1, 1), quantity=20.0)]
    profile = SimulationDemandProfile(
        source_type="test_mock",
        daily_demands=demands,
        mean_daily_demand=20.0,
        daily_demand_stddev=0.0,
    )

    res = run_single_simulation(
        product=prod,
        demand_profile=profile,
        demand_surge_pct=0.0,
        supplier_delay_days=0,
        horizon_days=1,
    )

    day1 = res.timeline[0]
    assert day1.beginning_inventory == 12
    assert day1.simulated_demand == 20
    assert day1.fulfilled_demand == 12
    assert day1.unmet_demand == 8
    assert day1.ending_inventory == 0  # Physical stock is zero, never -8
    assert day1.is_stockout is True
    assert res.metrics.total_unmet_demand == 8
    assert res.metrics.ending_inventory == 0


def test_simulation_engine_replenishment_triggers_and_moq():
    """6 & 7. Replenishment triggers when inventory position <= ROP and respects MOQ."""
    prod = Product(
        sku="TEST-MOQ",
        product_name="MOQ Item",
        on_hand_qty=30,
        reorder_point=40,
        lead_time_days=2,
        min_order_qty=100,  # Deficit is small, but MOQ is 100
        pack_size=1,
    )
    demands = [
        DemandPoint(date=date(2026, 1, 1) + timedelta(days=i), quantity=5.0) for i in range(5)
    ]
    profile = SimulationDemandProfile(
        source_type="test_mock",
        daily_demands=demands,
        mean_daily_demand=5.0,
        daily_demand_stddev=1.0,
    )

    res = run_single_simulation(
        product=prod,
        demand_profile=profile,
        demand_surge_pct=0.0,
        supplier_delay_days=0,
        horizon_days=5,
    )

    assert len(res.replenishment_events) == 1
    ev = res.replenishment_events[0]
    assert ev.ordered_quantity == 100
    assert "MOQ" in ev.trigger_reason or "deficit" in ev.trigger_reason.lower()


def test_simulation_engine_pack_size_rounding():
    """8. Replenishment respects pack-size multiples."""
    prod = Product(
        sku="TEST-PACK",
        product_name="Pack Size Item",
        on_hand_qty=20,
        reorder_point=50,
        lead_time_days=2,
        min_order_qty=10,
        pack_size=24,  # Must be rounded to multiples of 24
    )
    demands = [
        DemandPoint(date=date(2026, 1, 1) + timedelta(days=i), quantity=5.0) for i in range(5)
    ]
    profile = SimulationDemandProfile(
        source_type="test_mock",
        daily_demands=demands,
        mean_daily_demand=5.0,
        daily_demand_stddev=1.0,
    )

    res = run_single_simulation(
        product=prod,
        demand_profile=profile,
        demand_surge_pct=0.0,
        supplier_delay_days=0,
        horizon_days=5,
    )

    assert len(res.replenishment_events) == 1
    ev = res.replenishment_events[0]
    # Reorder triggered when inv position falls to or below ROP (13).
    # Pack size rounds order up to multiple of 24.
    assert ev.ordered_quantity % 24 == 0
    assert ev.ordered_quantity >= 13


def test_simulation_engine_boundary_order_arrival_order():
    """9. Order arriving on simulation day is added BEFORE demand fulfillment."""
    prod = Product(
        sku="TEST-BOUNDARY",
        product_name="Boundary Item",
        on_hand_qty=5,
        reorder_point=10,
        lead_time_days=1,  # Ordered Day 1, arrives Day 2
        min_order_qty=20,
        pack_size=1,
    )
    # Day 1 demand: 5 (leaves on_hand = 0, reorder triggered, arrives Day 2)
    # Day 2 demand: 15 (if arrival occurs before demand, 15 is fulfilled from 20)
    demands = [
        DemandPoint(date=date(2026, 1, 1), quantity=5.0),
        DemandPoint(date=date(2026, 1, 2), quantity=15.0),
    ]
    profile = SimulationDemandProfile(
        source_type="test_mock",
        daily_demands=demands,
        mean_daily_demand=10.0,
        daily_demand_stddev=1.0,
    )

    res = run_single_simulation(
        product=prod,
        demand_profile=profile,
        demand_surge_pct=0.0,
        supplier_delay_days=0,
        horizon_days=2,
    )

    # Day 1: start 5, demand 5 -> end 0, triggers reorder 20 arriving Jan 2
    # Day 2: start 0 + arrival 20 = 20 available. Demand 15 -> fulfilled 15, ending 5!
    day2 = res.timeline[1]
    assert day2.beginning_inventory == 0
    assert day2.incoming_replenishment == 20
    assert day2.fulfilled_demand == 15
    assert day2.unmet_demand == 0
    assert day2.ending_inventory == 5
    assert day2.is_stockout is False


def test_simulation_engine_orders_arriving_after_horizon():
    """10. Orders arriving after horizon are tracked in events but not added to on-hand stock."""
    prod = Product(
        sku="TEST-AFTER-HORIZON",
        product_name="Long Lead Time Item",
        on_hand_qty=10,
        reorder_point=20,
        lead_time_days=10,  # Ordered Day 1, arrives Day 11 (> 5d horizon)
        min_order_qty=30,
    )
    demands = [
        DemandPoint(date=date(2026, 1, 1) + timedelta(days=i), quantity=2.0) for i in range(5)
    ]
    profile = SimulationDemandProfile(
        source_type="test_mock",
        daily_demands=demands,
        mean_daily_demand=2.0,
        daily_demand_stddev=0.5,
    )

    res = run_single_simulation(
        product=prod,
        demand_profile=profile,
        demand_surge_pct=0.0,
        supplier_delay_days=0,
        horizon_days=5,
    )

    assert res.metrics.number_of_replenishment_orders == 1
    assert res.metrics.total_units_ordered == 30
    assert res.metrics.arrivals_within_horizon_count == 0  # Arrived after day 5!
    # Ending physical stock reflects only starting stock minus demand
    assert res.metrics.ending_inventory == 0
    # Ending inventory position reflects stock (0) + on_order (30) = 30
    assert res.metrics.ending_inventory_position == 30


def test_simulation_engine_zero_demand_zero_denominator_safety():
    """11. Zero demand does not crash with ZeroDivisionError and sets fulfillment rate to null/safe."""
    prod = Product(
        sku="TEST-ZERODEMAND",
        product_name="Zero Demand Item",
        on_hand_qty=50,
        lead_time_days=5,
    )
    demands = [
        DemandPoint(date=date(2026, 1, 1) + timedelta(days=i), quantity=0.0) for i in range(7)
    ]
    profile = SimulationDemandProfile(
        source_type="test_mock",
        daily_demands=demands,
        mean_daily_demand=0.0,
        daily_demand_stddev=0.0,
    )

    res = run_single_simulation(
        product=prod,
        demand_profile=profile,
        demand_surge_pct=0.0,
        supplier_delay_days=0,
        horizon_days=7,
    )

    assert res.metrics.total_simulated_demand == 0
    assert res.metrics.fulfillment_rate_pct is None  # Safe handling, not 0% or crash!
    assert res.metrics.total_unmet_demand == 0
    assert res.metrics.stockout_days_count == 0


def test_simulation_engine_missing_cost_inputs():
    """12. Missing cost inputs disclose unavailable reasons without fabricating costs."""
    prod = Product(
        sku="TEST-NOCOSTS",
        product_name="No Cost Item",
        unit_cost=None,
        holding_cost_rate=None,
        fixed_order_cost=None,
    )
    costs = calculate_simulation_costs(
        product=prod,
        daily_ending_stocks=[],
        orders_placed_count=0,
        total_unmet_demand=10,
    )
    assert costs.total_holding_cost is None
    assert costs.total_ordering_cost is None
    assert costs.total_shortage_cost is None
    assert costs.total_inventory_cost is None
    assert len(costs.unavailable_reasons) >= 2


def test_simulation_engine_deterministic_identical_runs():
    """14. Repeated runs with identical inputs produce identical outputs."""
    prod = Product(
        sku="TEST-DETERMINISTIC",
        product_name="Deterministic Item",
        on_hand_qty=40,
        reorder_point=30,
        lead_time_days=3,
        min_order_qty=20,
    )
    demands = [
        DemandPoint(date=date(2026, 1, 1) + timedelta(days=i), quantity=8.0) for i in range(10)
    ]
    profile = SimulationDemandProfile(
        source_type="test_mock",
        daily_demands=demands,
        mean_daily_demand=8.0,
        daily_demand_stddev=1.5,
    )

    run1 = run_single_simulation(
        product=prod,
        demand_profile=profile,
        demand_surge_pct=15.0,
        supplier_delay_days=2,
        horizon_days=10,
    )
    run2 = run_single_simulation(
        product=prod,
        demand_profile=profile,
        demand_surge_pct=15.0,
        supplier_delay_days=2,
        horizon_days=10,
    )

    assert run1.metrics.model_dump() == run2.metrics.model_dump()
    assert [p.model_dump() for p in run1.timeline] == [p.model_dump() for p in run2.timeline]


def test_reorder_point_under_stress_calculation():
    """Verify stressed ROP recalculation preserves formula integrity."""
    rop_info = calculate_stressed_reorder_point(
        mean_daily_demand=10.0,
        daily_demand_stddev=2.0,
        baseline_lead_time_days=4,
        supplier_delay_days=5,  # stressed lead time = 9
        demand_surge_pct=0.0,
        target_service_level=0.95,
    )
    # Baseline: lead_time = 4. sqrt(4) = 2. SS = ceil(1.6449 * 2 * 2) = ceil(6.579) = 7
    # Baseline ROP: ceil(10 * 4 + 7) = 47
    assert rop_info.baseline_safety_stock == 7
    assert rop_info.baseline_reorder_point == 47

    # Stressed: lead_time = 9. sqrt(9) = 3. SS = ceil(1.6449 * 2 * 3) = ceil(9.869) = 10
    # Stressed ROP: ceil(10 * 9 + 10) = 100
    assert rop_info.effective_lead_time_days == 9
    assert rop_info.stressed_safety_stock == 10
    assert rop_info.stressed_reorder_point == 100
    assert rop_info.stressed_reorder_point - rop_info.baseline_reorder_point == 53


# ==============================================================================
# 9.2 API & End-to-End Persistence Tests
# ==============================================================================


def test_api_create_and_get_scenario_run(client, db_session):
    """Verify POST /api/v1/products/{product_id}/scenarios and GET /api/v1/scenarios/{id}."""
    prod = create_test_product(db_session, sku="SKU-API-RUN-1")
    create_uniform_demand_records(db_session, product_id=prod.id, num_days=14, daily_qty=12.0)

    # 1. POST Create scenario
    req_payload = {
        "scenario_name": "Mild Surge Test",
        "demand_surge_pct": 20.0,
        "supplier_delay_days": 2,
        "horizon_days": 14,
    }
    create_res = client.post(f"/api/v1/products/{prod.id}/scenarios", json=req_payload)
    assert create_res.status_code == 201, create_res.text
    scen_data = create_res.json()

    assert scen_data["product_id"] == prod.id
    assert scen_data["sku"] == prod.sku
    assert scen_data["scenario_name"] == "Mild Surge Test"
    assert scen_data["demand_surge_pct"] == 20.0
    assert scen_data["supplier_delay_days"] == 2
    assert scen_data["horizon_days"] == 14
    assert scen_data["run_status"] == "completed"
    assert scen_data["scenario_metrics"] is not None
    assert scen_data["baseline_metrics"] is not None
    assert len(scen_data["timeline"]) == 14
    assert "demand_source_info" in scen_data
    scen_id = scen_data["id"]

    # 2. GET Retrieve scenario by ID
    get_res = client.get(f"/api/v1/scenarios/{scen_id}")
    assert get_res.status_code == 200
    retrieved = get_res.json()
    assert retrieved["id"] == scen_id
    assert retrieved["scenario_name"] == "Mild Surge Test"
    assert len(retrieved["timeline"]) == 14


def test_api_list_scenarios_for_product(client, db_session):
    """Verify GET /api/v1/products/{product_id}/scenarios pagination."""
    prod = create_test_product(db_session, sku="SKU-API-LIST-1")
    create_uniform_demand_records(db_session, product_id=prod.id, num_days=10, daily_qty=10.0)

    # Create 3 scenarios
    for i in range(3):
        client.post(
            f"/api/v1/products/{prod.id}/scenarios",
            json={
                "scenario_name": f"Scenario {i}",
                "demand_surge_pct": float(i * 10),
                "supplier_delay_days": i,
                "horizon_days": 7,
            },
        )

    # List with limit=2
    list_res = client.get(f"/api/v1/products/{prod.id}/scenarios?page=1&limit=2")
    assert list_res.status_code == 200
    page1 = list_res.json()
    assert page1["total"] == 3
    assert page1["page"] == 1
    assert page1["limit"] == 2
    assert len(page1["items"]) == 2

    # Page 2
    page2_res = client.get(f"/api/v1/products/{prod.id}/scenarios?page=2&limit=2")
    assert page2_res.status_code == 200
    page2 = page2_res.json()
    assert len(page2["items"]) == 1


def test_api_compare_scenarios_vs_baseline(client, db_session):
    """Verify POST /api/v1/products/{product_id}/scenarios/compare."""
    prod = create_test_product(db_session, sku="SKU-API-COMPARE-1", on_hand_qty=60)
    create_uniform_demand_records(db_session, product_id=prod.id, num_days=14, daily_qty=15.0)

    compare_req = {
        "horizon_days": 14,
        "scenarios": [
            {
                "scenario_name": "Moderate Surge",
                "demand_surge_pct": 25.0,
                "supplier_delay_days": 0,
            },
            {
                "scenario_name": "Severe Delay",
                "demand_surge_pct": 0.0,
                "supplier_delay_days": 4,
            },
            {
                "scenario_name": "Combined Stress",
                "demand_surge_pct": 30.0,
                "supplier_delay_days": 4,
            },
        ],
    }

    comp_res = client.post(f"/api/v1/products/{prod.id}/scenarios/compare", json=compare_req)
    assert comp_res.status_code == 200, comp_res.text
    comp_data = comp_res.json()

    assert comp_data["product_id"] == prod.id
    assert comp_data["baseline"] is not None
    assert comp_data["baseline"]["demand_surge_pct"] == 0.0
    assert len(comp_data["comparisons"]) == 3
    assert len(comp_data["insights"]) == 3

    # Check that deltas are calculated vs baseline
    for item in comp_data["comparisons"]:
        assert item["unmet_demand_difference_vs_baseline"] is not None
        assert item["stockout_days_difference_vs_baseline"] is not None


def test_api_invalid_product_and_scenario_id_returns_404(client):
    """Verify non-existent product and scenario IDs return 404."""
    # Product not found
    res_p = client.post(
        "/api/v1/products/999999/scenarios",
        json={"scenario_name": "Test", "demand_surge_pct": 0, "supplier_delay_days": 0},
    )
    assert res_p.status_code == 404
    assert res_p.json()["error"]["code"] == "PRODUCT_NOT_FOUND"

    # Scenario not found
    res_s = client.get("/api/v1/scenarios/999999")
    assert res_s.status_code == 404
    assert res_s.json()["error"]["code"] == "SCENARIO_NOT_FOUND"


def test_api_validation_rejects_negative_or_excessive_inputs(client, db_session):
    """Verify API boundary rejects invalid input values with HTTP 422."""
    prod = create_test_product(db_session, sku="SKU-VALIDATION-1")

    # Negative demand surge
    res1 = client.post(
        f"/api/v1/products/{prod.id}/scenarios",
        json={"scenario_name": "Bad", "demand_surge_pct": -5.0, "supplier_delay_days": 0},
    )
    assert res1.status_code == 422

    # Excessive demand surge (> 500%)
    res2 = client.post(
        f"/api/v1/products/{prod.id}/scenarios",
        json={"scenario_name": "Bad", "demand_surge_pct": 600.0, "supplier_delay_days": 0},
    )
    assert res2.status_code == 422

    # Negative supplier delay
    res3 = client.post(
        f"/api/v1/products/{prod.id}/scenarios",
        json={"scenario_name": "Bad", "demand_surge_pct": 10.0, "supplier_delay_days": -2},
    )
    assert res3.status_code == 422

    # Excessive supplier delay (> 90 days)
    res4 = client.post(
        f"/api/v1/products/{prod.id}/scenarios",
        json={"scenario_name": "Bad", "demand_surge_pct": 10.0, "supplier_delay_days": 100},
    )
    assert res4.status_code == 422

    # Horizon too short (< 1) or too long (> 365)
    res5 = client.post(
        f"/api/v1/products/{prod.id}/scenarios",
        json={
            "scenario_name": "Bad",
            "demand_surge_pct": 0.0,
            "supplier_delay_days": 0,
            "horizon_days": 0,
        },
    )
    assert res5.status_code == 422


def test_simulation_never_mutates_real_stock_or_demand(client, db_session):
    """Verify executing scenarios never mutates on_hand_qty, demand records, or recommendations."""
    prod = create_test_product(db_session, sku="SKU-IMMUTABILITY-1", on_hand_qty=77)
    records = create_uniform_demand_records(
        db_session, product_id=prod.id, num_days=10, daily_qty=15.0
    )

    # Capture initial states
    initial_stock = prod.on_hand_qty
    initial_demands = [(r.id, r.quantity) for r in records]

    # Run heavy stress scenario
    res = client.post(
        f"/api/v1/products/{prod.id}/scenarios",
        json={
            "scenario_name": "Heavy Stress",
            "demand_surge_pct": 100.0,
            "supplier_delay_days": 14,
            "horizon_days": 30,
        },
    )
    assert res.status_code == 201

    # Re-fetch from DB
    db_session.refresh(prod)
    assert prod.on_hand_qty == initial_stock  # Real on-hand inventory untouched!

    for r_id, orig_qty in initial_demands:
        rec = db_session.execute(select(DemandRecord).where(DemandRecord.id == r_id)).scalar_one()
        assert rec.quantity == orig_qty  # Real demand records untouched!


def test_historical_scenarios_remain_immutable_after_new_imports(client, db_session):
    """Verify saved scenario snapshots are not altered by subsequent data additions."""
    prod = create_test_product(db_session, sku="SKU-IMMUTABLE-RUN-1")
    create_uniform_demand_records(db_session, product_id=prod.id, num_days=10, daily_qty=10.0)

    # 1. Run scenario
    res1 = client.post(
        f"/api/v1/products/{prod.id}/scenarios",
        json={
            "scenario_name": "Run Snapshot 1",
            "demand_surge_pct": 10.0,
            "supplier_delay_days": 1,
        },
    )
    assert res1.status_code == 201
    scen_id = res1.json()["id"]
    orig_unmet = res1.json()["projected_unmet_demand"]

    # 2. Add dramatic new demand records
    for i in range(10):
        db_session.add(
            DemandRecord(
                product_id=prod.id,
                date=date(2026, 2, 1) + timedelta(days=i),
                quantity=1000.0,
            )
        )
    db_session.commit()

    # 3. Retrieve saved scenario again
    res2 = client.get(f"/api/v1/scenarios/{scen_id}")
    assert res2.status_code == 200
    assert res2.json()["projected_unmet_demand"] == orig_unmet
    assert res2.json()["scenario_name"] == "Run Snapshot 1"


# ==============================================================================
# 9.3 Phase 6 Focused Correctness Audit Regression Tests
# ==============================================================================


def test_forecast_run_mismatched_product_rejected(client, db_session):
    """Area 1: Verify forecast_run_id belonging to another product is rejected with 422."""
    prod_a = create_test_product(db_session, sku="SKU-PROD-A")
    prod_b = create_test_product(db_session, sku="SKU-PROD-B")
    create_uniform_demand_records(db_session, product_id=prod_a.id, num_days=10)
    create_uniform_demand_records(db_session, product_id=prod_b.id, num_days=10)

    # Create forecast run for Product B
    fr_b = ForecastRun(
        product_id=prod_b.id,
        baseline_method="moving_average",
        candidate_method="prophet",
        training_start_date=date(2025, 1, 1),
        training_end_date=date(2025, 1, 7),
        holdout_start_date=date(2025, 1, 8),
        holdout_end_date=date(2025, 1, 10),
        status="completed",
        candidate_config={"status": "completed", "predictions": []},
    )
    db_session.add(fr_b)
    db_session.commit()

    # Attempt to simulate Product A with Product B's forecast run
    res = client.post(
        f"/api/v1/products/{prod_a.id}/scenarios",
        json={"scenario_name": "Mismatch Test", "forecast_run_id": fr_b.id},
    )
    assert res.status_code == 422
    assert "belongs to product ID" in res.json()["error"]["message"]


def test_forecast_run_failed_or_unavailable_status_rejected(client, db_session):
    """Area 1: Verify failed or unavailable forecast runs cannot be used for simulation."""
    prod = create_test_product(db_session, sku="SKU-FAILED-FC")
    create_uniform_demand_records(db_session, product_id=prod.id, num_days=10)

    fr_failed = ForecastRun(
        product_id=prod.id,
        baseline_method="moving_average",
        candidate_method="prophet",
        training_start_date=date(2025, 1, 1),
        training_end_date=date(2025, 1, 7),
        holdout_start_date=date(2025, 1, 8),
        holdout_end_date=date(2025, 1, 10),
        status="failed",
        failure_reason="Prophet fitting converged to NaN",
    )
    db_session.add(fr_failed)
    db_session.commit()

    res = client.post(
        f"/api/v1/products/{prod.id}/scenarios",
        json={"scenario_name": "Failed Run Test", "forecast_run_id": fr_failed.id},
    )
    assert res.status_code == 422
    assert "failed" in res.json()["error"]["message"]
    assert "cannot be used for simulation" in res.json()["error"]["message"]


def test_forecast_run_historical_holdout_dates_not_disguised_as_future_forecast(client, db_session):
    """Area 1: Verify Phase 5 historical holdout predictions cannot be disguised as future forecasts."""
    prod = create_test_product(db_session, sku="SKU-DISGUISE-CHECK")
    create_uniform_demand_records(db_session, product_id=prod.id, num_days=10)

    # Forecast run evaluated strictly on historical holdout: 2025-01-08 to 2025-01-10
    preds = [
        {"date": "2025-01-08", "predicted": 15.0},
        {"date": "2025-01-09", "predicted": 16.0},
        {"date": "2025-01-10", "predicted": 17.0},
    ]
    fr = ForecastRun(
        product_id=prod.id,
        baseline_method="moving_average",
        candidate_method="prophet",
        training_start_date=date(2025, 1, 1),
        training_end_date=date(2025, 1, 7),
        holdout_start_date=date(2025, 1, 8),
        holdout_end_date=date(2025, 1, 10),
        status="completed",
        candidate_config={"status": "completed", "predictions": preds},
    )
    db_session.add(fr)
    db_session.commit()

    # User attempts to run future simulation (omitted start_date defaults to tomorrow)
    res = client.post(
        f"/api/v1/products/{prod.id}/scenarios",
        json={"scenario_name": "Future Disguise Attempt", "forecast_run_id": fr.id},
    )
    assert res.status_code == 422
    err_msg = res.json()["error"]["message"]
    assert "historical benchmark evaluated on past holdout dates" in err_msg
    assert "cannot supply future forecasts" in err_msg


def test_forecast_run_missing_dates_detected_not_silently_filled(client, db_session):
    """Area 1: Verify missing dates in a forecast snapshot are detected rather than silently imputed."""
    prod = create_test_product(db_session, sku="SKU-MISSING-FC-DATES")
    create_uniform_demand_records(db_session, product_id=prod.id, num_days=10)

    # Predictions exist for Jan 1 and Jan 3, but Jan 2 is missing!
    preds = [
        {"date": "2025-01-01", "predicted": 10.0},
        {"date": "2025-01-03", "predicted": 12.0},
    ]
    fr = ForecastRun(
        product_id=prod.id,
        baseline_method="moving_average",
        candidate_method="prophet",
        training_start_date=date(2024, 12, 20),
        training_end_date=date(2024, 12, 31),
        holdout_start_date=date(2025, 1, 1),
        holdout_end_date=date(2025, 1, 3),
        status="completed",
        candidate_config={"status": "completed", "predictions": preds},
    )
    db_session.add(fr)
    db_session.commit()

    # Request simulation covering Jan 1 to Jan 3 (3 days)
    res = client.post(
        f"/api/v1/products/{prod.id}/scenarios",
        json={
            "scenario_name": "Missing Dates Check",
            "forecast_run_id": fr.id,
            "start_date": "2025-01-01",
            "horizon_days": 7,
        },
    )
    assert res.status_code == 422
    assert "does not contain forecast predictions for" in res.json()["error"]["message"]


def test_forecast_run_exact_historical_alignment_succeeds(client, db_session):
    """Area 1: Exact alignment between simulation dates and holdout predictions succeeds with full fidelity."""
    prod = create_test_product(db_session, sku="SKU-EXACT-FC-ALIGN")
    create_uniform_demand_records(db_session, product_id=prod.id, num_days=14)

    start_h = date(2025, 1, 1)
    preds = [
        {"date": (start_h + timedelta(days=i)).isoformat(), "predicted": float(10 + i)}
        for i in range(7)
    ]
    fr = ForecastRun(
        product_id=prod.id,
        baseline_method="moving_average",
        candidate_method="prophet",
        training_start_date=date(2024, 12, 1),
        training_end_date=date(2024, 12, 31),
        holdout_start_date=start_h,
        holdout_end_date=start_h + timedelta(days=6),
        status="completed",
        candidate_config={"status": "completed", "predictions": preds},
    )
    db_session.add(fr)
    db_session.commit()

    res = client.post(
        f"/api/v1/products/{prod.id}/scenarios",
        json={
            "scenario_name": "Exact Align Backtest",
            "forecast_run_id": fr.id,
            "start_date": "2025-01-01",
            "horizon_days": 7,
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["demand_source_info"]["source_type"] == "forecast_snapshot"
    assert data["demand_source_info"]["forecast_run_id"] == fr.id
    assert len(data["timeline"]) == 7
    # Day 1 prediction was 10, Day 7 prediction was 16
    assert data["timeline"][0]["demand"] == 10
    assert data["timeline"][6]["demand"] == 16


def test_insufficient_historical_demand_returns_clean_422(client, db_session):
    """Area 3: Product with insufficient demand history (< 2 observations) returns clean 422."""
    prod = create_test_product(db_session, sku="SKU-ONE-DEMAND")
    # Only 1 demand observation
    db_session.add(
        DemandRecord(
            product_id=prod.id,
            date=date(2026, 1, 1),
            quantity=15.0,
        )
    )
    db_session.commit()

    res = client.post(
        f"/api/v1/products/{prod.id}/scenarios",
        json={"scenario_name": "Insufficient Test", "horizon_days": 14},
    )
    assert res.status_code == 422
    assert "Insufficient demand history" in res.json()["error"]["message"]


def test_costs_partial_total_not_represented_as_complete_impact():
    """Area 4: When holding cost is missing, ordering cost alone is not represented as total financial impact."""
    prod = Product(
        sku="TEST-PARTIAL-COST",
        name="Partial Cost Product",
        ordering_cost=50.0,
        holding_cost_rate=None,
        holding_cost_per_unit_year=None,
        unit_cost=None,
    )
    costs = calculate_simulation_costs(
        product=prod,
        daily_ending_stocks=[20, 20, 20],
        orders_placed_count=2,
        total_unmet_demand=0,
    )
    assert costs.total_ordering_cost == 100.0
    assert costs.total_holding_cost is None
    # total_inventory_cost must be None, NOT 100.0!
    assert costs.total_inventory_cost is None
    assert costs.costs_fully_calculated is False
    assert any("partial cost totals are not presented" in r for r in costs.unavailable_reasons)


def test_compare_incompatible_horizon_or_demand_basis_rejected(client, db_session):
    """Area 5: Comparison request with conflicting sub-scenario horizon or demand source returns 422."""
    prod = create_test_product(db_session, sku="SKU-INCOMPAT-COMPARE")
    create_uniform_demand_records(db_session, product_id=prod.id, num_days=14)

    # 1. Conflicting sub-scenario horizon
    res1 = client.post(
        f"/api/v1/products/{prod.id}/scenarios/compare",
        json={
            "horizon_days": 14,
            "scenarios": [
                {
                    "scenario_name": "Conflict Horizon",
                    "demand_surge_pct": 10.0,
                    "supplier_delay_days": 0,
                    "horizon_days": 30,  # Conflicts with request.horizon_days = 14!
                }
            ],
        },
    )
    assert res1.status_code == 422
    assert res1.json()["error"]["code"] == "INCOMPATIBLE_COMPARISON_HORIZON"

    # 2. Conflicting sub-scenario forecast_run_id
    res2 = client.post(
        f"/api/v1/products/{prod.id}/scenarios/compare",
        json={
            "horizon_days": 14,
            "forecast_run_id": None,
            "scenarios": [
                {
                    "scenario_name": "Conflict Demand",
                    "demand_surge_pct": 10.0,
                    "supplier_delay_days": 0,
                    "forecast_run_id": 999,  # Conflicts with parent demand basis!
                }
            ],
        },
    )
    assert res2.status_code == 422
    assert res2.json()["error"]["code"] == "INCOMPATIBLE_DEMAND_BASIS"


def test_list_all_scenarios_global_and_filtered(client, db_session):
    """Verify GET /scenarios returns paginated runs globally or filtered by product_id."""
    prod1 = create_test_product(db_session, sku="SKU-SCEN-GLOB-1")
    prod2 = create_test_product(db_session, sku="SKU-SCEN-GLOB-2")
    create_uniform_demand_records(db_session, product_id=prod1.id, num_days=14)
    create_uniform_demand_records(db_session, product_id=prod2.id, num_days=14)

    client.post(
        f"/api/v1/products/{prod1.id}/scenarios",
        json={"scenario_name": "Glob Scen 1", "demand_surge_pct": 10.0},
    )
    client.post(
        f"/api/v1/products/{prod2.id}/scenarios",
        json={"scenario_name": "Glob Scen 2", "demand_surge_pct": 20.0},
    )

    # Global list
    resp_all = client.get("/api/v1/scenarios")
    assert resp_all.status_code == 200
    data_all = resp_all.json()
    assert data_all["total"] >= 2
    skus = {item["sku"] for item in data_all["items"]}
    assert "SKU-SCEN-GLOB-1" in skus
    assert "SKU-SCEN-GLOB-2" in skus

    # Filtered by product_id
    resp_p1 = client.get(f"/api/v1/scenarios?product_id={prod1.id}")
    assert resp_p1.status_code == 200
    data_p1 = resp_p1.json()
    assert data_p1["total"] >= 1
    assert all(item["product_id"] == prod1.id for item in data_p1["items"])
