import os
import sqlite3
import tempfile
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Fixture providing isolated testing settings."""
    return Settings(
        ENVIRONMENT="testing",
        PROJECT_NAME="StockSense Test",
        VERSION="0.1.0-test",
        DATABASE_URL="sqlite:///:memory:",
        SECRET_KEY="test_secret_key_for_automated_testing_purposes_only",
        CORS_ORIGINS=["http://testserver"],
        LOG_LEVEL="DEBUG",
    )


@pytest.fixture(scope="function")
def test_engine():
    """
    In-memory SQLite engine with foreign key enforcement enabled for isolated test runs.
    Uses StaticPool so connections share the in-memory database.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        if isinstance(dbapi_connection, sqlite3.Connection):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(test_engine) -> Generator[Session, None, None]:
    """Provides a fresh transactional session for a single test."""
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function")
def client(test_settings, test_engine, db_session) -> Generator[TestClient, None, None]:
    """TestClient wired to the isolated in-memory test database."""
    app = create_app(custom_settings=test_settings)

    def override_get_db() -> Generator[Session, None, None]:
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def temp_db_file() -> Generator[str, None, None]:
    """Provides a temporary SQLite database file for file-based migration testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        yield path
    finally:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
