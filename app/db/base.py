from sqlalchemy import MetaData
from sqlalchemy.orm import declarative_base

# Explicit constraint naming conventions required for robust Alembic migrations across
# both SQLite (batch mode) and PostgreSQL.
POSTGRES_SQLITE_NAMING_CONVENTIONS = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=POSTGRES_SQLITE_NAMING_CONVENTIONS)

Base = declarative_base(metadata=metadata)
