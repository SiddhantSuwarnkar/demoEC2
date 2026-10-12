from fastapi import APIRouter, HTTPException, status
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import DatabaseError

from app.core.config import Settings
from app.main import create_app


class SamplePayload(BaseModel):
    item_count: int
    tag: str


def test_validation_error_response_format(test_settings: Settings):
    """Verify request validation errors follow the standardized error response contract."""
    test_router = APIRouter()

    @test_router.post("/test-validation")
    def sample_endpoint(payload: SamplePayload):
        return {"status": "ok"}

    app = create_app(custom_settings=test_settings)
    app.include_router(test_router)

    with TestClient(app) as test_client:
        # Send invalid payload (item_count string instead of int, tag missing)
        response = test_client.post("/test-validation", json={"item_count": "not_an_int"})
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "VALIDATION_ERROR"
        assert "validation failed" in data["error"]["message"].lower()
        assert isinstance(data["error"]["details"], list)
        assert len(data["error"]["details"]) > 0


def test_unexpected_exception_hides_stack_trace_and_secrets(test_settings: Settings):
    """Verify unhandled server exceptions return generic error without exposing stack trace or internals."""
    test_router = APIRouter()

    @test_router.get("/test-internal-error")
    def trigger_crash():
        # Simulate unexpected crash with secret in local variable
        internal_secret = "SUPER_SECRET_TOKEN_XYZ_123"
        raise RuntimeError(f"Unexpected internal failure involving {internal_secret}")

    app = create_app(custom_settings=test_settings)
    app.include_router(test_router)

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/test-internal-error")
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "INTERNAL_SERVER_ERROR"
        # Confirm no stack trace or secrets are leaked to the public response
        response_text = response.text
        assert "SUPER_SECRET_TOKEN" not in response_text
        assert "Traceback" not in response_text
        assert "RuntimeError" not in response_text


def test_database_error_returns_controlled_response(test_settings: Settings):
    """Verify database exceptions return controlled DATABASE_ERROR without leaking raw SQL."""
    test_router = APIRouter()

    @test_router.get("/test-db-error")
    def trigger_db_crash():
        raise DatabaseError(
            "SELECT * FROM sensitive_users_table", {}, orig=Exception("DB connection broken")
        )

    app = create_app(custom_settings=test_settings)
    app.include_router(test_router)

    with TestClient(app) as test_client:
        response = test_client.get("/test-db-error")
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "DATABASE_ERROR"
        # Client response must be generic
        assert "sensitive_users_table" not in response.text


def test_http_exception_follows_uniform_contract(test_settings: Settings):
    """Verify HTTP exceptions follow the unified error contract."""
    test_router = APIRouter()

    @test_router.get("/test-not-found")
    def not_found_endpoint():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource was not found")

    app = create_app(custom_settings=test_settings)
    app.include_router(test_router)

    with TestClient(app) as test_client:
        response = test_client.get("/test-not-found")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "HTTP_404"
        assert data["error"]["message"] == "Resource was not found"
