from sqlalchemy import JSON, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship, synonym

from app.db.base import Base


class ForecastRun(Base):
    __tablename__ = "forecast_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    baseline_method = Column(String(100), nullable=False, default="moving_average")
    candidate_method = Column(String(100), nullable=False)
    training_start_date = Column(Date, nullable=True)
    training_end_date = Column(Date, nullable=True)
    holdout_start_date = Column(Date, nullable=True)
    holdout_end_date = Column(Date, nullable=True)
    baseline_config = Column(JSON, nullable=True)
    candidate_config = Column(JSON, nullable=True)
    mae_baseline = Column(Float, nullable=True)
    baseline_mae = synonym("mae_baseline")
    mae_candidate = Column(Float, nullable=True)
    candidate_mae = synonym("mae_candidate")
    wape_baseline = Column(Float, nullable=True)
    baseline_wape = synonym("wape_baseline")
    wape_candidate = Column(Float, nullable=True)
    candidate_wape = synonym("wape_candidate")
    data_version = Column(String(100), nullable=True)
    status = Column(String(32), nullable=False, default="completed")
    run_status = synonym("status")
    failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    product = relationship("Product", back_populates="forecast_runs")

    def __repr__(self) -> str:
        return (
            f"<ForecastRun(id={self.id}, product_id={self.product_id}, "
            f"baseline='{self.baseline_method}', candidate='{self.candidate_method}')>"
        )
