from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.anomaly import AnomalyReview
from app.models.demand import DemandRecord
from app.models.forecast import ForecastRun
from app.models.product import Product
from app.models.recommendation import Recommendation
from app.models.scenario import ScenarioRun


def test_session_lifecycle(db_session: Session):
    """Verify database sessions can be cleanly created, used, and closed."""
    assert db_session.is_active is True
    # Can query without error
    products_count = db_session.query(Product).count()
    assert products_count == 0


def test_product_crud(db_session: Session):
    """Verify basic CRUD on the Product model."""
    prod = Product(
        sku="TEST-SKU-001",
        name="Test Product One",
        category="Electronics",
        current_stock=100,
        safety_stock=20,
        reorder_point=40,
        lead_time_days=5,
        unit_cost=Decimal("15.50"),
    )
    db_session.add(prod)
    db_session.commit()
    db_session.refresh(prod)

    assert prod.id is not None
    assert prod.sku == "TEST-SKU-001"
    assert prod.current_stock == 100

    # Query back
    fetched = db_session.query(Product).filter_by(sku="TEST-SKU-001").first()
    assert fetched is not None
    assert fetched.name == "Test Product One"


def test_negative_stock_constraint_fails(db_session: Session):
    """Verify CheckConstraint prevents negative current_stock."""
    prod = Product(
        sku="BAD-STOCK",
        name="Invalid Stock Product",
        current_stock=-10,  # Violates current_stock >= 0
    )
    db_session.add(prod)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_lead_time_constraint_fails(db_session: Session):
    """Verify CheckConstraint prevents lead_time_days < 1."""
    prod = Product(
        sku="BAD-LEAD",
        name="Invalid Lead Time",
        lead_time_days=0,  # Violates lead_time_days >= 1
    )
    db_session.add(prod)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_unique_sku_constraint(db_session: Session):
    """Verify SKU uniqueness constraint."""
    p1 = Product(sku="DUP-SKU", name="Product 1")
    p2 = Product(sku="DUP-SKU", name="Product 2")
    db_session.add(p1)
    db_session.commit()

    db_session.add(p2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_foreign_key_enforcement_on_demand_records(db_session: Session):
    """Verify foreign key constraint rejects non-existent product_id."""
    invalid_demand = DemandRecord(
        product_id=999999,  # Nonexistent
        date=date(2026, 1, 1),
        quantity=50.0,
    )
    db_session.add(invalid_demand)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_cascade_delete_removes_child_records(db_session: Session):
    """Verify deleting a product cascades to its associated records."""
    prod = Product(sku="CASCADE-SKU", name="Cascade Product")
    db_session.add(prod)
    db_session.commit()

    demand = DemandRecord(product_id=prod.id, date=date(2026, 1, 1), quantity=10.0)
    forecast = ForecastRun(product_id=prod.id, candidate_method="prophet")
    rec = Recommendation(
        product_id=prod.id,
        recommended_order_quantity=50,
        current_stock=10,
        safety_stock=5,
        reorder_point=15,
    )
    scenario = ScenarioRun(product_id=prod.id, scenario_name="High Surge")

    db_session.add_all([demand, forecast, rec, scenario])
    db_session.commit()

    # Now delete product
    db_session.delete(prod)
    db_session.commit()

    # Check that children were deleted
    assert db_session.query(DemandRecord).filter_by(product_id=prod.id).count() == 0
    assert db_session.query(ForecastRun).filter_by(product_id=prod.id).count() == 0
    assert db_session.query(Recommendation).filter_by(product_id=prod.id).count() == 0
    assert db_session.query(ScenarioRun).filter_by(product_id=prod.id).count() == 0


def test_anomaly_review_classification_constraint(db_session: Session):
    """Verify classification must be promotion, data_error, or unknown."""
    prod = Product(sku="ANOMALY-SKU", name="Anomaly Product")
    db_session.add(prod)
    db_session.commit()

    demand = DemandRecord(
        product_id=prod.id, date=date(2026, 1, 1), quantity=100.0, is_anomaly=True
    )
    db_session.add(demand)
    db_session.commit()

    # Invalid classification
    review = AnomalyReview(
        demand_record_id=demand.id,
        classification="invalid_random_type",
        original_quantity=100.0,
    )
    db_session.add(review)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Valid classification
    valid_review = AnomalyReview(
        demand_record_id=demand.id,
        classification="promotion",
        original_quantity=100.0,
    )
    db_session.add(valid_review)
    db_session.commit()
    assert valid_review.id is not None
