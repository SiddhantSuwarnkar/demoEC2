from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class ScenarioRun(Base):
    __tablename__ = "scenario_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    baseline_recommendation_id = Column(
        Integer, ForeignKey("recommendations.id", ondelete="SET NULL"), nullable=True
    )
    baseline_forecast_run_id = Column(
        Integer, ForeignKey("forecast_runs.id", ondelete="SET NULL"), nullable=True
    )
    scenario_name = Column(String(128), nullable=False)
    demand_surge_pct = Column(Float, nullable=False, default=0.0)
    supplier_delay_days = Column(Integer, nullable=False, default=0)
    horizon_days = Column(Integer, nullable=False, default=30)
    run_count = Column(Integer, nullable=False, default=1)
    random_seed = Column(BigInteger, nullable=True)
    scenario_config = Column(JSON, nullable=True)
    baseline_metrics = Column(JSON, nullable=True)
    scenario_metrics = Column(JSON, nullable=True)
    projected_unmet_demand = Column(Float, nullable=True)
    projected_total_cost = Column(Float, nullable=True)
    parameters = Column(JSON, nullable=True)
    results_summary = Column(JSON, nullable=True)
    run_status = Column(String(30), nullable=False, default="completed")
    failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "demand_surge_pct >= 0",
            name="ck_scenario_runs_demand_surge_non_negative",
        ),
        CheckConstraint(
            "supplier_delay_days >= 0",
            name="ck_scenario_runs_delay_days_non_negative",
        ),
    )

    # Relationships
    product = relationship("Product", back_populates="scenario_runs")
    baseline_recommendation = relationship("Recommendation")
    baseline_forecast_run = relationship("ForecastRun")

    def __repr__(self) -> str:
        return (
            f"<ScenarioRun(id={self.id}, product_id={self.product_id}, "
            f"name='{self.scenario_name}', surge={self.demand_surge_pct}%, delay={self.supplier_delay_days}d)>"
        )
