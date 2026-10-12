"""StockSense - Phase 4 Anomaly Review and Classification Tests.

Covers all 19 requirements specified in the Phase 4 specification:
1. Detection of a clear high-demand spike.
2. Detection of a clear low-demand anomaly.
3. Promotion-flagged demand spikes.
4. Potential data-error classification without modifying raw demand.
5. Stockout-affected demand warnings.
6. Constant-demand histories and zero MAD.
7. Zero-demand observations.
8. Insufficient demand history.
9. Missing calendar dates not being converted to zero demand.
10. Duplicate detection runs not creating duplicate candidates (idempotency).
11. Existing user decisions surviving detection reruns.
12. Classification validation and invalid statuses.
13. Missing product and anomaly identifiers (404 errors).
14. Date-range and pagination validation.
15. Persistence and retrieval of review details.
16. Detection determinism.
17. Repeated updates and dismissal workflow.
18. Regression test: replenishment calculations protected (no silent raw data mutation).
19. PostgreSQL integration tests using the existing development database.
"""

from datetime import date, timedelta
from typing import List

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import check_database_connection, engine
from app.main import app
from app.models.anomaly import AnomalyReview
from app.models.demand import DemandRecord
from app.models.product import Product
from app.services.anomaly_detector import (
    AnomalyDetectorConfig,
    detect_anomalies,
)
from app.services.replenishment_service import ReplenishmentService


def _create_product_with_demand(
    db: Session,
    sku: str,
    quantities: List[float],
    start_date: date = date(2026, 1, 1),
    promotions: List[bool] = None,
    stockouts: List[bool] = None,
) -> Product:
    """Helper to populate a test product and chronological demand series."""
    product = Product(
        sku=sku,
        name=f"Test Product {sku}",
        unit_of_measure="piece",
        current_stock=100,
        committed_qty=10,
        lead_time_days=7,
        lead_time_stddev_days=1.5,
        target_service_level=0.95,
        unit_cost=25.0,
        ordering_cost=50.0,
        holding_cost_rate=0.20,
    )
    db.add(product)
    db.flush()

    for idx, qty in enumerate(quantities):
        d_date = start_date + timedelta(days=idx)
        is_promo = bool(promotions[idx]) if promotions and idx < len(promotions) else False
        is_stockout = bool(stockouts[idx]) if stockouts and idx < len(stockouts) else False
        record = DemandRecord(
            product_id=product.id,
            date=d_date,
            quantity=qty,
            promotion_flag=is_promo,
            stockout_flag=is_stockout,
        )
        db.add(record)

    db.commit()
    db.refresh(product)
    return product


# 1. Detection of a clear high-demand spike
def test_detection_clear_high_demand_spike(client: TestClient, db_session: Session):
    # Baseline ~ 20, spike to 120 on day 5
    quantities = [20.0, 21.0, 19.0, 20.0, 22.0, 120.0, 20.0, 21.0]
    prod = _create_product_with_demand(db_session, "SKU-SPIKE-1", quantities)

    res = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    assert res.status_code == 200
    data = res.json()

    assert data["detected_anomalies_count"] >= 1
    assert data["new_candidates_created"] >= 1
    candidate = next(c for c in data["candidates"] if c["original_quantity"] == 120.0)
    assert candidate["anomaly_type"] == "high_demand_spike"
    assert candidate["suggested_classification"] == "unknown"
    assert candidate["review_status"] == "pending"
    assert candidate["anomaly_score"] > 3.0


# 2. Detection of a clear low-demand anomaly
def test_detection_clear_low_demand_drop(client: TestClient, db_session: Session):
    # Baseline ~ 100, drop to 5 on day 5 (no stockout flag)
    quantities = [100.0, 102.0, 98.0, 101.0, 99.0, 5.0, 100.0, 102.0]
    prod = _create_product_with_demand(db_session, "SKU-DROP-1", quantities)

    res = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    assert res.status_code == 200
    data = res.json()

    candidate = next(c for c in data["candidates"] if c["original_quantity"] == 5.0)
    assert candidate["anomaly_type"] == "low_demand_drop"
    assert candidate["suggested_classification"] == "unknown"
    assert candidate["review_status"] == "pending"


# 3. Promotion-flagged demand spikes
def test_promotion_flagged_demand_spikes(client: TestClient, db_session: Session):
    # Baseline ~ 20, spike of 150 with promotion_flag=True
    quantities = [20.0, 21.0, 20.0, 19.0, 22.0, 150.0, 20.0]
    promos = [False, False, False, False, False, True, False]
    prod = _create_product_with_demand(db_session, "SKU-PROMO-1", quantities, promotions=promos)

    res = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    assert res.status_code == 200
    data = res.json()

    candidate = next(c for c in data["candidates"] if c["original_quantity"] == 150.0)
    assert candidate["anomaly_type"] == "promotion_spike"
    assert candidate["suggested_classification"] == "promotion"
    assert candidate["promotion_flag"] is True
    # Initial status is still pending human review
    assert candidate["review_status"] == "pending"


# 4. Potential data-error classification without modifying raw demand
def test_potential_data_error_classification_without_modifying_raw_demand(
    client: TestClient, db_session: Session
):
    quantities = [30.0, 31.0, 29.0, 30.0, 32.0, 250.0, 30.0]
    prod = _create_product_with_demand(db_session, "SKU-DATA-ERR", quantities)

    # 1. Detect candidate
    det_res = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    assert det_res.status_code == 200
    candidate = det_res.json()["candidates"][0]
    anomaly_id = candidate["id"]
    demand_record_id = candidate["demand_record_id"]

    # 2. Confirm classification as data_error with review notes and adjusted quantity
    patch_res = client.patch(
        f"/api/v1/anomalies/{anomaly_id}/review",
        json={
            "classification": "data_error",
            "reviewer_notes": "Barcode scanner glitch scanned carton instead of each",
            "reviewer": "InventoryAnalyst1",
            "adjusted_quantity": 30.0,
        },
    )
    assert patch_res.status_code == 200
    reviewed_data = patch_res.json()
    assert reviewed_data["classification"] == "data_error"
    assert reviewed_data["user_confirmed_classification"] == "data_error"
    assert reviewed_data["review_status"] == "reviewed"
    assert reviewed_data["adjusted_quantity"] == 30.0

    # 3. CRITICAL: Verify source DemandRecord was NOT modified in the database
    raw_demand = db_session.query(DemandRecord).filter(DemandRecord.id == demand_record_id).first()
    assert raw_demand is not None
    assert float(raw_demand.quantity) == 250.0  # Original demand value strictly preserved!


# 5. Stockout-affected demand warnings
def test_stockout_affected_demand_warnings(client: TestClient, db_session: Session):
    quantities = [40.0, 42.0, 38.0, 41.0, 39.0, 0.0, 40.0]
    stockouts = [False, False, False, False, False, True, False]
    prod = _create_product_with_demand(
        db_session, "SKU-STOCKOUT-1", quantities, stockouts=stockouts
    )

    res = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    assert res.status_code == 200
    data = res.json()

    candidate = next(c for c in data["candidates"] if c["original_quantity"] == 0.0)
    assert candidate["anomaly_type"] == "stockout_affected"
    assert candidate["suggested_classification"] == "unknown"
    assert candidate["stockout_flag"] is True
    assert "stockout" in candidate["detector_explanation"].lower()


# 6. Constant-demand histories and zero MAD
def test_constant_demand_histories_and_zero_mad(client: TestClient, db_session: Session):
    quantities = [15.0, 15.0, 15.0, 15.0, 15.0, 15.0]
    prod = _create_product_with_demand(db_session, "SKU-CONST-1", quantities)

    res = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    assert res.status_code == 200
    data = res.json()

    # Zero MAD & constant demand: 0 anomalies flagged
    assert data["detected_anomalies_count"] == 0
    assert any("constant demand" in n.lower() for n in data["notes"])


# 7. Zero-demand observations within regular low variance
def test_zero_demand_observations_within_normal_variance(client: TestClient, db_session: Session):
    # Intermittent low demand where 0 is typical and expected
    quantities = [0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 1.0]
    prod = _create_product_with_demand(db_session, "SKU-SPARSE-1", quantities)

    res = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    assert res.status_code == 200
    data = res.json()
    # Explicit zeros should not trigger false positive outlier spikes
    assert data["detected_anomalies_count"] == 0


# 8. Insufficient demand history
def test_insufficient_demand_history(client: TestClient, db_session: Session):
    # Product with 0 records
    p0 = Product(sku="SKU-ZERO-HIST", name="Zero Hist", unit_of_measure="piece")
    db_session.add(p0)
    db_session.commit()

    res0 = client.post(f"/api/v1/products/{p0.id}/anomalies/detect")
    assert res0.status_code == 400
    msg0 = res0.json().get("error", {}).get("message", "") or res0.json().get("detail", "")
    assert "no demand records" in msg0.lower()

    # Product with only 2 records (< minimum 5)
    p2 = _create_product_with_demand(db_session, "SKU-TWO-HIST", [10.0, 12.0])
    res2 = client.post(f"/api/v1/products/{p2.id}/anomalies/detect")
    assert res2.status_code == 400
    msg2 = res2.json().get("error", {}).get("message", "") or res2.json().get("detail", "")
    assert "insufficient demand history" in msg2.lower()


# 9. Missing calendar dates not being converted to zero demand
def test_missing_calendar_dates_not_converted_to_zero_demand(db_session: Session):
    # Dates spaced 5 days apart: 5 observations over a 20-day span
    product = Product(sku="SKU-SPACED-1", name="Spaced", unit_of_measure="piece")
    db_session.add(product)
    db_session.flush()

    for i in range(5):
        db_session.add(
            DemandRecord(
                product_id=product.id,
                date=date(2026, 1, 1) + timedelta(days=i * 5),
                quantity=50.0,
            )
        )
    db_session.commit()

    records = (
        db_session.query(DemandRecord)
        .filter(DemandRecord.product_id == product.id)
        .order_by(DemandRecord.date.asc())
        .all()
    )
    result = detect_anomalies(records, AnomalyDetectorConfig(min_history_records=5))

    # The detector must evaluate only the 5 actual observations
    assert result.total_records_analyzed == 5
    assert result.median == 50.0
    assert len(result.candidates) == 0


# 10. Duplicate detection runs not creating duplicate candidates (idempotency)
def test_duplicate_detection_runs_idempotent(client: TestClient, db_session: Session):
    quantities = [20.0, 21.0, 19.0, 20.0, 22.0, 140.0, 20.0]
    prod = _create_product_with_demand(db_session, "SKU-IDEMP-1", quantities)

    # First run
    res1 = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["new_candidates_created"] == 1
    assert d1["existing_candidates_preserved"] == 0

    # Second run immediately after
    res2 = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["new_candidates_created"] == 0
    assert d2["existing_candidates_preserved"] == 1

    # Verify database table has exactly 1 row
    count = db_session.query(AnomalyReview).filter(AnomalyReview.product_id == prod.id).count()
    assert count == 1


# 11. Existing user decisions surviving detection reruns
def test_existing_user_decisions_surviving_detection_rerun(client: TestClient, db_session: Session):
    quantities = [20.0, 21.0, 19.0, 20.0, 22.0, 130.0, 20.0]
    prod = _create_product_with_demand(db_session, "SKU-SURVIVE-1", quantities)

    # 1. Detect
    det1 = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    anomaly_id = det1.json()["candidates"][0]["id"]

    # 2. User reviews and classifies as 'promotion'
    patch_res = client.patch(
        f"/api/v1/anomalies/{anomaly_id}/review",
        json={
            "classification": "promotion",
            "reviewer_notes": "Confirmed flash marketing sale on Instagram",
            "reviewer": "MarketingLead",
        },
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["review_status"] == "reviewed"

    # 3. Detection reruns (e.g. nightly job)
    det2 = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    assert det2.status_code == 200

    # 4. Verify candidate review retained user classification and notes
    get_res = client.get(f"/api/v1/anomalies/{anomaly_id}")
    data = get_res.json()
    assert data["classification"] == "promotion"
    assert data["review_status"] == "reviewed"
    assert "Confirmed flash marketing sale" in data["reviewer_notes"]


# 12. Classification validation and invalid statuses
def test_classification_and_status_validation(client: TestClient, db_session: Session):
    quantities = [20.0, 21.0, 19.0, 20.0, 22.0, 110.0, 20.0]
    prod = _create_product_with_demand(db_session, "SKU-VAL-1", quantities)

    det = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    anomaly_id = det.json()["candidates"][0]["id"]

    # Invalid classification value
    res_bad_class = client.patch(
        f"/api/v1/anomalies/{anomaly_id}/review",
        json={"classification": "not_a_real_classification"},
    )
    assert res_bad_class.status_code in (400, 422)

    # Invalid review status value
    res_bad_status = client.patch(
        f"/api/v1/anomalies/{anomaly_id}/review",
        json={"review_status": "approved"},  # 'approved' belongs to recommendations
    )
    assert res_bad_status.status_code in (400, 422)

    # Negative adjusted quantity
    res_neg_qty = client.patch(
        f"/api/v1/anomalies/{anomaly_id}/review",
        json={"adjusted_quantity": -5.0},
    )
    assert res_neg_qty.status_code == 422


# 13. Missing product and anomaly identifiers (404 errors)
def test_missing_product_and_anomaly_identifiers(client: TestClient):
    assert client.post("/api/v1/products/999999/anomalies/detect").status_code == 404
    assert client.get("/api/v1/products/999999/anomalies").status_code == 404
    assert client.get("/api/v1/products/999999/anomalies/summary").status_code == 404
    assert client.get("/api/v1/anomalies/999999").status_code == 404
    assert (
        client.patch(
            "/api/v1/anomalies/999999/review", json={"classification": "unknown"}
        ).status_code
        == 404
    )


# 14. Date-range and pagination validation
def test_date_range_and_pagination_validation(client: TestClient, db_session: Session):
    quantities = [20.0, 21.0, 19.0, 20.0, 22.0, 150.0, 20.0]
    prod = _create_product_with_demand(db_session, "SKU-PAG-1", quantities)
    client.post(f"/api/v1/products/{prod.id}/anomalies/detect")

    # Inverted date range
    res_inv = client.get(
        f"/api/v1/products/{prod.id}/anomalies",
        params={"start_date": "2026-02-01", "end_date": "2026-01-01"},
    )
    assert res_inv.status_code == 400
    msg_inv = res_inv.json().get("error", {}).get("message", "") or res_inv.json().get("detail", "")
    assert "start_date" in msg_inv.lower()

    # Invalid pagination: limit=0 or limit > 100
    assert (
        client.get(f"/api/v1/products/{prod.id}/anomalies", params={"limit": 0}).status_code == 422
    )
    assert (
        client.get(f"/api/v1/products/{prod.id}/anomalies", params={"limit": 105}).status_code
        == 422
    )
    assert (
        client.get(f"/api/v1/products/{prod.id}/anomalies", params={"skip": -1}).status_code == 422
    )


# 15. Persistence and retrieval of review details
def test_persistence_and_retrieval_of_review_details(client: TestClient, db_session: Session):
    quantities = [25.0, 24.0, 26.0, 25.0, 25.0, 160.0, 25.0]
    prod = _create_product_with_demand(db_session, "SKU-PERSIST-1", quantities)

    # Detect
    det = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    anomaly_id = det.json()["candidates"][0]["id"]

    # Submit review via spec alias endpoint POST /anomalies/{id}/reviews
    rev_res = client.post(
        f"/api/v1/anomalies/{anomaly_id}/reviews",
        json={
            "classification": "promotion",
            "reviewer_notes": "Black Friday special promotion",
            "reviewer": "ManagerJane",
            "user_treatment": "keep",
        },
    )
    assert rev_res.status_code == 200

    # Retrieve single review detail
    detail_res = client.get(f"/api/v1/anomalies/{anomaly_id}")
    assert detail_res.status_code == 200
    d = detail_res.json()

    assert d["id"] == anomaly_id
    assert d["product_id"] == prod.id
    assert d["sku"] == "SKU-PERSIST-1"
    assert d["original_quantity"] == 160.0
    assert d["classification"] == "promotion"
    assert d["review_status"] == "reviewed"
    assert "Black Friday" in d["reviewer_notes"]
    assert d["user_treatment"] == "keep"
    assert d["detector_version"] == "median_mad_v1.0"
    assert d["anomaly_score"] is not None
    assert d["observation_date"] is not None


# 16. Detection determinism
def test_detection_determinism(db_session: Session):
    quantities = [10.0, 12.0, 11.0, 10.0, 13.0, 95.0, 11.0, 12.0]
    prod = _create_product_with_demand(db_session, "SKU-DETERM-1", quantities)

    records = (
        db_session.query(DemandRecord)
        .filter(DemandRecord.product_id == prod.id)
        .order_by(DemandRecord.date.asc())
        .all()
    )

    r1 = detect_anomalies(records)
    r2 = detect_anomalies(records)

    assert r1.median == r2.median
    assert r1.mad == r2.mad
    assert r1.upper_bound == r2.upper_bound
    assert r1.lower_bound == r2.lower_bound
    assert len(r1.candidates) == len(r2.candidates)
    assert r1.candidates[0].score == r2.candidates[0].score


# 17. Repeated updates and dismissal workflow
def test_dismissal_and_reopen_workflow(client: TestClient, db_session: Session):
    quantities = [20.0, 21.0, 19.0, 20.0, 22.0, 115.0, 20.0]
    prod = _create_product_with_demand(db_session, "SKU-DISMISS-1", quantities)

    det = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    anomaly_id = det.json()["candidates"][0]["id"]

    # 1. Dismiss as false positive
    dis_res = client.post(
        f"/api/v1/anomalies/{anomaly_id}/dismiss",
        params={"notes": "Normal bulk purchase by regular customer"},
    )
    assert dis_res.status_code == 200
    assert dis_res.json()["review_status"] == "dismissed"

    # 2. Re-open and reclassify
    reopen_res = client.patch(
        f"/api/v1/anomalies/{anomaly_id}/review",
        json={"classification": "unknown", "review_status": "reviewed"},
    )
    assert reopen_res.status_code == 200
    assert reopen_res.json()["review_status"] == "reviewed"

    # 3. Check summary counts
    sum_res = client.get(f"/api/v1/products/{prod.id}/anomalies/summary")
    assert sum_res.status_code == 200
    s_data = sum_res.json()
    assert s_data["reviewed_count"] == 1
    assert s_data["dismissed_count"] == 0


# 18. Regression test: protect the replenishment engine
def test_regression_protect_replenishment_engine(client: TestClient, db_session: Session):
    # Establish a product with complete parameters and demand series
    quantities = [20.0, 22.0, 21.0, 20.0, 23.0, 120.0, 20.0, 21.0]
    prod = _create_product_with_demand(db_session, "SKU-REPLEN-REG", quantities)

    # 1. Calculate baseline recommendation before anomaly detection
    rec_before, _ = ReplenishmentService.calculate_for_product(db_session, prod.id)
    ss_before = rec_before.safety_stock
    rop_before = rec_before.reorder_point
    roq_before = rec_before.recommended_order_quantity

    # 2. Trigger anomaly detection and confirm anomaly as 'data_error'
    det = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    anomaly_id = det.json()["candidates"][0]["id"]
    client.patch(
        f"/api/v1/anomalies/{anomaly_id}/review",
        json={"classification": "data_error", "adjusted_quantity": 20.0},
    )

    # 3. Calculate recommendation again
    rec_after, _ = ReplenishmentService.calculate_for_product(db_session, prod.id)
    ss_after = rec_after.safety_stock
    rop_after = rec_after.reorder_point
    roq_after = rec_after.recommended_order_quantity

    # In Phase 4, the replenishment engine must calculate identically because raw observations are never mutated!
    assert ss_before == ss_after
    assert rop_before == rop_after
    assert roq_before == roq_after


# 19. PostgreSQL integration tests using the existing development database
def test_postgres_live_anomalies():
    """Verify Phase 4 anomaly workflow runs against live PostgreSQL container if reachable."""
    if settings.is_sqlite:
        pytest.skip("Test environment is configured with SQLite; skipping live PostgreSQL checks.")
    if not check_database_connection(engine):
        pytest.skip("PostgreSQL database is not reachable on port 5434.")

    with TestClient(app) as c:
        # Check global summary
        res = c.get("/api/v1/anomalies/summary")
        assert res.status_code == 200
        data = res.json()
        assert "total_anomalies" in data
        assert "by_classification" in data


# 20. Audit Test: Low-demand drop detection when raw lower bound reaches zero
def test_low_demand_detection_when_lower_bound_is_zero(client: TestClient, db_session: Session):
    # Median is 30.0, std_est is 10.0, k is 3.0 => raw_lower_bound = 30 - 30 = 0.0
    # Drop to 0.0 has modified Z-score = 3.0 >= k.
    # Must be detected as low_demand_drop even though lower_bound is 0.0!
    quantities = [30.0, 30.0, 20.0, 40.0, 30.0, 0.0, 30.0]
    prod = _create_product_with_demand(db_session, "SKU-LOW-BOUND-ZERO", quantities)

    res = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    assert res.status_code == 200
    data = res.json()

    candidates = [c for c in data["candidates"] if c["original_quantity"] == 0.0]
    assert len(candidates) >= 1
    candidate = candidates[0]
    assert candidate["anomaly_type"] in ("low_demand_drop", "stockout_affected")
    assert candidate["anomaly_score"] >= 3.0


# 21. Audit Test: Pending candidate unconfirmed state and summary isolation
def test_pending_candidate_unconfirmed_state_and_summary_isolation(
    client: TestClient, db_session: Session
):
    # Baseline ~ 20, spike of 160 with promotion_flag=True
    quantities = [20.0, 21.0, 20.0, 19.0, 22.0, 160.0, 20.0]
    promos = [False, False, False, False, False, True, False]
    prod = _create_product_with_demand(db_session, "SKU-AUDIT-ISO", quantities, promotions=promos)

    det_res = client.post(f"/api/v1/products/{prod.id}/anomalies/detect")
    assert det_res.status_code == 200
    cand = det_res.json()["candidates"][0]

    # Verify candidate is suggested as promotion, but user_confirmed is None and classification is unknown
    assert cand["suggested_classification"] == "promotion"
    assert cand["user_confirmed_classification"] is None
    assert cand["classification"] == "unknown"
    assert cand["review_status"] == "pending"

    # In summary, confirmed promotion count must be 0, while suggested promotion is 1
    sum_res1 = client.get(f"/api/v1/products/{prod.id}/anomalies/summary")
    s1 = sum_res1.json()
    assert s1["by_classification"]["promotion"] == 0
    assert s1["by_suggested_classification"]["promotion"] == 1
    assert s1["pending_count"] == 1
    assert s1["reviewed_count"] == 0

    # User confirms the review
    patch_res = client.patch(
        f"/api/v1/anomalies/{cand['id']}/review",
        json={"classification": "promotion", "reviewer_notes": "Confirmed campaign"},
    )
    assert patch_res.status_code == 200
    c_reviewed = patch_res.json()
    assert c_reviewed["user_confirmed_classification"] == "promotion"
    assert c_reviewed["classification"] == "promotion"
    assert c_reviewed["review_status"] == "reviewed"

    # In summary, confirmed promotion is now 1
    sum_res2 = client.get(f"/api/v1/products/{prod.id}/anomalies/summary")
    s2 = sum_res2.json()
    assert s2["by_classification"]["promotion"] == 1
    assert s2["reviewed_count"] == 1
    assert s2["pending_count"] == 0


# 22. Audit Test: Suggested classification filter in API list
def test_suggested_classification_filter_in_api_list(client: TestClient, db_session: Session):
    quantities = [20.0, 21.0, 20.0, 19.0, 22.0, 180.0, 20.0]
    promos = [False, False, False, False, False, True, False]
    prod = _create_product_with_demand(db_session, "SKU-SUGG-FILT", quantities, promotions=promos)
    client.post(f"/api/v1/products/{prod.id}/anomalies/detect")

    # Filter by suggested_classification=promotion
    res_sugg = client.get(
        f"/api/v1/products/{prod.id}/anomalies",
        params={"suggested_classification": "promotion"},
    )
    assert res_sugg.status_code == 200
    items = res_sugg.json()["items"]
    assert len(items) == 1
    assert items[0]["suggested_classification"] == "promotion"
    assert items[0]["user_confirmed_classification"] is None
