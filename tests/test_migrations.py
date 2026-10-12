import os

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


def test_alembic_upgrade_and_downgrade(temp_db_file: str):
    """
    Verify Alembic migrations can be applied to a clean test database,
    all 6 tables are created, and downgrade/re-upgrade operate cleanly.
    """
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    alembic_ini_path = os.path.join(backend_dir, "alembic.ini")

    alembic_cfg = Config(alembic_ini_path)
    # Point alembic to the temp db file
    db_url = f"sqlite:///{temp_db_file.replace(os.sep, '/')}"
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    # 1. Apply migration up to head
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    # 2. Verify all six core tables exist
    expected_tables = {
        "products",
        "demand_records",
        "anomaly_reviews",
        "forecast_runs",
        "recommendations",
        "scenario_runs",
    }
    assert expected_tables.issubset(set(table_names)), (
        f"Missing tables in migration: {expected_tables - set(table_names)}"
    )

    # Verify column existence in products
    columns = [col["name"] for col in inspector.get_columns("products")]
    assert "sku" in columns
    assert "current_stock" in columns
    assert "reorder_point" in columns
    assert "lead_time_days" in columns

    # 3. Test downgrade to base
    command.downgrade(alembic_cfg, "base")
    inspector = inspect(engine)
    tables_after_downgrade = inspector.get_table_names()
    # The 6 core tables should be dropped (alembic_version table may remain)
    for table in expected_tables:
        assert table not in tables_after_downgrade, f"Table {table} was not dropped by downgrade"

    # 4. Re-apply upgrade to ensure idempotence / clean replay
    command.upgrade(alembic_cfg, "head")
    inspector = inspect(engine)
    assert expected_tables.issubset(set(inspector.get_table_names()))

    engine.dispose()
