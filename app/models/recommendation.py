from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship, synonym

from app.db.base import Base


class Recommendation(Base):
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    calculated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    formula_version = Column(String(50), nullable=False, default="v1.0")
    input_snapshot = Column(JSON, nullable=True)
    inventory_position = Column(Numeric(14, 3), nullable=True)
    mean_daily_demand = Column(Numeric(14, 6), nullable=True)
    daily_demand_stddev = Column(Numeric(14, 6), nullable=True)
    average_lead_time_days = Column(Numeric(10, 3), nullable=True)
    lead_time_stddev_days = Column(Numeric(10, 3), nullable=True)
    target_service_level = Column(Numeric(5, 4), nullable=True)
    z_factor = Column(Numeric(10, 6), nullable=True)
    expected_lead_time_demand = Column(Numeric(14, 6), nullable=True)
    safety_stock = Column(Integer, nullable=False)
    reorder_point = Column(Integer, nullable=False)
    annual_demand = Column(Numeric(14, 3), nullable=True)
    raw_eoq = Column(Numeric(14, 3), nullable=True)
    recommended_order_quantity = Column(Integer, nullable=False)
    suggested_order_qty = synonym("recommended_order_quantity")
    current_stock = Column(Integer, nullable=False)
    urgency = Column(String(32), nullable=False, default="medium")
    explanation = Column(Text, nullable=True)
    assumptions = Column(JSON, nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "recommended_order_quantity >= 0",
            name="ck_recommendations_order_qty_non_negative",
        ),
        CheckConstraint(
            "urgency IN ('low', 'medium', 'high', 'critical')",
            name="ck_recommendations_urgency",
        ),
    )

    # Relationships
    product = relationship("Product", back_populates="recommendations")

    def __repr__(self) -> str:
        return (
            f"<Recommendation(id={self.id}, product_id={self.product_id}, "
            f"qty={self.recommended_order_quantity}, urgency='{self.urgency}')>"
        )
