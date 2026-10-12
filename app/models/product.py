from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import relationship, synonym

from app.db.base import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sku = Column(String(64), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    product_name = synonym("name")
    category = Column(String(100), nullable=True, index=True)
    unit_of_measure = Column(String(32), nullable=False, default="piece")

    # Inventory Quantities
    current_stock = Column(Integer, nullable=False, default=0)
    on_hand_qty = synonym("current_stock")
    committed_qty = Column(Integer, nullable=False, default=0)
    safety_stock = Column(Integer, nullable=False, default=0)
    reorder_point = Column(Integer, nullable=False, default=0)

    # Lead Time Profile
    lead_time_days = Column(Integer, nullable=True)
    average_lead_time_days = synonym("lead_time_days")
    lead_time_stddev_days = Column(Float, nullable=True)

    # Costs & Economic Parameters
    unit_cost = Column(Numeric(12, 2), nullable=True)
    ordering_cost = Column(Numeric(12, 2), nullable=True)
    fixed_order_cost = synonym("ordering_cost")
    holding_cost_rate = Column(Numeric(5, 4), nullable=True)
    holding_cost_per_unit_year = Column(Numeric(12, 2), nullable=True)
    stockout_cost = Column(Numeric(12, 2), nullable=True)
    target_service_level = Column(Float, nullable=False, default=0.95)

    # Supplier Constraints
    minimum_order_qty = Column(Integer, nullable=True)
    min_order_qty = synonym("minimum_order_qty")
    pack_size = Column(Integer, nullable=True)

    # Status & Tracking
    import_id = Column(
        String(36), ForeignKey("data_imports.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("current_stock >= 0", name="ck_products_current_stock_non_negative"),
        CheckConstraint("committed_qty >= 0", name="ck_products_committed_qty_non_negative"),
        CheckConstraint("safety_stock >= 0", name="ck_products_safety_stock_non_negative"),
        CheckConstraint("reorder_point >= 0", name="ck_products_reorder_point_non_negative"),
        CheckConstraint(
            "lead_time_days IS NULL OR lead_time_days >= 1", name="ck_products_lead_time_positive"
        ),
        CheckConstraint(
            "lead_time_stddev_days IS NULL OR lead_time_stddev_days >= 0",
            name="ck_products_lead_time_stddev_non_negative",
        ),
        CheckConstraint(
            "unit_cost IS NULL OR unit_cost >= 0", name="ck_products_unit_cost_non_negative"
        ),
        CheckConstraint(
            "target_service_level > 0 AND target_service_level < 1",
            name="ck_products_service_level_range",
        ),
        CheckConstraint(
            "minimum_order_qty IS NULL OR minimum_order_qty >= 1", name="ck_products_moq_positive"
        ),
        CheckConstraint(
            "pack_size IS NULL OR pack_size >= 1", name="ck_products_pack_size_positive"
        ),
    )

    # Relationships
    demand_records = relationship(
        "DemandRecord", back_populates="product", cascade="all, delete-orphan"
    )
    anomaly_reviews = relationship(
        "AnomalyReview", back_populates="product", cascade="all, delete-orphan"
    )
    forecast_runs = relationship(
        "ForecastRun", back_populates="product", cascade="all, delete-orphan"
    )
    recommendations = relationship(
        "Recommendation", back_populates="product", cascade="all, delete-orphan"
    )
    scenario_runs = relationship(
        "ScenarioRun", back_populates="product", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Product(id={self.id}, sku='{self.sku}', name='{self.name}')>"
