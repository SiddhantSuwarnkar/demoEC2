import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.core.config import settings
from app.db.session import check_database_connection, engine
from app.main import app


@pytest.fixture(scope="module")
def postgres_live_check():
    """Skip if live PostgreSQL database is not reachable."""
    if settings.is_sqlite:
        pytest.skip("Test environment is configured with SQLite; skipping live PostgreSQL checks.")
    if not check_database_connection(engine):
        pytest.skip("PostgreSQL database is not reachable on configured port.")
    return True


def test_postgres_container_connectivity(postgres_live_check):
    """Verify live PostgreSQL connection and dialect."""
    assert check_database_connection(engine) is True
    assert engine.dialect.name == "postgresql"


def test_postgres_schema_tables_exist(postgres_live_check):
    """Verify all 6 core tables exist in the live PostgreSQL database."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    expected = {
        "products",
        "demand_records",
        "anomaly_reviews",
        "forecast_runs",
        "recommendations",
        "scenario_runs",
    }
    assert expected.issubset(tables)


def test_postgres_http_retrieval_demo_data(postgres_live_check):
    """
    Verify Exit Requirement C:
    Demo products and their daily demand can be retrieved through the read-only HTTP endpoints
    against the live PostgreSQL database.
    """
    with TestClient(app) as client:
        # 1. Retrieve products
        res = client.get("/api/v1/products")
        assert res.status_code == 200
        prods = res.json()
        assert len(prods) >= 2
        skus = {p["sku"] for p in prods}
        assert "SKU-001" in skus
        assert "SKU-002" in skus

        # Find SKU-001
        p1 = next(p for p in prods if p["sku"] == "SKU-001")
        assert p1["current_stock"] == 80
        assert p1["unit_of_measure"] == "piece"

        # 2. Retrieve SKU-001 details by ID
        p1_res = client.get(f"/api/v1/products/{p1['id']}")
        assert p1_res.status_code == 200
        assert p1_res.json()["sku"] == "SKU-001"

        # 3. Retrieve SKU-001 demand
        d1_res = client.get(f"/api/v1/products/{p1['id']}/demand")
        assert d1_res.status_code == 200
        demands = d1_res.json()
        assert len(demands) >= 7

        # Verify stockout-affected observation and promotion surge are present
        stockouts = [d for d in demands if d["stockout_flag"] is True]
        assert len(stockouts) >= 1
        assert stockouts[0]["quantity"] == 0.0

        promos = [d for d in demands if d["promotion_flag"] is True]
        assert len(promos) >= 1
        assert promos[0]["quantity"] == 48.0


def test_postgres_replenishment_calculation_live(postgres_live_check):
    """
    Verify Phase 3:
    Replenishment recommendation calculation, snapshot persistence, and retrieval
    against live PostgreSQL container database.
    """
    with TestClient(app) as client:
        # 1. Retrieve SKU-001
        prods_res = client.get("/api/v1/products")
        assert prods_res.status_code == 200
        prods = prods_res.json()
        p1 = next(p for p in prods if p["sku"] == "SKU-001")

        # 2. Calculate and persist recommendation
        calc_res = client.post(f"/api/v1/products/{p1['id']}/recommendations/calculate")
        assert calc_res.status_code == 200
        rec = calc_res.json()
        assert rec["product_id"] == p1["id"]
        assert rec["reorder_point"] > 0
        assert rec["safety_stock"] > 0
        assert rec["formula_version"] == "v1.0"
        assert rec["input_snapshot"]["sku"] == "SKU-001"
        assert rec["assumptions"]["demand_model"]["mean_daily_demand"] > 0

        # 3. Retrieve latest recommendation
        latest_res = client.get(f"/api/v1/products/{p1['id']}/recommendations/latest")
        assert latest_res.status_code == 200
        assert latest_res.json()["id"] == rec["id"]

        # 4. Retrieve recommendations overview
        overview_res = client.get("/api/v1/recommendations?latest_only=true")
        assert overview_res.status_code == 200
        assert any(item["id"] == rec["id"] for item in overview_res.json()["items"])


def test_postgres_forecast_benchmark_live(postgres_live_check):
    """
    Verify Phase 5:
    Forecast benchmark execution, snapshot persistence in forecast_runs table,
    and metrics retrieval against live PostgreSQL container database.
    """
    import uuid
    from datetime import date, timedelta

    from app.db.session import SessionLocal
    from app.models.demand import DemandRecord
    from app.models.product import Product

    test_sku = f"SKU-PG-FC-{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        # Create dedicated live test product with 14 observations
        prod = Product(
            sku=test_sku,
            product_name="Postgres Forecast Test Product",
            unit_of_measure="piece",
            on_hand_qty=50,
            safety_stock=10,
            reorder_point=25,
            unit_cost=12.0,
            lead_time_days=5,
        )
        db.add(prod)
        db.commit()
        db.refresh(prod)

        start = date(2026, 2, 1)
        for i in range(14):
            db.add(
                DemandRecord(
                    product_id=prod.id,
                    date=start + timedelta(days=i),
                    quantity=float(15 + (i % 5)),
                    promotion_flag=False,
                    stockout_flag=False,
                )
            )
        db.commit()

        prod_id = prod.id

    with TestClient(app) as client:
        # 1. Run forecast benchmark
        res = client.post(
            f"/api/v1/products/{prod_id}/forecast-benchmark",
            json={"test_size": 0.25, "window_size": 5},
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["product_id"] == prod_id
        assert data["status"] == "completed"
        assert data["baseline_method"] == "moving_average"
        assert data["candidate_method"] == "prophet"
        assert data["mae_baseline"] is not None
        assert data["mae_candidate"] is not None
        run_id = data["id"]

        # 2. Retrieve persisted run
        get_res = client.get(f"/api/v1/forecast-runs/{run_id}")
        assert get_res.status_code == 200
        assert get_res.json()["id"] == run_id

        # 3. Retrieve metrics details
        metrics_res = client.get(f"/api/v1/forecast-runs/{run_id}/metrics")
        assert metrics_res.status_code == 200
        m_data = metrics_res.json()
        assert len(m_data["baseline_metrics"]["predictions"]) > 0
        assert len(m_data["candidate_metrics"]["predictions"]) > 0


def test_postgres_scenario_simulation_live(postgres_live_check):
    """
    Verify Phase 6:
    What-If scenario simulation, snapshot persistence in scenario_runs table,
    retrieval, and comparison against live PostgreSQL container database.
    """
    import uuid
    from datetime import date, timedelta

    from app.db.session import SessionLocal
    from app.models.demand import DemandRecord
    from app.models.product import Product

    test_sku = f"SKU-PG-SIM-{uuid.uuid4().hex[:8]}"

    with SessionLocal() as db:
        prod = Product(
            sku=test_sku,
            product_name="Postgres Simulation Test Product",
            unit_of_measure="piece",
            on_hand_qty=60,
            safety_stock=15,
            reorder_point=35,
            unit_cost=10.0,
            holding_cost_rate=0.20,
            fixed_order_cost=40.0,
            lead_time_days=4,
            min_order_qty=20,
            pack_size=5,
        )
        db.add(prod)
        db.commit()
        db.refresh(prod)

        start = date(2026, 2, 1)
        for i in range(14):
            db.add(
                DemandRecord(
                    product_id=prod.id,
                    date=start + timedelta(days=i),
                    quantity=8.0,
                    promotion_flag=False,
                    stockout_flag=False,
                )
            )
        db.commit()
        prod_id = prod.id
        initial_stock = prod.on_hand_qty

    with TestClient(app) as client:
        # 1. Execute What-If scenario (25% surge, 3-day supplier delay)
        sim_res = client.post(
            f"/api/v1/products/{prod_id}/scenarios",
            json={
                "scenario_name": "Postgres Live Stress Test",
                "demand_surge_pct": 25.0,
                "supplier_delay_days": 3,
                "horizon_days": 14,
            },
        )
        assert sim_res.status_code == 201, sim_res.text
        data = sim_res.json()
        assert data["product_id"] == prod_id
        assert data["sku"] == test_sku
        assert data["run_status"] == "completed"
        assert len(data["timeline"]) == 14
        assert data["scenario_metrics"] is not None
        assert data["baseline_metrics"] is not None
        scenario_id = data["id"]

        # 2. Retrieve persisted scenario
        get_res = client.get(f"/api/v1/scenarios/{scenario_id}")
        assert get_res.status_code == 200
        assert get_res.json()["id"] == scenario_id
        assert get_res.json()["scenario_name"] == "Postgres Live Stress Test"

        # 3. List scenarios for product
        list_res = client.get(f"/api/v1/products/{prod_id}/scenarios")
        assert list_res.status_code == 200
        assert list_res.json()["total"] >= 1

        # 4. Run scenario comparison
        comp_res = client.post(
            f"/api/v1/products/{prod_id}/scenarios/compare",
            json={
                "horizon_days": 14,
                "scenarios": [
                    {
                        "scenario_name": "Stress A",
                        "demand_surge_pct": 20.0,
                        "supplier_delay_days": 2,
                    }
                ],
            },
        )
        assert comp_res.status_code == 200
        comp_data = comp_res.json()
        assert comp_data["product_id"] == prod_id
        assert comp_data["baseline"] is not None
        assert len(comp_data["comparisons"]) == 1

        # 5. Verify real stock was never mutated
        with SessionLocal() as db:
            refreshed_prod = db.get(Product, prod_id)
            assert refreshed_prod.on_hand_qty == initial_stock
