import sqlite3
from typing import Generator, Optional

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.logging import logger


def build_engine(database_url: Optional[str] = None) -> Engine:
    """
    Construct an engine with dialect-specific optimizations.
    Configures SQLite foreign keys and pool parameters appropriately.
    """
    url = database_url or settings.DATABASE_URL
    is_sqlite = url.lower().startswith("sqlite")

    connect_args = {}
    engine_kwargs = {
        "future": True,
    }

    if is_sqlite:
        connect_args["check_same_thread"] = False
        if ":memory:" in url:
            # Memory SQLite databases require StaticPool to preserve tables across connections
            engine_kwargs["poolclass"] = StaticPool
    else:
        # PostgreSQL production connection pooling
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["pool_size"] = 10
        engine_kwargs["max_overflow"] = 20

    engine = create_engine(url, connect_args=connect_args, **engine_kwargs)

    if is_sqlite:
        # Enforce foreign key constraints in SQLite
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):  # type: ignore
            if isinstance(dbapi_connection, sqlite3.Connection):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

    return engine


# Default application engine and session factory
engine = build_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency yielding a managed database session.
    Ensures sessions are cleanly closed even on errors.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database_connection(target_engine: Optional[Engine] = None) -> bool:
    """
    Execute a lightweight connectivity probe ('SELECT 1').
    Returns True if reachable, False otherwise.
    Never exposes credentials or internal errors publicly.
    """
    active_engine = target_engine or engine
    try:
        with active_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("Database readiness probe failed: %s", str(exc))
        return False
