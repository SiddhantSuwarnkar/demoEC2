from unittest.mock import MagicMock

from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db.session import get_db
from app.main import create_app


def test_health_live_endpoint(client: TestClient):
    """GET /health/live confirms application process is running."""
    response = client.get("/health/live")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "stocksense"
    assert "environment" in data


def test_health_ready_success(client: TestClient):
    """GET /health/ready succeeds when database is reachable."""
    response = client.get("/health/ready")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "ready"
    assert data["database"] == "connected"


def test_health_ready_fails_when_database_unavailable(test_settings):
    """
    GET /health/ready returns 503 when the database is unreachable,
    without exposing connection secrets or stack traces.
    """
    app = create_app(custom_settings=test_settings)

    # Mock failing database session
    mock_failing_session = MagicMock()
    mock_failing_session.execute.side_effect = OperationalError(
        "connection failure", params=None, orig=Exception("Database down")
    )

    def override_failing_db():
        yield mock_failing_session

    app.dependency_overrides[get_db] = override_failing_db

    with TestClient(app) as test_client:
        response = test_client.get("/health/ready")
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        data = response.json()
        assert data["status"] == "unavailable"
        assert data["database"] == "disconnected"
        # Confirm no sensitive details or stack traces are leaked
        assert "password" not in str(data).lower()
        assert "traceback" not in str(data).lower()
