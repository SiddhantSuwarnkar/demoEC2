"""0003_align_schema

Revision ID: 0003_align_schema
Revises: 0002_data_imports
Create Date: 2026-10-11 14:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_align_schema"
down_revision: Union[str, None] = "0002_data_imports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. anomaly_reviews table extensions
    with op.batch_alter_table("anomaly_reviews") as batch_op:
        batch_op.add_column(sa.Column("product_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("detector_version", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("anomaly_type", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("anomaly_score", sa.Numeric(precision=12, scale=6), nullable=True))
        batch_op.add_column(sa.Column("affected_start_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("affected_end_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("detector_explanation", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("ai_suggestion", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("review_status", sa.String(length=30), server_default="pending", nullable=False)
        )
        batch_op.add_column(sa.Column("user_treatment", sa.String(length=50), nullable=True))
        batch_op.create_foreign_key(
            "fk_anomaly_reviews_product_id",
            "products",
            ["product_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index("ix_anomaly_reviews_product_id", ["product_id"])

    # 2. forecast_runs table extensions
    with op.batch_alter_table("forecast_runs") as batch_op:
        batch_op.add_column(sa.Column("training_start_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("training_end_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("baseline_config", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("candidate_config", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("data_version", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("failure_reason", sa.Text(), nullable=True))

    # 3. recommendations table extensions
    with op.batch_alter_table("recommendations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "calculated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("formula_version", sa.String(length=50), server_default="v1.0", nullable=False)
        )
        batch_op.add_column(sa.Column("input_snapshot", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("inventory_position", sa.Numeric(precision=14, scale=3), nullable=True))
        batch_op.add_column(sa.Column("mean_daily_demand", sa.Numeric(precision=14, scale=6), nullable=True))
        batch_op.add_column(sa.Column("daily_demand_stddev", sa.Numeric(precision=14, scale=6), nullable=True))
        batch_op.add_column(sa.Column("average_lead_time_days", sa.Numeric(precision=10, scale=3), nullable=True))
        batch_op.add_column(sa.Column("lead_time_stddev_days", sa.Numeric(precision=10, scale=3), nullable=True))
        batch_op.add_column(sa.Column("target_service_level", sa.Numeric(precision=5, scale=4), nullable=True))
        batch_op.add_column(sa.Column("z_factor", sa.Numeric(precision=10, scale=6), nullable=True))
        batch_op.add_column(sa.Column("expected_lead_time_demand", sa.Numeric(precision=14, scale=6), nullable=True))
        batch_op.add_column(sa.Column("annual_demand", sa.Numeric(precision=14, scale=3), nullable=True))
        batch_op.add_column(sa.Column("raw_eoq", sa.Numeric(precision=14, scale=3), nullable=True))

    # 4. scenario_runs table extensions
    with op.batch_alter_table("scenario_runs") as batch_op:
        batch_op.add_column(sa.Column("baseline_recommendation_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("baseline_forecast_run_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("horizon_days", sa.Integer(), server_default="30", nullable=False))
        batch_op.add_column(sa.Column("run_count", sa.Integer(), server_default="1", nullable=False))
        batch_op.add_column(sa.Column("random_seed", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("scenario_config", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("baseline_metrics", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("scenario_metrics", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("run_status", sa.String(length=30), server_default="completed", nullable=False)
        )
        batch_op.add_column(sa.Column("failure_reason", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_scenario_runs_baseline_recommendation",
            "recommendations",
            ["baseline_recommendation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_scenario_runs_baseline_forecast_run",
            "forecast_runs",
            ["baseline_forecast_run_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("scenario_runs") as batch_op:
        batch_op.drop_constraint("fk_scenario_runs_baseline_forecast_run", type_="foreignkey")
        batch_op.drop_constraint("fk_scenario_runs_baseline_recommendation", type_="foreignkey")
        batch_op.drop_column("failure_reason")
        batch_op.drop_column("run_status")
        batch_op.drop_column("scenario_metrics")
        batch_op.drop_column("baseline_metrics")
        batch_op.drop_column("scenario_config")
        batch_op.drop_column("random_seed")
        batch_op.drop_column("run_count")
        batch_op.drop_column("horizon_days")
        batch_op.drop_column("baseline_forecast_run_id")
        batch_op.drop_column("baseline_recommendation_id")

    with op.batch_alter_table("recommendations") as batch_op:
        batch_op.drop_column("raw_eoq")
        batch_op.drop_column("annual_demand")
        batch_op.drop_column("expected_lead_time_demand")
        batch_op.drop_column("z_factor")
        batch_op.drop_column("target_service_level")
        batch_op.drop_column("lead_time_stddev_days")
        batch_op.drop_column("average_lead_time_days")
        batch_op.drop_column("daily_demand_stddev")
        batch_op.drop_column("mean_daily_demand")
        batch_op.drop_column("inventory_position")
        batch_op.drop_column("input_snapshot")
        batch_op.drop_column("formula_version")
        batch_op.drop_column("calculated_at")

    with op.batch_alter_table("forecast_runs") as batch_op:
        batch_op.drop_column("failure_reason")
        batch_op.drop_column("data_version")
        batch_op.drop_column("candidate_config")
        batch_op.drop_column("baseline_config")
        batch_op.drop_column("training_end_date")
        batch_op.drop_column("training_start_date")

    with op.batch_alter_table("anomaly_reviews") as batch_op:
        batch_op.drop_index("ix_anomaly_reviews_product_id")
        batch_op.drop_constraint("fk_anomaly_reviews_product_id", type_="foreignkey")
        batch_op.drop_column("user_treatment")
        batch_op.drop_column("review_status")
        batch_op.drop_column("ai_suggestion")
        batch_op.drop_column("detector_explanation")
        batch_op.drop_column("affected_end_date")
        batch_op.drop_column("affected_start_date")
        batch_op.drop_column("anomaly_score")
        batch_op.drop_column("anomaly_type")
        batch_op.drop_column("detector_version")
        batch_op.drop_column("product_id")
