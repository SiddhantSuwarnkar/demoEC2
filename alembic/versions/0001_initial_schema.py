"""0001_initial_schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-10-11 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. products table
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("unit_of_measure", sa.String(length=32), nullable=False, server_default="piece"),
        sa.Column("current_stock", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("committed_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("safety_stock", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reorder_point", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lead_time_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("lead_time_stddev_days", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("unit_cost", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0.00"),
        sa.Column("ordering_cost", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("holding_cost_rate", sa.Numeric(precision=5, scale=4), nullable=False, server_default="0.2000"),
        sa.Column("stockout_cost", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("target_service_level", sa.Float(), nullable=False, server_default="0.95"),
        sa.Column("minimum_order_qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("pack_size", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("current_stock >= 0", name="ck_products_current_stock_non_negative"),
        sa.CheckConstraint("committed_qty >= 0", name="ck_products_committed_qty_non_negative"),
        sa.CheckConstraint("safety_stock >= 0", name="ck_products_safety_stock_non_negative"),
        sa.CheckConstraint("reorder_point >= 0", name="ck_products_reorder_point_non_negative"),
        sa.CheckConstraint("lead_time_days >= 1", name="ck_products_lead_time_positive"),
        sa.CheckConstraint("lead_time_stddev_days >= 0", name="ck_products_lead_time_stddev_non_negative"),
        sa.CheckConstraint("unit_cost >= 0", name="ck_products_unit_cost_non_negative"),
        sa.CheckConstraint("target_service_level > 0 AND target_service_level < 1", name="ck_products_service_level_range"),
        sa.CheckConstraint("minimum_order_qty >= 1", name="ck_products_moq_positive"),
        sa.CheckConstraint("pack_size >= 1", name="ck_products_pack_size_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_products"),
    )
    op.create_index("ix_products_sku", "products", ["sku"], unique=True)
    op.create_index("ix_products_category", "products", ["category"], unique=False)

    # 2. demand_records table
    op.create_table(
        "demand_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("stockout_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("promotion_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_anomaly", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("quantity >= 0", name="ck_demand_records_quantity_non_negative"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_demand_records_product_id_products", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_demand_records"),
        sa.UniqueConstraint("product_id", "date", name="uq_demand_records_product_date"),
    )
    op.create_index("ix_demand_records_product_id", "demand_records", ["product_id"], unique=False)
    op.create_index("ix_demand_records_date", "demand_records", ["date"], unique=False)

    # 3. anomaly_reviews table
    op.create_table(
        "anomaly_reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("demand_record_id", sa.Integer(), nullable=False),
        sa.Column("classification", sa.String(length=32), nullable=False),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("original_quantity", sa.Float(), nullable=False),
        sa.Column("adjusted_quantity", sa.Float(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("classification IN ('promotion', 'data_error', 'unknown')", name="ck_anomaly_reviews_classification"),
        sa.CheckConstraint("original_quantity >= 0", name="ck_anomaly_reviews_original_qty_non_negative"),
        sa.CheckConstraint("adjusted_quantity IS NULL OR adjusted_quantity >= 0", name="ck_anomaly_reviews_adjusted_qty_non_negative"),
        sa.ForeignKeyConstraint(["demand_record_id"], ["demand_records.id"], name="fk_anomaly_reviews_demand_record_id_demand_records", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_anomaly_reviews"),
        sa.UniqueConstraint("demand_record_id", name="uq_anomaly_reviews_demand_record_id"),
    )
    op.create_index("ix_anomaly_reviews_demand_record_id", "anomaly_reviews", ["demand_record_id"], unique=True)

    # 4. forecast_runs table
    op.create_table(
        "forecast_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("baseline_method", sa.String(length=64), nullable=False, server_default="moving_average"),
        sa.Column("candidate_method", sa.String(length=64), nullable=False),
        sa.Column("mae_baseline", sa.Float(), nullable=True),
        sa.Column("mae_candidate", sa.Float(), nullable=True),
        sa.Column("wape_baseline", sa.Float(), nullable=True),
        sa.Column("wape_candidate", sa.Float(), nullable=True),
        sa.Column("holdout_start_date", sa.Date(), nullable=True),
        sa.Column("holdout_end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="completed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_forecast_runs_product_id_products", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_forecast_runs"),
    )
    op.create_index("ix_forecast_runs_product_id", "forecast_runs", ["product_id"], unique=False)

    # 5. recommendations table
    op.create_table(
        "recommendations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("recommended_order_quantity", sa.Integer(), nullable=False),
        sa.Column("current_stock", sa.Integer(), nullable=False),
        sa.Column("safety_stock", sa.Integer(), nullable=False),
        sa.Column("reorder_point", sa.Integer(), nullable=False),
        sa.Column("urgency", sa.String(length=32), nullable=False, server_default="medium"),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("assumptions", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("recommended_order_quantity >= 0", name="ck_recommendations_order_qty_non_negative"),
        sa.CheckConstraint("urgency IN ('low', 'medium', 'high', 'critical')", name="ck_recommendations_urgency"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_recommendations_status"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_recommendations_product_id_products", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_recommendations"),
    )
    op.create_index("ix_recommendations_product_id", "recommendations", ["product_id"], unique=False)

    # 6. scenario_runs table
    op.create_table(
        "scenario_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("scenario_name", sa.String(length=128), nullable=False),
        sa.Column("demand_surge_pct", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("supplier_delay_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("projected_unmet_demand", sa.Float(), nullable=True),
        sa.Column("projected_total_cost", sa.Float(), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column("results_summary", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("demand_surge_pct >= 0", name="ck_scenario_runs_demand_surge_non_negative"),
        sa.CheckConstraint("supplier_delay_days >= 0", name="ck_scenario_runs_delay_days_non_negative"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_scenario_runs_product_id_products", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_scenario_runs"),
    )
    op.create_index("ix_scenario_runs_product_id", "scenario_runs", ["product_id"], unique=False)


def downgrade() -> None:
    op.drop_table("scenario_runs")
    op.drop_table("recommendations")
    op.drop_table("forecast_runs")
    op.drop_table("anomaly_reviews")
    op.drop_table("demand_records")
    op.drop_table("products")
