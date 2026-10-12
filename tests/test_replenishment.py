"""StockSense - Phase 3 Automated Replenishment Engine Tests.

Comprehensive test suite covering all 19 Phase 3 requirements:
1. Safety stock with constant lead time
2. Safety stock with variable lead time
3. Reorder point
4. Inventory position under supported schema
5. No order when inventory position is above the reorder point
6. Replenishment trigger when inventory position is at or below the reorder point
7. EOQ with valid positive inputs
8. EOQ unavailable when required costs are missing or invalid
9. MOQ adjustment
10. Pack-size rounding
11. Zero and constant demand
12. Missing dates not silently treated as zero demand
13. Stockout-affected demand warnings and handling policy
14. Insufficient demand history
15. Invalid service level and invalid lead-time values
16. Missing product and missing supplier settings
17. Recommendation persistence and retrieval
18. Deterministic repeat calculations for identical inputs
19. Regression tests confirming Phase 1 and Phase 2 behavior remains intact
"""

import math
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.demand import DemandRecord
from app.models.product import Product
from app.services.replenishment_calculator import (
    DemandObservation,
    InsufficientDemandHistoryError,
    InvalidReplenishmentParameterError,
    ReplenishmentInputs,
    calculate_demand_statistics,
    calculate_eoq,
    calculate_reorder_point,
    calculate_replenishment,
    calculate_safety_stock,
    calculate_z_factor,
)

# -------------------------------------------------------------------------
# Pure Calculation Engine Tests
# -------------------------------------------------------------------------


def test_safety_stock_constant_lead_time():
    """Test 1: Safety stock with constant lead time using Z * sigma_d * sqrt(L)."""
    # L = 9 days, sigma_L = 0.0, sigma_d = 4.0, target CSL = 0.95 (Z ≈ 1.644854)
    # raw SS = 1.644854 * 4.0 * sqrt(9) = 1.644854 * 12 = 19.7382
    # int SS (ceil) = 20
    z = calculate_z_factor(0.95)
    raw_ss, int_ss, model = calculate_safety_stock(
        mean_daily_demand=25.0,
        daily_demand_stddev=4.0,
        lead_time_days=9.0,
        lead_time_stddev_days=0.0,
        z_factor=z,
    )
    assert model == "constant_lead_time"
    assert math.isclose(raw_ss, 19.7382, rel_tol=1e-3)
    assert int_ss == 20


def test_safety_stock_variable_lead_time():
    """Test 2: Safety stock with variable lead time using Z * sqrt(L * sigma_d^2 + d_bar^2 * sigma_L^2)."""
    # mean_d = 10.0, sigma_d = 2.0
    # L = 4.0, sigma_L = 1.5
    # Variance term = 4 * 4 + 100 * 2.25 = 16 + 225 = 241
    # sqrt(241) ≈ 15.524175
    # Z(0.95) ≈ 1.644854
    # raw SS = 1.644854 * 15.524175 ≈ 25.5350
    # int SS = 26
    z = calculate_z_factor(0.95)
    raw_ss, int_ss, model = calculate_safety_stock(
        mean_daily_demand=10.0,
        daily_demand_stddev=2.0,
        lead_time_days=4.0,
        lead_time_stddev_days=1.5,
        z_factor=z,
    )
    assert model == "variable_lead_time"
    assert math.isclose(raw_ss, 25.5350, rel_tol=1e-3)
    assert int_ss == 26


def test_reorder_point_calculation():
    """Test 3: Reorder point (ROP) = expected lead-time demand + safety stock."""
    mean_d = 12.5
    lead_time = 6.0
    safety_stock = 15
    # expected LTD = 12.5 * 6.0 = 75.0
    # ROP = ceil(75.0 + 15) = 90
    expected_ltd, rop = calculate_reorder_point(
        mean_daily_demand=mean_d,
        lead_time_days=lead_time,
        safety_stock=safety_stock,
    )
    assert expected_ltd == 75.0
    assert rop == 90


def test_inventory_position_under_supported_schema():
    """Test 4: Inventory position = on_hand_qty - committed_qty (incoming POs documented as un-tracked)."""
    inputs = ReplenishmentInputs(
        product_id=1,
        sku="TEST-SKU",
        product_name="Test Product",
        on_hand_qty=120,
        committed_qty=35,
        lead_time_days=5,
        target_service_level=0.95,
    )
    obs = [
        DemandObservation(date=date(2026, 9, 1), quantity=10.0),
        DemandObservation(date=date(2026, 9, 2), quantity=12.0),
        DemandObservation(date=date(2026, 9, 3), quantity=14.0),
    ]
    res = calculate_replenishment(inputs, obs)
    assert res.inventory_position == 85
    assert (
        "Incoming open purchase orders are not currently tracked"
        in res.assumptions["inventory_position"]["limitation"]
    )


def test_no_order_when_inventory_position_above_rop():
    """Test 5: If inventory position > ROP, recommend 0 units under selected policy."""
    inputs = ReplenishmentInputs(
        product_id=1,
        sku="TEST-HEALTHY",
        product_name="Healthy Product",
        on_hand_qty=200,
        committed_qty=10,
        lead_time_days=4,
        target_service_level=0.95,
        ordering_cost=Decimal("50.00"),
        holding_cost_per_unit_year=Decimal("5.00"),
        minimum_order_qty=20,
        pack_size=5,
    )
    # mean demand = 10, LTD = 40, SS ≈ 10, ROP ≈ 50. Inventory pos = 190 >> 50.
    obs = [
        DemandObservation(date=date(2026, 9, 1), quantity=10.0),
        DemandObservation(date=date(2026, 9, 2), quantity=10.0),
        DemandObservation(date=date(2026, 9, 3), quantity=10.0),
    ]
    res = calculate_replenishment(inputs, obs)
    assert res.inventory_position == 190
    assert res.reorder_point < res.inventory_position
    assert res.reorder_triggered is False
    assert res.calculation_status == "healthy"
    assert res.recommended_order_quantity == 0
    assert "No replenishment order is currently recommended" in res.explanation


def test_replenishment_trigger_when_inventory_position_at_or_below_rop():
    """Test 6: If inventory position <= ROP, trigger replenishment and propose quantity."""
    inputs = ReplenishmentInputs(
        product_id=1,
        sku="TEST-TRIGGER",
        product_name="Triggered Product",
        on_hand_qty=30,
        committed_qty=10,  # inventory position = 20
        lead_time_days=5,
        target_service_level=0.95,
    )
    # mean demand = 10, LTD = 50. ROP > 50. Inventory pos = 20 <= ROP -> trigger!
    obs = [
        DemandObservation(date=date(2026, 9, 1), quantity=10.0),
        DemandObservation(date=date(2026, 9, 2), quantity=10.0),
    ]
    res = calculate_replenishment(inputs, obs)
    assert res.reorder_triggered is True
    assert res.calculation_status == "reorder_now"
    assert res.recommended_order_quantity > 0
    assert res.urgency in ("medium", "high", "critical")


def test_eoq_with_valid_positive_inputs():
    """Test 7: EOQ calculation with valid positive annual demand, ordering cost, and holding cost."""
    # mean daily demand = 10 -> Annual D = 3650
    # S = 50.00, H = 4.00
    # EOQ = sqrt(2 * 3650 * 50 / 4) = sqrt(91250) ≈ 302.076
    raw_eoq, available, reason = calculate_eoq(
        mean_daily_demand=10.0,
        ordering_cost=Decimal("50.00"),
        holding_cost_per_unit_year=Decimal("4.00"),
    )
    assert available is True
    assert reason is None
    assert raw_eoq is not None
    assert math.isclose(raw_eoq, 302.076, rel_tol=1e-3)


def test_eoq_unavailable_when_required_costs_missing_or_invalid():
    """Test 8: EOQ marked unavailable with clear reason when required costs are missing or <= 0."""
    # 1. Missing ordering cost
    eoq1, avail1, reason1 = calculate_eoq(
        mean_daily_demand=10.0,
        ordering_cost=None,
        holding_cost_per_unit_year=Decimal("5.00"),
    )
    assert avail1 is False
    assert eoq1 is None
    assert "Ordering cost ($S) is missing" in reason1

    # 2. Missing holding cost
    eoq2, avail2, reason2 = calculate_eoq(
        mean_daily_demand=10.0,
        ordering_cost=Decimal("50.00"),
        holding_cost_per_unit_year=None,
    )
    assert avail2 is False
    assert eoq2 is None
    assert "Annual holding cost per unit ($H) is missing" in reason2

    # 3. Non-positive holding cost (0 or negative)
    eoq3, avail3, reason3 = calculate_eoq(
        mean_daily_demand=10.0,
        ordering_cost=Decimal("50.00"),
        holding_cost_per_unit_year=Decimal("0.00"),
    )
    assert avail3 is False
    assert eoq3 is None
    assert "holding cost per unit ($H) is missing or non-positive" in reason3

    # 4. Zero demand
    eoq4, avail4, reason4 = calculate_eoq(
        mean_daily_demand=0.0,
        ordering_cost=Decimal("50.00"),
        holding_cost_per_unit_year=Decimal("5.00"),
    )
    assert avail4 is False
    assert eoq4 is None
    assert "Annual demand is non-positive" in reason4


def test_moq_adjustment():
    """Test 9: Minimum Order Quantity (MOQ) adjusts quantity upward when baseline < MOQ."""
    inputs = ReplenishmentInputs(
        product_id=1,
        sku="TEST-MOQ",
        product_name="MOQ Product",
        on_hand_qty=20,
        committed_qty=0,
        lead_time_days=3,
        target_service_level=0.95,
        minimum_order_qty=50,  # MOQ is 50
    )
    # mean demand = 10, LTD = 30, SS = 0. ROP = 30. Deficit = 10.
    # Without costs, baseline = 10. MOQ = 50 -> must bump to 50!
    obs = [
        DemandObservation(date=date(2026, 9, 1), quantity=10.0),
        DemandObservation(date=date(2026, 9, 2), quantity=10.0),
    ]
    res = calculate_replenishment(inputs, obs)
    assert res.reorder_triggered is True
    assert res.proposed_baseline_quantity == 10
    assert res.moq_applied is True
    assert res.recommended_order_quantity == 50
    assert "Quantity adjusted from 10 to 50 units" in res.explanation


def test_pack_size_rounding():
    """Test 10: Pack size rounds proposed order up to the nearest integer multiple."""
    inputs = ReplenishmentInputs(
        product_id=1,
        sku="TEST-PACK",
        product_name="Pack Product",
        on_hand_qty=15,
        committed_qty=0,
        lead_time_days=3,
        target_service_level=0.95,
        pack_size=12,  # Pack size is 12
    )
    # mean demand = 10, LTD = 30, SS = 0. ROP = 30. Deficit = 15.
    # Baseline = 15. Pack size 12 -> ceil(15/12)*12 = 24!
    obs = [
        DemandObservation(date=date(2026, 9, 1), quantity=10.0),
        DemandObservation(date=date(2026, 9, 2), quantity=10.0),
    ]
    res = calculate_replenishment(inputs, obs)
    assert res.reorder_triggered is True
    assert res.proposed_baseline_quantity == 15
    assert res.pack_size_applied is True
    assert res.recommended_order_quantity == 24
    assert "Quantity rounded up from 15 to 24 units" in res.explanation


def test_zero_and_constant_demand():
    """Test 11: Correct statistical handling of constant demand and zero demand."""
    # 1. Constant demand (sigma_d = 0)
    obs_const = [
        DemandObservation(date=date(2026, 9, 1), quantity=20.0),
        DemandObservation(date=date(2026, 9, 2), quantity=20.0),
        DemandObservation(date=date(2026, 9, 3), quantity=20.0),
    ]
    stats_const = calculate_demand_statistics(obs_const)
    assert stats_const.mean_daily_demand == 20.0
    assert stats_const.daily_demand_stddev == 0.0

    # Safety stock under constant demand and constant lead time must be 0
    raw_ss, int_ss, _ = calculate_safety_stock(20.0, 0.0, 5.0, 0.0, 1.644854)
    assert raw_ss == 0.0
    assert int_ss == 0

    # 2. Zero demand (mean = 0, stddev = 0)
    obs_zero = [
        DemandObservation(date=date(2026, 9, 1), quantity=0.0),
        DemandObservation(date=date(2026, 9, 2), quantity=0.0),
    ]
    stats_zero = calculate_demand_statistics(obs_zero)
    assert stats_zero.mean_daily_demand == 0.0
    assert stats_zero.daily_demand_stddev == 0.0


def test_missing_dates_not_silently_treated_as_zero_demand():
    """Test 12: Missing dates are not silently filled with zero sales; statistics computed strictly from observations."""
    obs_gap = [
        DemandObservation(date=date(2026, 9, 1), quantity=20.0),
        DemandObservation(date=date(2026, 9, 10), quantity=30.0),  # 8 calendar days missing between
    ]
    stats = calculate_demand_statistics(obs_gap)
    # Mean of [20, 30] is 25.0, NOT 50 / 10 = 5.0!
    assert stats.mean_daily_demand == 25.0
    assert stats.recorded_observations_count == 2
    assert stats.calendar_span_days == 10
    assert stats.missing_calendar_dates_count == 8
    assert any("missing calendar date(s)" in w for w in stats.data_quality_warnings)


def test_stockout_affected_demand_warnings_and_policy():
    """Test 13: Observations with stockout_flag=True generate explicit data quality warnings."""
    obs_stockout = [
        DemandObservation(date=date(2026, 9, 1), quantity=15.0, stockout_flag=False),
        DemandObservation(date=date(2026, 9, 2), quantity=0.0, stockout_flag=True),
        DemandObservation(date=date(2026, 9, 3), quantity=2.0, stockout_flag=True),
        DemandObservation(date=date(2026, 9, 4), quantity=18.0, stockout_flag=False),
    ]
    stats = calculate_demand_statistics(obs_stockout)
    assert stats.stockout_observations_count == 2
    assert stats.stockout_ratio == 0.5
    assert any("occurred during out-of-stock periods" in w for w in stats.data_quality_warnings)


def test_insufficient_demand_history():
    """Test 14: Insufficient demand history raises InsufficientDemandHistoryError."""
    # 0 observations
    with pytest.raises(InsufficientDemandHistoryError, match="No demand records"):
        calculate_demand_statistics([])

    # 1 observation
    with pytest.raises(
        InsufficientDemandHistoryError, match="At least 2 historical demand observations"
    ):
        calculate_demand_statistics([DemandObservation(date=date(2026, 9, 1), quantity=10.0)])


def test_invalid_service_level_and_lead_time_values():
    """Test 15: Invalid service levels and lead times raise InvalidReplenishmentParameterError."""
    # Service level <= 0 or >= 1
    with pytest.raises(
        InvalidReplenishmentParameterError, match="Target service level must be strictly between"
    ):
        calculate_z_factor(0.0)

    with pytest.raises(
        InvalidReplenishmentParameterError, match="Target service level must be strictly between"
    ):
        calculate_z_factor(1.0)

    with pytest.raises(
        InvalidReplenishmentParameterError, match="Target service level must be strictly between"
    ):
        calculate_z_factor(1.2)

    # Lead time <= 0
    with pytest.raises(
        InvalidReplenishmentParameterError, match="Average supplier lead time must be positive"
    ):
        calculate_safety_stock(10.0, 2.0, 0.0, 0.0, 1.644854)

    # Negative lead time stddev
    with pytest.raises(
        InvalidReplenishmentParameterError, match="Lead time standard deviation cannot be negative"
    ):
        calculate_safety_stock(10.0, 2.0, 5.0, -1.0, 1.644854)


# -------------------------------------------------------------------------
# API & Database Integration Tests
# -------------------------------------------------------------------------


def test_missing_product_and_missing_supplier_settings_api(client: TestClient, db_session: Session):
    """Test 16: API handles missing product (404) and missing supplier lead time (422) explicitly."""
    # 1. Product does not exist -> 404 Not Found
    res_404 = client.post("/api/v1/products/999999/recommendations/calculate")
    assert res_404.status_code == 404
    assert "not found" in res_404.json()["error"]["message"].lower()

    # 2. Product exists but has no lead time configured -> 422 Unprocessable Entity
    prod = Product(
        sku="NO-LEAD-TIME",
        name="No Lead Time Product",
        unit_of_measure="piece",
        current_stock=100,
        lead_time_days=None,  # Missing supplier setting!
    )
    db_session.add(prod)
    db_session.commit()
    db_session.refresh(prod)

    # Add demand records
    for i in range(5):
        db_session.add(
            DemandRecord(
                product_id=prod.id,
                date=date(2026, 9, 1) + timedelta(days=i),
                quantity=10.0,
            )
        )
    db_session.commit()

    res_422 = client.post(f"/api/v1/products/{prod.id}/recommendations/calculate")
    assert res_422.status_code == 422
    assert "no supplier lead time configured" in res_422.json()["error"]["message"].lower()


def test_recommendation_persistence_and_retrieval(client: TestClient, db_session: Session):
    """Test 17: Calculate, persist, and retrieve recommendation snapshots via API endpoints."""
    prod = Product(
        sku="TEST-PERSIST-1",
        name="Persistence Test Product",
        unit_of_measure="piece",
        current_stock=25,
        committed_qty=5,  # inv pos = 20
        lead_time_days=4,
        lead_time_stddev_days=1.0,
        target_service_level=0.95,
        unit_cost=Decimal("20.00"),
        ordering_cost=Decimal("40.00"),
        holding_cost_per_unit_year=Decimal("4.00"),
        minimum_order_qty=30,
        pack_size=10,
    )
    db_session.add(prod)
    db_session.commit()
    db_session.refresh(prod)

    # Seed 10 days of demand
    for i in range(10):
        db_session.add(
            DemandRecord(
                product_id=prod.id,
                date=date(2026, 9, 1) + timedelta(days=i),
                quantity=15.0 + (i % 3),
            )
        )
    db_session.commit()

    # 1. Calculate and persist snapshot
    calc_res = client.post(f"/api/v1/products/{prod.id}/recommendations/calculate")
    assert calc_res.status_code == 200
    rec_data = calc_res.json()
    rec_id = rec_data["id"]
    assert rec_data["product_id"] == prod.id
    assert rec_data["reorder_point"] > 0
    assert rec_data["safety_stock"] > 0
    assert rec_data["recommended_order_quantity"] >= 30  # MOQ respected
    assert rec_data["input_snapshot"] is not None
    assert rec_data["assumptions"] is not None

    # 2. Retrieve latest recommendation for product
    latest_res = client.get(f"/api/v1/products/{prod.id}/recommendations/latest")
    assert latest_res.status_code == 200
    assert latest_res.json()["id"] == rec_id

    # 3. Retrieve historical recommendations for product
    history_res = client.get(f"/api/v1/products/{prod.id}/recommendations")
    assert history_res.status_code == 200
    assert len(history_res.json()) >= 1
    assert history_res.json()[0]["id"] == rec_id

    # 4. List overview recommendations with filtering
    overview_res = client.get("/api/v1/recommendations?latest_only=true")
    assert overview_res.status_code == 200
    overview_data = overview_res.json()
    assert overview_data["total"] >= 1
    assert any(item["id"] == rec_id for item in overview_data["items"])

    # 5. Retrieve recommendation by ID
    single_res = client.get(f"/api/v1/recommendations/{rec_id}")
    assert single_res.status_code == 200
    assert single_res.json()["id"] == rec_id

    # 6. Update status
    patch_res = client.patch(
        f"/api/v1/recommendations/{rec_id}/status",
        json={"status": "approved"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["status"] == "approved"


def test_deterministic_repeat_calculations():
    """Test 18: Identical inputs produce byte-identical and value-identical outputs."""
    inputs = ReplenishmentInputs(
        product_id=42,
        sku="DETERMINISTIC-SKU",
        product_name="Deterministic Product",
        on_hand_qty=40,
        committed_qty=10,
        lead_time_days=6,
        lead_time_stddev_days=1.2,
        target_service_level=0.95,
        ordering_cost=Decimal("60.00"),
        holding_cost_per_unit_year=Decimal("6.00"),
        minimum_order_qty=25,
        pack_size=5,
    )
    obs = [
        DemandObservation(date=date(2026, 9, 1), quantity=12.0),
        DemandObservation(date=date(2026, 9, 2), quantity=15.0),
        DemandObservation(date=date(2026, 9, 3), quantity=11.0),
        DemandObservation(date=date(2026, 9, 4), quantity=14.0),
        DemandObservation(date=date(2026, 9, 5), quantity=18.0),
    ]

    res1 = calculate_replenishment(inputs, obs)
    res2 = calculate_replenishment(inputs, obs)

    assert res1.mean_daily_demand == res2.mean_daily_demand
    assert res1.daily_demand_stddev == res2.daily_demand_stddev
    assert res1.safety_stock == res2.safety_stock
    assert res1.safety_stock_exact == res2.safety_stock_exact
    assert res1.reorder_point == res2.reorder_point
    assert res1.raw_eoq == res2.raw_eoq
    assert res1.recommended_order_quantity == res2.recommended_order_quantity
    assert res1.explanation == res2.explanation
    assert res1.assumptions == res2.assumptions


def test_regression_phase1_and_phase2_intact(client: TestClient):
    """Test 19: Regression tests confirming Phase 1 and Phase 2 endpoints remain intact."""
    # Phase 1: Health & API status
    live_res = client.get("/health/live")
    assert live_res.status_code == 200
    assert live_res.json()["status"] == "ok"

    ready_res = client.get("/health/ready")
    assert ready_res.status_code == 200
    assert ready_res.json()["status"] == "ready"

    api_health_res = client.get("/api/v1/health")
    assert api_health_res.status_code == 200
    assert api_health_res.json()["status"] == "ready"

    status_res = client.get("/api/v1/status")
    assert status_res.status_code == 200
    assert status_res.json()["status"] == "active"

    # Phase 1: Products listing
    prod_res = client.get("/api/v1/products")
    assert prod_res.status_code == 200
    assert isinstance(prod_res.json(), list)

    # Phase 2: Imports preview endpoint active and responsive
    csv_content = (
        "sku,product_name,unit_of_measure,on_hand_qty\nREG-1,Regression Product,piece,50\n"
    )
    import_res = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.csv", csv_content.encode("utf-8"), "text/csv")},
    )
    assert import_res.status_code == 200
    assert import_res.json()["status"] == "previewed"
    assert import_res.json()["can_confirm"] is True


def test_boundary_inventory_position_equals_rop_with_eoq():
    """Audit Test 20: When inventory position equals ROP and EOQ is available, propose max(ceil(EOQ), 0) = ceil(EOQ)."""
    # mean demand = 10, L = 5, LTD = 50, SS = 0 -> ROP = 50.
    # on_hand = 50, committed = 0 -> inv_pos = 50 (exactly ROP!).
    # S = 50, H = 4 -> EOQ ≈ 302.076 (ceil = 303).
    inputs = ReplenishmentInputs(
        product_id=101,
        sku="TEST-BOUNDARY-EOQ",
        product_name="Boundary EOQ Product",
        on_hand_qty=50,
        committed_qty=0,
        lead_time_days=5,
        target_service_level=0.95,
        ordering_cost=Decimal("50.00"),
        holding_cost_per_unit_year=Decimal("4.00"),
    )
    obs = [
        DemandObservation(date=date(2026, 9, 1), quantity=10.0),
        DemandObservation(date=date(2026, 9, 2), quantity=10.0),
    ]
    res = calculate_replenishment(inputs, obs)
    assert res.inventory_position == 50
    assert res.reorder_point == 50
    assert res.reorder_triggered is True  # IP <= ROP triggers
    assert res.proposed_baseline_quantity == 303  # max(303, 0)
    assert res.recommended_order_quantity == 303
    assert "net deficit to ROP = 0" in res.explanation
    assert "Final recommended order quantity: 303 units." in res.explanation


def test_boundary_inventory_position_equals_rop_without_eoq_zero_deficit():
    """Audit Test 21: When inventory position equals ROP and EOQ is unavailable, net deficit is 0, propose 0 units, and MOQ does not force an order."""
    # mean demand = 10, L = 5, LTD = 50, SS = 0 -> ROP = 50.
    # on_hand = 50, committed = 0 -> inv_pos = 50.
    # Costs missing -> EOQ unavailable. Net deficit = 0.
    # minimum_order_qty = 50 -> must NOT fabricate an order!
    inputs = ReplenishmentInputs(
        product_id=102,
        sku="TEST-BOUNDARY-NO-EOQ",
        product_name="Boundary No EOQ Product",
        on_hand_qty=50,
        committed_qty=0,
        lead_time_days=5,
        target_service_level=0.95,
        ordering_cost=None,
        holding_cost_per_unit_year=None,
        minimum_order_qty=50,
        pack_size=10,
    )
    obs = [
        DemandObservation(date=date(2026, 9, 1), quantity=10.0),
        DemandObservation(date=date(2026, 9, 2), quantity=10.0),
    ]
    res = calculate_replenishment(inputs, obs)
    assert res.inventory_position == 50
    assert res.reorder_point == 50
    assert res.reorder_triggered is True
    assert res.eoq_available is False
    assert res.proposed_baseline_quantity == 0
    assert res.moq_applied is False
    assert res.pack_size_applied is False
    assert res.recommended_order_quantity == 0
    assert "Net deficit to ROP is 0 units, and EOQ is unavailable" in res.explanation
    assert "Final recommended order quantity: 0 units." in res.explanation


def test_historical_recommendation_immutability_on_product_update(
    client: TestClient, db_session: Session
):
    """Audit Test 22: Confirm that mutating product settings does NOT alter older persisted recommendation snapshots."""
    prod = Product(
        sku="TEST-IMMUTABLE-1",
        name="Immutable Audit Product",
        unit_of_measure="piece",
        current_stock=20,
        lead_time_days=3,
        lead_time_stddev_days=0.5,
        target_service_level=0.90,
    )
    db_session.add(prod)
    db_session.commit()
    db_session.refresh(prod)

    for i in range(5):
        db_session.add(
            DemandRecord(
                product_id=prod.id,
                date=date(2026, 9, 1) + timedelta(days=i),
                quantity=10.0,
            )
        )
    db_session.commit()

    # 1. Calculate recommendation #1 with lead_time_days=3, tsl=0.90
    res1 = client.post(f"/api/v1/products/{prod.id}/recommendations/calculate")
    assert res1.status_code == 200
    rec1_data = res1.json()
    rec1_id = rec1_data["id"]
    original_ss = rec1_data["safety_stock"]
    original_rop = rec1_data["reorder_point"]
    assert rec1_data["input_snapshot"]["lead_time_days"] == 3
    assert float(rec1_data["target_service_level"]) == 0.90

    # 2. Mutate product settings drastically
    prod.lead_time_days = 20
    prod.target_service_level = 0.99
    prod.current_stock = 500
    db_session.commit()

    # 3. Retrieve recommendation #1 again from database
    rec1_check = client.get(f"/api/v1/recommendations/{rec1_id}")
    assert rec1_check.status_code == 200
    rec1_after = rec1_check.json()
    assert rec1_after["id"] == rec1_id
    assert rec1_after["safety_stock"] == original_ss
    assert rec1_after["reorder_point"] == original_rop
    assert rec1_after["input_snapshot"]["lead_time_days"] == 3
    assert float(rec1_after["target_service_level"]) == 0.90
    assert rec1_after["current_stock"] == 20

    # 4. Calculate recommendation #2 with new settings
    res2 = client.post(f"/api/v1/products/{prod.id}/recommendations/calculate")
    assert res2.status_code == 200
    rec2_data = res2.json()
    assert rec2_data["id"] != rec1_id
    assert rec2_data["input_snapshot"]["lead_time_days"] == 20
    assert float(rec2_data["target_service_level"]) == 0.99
    assert rec2_data["current_stock"] == 500

    # History shows both distinct snapshots
    history_res = client.get(f"/api/v1/products/{prod.id}/recommendations")
    assert history_res.status_code == 200
    history = history_res.json()
    assert len(history) == 2
    assert history[0]["id"] == rec2_data["id"]
    assert history[1]["id"] == rec1_id


def test_status_update_endpoint_validation_and_safety(client: TestClient, db_session: Session):
    """Audit Test 23: Status update endpoint rejects invalid status codes (422) and safely mutates only status."""
    prod = Product(
        sku="TEST-STATUS-SAFETY",
        name="Status Safety Product",
        unit_of_measure="piece",
        current_stock=10,
        lead_time_days=4,
        target_service_level=0.95,
    )
    db_session.add(prod)
    db_session.commit()
    db_session.refresh(prod)

    for i in range(3):
        db_session.add(
            DemandRecord(
                product_id=prod.id,
                date=date(2026, 9, 1) + timedelta(days=i),
                quantity=5.0,
            )
        )
    db_session.commit()

    calc_res = client.post(f"/api/v1/products/{prod.id}/recommendations/calculate")
    assert calc_res.status_code == 200
    rec_id = calc_res.json()["id"]

    # 1. Invalid status value (not in Literal['pending', 'approved', 'rejected']) -> 422
    invalid_patch = client.patch(
        f"/api/v1/recommendations/{rec_id}/status",
        json={"status": "invalid_status_value"},
    )
    assert invalid_patch.status_code == 422

    # 2. Nonexistent recommendation ID -> 404
    not_found_patch = client.patch(
        "/api/v1/recommendations/999999/status",
        json={"status": "approved"},
    )
    assert not_found_patch.status_code == 404

    # 3. Valid status -> 200, only status modified
    valid_patch = client.patch(
        f"/api/v1/recommendations/{rec_id}/status",
        json={"status": "rejected"},
    )
    assert valid_patch.status_code == 200
    data = valid_patch.json()
    assert data["status"] == "rejected"
    assert data["id"] == rec_id
    assert data["product_id"] == prod.id
