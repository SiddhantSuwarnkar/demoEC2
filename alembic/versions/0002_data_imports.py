"""0002_data_imports

Revision ID: 0002_data_imports
Revises: 0001_initial_schema
Create Date: 2026-10-11 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_data_imports"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create data_imports table
    op.create_table(
        "data_imports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("file_type", sa.String(length=32), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="previewed"),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("can_confirm", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("staged_data", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_data_imports"),
    )
    op.create_index("ix_data_imports_status", "data_imports", ["status"], unique=False)
    op.create_index("ix_data_imports_file_type", "data_imports", ["file_type"], unique=False)

    # 2. Create import_row_errors table
    op.create_table(
        "import_row_errors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("import_id", sa.String(length=36), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("column_name", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("is_warning", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["data_imports.id"],
            name="fk_import_row_errors_import_id_data_imports",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_import_row_errors"),
    )
    op.create_index("ix_import_row_errors_import_id", "import_row_errors", ["import_id"], unique=False)

    # 3. Add holding_cost_per_unit_year, import_id, and make optional fields nullable on products
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("holding_cost_per_unit_year", sa.Numeric(precision=12, scale=2), nullable=True)
        )
        batch_op.add_column(
            sa.Column("import_id", sa.String(length=36), nullable=True)
        )
        batch_op.alter_column(
            "unit_cost",
            existing_type=sa.Numeric(precision=12, scale=2),
            nullable=True,
            server_default=None,
        )
        batch_op.alter_column(
            "holding_cost_rate",
            existing_type=sa.Numeric(precision=5, scale=4),
            nullable=True,
            server_default=None,
        )
        batch_op.alter_column(
            "lead_time_days",
            existing_type=sa.Integer(),
            nullable=True,
            server_default=None,
        )
        batch_op.alter_column(
            "lead_time_stddev_days",
            existing_type=sa.Float(),
            nullable=True,
            server_default=None,
        )
        batch_op.alter_column(
            "minimum_order_qty",
            existing_type=sa.Integer(),
            nullable=True,
            server_default=None,
        )
        batch_op.alter_column(
            "pack_size",
            existing_type=sa.Integer(),
            nullable=True,
            server_default=None,
        )
        batch_op.create_foreign_key(
            "fk_products_import_id_data_imports",
            "data_imports",
            ["import_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_products_import_id", ["import_id"], unique=False)

    # 4. Add import_id to demand_records
    with op.batch_alter_table("demand_records", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("import_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_demand_records_import_id_data_imports",
            "data_imports",
            ["import_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_demand_records_import_id", ["import_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("demand_records", schema=None) as batch_op:
        batch_op.drop_index("ix_demand_records_import_id")
        batch_op.drop_constraint("fk_demand_records_import_id_data_imports", type_="foreignkey")
        batch_op.drop_column("import_id")

    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_index("ix_products_import_id")
        batch_op.drop_constraint("fk_products_import_id_data_imports", type_="foreignkey")
        batch_op.drop_column("import_id")
        batch_op.drop_column("holding_cost_per_unit_year")
        batch_op.alter_column(
            "pack_size",
            existing_type=sa.Integer(),
            nullable=False,
            server_default="1",
        )
        batch_op.alter_column(
            "minimum_order_qty",
            existing_type=sa.Integer(),
            nullable=False,
            server_default="1",
        )
        batch_op.alter_column(
            "lead_time_stddev_days",
            existing_type=sa.Float(),
            nullable=False,
            server_default="0.0",
        )
        batch_op.alter_column(
            "lead_time_days",
            existing_type=sa.Integer(),
            nullable=False,
            server_default="1",
        )
        batch_op.alter_column(
            "holding_cost_rate",
            existing_type=sa.Numeric(precision=5, scale=4),
            nullable=False,
            server_default="0.2000",
        )
        batch_op.alter_column(
            "unit_cost",
            existing_type=sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default="0.00",
        )

    op.drop_index("ix_import_row_errors_import_id", table_name="import_row_errors")
    op.drop_table("import_row_errors")
    op.drop_index("ix_data_imports_file_type", table_name="data_imports")
    op.drop_index("ix_data_imports_status", table_name="data_imports")
    op.drop_table("data_imports")
