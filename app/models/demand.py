from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship, synonym

from app.db.base import Base


class DemandRecord(Base):
    __tablename__ = "demand_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date = Column(Date, nullable=False, index=True)
    quantity = Column(Float, nullable=False)
    units_sold = synonym("quantity")
    stockout_flag = Column(Boolean, nullable=False, default=False)
    promotion_flag = Column(Boolean, nullable=False, default=False)
    is_anomaly = Column(Boolean, nullable=False, default=False)
    notes = Column(String(255), nullable=True)
    import_id = Column(
        String(36), ForeignKey("data_imports.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_demand_records_quantity_non_negative"),
        UniqueConstraint("product_id", "date", name="uq_demand_records_product_date"),
    )

    # Relationships
    product = relationship("Product", back_populates="demand_records")
    anomaly_review = relationship(
        "AnomalyReview", back_populates="demand_record", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<DemandRecord(id={self.id}, product_id={self.product_id}, date='{self.date}', quantity={self.quantity})>"
