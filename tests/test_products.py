from datetime import date
from decimal import Decimal

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.demand import DemandRecord
from app.models.product import Product
from app.seed.demo_data import seed_demo_data


def test_list_products_empty(client: TestClient):
    """GET /api/v1/products returns empty list when no products configured."""
    response = client.get("/api/v1/products")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


def test_list_products_and_get_details(client: TestClient, db_session: Session):
    """Verify listing and retrieving product details."""
    prod = Product(
        sku="NOTEBOOK-01",
        name="Lined Notebook",
        category="Stationery",
        unit_of_measure="piece",
        current_stock=50,
        committed_qty=5,
        safety_stock=10,
        reorder_point=25,
        lead_time_days=3,
        unit_cost=Decimal("12.50"),
    )
    db_session.add(prod)
    db_session.commit()

    # 1. List products
    response = client.get("/api/v1/products")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["sku"] == "NOTEBOOK-01"
    assert data[0]["current_stock"] == 50

    # 2. Get specific product
    p_id = data[0]["id"]
    detail_res = client.get(f"/api/v1/products/{p_id}")
    assert detail_res.status_code == status.HTTP_200_OK
    detail_data = detail_res.json()
    assert detail_data["id"] == p_id
    assert detail_data["name"] == "Lined Notebook"


def test_get_product_not_found(client: TestClient):
    """GET /api/v1/products/{id} returns 404 for unknown product."""
    response = client.get("/api/v1/products/999999")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "HTTP_404"


def test_get_product_demand_chronological(client: TestClient, db_session: Session):
    """GET /api/v1/products/{id}/demand returns ordered demand records."""
    prod = Product(sku="CHRONO-01", name="Chronological Test Item")
    db_session.add(prod)
    db_session.commit()

    # Add out of order records
    d2 = DemandRecord(product_id=prod.id, date=date(2026, 9, 2), quantity=15.0, stockout_flag=False)
    d1 = DemandRecord(product_id=prod.id, date=date(2026, 9, 1), quantity=10.0, stockout_flag=False)
    d3 = DemandRecord(
        product_id=prod.id,
        date=date(2026, 9, 3),
        quantity=0.0,
        stockout_flag=True,
        notes="stockout",
    )
    db_session.add_all([d2, d1, d3])
    db_session.commit()

    response = client.get(f"/api/v1/products/{prod.id}/demand")
    assert response.status_code == status.HTTP_200_OK
    records = response.json()
    assert len(records) == 3
    # Must be in chronological order
    assert records[0]["date"] == "2026-09-01"
    assert records[0]["quantity"] == 10.0
    assert records[1]["date"] == "2026-09-02"
    assert records[2]["date"] == "2026-09-03"
    assert records[2]["stockout_flag"] is True
    assert records[2]["notes"] == "stockout"


def test_get_demand_for_unknown_product_returns_404(client: TestClient):
    """GET /api/v1/products/{id}/demand returns 404 if product does not exist."""
    response = client.get("/api/v1/products/999999/demand")
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_seed_demo_data_execution(db_session: Session):
    """Verify deterministic demo data seeder correctly populates entities idempotently."""
    res = seed_demo_data(db_session)
    assert res["products"] == 9
    assert res["demand_records"] == 405

    # Verify products exist
    prods = db_session.query(Product).order_by(Product.sku.asc()).all()
    assert len(prods) == 9
    assert prods[0].sku == "SKU-001"
    assert prods[1].sku == "SKU-002"

    # Verify idempotency
    res2 = seed_demo_data(db_session)
    assert res2["products"] == 0
    assert res2["demand_records"] == 0
