import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import check_database_connection, engine
from app.main import app as live_app
from app.models.data_import import DataImport
from app.models.demand import DemandRecord
from app.models.product import Product
from app.seed.demo_data import seed_demo_data


def test_valid_product_csv_preview(client: TestClient, db_session: Session):
    """Test 1: Valid product CSV preview succeeds with 0 errors and bounded preview."""
    csv_content = (
        "sku,product_name,unit_of_measure,on_hand_qty,committed_qty,unit_cost,ordering_cost,"
        "holding_cost_per_unit_year,target_service_level,minimum_order_qty,pack_size,lead_time_days,lead_time_stddev_days\n"
        "TEST-P1,Test Product 1,piece,100,10,25.50,50.00,5.10,0.95,5,1,3,0.5\n"
        "TEST-P2,Test Product 2,kg,50,0,12.00,30.00,2.40,0.90,1,1,2,0.0\n"
    )

    response = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.csv", csv_content.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "previewed"
    assert data["can_confirm"] is True
    assert data["total_rows"] == 2
    assert data["accepted_rows"] == 2
    assert data["rejected_rows"] == 0
    assert len(data["errors"]) == 0
    assert len(data["preview_records"]) == 2
    assert data["preview_records"][0]["sku"] == "TEST-P1"
    assert data["preview_records"][0]["on_hand_qty"] == 100
    assert data["preview_records"][1]["sku"] == "TEST-P2"


def test_valid_daily_demand_csv_preview(client: TestClient, db_session: Session):
    """Test 2: Valid daily demand CSV preview succeeds when products exist in database."""
    # Create product first
    prod = Product(
        sku="TEST-DEM-1", name="Demand Test Product", unit_of_measure="piece", current_stock=50
    )
    db_session.add(prod)
    db_session.commit()

    csv_content = (
        "sku,date,units_sold,stockout_flag,promotion_flag,notes\n"
        "TEST-DEM-1,2026-09-01,15,false,false,normal sales\n"
        "TEST-DEM-1,2026-09-02,0,true,false,out of stock\n"
        "TEST-DEM-1,2026-09-03,45,false,true,weekend surge\n"
    )

    response = client.post(
        "/api/v1/imports/demand/preview",
        files={"file": ("demand.csv", csv_content.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "previewed"
    assert data["can_confirm"] is True
    assert data["total_rows"] == 3
    assert data["accepted_rows"] == 3
    assert data["rejected_rows"] == 0
    assert len(data["errors"]) == 0
    assert len(data["preview_records"]) == 3
    assert data["preview_records"][1]["units_sold"] == 0.0
    assert data["preview_records"][1]["stockout_flag"] is True
    assert data["preview_records"][2]["promotion_flag"] is True


def test_missing_required_columns(client: TestClient):
    """Test 3: Missing required columns generates blocking header diagnostics and cannot confirm."""
    # Missing on_hand_qty
    csv_missing_col = "sku,product_name,unit_of_measure\nTEST-BAD,Bad Product,piece\n"

    response = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.csv", csv_missing_col.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["can_confirm"] is False
    assert any(
        e["error_code"] == "MISSING_REQUIRED_COLUMN" and e["column_name"] == "on_hand_qty"
        for e in data["errors"]
    )


def test_invalid_numeric_values_and_dates(client: TestClient, db_session: Session):
    """Test 4: Invalid numeric values and malformed dates are flagged with clear diagnostics."""
    prod = Product(
        sku="TEST-NUM-1", name="Product for Date Test", unit_of_measure="piece", current_stock=20
    )
    db_session.add(prod)
    db_session.commit()

    # Product with non-numeric on_hand_qty and invalid target_service_level (> 1)
    bad_product_csv = (
        "sku,product_name,unit_of_measure,on_hand_qty,target_service_level\n"
        "BAD-NUM,Bad Number Prod,piece,NOT_A_NUMBER,1.5\n"
    )
    res_p = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.csv", bad_product_csv.encode("utf-8"), "text/csv")},
    )
    assert res_p.status_code == 200
    p_data = res_p.json()
    assert p_data["can_confirm"] is False
    error_codes = {e["error_code"] for e in p_data["errors"]}
    assert "INVALID_NON_NEGATIVE_NUMBER" in error_codes
    assert "INVALID_SERVICE_LEVEL" in error_codes

    # Demand with invalid date (non-ISO and impossible date)
    bad_demand_csv = "sku,date,units_sold\nTEST-NUM-1,2026-02-31,10\n"
    res_d = client.post(
        "/api/v1/imports/demand/preview",
        files={"file": ("demand.csv", bad_demand_csv.encode("utf-8"), "text/csv")},
    )
    assert res_d.status_code == 200
    d_data = res_d.json()
    assert d_data["can_confirm"] is False
    assert any(e["error_code"] == "INVALID_DATE_FORMAT" for e in d_data["errors"])


def test_negative_quantities_rejected(client: TestClient, db_session: Session):
    """Test 5: Negative quantities in on_hand_qty or units_sold are rejected."""
    prod = Product(
        sku="TEST-NEG-1",
        name="Product for Negative Test",
        unit_of_measure="piece",
        current_stock=10,
    )
    db_session.add(prod)
    db_session.commit()

    # Negative product stock
    neg_prod_csv = (
        "sku,product_name,unit_of_measure,on_hand_qty\nNEG-P,Negative Product,piece,-15\n"
    )
    res_p = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.csv", neg_prod_csv.encode("utf-8"), "text/csv")},
    )
    assert res_p.status_code == 200
    assert res_p.json()["can_confirm"] is False
    assert any(e["error_code"] == "INVALID_NON_NEGATIVE_NUMBER" for e in res_p.json()["errors"])

    # Negative demand quantity
    neg_demand_csv = "sku,date,units_sold\nTEST-NEG-1,2026-09-01,-8\n"
    res_d = client.post(
        "/api/v1/imports/demand/preview",
        files={"file": ("demand.csv", neg_demand_csv.encode("utf-8"), "text/csv")},
    )
    assert res_d.status_code == 200
    assert res_d.json()["can_confirm"] is False
    assert any(e["error_code"] == "NEGATIVE_QUANTITY" for e in res_d.json()["errors"])


def test_duplicate_skus_and_duplicate_sku_date_pairs(client: TestClient, db_session: Session):
    """Test 6: Duplicate SKUs within file and duplicate SKU/date pairs are detected and blocked."""
    prod = Product(
        sku="TEST-DUP-SKU", name="Product for Dup Test", unit_of_measure="piece", current_stock=10
    )
    db_session.add(prod)
    db_session.commit()

    # Duplicate SKU within product file
    dup_prod_csv = (
        "sku,product_name,unit_of_measure,on_hand_qty\n"
        "SKU-CLASH,Clash Product 1,piece,10\n"
        "SKU-CLASH,Clash Product 2,piece,20\n"
    )
    res_p = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.csv", dup_prod_csv.encode("utf-8"), "text/csv")},
    )
    assert res_p.status_code == 200
    assert res_p.json()["can_confirm"] is False
    assert any(e["error_code"] == "DUPLICATE_SKU_IN_FILE" for e in res_p.json()["errors"])

    # Duplicate (sku, date) within demand file
    dup_demand_csv = "sku,date,units_sold\nTEST-DUP-SKU,2026-09-05,10\nTEST-DUP-SKU,2026-09-05,25\n"
    res_d = client.post(
        "/api/v1/imports/demand/preview",
        files={"file": ("demand.csv", dup_demand_csv.encode("utf-8"), "text/csv")},
    )
    assert res_d.status_code == 200
    assert res_d.json()["can_confirm"] is False
    assert any(e["error_code"] == "DUPLICATE_DEMAND_IN_FILE" for e in res_d.json()["errors"])


def test_unknown_skus_in_demand_data(client: TestClient):
    """Test 7: Demand records referring to unknown SKUs are blocked without placeholder creation."""
    unknown_demand_csv = "sku,date,units_sold\nUNKNOWN-SKU-999,2026-09-01,10\n"
    res = client.post(
        "/api/v1/imports/demand/preview",
        files={"file": ("demand.csv", unknown_demand_csv.encode("utf-8"), "text/csv")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["can_confirm"] is False
    assert any(e["error_code"] == "UNKNOWN_SKU" for e in data["errors"])


def test_explicit_zero_demand_preservation(client: TestClient, db_session: Session):
    """Test 8: Explicit zero demand is preserved as zero, distinct from missing dates."""
    prod = Product(
        sku="TEST-ZERO-1", name="Product for Zero Test", unit_of_measure="piece", current_stock=30
    )
    db_session.add(prod)
    db_session.commit()

    csv_content = (
        "sku,date,units_sold,stockout_flag,promotion_flag\nTEST-ZERO-1,2026-09-01,0,false,false\n"
    )
    preview_res = client.post(
        "/api/v1/imports/demand/preview",
        files={"file": ("demand.csv", csv_content.encode("utf-8"), "text/csv")},
    )
    assert preview_res.status_code == 200
    preview_data = preview_res.json()
    assert preview_data["can_confirm"] is True
    assert preview_data["preview_records"][0]["units_sold"] == 0.0

    # Confirm and verify in DB
    confirm_res = client.post(f"/api/v1/imports/{preview_data['import_id']}/confirm")
    assert confirm_res.status_code == 200

    rec = db_session.query(DemandRecord).filter(DemandRecord.product_id == prod.id).first()
    assert rec is not None
    assert rec.quantity == 0.0


def test_stockout_and_promotion_flag_parsing(client: TestClient, db_session: Session):
    """Test 9: Boolean flags parse various true/false representations and reject ambiguous values."""
    prod = Product(
        sku="TEST-BOOL-1",
        name="Product for Boolean Test",
        unit_of_measure="piece",
        current_stock=30,
    )
    db_session.add(prod)
    db_session.commit()

    # Valid truthy/falsy values: 1/0, true/false, yes/no
    valid_csv = (
        "sku,date,units_sold,stockout_flag,promotion_flag\n"
        "TEST-BOOL-1,2026-09-01,10,1,0\n"
        "TEST-BOOL-1,2026-09-02,12,true,false\n"
        "TEST-BOOL-1,2026-09-03,14,yes,no\n"
    )
    res_val = client.post(
        "/api/v1/imports/demand/preview",
        files={"file": ("demand.csv", valid_csv.encode("utf-8"), "text/csv")},
    )
    assert res_val.status_code == 200
    assert res_val.json()["can_confirm"] is True
    records = res_val.json()["preview_records"]
    assert records[0]["stockout_flag"] is True
    assert records[0]["promotion_flag"] is False
    assert records[1]["stockout_flag"] is True
    assert records[1]["promotion_flag"] is False
    assert records[2]["stockout_flag"] is True
    assert records[2]["promotion_flag"] is False

    # Invalid boolean string
    invalid_csv = "sku,date,units_sold,stockout_flag\nTEST-BOOL-1,2026-09-04,10,maybe\n"
    res_inv = client.post(
        "/api/v1/imports/demand/preview",
        files={"file": ("demand.csv", invalid_csv.encode("utf-8"), "text/csv")},
    )
    assert res_inv.status_code == 200
    assert res_inv.json()["can_confirm"] is False
    assert any(e["error_code"] == "INVALID_BOOLEAN" for e in res_inv.json()["errors"])


def test_optional_values_remaining_null(client: TestClient, db_session: Session):
    """Test 10: Optional values not provided remain NULL/None instead of fabricating facts."""
    csv_content = (
        "sku,product_name,unit_of_measure,on_hand_qty\nTEST-NULL-1,Null Test Product,piece,50\n"
    )
    preview_res = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.csv", csv_content.encode("utf-8"), "text/csv")},
    )
    assert preview_res.status_code == 200
    prev_json = preview_res.json()
    import_id = prev_json["import_id"]

    # Check that visible warnings are emitted for permitted defaults (committed_qty=0, target_service_level=0.95)
    warnings = prev_json.get("warnings", [])
    warn_codes = {w["error_code"] for w in warnings}
    assert "DEFAULT_COMMITTED_QTY_APPLIED" in warn_codes
    assert "DEFAULT_SERVICE_LEVEL_APPLIED" in warn_codes

    # Check preview record contains None for missing optional costs and supplier params
    preview_rec = prev_json["preview_records"][0]
    assert preview_rec["unit_cost"] is None
    assert preview_rec["ordering_cost"] is None
    assert preview_rec["holding_cost_rate"] is None
    assert preview_rec["holding_cost_per_unit_year"] is None
    assert preview_rec["lead_time_days"] is None
    assert preview_rec["lead_time_stddev_days"] is None
    assert preview_rec["minimum_order_qty"] is None
    assert preview_rec["pack_size"] is None

    confirm_res = client.post(f"/api/v1/imports/{import_id}/confirm")
    assert confirm_res.status_code == 200

    product = db_session.query(Product).filter(Product.sku == "TEST-NULL-1").first()
    assert product is not None
    assert product.unit_cost is None
    assert product.ordering_cost is None
    assert product.holding_cost_rate is None
    assert product.holding_cost_per_unit_year is None
    assert product.category is None
    assert product.minimum_order_qty is None
    assert product.pack_size is None
    assert product.lead_time_days is None
    assert product.lead_time_stddev_days is None


def test_rejected_or_failed_import_cannot_be_confirmed(client: TestClient):
    """Test that an import with validation errors or status 'rejected' cannot be confirmed."""
    bad_csv = "sku,product_name\nBAD-1,Bad\n"
    res = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("bad.csv", bad_csv.encode("utf-8"), "text/csv")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "rejected"
    assert data["can_confirm"] is False
    bad_id = data["import_id"]

    # Attempt confirmation
    conf_res = client.post(f"/api/v1/imports/{bad_id}/confirm")
    assert conf_res.status_code == 400
    assert conf_res.json()["error"]["code"] == "CANNOT_CONFIRM_IMPORT"


def test_missing_demand_dates_are_not_converted_to_zero_records(
    client: TestClient, db_session: Session
):
    """Test that missing dates between observations are never synthesized into zero demand."""
    prod = Product(
        sku="TEST-SPARSE-1", name="Sparse Demand Product", unit_of_measure="piece", current_stock=40
    )
    db_session.add(prod)
    db_session.commit()

    # CSV has demand on Day 1 and Day 5, skipping Days 2, 3, 4
    csv_content = "sku,date,units_sold\nTEST-SPARSE-1,2026-09-01,10\nTEST-SPARSE-1,2026-09-05,20\n"
    res = client.post(
        "/api/v1/imports/demand/preview",
        files={"file": ("demand.csv", csv_content.encode("utf-8"), "text/csv")},
    )
    assert res.status_code == 200
    import_id = res.json()["import_id"]
    assert res.json()["accepted_rows"] == 2

    # Confirm
    conf = client.post(f"/api/v1/imports/{import_id}/confirm")
    assert conf.status_code == 200

    # Exactly 2 demand records must exist in DB; non-reported dates are NOT zero records
    records = (
        db_session.query(DemandRecord)
        .filter(DemandRecord.product_id == prod.id)
        .order_by(DemandRecord.date)
        .all()
    )
    assert len(records) == 2
    assert [r.date.isoformat() for r in records] == ["2026-09-01", "2026-09-05"]
    assert [r.quantity for r in records] == [10.0, 20.0]


def test_preview_does_not_write_business_records(client: TestClient, db_session: Session):
    """Test 11: Uploading a file and previewing must NOT write business records to database."""
    prod_count_before = db_session.query(Product).count()
    demand_count_before = db_session.query(DemandRecord).count()

    csv_content = (
        "sku,product_name,unit_of_measure,on_hand_qty\nPREVIEW-ONLY-1,Preview Only,piece,100\n"
    )
    res = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.csv", csv_content.encode("utf-8"), "text/csv")},
    )
    assert res.status_code == 200

    prod_count_after = db_session.query(Product).count()
    demand_count_after = db_session.query(DemandRecord).count()

    assert prod_count_before == prod_count_after
    assert demand_count_before == demand_count_after


def test_successful_confirmation_and_persistence(client: TestClient, db_session: Session):
    """Test 12: Successful confirmation persists records transactionally to database."""
    csv_content = (
        "sku,product_name,unit_of_measure,on_hand_qty,unit_cost,minimum_order_qty,pack_size\n"
        "PERSIST-SKU-1,Persist Product,piece,85,15.00,10,5\n"
    )
    preview_res = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.csv", csv_content.encode("utf-8"), "text/csv")},
    )
    assert preview_res.status_code == 200
    import_id = preview_res.json()["import_id"]

    confirm_res = client.post(f"/api/v1/imports/{import_id}/confirm")
    assert confirm_res.status_code == 200
    confirm_data = confirm_res.json()
    assert confirm_data["status"] == "confirmed"
    assert confirm_data["persisted_rows"] == 1

    # Verify DB persistence
    p = db_session.query(Product).filter(Product.sku == "PERSIST-SKU-1").first()
    assert p is not None
    assert p.current_stock == 85
    assert p.minimum_order_qty == 10
    assert p.pack_size == 5
    assert p.import_id == import_id


def test_confirmation_retry_idempotent(client: TestClient, db_session: Session):
    """Test 13: Confirmation retry does not insert duplicate records and returns confirmed summary."""
    csv_content = (
        "sku,product_name,unit_of_measure,on_hand_qty\nRETRY-SKU-1,Retry Test Product,piece,40\n"
    )
    preview_res = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.csv", csv_content.encode("utf-8"), "text/csv")},
    )
    import_id = preview_res.json()["import_id"]

    # First confirmation
    res1 = client.post(f"/api/v1/imports/{import_id}/confirm")
    assert res1.status_code == 200

    count_after_first = db_session.query(Product).filter(Product.sku == "RETRY-SKU-1").count()
    assert count_after_first == 1

    # Second confirmation (retry)
    res2 = client.post(f"/api/v1/imports/{import_id}/confirm")
    assert res2.status_code == 200
    assert res2.json()["status"] == "confirmed"

    count_after_second = db_session.query(Product).filter(Product.sku == "RETRY-SKU-1").count()
    assert count_after_second == 1


def test_revalidation_failure_when_data_changes_after_preview(
    client: TestClient, db_session: Session
):
    """Test 14: Confirmation revalidates against current database state and fails if conflict emerged."""
    csv_content = (
        "sku,product_name,unit_of_measure,on_hand_qty\nREVAL-SKU-1,Reval Product,piece,50\n"
    )
    preview_res = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.csv", csv_content.encode("utf-8"), "text/csv")},
    )
    assert preview_res.status_code == 200
    import_id = preview_res.json()["import_id"]

    # Before confirming, inject product with REVAL-SKU-1 into database directly
    interfering_product = Product(
        sku="REVAL-SKU-1", name="Interfering Product", unit_of_measure="piece", current_stock=10
    )
    db_session.add(interfering_product)
    db_session.commit()

    # Confirmation should fail revalidation with 409 Conflict
    confirm_res = client.post(f"/api/v1/imports/{import_id}/confirm")
    assert confirm_res.status_code == 409
    err = confirm_res.json()
    assert err["error"]["code"] == "REVALIDATION_FAILED"

    # Import state marked as failed
    detail_res = client.get(f"/api/v1/imports/{import_id}")
    assert detail_res.status_code == 200
    assert detail_res.json()["status"] == "failed"


def test_transaction_rollback_on_failed_import(client: TestClient, db_session: Session):
    """Test 15: Database failure during confirmation triggers complete transaction rollback."""
    csv_content = (
        "sku,product_name,unit_of_measure,on_hand_qty\nROLLBACK-SKU-1,Rollback Product,piece,60\n"
    )
    preview_res = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.csv", csv_content.encode("utf-8"), "text/csv")},
    )
    import_id = preview_res.json()["import_id"]

    # Mock db.add to simulate a database failure mid-import
    with patch.object(
        Session, "add", side_effect=RuntimeError("Simulated mid-transaction failure")
    ):
        confirm_res = client.post(f"/api/v1/imports/{import_id}/confirm")
        assert confirm_res.status_code == 500

    # Ensure no product was persisted
    persisted = db_session.query(Product).filter(Product.sku == "ROLLBACK-SKU-1").first()
    assert persisted is None


def test_oversized_file_and_row_count_limits(client: TestClient):
    """Test 16: Oversized file raises 413, and excessive row counts are rejected."""
    # 1. Oversized file (> 5MB)
    large_bytes = b"sku,product_name,unit_of_measure,on_hand_qty\n" + b"A,B,piece,1\n" * 500000
    assert len(large_bytes) > settings.MAX_UPLOAD_SIZE_BYTES

    res_large = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.csv", large_bytes, "text/csv")},
    )
    assert res_large.status_code == 413

    # 2. Row count limit exceeded (> MAX_IMPORT_ROWS)
    lines = ["sku,product_name,unit_of_measure,on_hand_qty"]
    for i in range(settings.MAX_IMPORT_ROWS + 5):
        lines.append(f"ROW-{i},Row {i},piece,1")
    large_csv = "\n".join(lines)

    res_rows = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.csv", large_csv.encode("utf-8"), "text/csv")},
    )
    assert res_rows.status_code == 200
    data = res_rows.json()
    assert data["can_confirm"] is False
    assert any(e["error_code"] == "ROW_COUNT_LIMIT_EXCEEDED" for e in data["errors"])


def test_malformed_csv_input_and_empty_files(client: TestClient):
    """Test 17: Malformed CSV and empty files are handled with actionable error details."""
    # Empty file
    res_empty = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert res_empty.status_code == 200
    assert res_empty.json()["can_confirm"] is False
    assert any(e["error_code"] == "EMPTY_FILE" for e in res_empty.json()["errors"])

    # Non-CSV extension
    res_ext = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.xlsx", b"dummy content", "application/vnd.ms-excel")},
    )
    assert res_ext.status_code == 400
    assert res_ext.json()["error"]["code"] == "INVALID_FILE_EXTENSION"

    # Mismatched column count
    malformed_csv = "sku,product_name,unit_of_measure,on_hand_qty\nM1,Mismatched Row\n"
    res_mal = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("malformed.csv", malformed_csv.encode("utf-8"), "text/csv")},
    )
    assert res_mal.status_code == 200
    assert res_mal.json()["can_confirm"] is False
    assert any(e["error_code"] == "MALFORMED_ROW" for e in res_mal.json()["errors"])


def test_existing_demo_data_remains_intact(client: TestClient, db_session: Session):
    """Test 18: Seeding demo data and importing new records preserves demo products and demand intact."""
    seed_demo_data(db_session)

    # Check initial demo counts
    demo1 = db_session.query(Product).filter(Product.sku == "SKU-001").first()
    assert demo1 is not None
    assert demo1.current_stock == 80
    demo1_demands = (
        db_session.query(DemandRecord).filter(DemandRecord.product_id == demo1.id).count()
    )
    assert demo1_demands == 45

    # Import new product
    new_csv = "sku,product_name,unit_of_measure,on_hand_qty\nNON-DEMO-99,Non Demo Item,piece,35\n"
    prev = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.csv", new_csv.encode("utf-8"), "text/csv")},
    )
    client.post(f"/api/v1/imports/{prev.json()['import_id']}/confirm")

    # Verify demo product is completely unchanged
    demo1_after = db_session.query(Product).filter(Product.sku == "SKU-001").first()
    assert demo1_after.current_stock == 80
    demo1_demands_after = (
        db_session.query(DemandRecord).filter(DemandRecord.product_id == demo1_after.id).count()
    )
    assert demo1_demands_after == 45


def test_generic_import_endpoint_and_suppliers(client: TestClient, db_session: Session):
    """Test generic /imports/preview and supplier settings workflow."""
    prod = Product(
        sku="SUPP-SKU-1", name="Supplier Test Product", unit_of_measure="piece", current_stock=20
    )
    db_session.add(prod)
    db_session.commit()

    csv_content = "sku,average_lead_time_days,lead_time_stddev_days\nSUPP-SKU-1,6,1.5\n"
    # Using generic preview with query parameter
    res = client.post(
        "/api/v1/imports/preview?file_type=suppliers",
        files={"file": ("supplier_settings.csv", csv_content.encode("utf-8"), "text/csv")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["file_type"] == "suppliers"
    assert data["can_confirm"] is True

    # Confirm
    conf = client.post(f"/api/v1/imports/{data['import_id']}/confirm")
    assert conf.status_code == 200

    # Verify updated product lead time
    updated = db_session.query(Product).filter(Product.sku == "SUPP-SKU-1").first()
    assert updated.lead_time_days == 6
    assert updated.lead_time_stddev_days == 1.5


def test_postgres_import_pipeline_live():
    """Test 19: Full preview, confirmation, and rollback pipeline verified against live PostgreSQL container."""
    if settings.is_sqlite:
        pytest.skip("Test environment is configured with SQLite; skipping live PostgreSQL checks.")
    if not check_database_connection(engine):
        pytest.skip("PostgreSQL container is not reachable.")

    with TestClient(live_app) as live_client:
        # 1. Preview new test product with random SKU and omitted optional costs/lead time
        unique_sku = f"PG-LIVE-{uuid.uuid4().hex[:8].upper()}"
        csv_prod = (
            f"sku,product_name,unit_of_measure,on_hand_qty,unit_cost\n"
            f"{unique_sku},Postgres Live Product,piece,55,19.99\n"
        )
        prev_res = live_client.post(
            "/api/v1/imports/products/preview",
            files={"file": ("pg_test_products.csv", csv_prod.encode("utf-8"), "text/csv")},
        )
        assert prev_res.status_code == 200
        prev_data = prev_res.json()
        assert prev_data["can_confirm"] is True, f"Validation errors: {prev_data.get('errors')}"
        prod_import_id = prev_data["import_id"]
        dem_import_id = None
        supp_import_id = None

        try:
            # Verify not yet in PostgreSQL products list
            prods_res = live_client.get("/api/v1/products")
            assert not any(p["sku"] == unique_sku for p in prods_res.json())

            # 2. Confirm product import on PostgreSQL
            conf_res = live_client.post(f"/api/v1/imports/{prod_import_id}/confirm")
            assert conf_res.status_code == 200
            assert conf_res.json()["status"] == "confirmed"

            # 3. Retrieve through standard Phase 1 products API to verify live PostgreSQL persistence
            prods_after = live_client.get("/api/v1/products")
            matching = next(p for p in prods_after.json() if p["sku"] == unique_sku)
            assert matching["current_stock"] == 55
            assert float(matching["unit_cost"]) == 19.99
            assert matching["lead_time_days"] is None  # Lead time not fabricated

            # 4. Safe retry check on live PostgreSQL
            conf_retry = live_client.post(f"/api/v1/imports/{prod_import_id}/confirm")
            assert conf_retry.status_code == 200
            assert conf_retry.json()["status"] == "confirmed"

            # 5. Import daily demand for this isolated product on live PostgreSQL
            csv_demand = (
                f"sku,date,units_sold,stockout_flag,promotion_flag\n"
                f"{unique_sku},2026-10-01,12,false,false\n"
                f"{unique_sku},2026-10-02,0,true,false\n"
            )
            prev_dem = live_client.post(
                "/api/v1/imports/demand/preview",
                files={"file": ("pg_demand.csv", csv_demand.encode("utf-8"), "text/csv")},
            )
            assert prev_dem.status_code == 200
            dem_import_id = prev_dem.json()["import_id"]
            assert prev_dem.json()["can_confirm"] is True

            conf_dem = live_client.post(f"/api/v1/imports/{dem_import_id}/confirm")
            assert conf_dem.status_code == 200
            assert conf_dem.json()["persisted_rows"] == 2

            # Verify demand retrieval on live PostgreSQL
            dem_res = live_client.get(f"/api/v1/products/{matching['id']}/demand")
            assert dem_res.status_code == 200
            dem_records = dem_res.json()
            assert len(dem_records) == 2
            assert dem_records[0]["quantity"] == 12.0
            assert dem_records[1]["quantity"] == 0.0
            assert dem_records[1]["stockout_flag"] is True

            # 6. Import supplier settings to update lead time on live PostgreSQL
            csv_supp = f"sku,average_lead_time_days\n{unique_sku},7\n"
            prev_supp = live_client.post(
                "/api/v1/imports/suppliers/preview",
                files={"file": ("pg_suppliers.csv", csv_supp.encode("utf-8"), "text/csv")},
            )
            assert prev_supp.status_code == 200
            supp_data = prev_supp.json()
            assert supp_data["can_confirm"] is True
            assert any(
                w["error_code"] == "DEFAULT_LEAD_TIME_STDDEV_APPLIED" for w in supp_data["warnings"]
            )
            supp_import_id = supp_data["import_id"]

            conf_supp = live_client.post(f"/api/v1/imports/{supp_import_id}/confirm")
            assert conf_supp.status_code == 200

            # Verify updated lead time in live PostgreSQL
            prod_updated = live_client.get(f"/api/v1/products/{matching['id']}").json()
            assert prod_updated["lead_time_days"] == 7
            assert prod_updated["lead_time_stddev_days"] == 0.0

        finally:
            # 7. Clean up test data from live PostgreSQL container to preserve clean demo state
            import_ids_to_clean = [
                i for i in [prod_import_id, dem_import_id, supp_import_id] if i is not None
            ]
            with Session(engine) as cleanup_session:
                live_prod = (
                    cleanup_session.query(Product).filter(Product.sku.like("PG-LIVE-%")).first()
                )
                if live_prod:
                    cleanup_session.query(DemandRecord).filter(
                        DemandRecord.product_id == live_prod.id
                    ).delete()
                    cleanup_session.delete(live_prod)
                if import_ids_to_clean:
                    cleanup_session.query(DataImport).filter(
                        DataImport.id.in_(import_ids_to_clean)
                    ).delete()
                cleanup_session.commit()


def test_reset_catalog_and_clear_existing_import(client: TestClient, db_session: Session):
    """Test 18: Resetting catalog and confirming import with clear_existing=True cleans old items."""
    # 1. Create an initial product
    old_prod = Product(
        sku="OLD-SKU-001",
        name="Old Legacy Product",
        unit_of_measure="unit",
        current_stock=50,
        committed_qty=5,
    )
    db_session.add(old_prod)
    db_session.commit()

    # 2. Stage a new product import
    new_csv = (
        "sku,product_name,unit_of_measure,on_hand_qty,committed_qty,unit_cost\n"
        "NEW-SKU-999,Fresh Clean Product,units,100,10,250.0\n"
    )
    preview_res = client.post(
        "/api/v1/imports/products/preview",
        files={"file": ("products.csv", new_csv.encode("utf-8"), "text/csv")},
    )
    assert preview_res.status_code == 200
    import_id = preview_res.json()["import_id"]

    # 3. Confirm with clear_existing=True
    conf_res = client.post(f"/api/v1/imports/{import_id}/confirm?clear_existing=true")
    assert conf_res.status_code == 200
    conf_data = conf_res.json()
    assert conf_data["status"] == "confirmed"
    assert conf_data["persisted_rows"] == 1

    # 4. Check products list in DB: OLD-SKU-001 should be gone, only NEW-SKU-999 should exist
    prods = client.get("/api/v1/products").json()
    skus = [p["sku"] for p in prods]
    assert "NEW-SKU-999" in skus
    assert "OLD-SKU-001" not in skus

    # 5. Test POST /api/v1/imports/reset directly
    reset_res = client.post("/api/v1/imports/reset")
    assert reset_res.status_code == 200
    assert reset_res.json()["status"] == "success"

    # 6. Verify products table is now empty
    empty_prods = client.get("/api/v1/products").json()
    assert len(empty_prods) == 0

    # 7. Test DELETE /api/v1/products
    del_res = client.delete("/api/v1/products")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "success"

