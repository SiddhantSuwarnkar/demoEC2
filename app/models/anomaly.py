from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship, synonym

from app.db.base import Base


class AnomalyReview(Base):
    __tablename__ = "anomaly_reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(
        Integer,
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    demand_record_id = Column(
        Integer,
        ForeignKey("demand_records.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
        index=True,
    )
    detector_version = Column(String(100), nullable=True)
    anomaly_type = Column(String(50), nullable=True)
    anomaly_score = Column(Numeric(12, 6), nullable=True)
    affected_start_date = Column(Date, nullable=True)
    affected_end_date = Column(Date, nullable=True)
    detector_explanation = Column(Text, nullable=True)
    ai_suggestion = Column(Text, nullable=True)
    review_status = Column(String(30), nullable=False, default="pending")
    classification = Column(String(32), nullable=False)
    user_classification = synonym("classification")
    user_treatment = Column(String(50), nullable=True)
    reviewer_notes = Column(Text, nullable=True)
    original_quantity = Column(Float, nullable=False)
    adjusted_quantity = Column(Float, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "classification IN ('promotion', 'data_error', 'unknown')",
            name="ck_anomaly_reviews_classification",
        ),
        CheckConstraint(
            "original_quantity >= 0", name="ck_anomaly_reviews_original_qty_non_negative"
        ),
        CheckConstraint(
            "adjusted_quantity IS NULL OR adjusted_quantity >= 0",
            name="ck_anomaly_reviews_adjusted_qty_non_negative",
        ),
    )

    # Relationships
    demand_record = relationship("DemandRecord", back_populates="anomaly_review")
    product = relationship("Product", back_populates="anomaly_reviews")

    def __repr__(self) -> str:
        return (
            f"<AnomalyReview(id={self.id}, demand_record_id={self.demand_record_id}, "
            f"classification='{self.classification}')>"
        )
