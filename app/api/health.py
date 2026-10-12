from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import logger
from app.db.session import get_db
from app.schemas.common import HealthLiveResponse, HealthReadyResponse

router = APIRouter(prefix="/health", tags=["Health"])


@router.get(
    "/live",
    response_model=HealthLiveResponse,
    status_code=status.HTTP_200_OK,
    summary="Liveness Probe",
    description="Confirms that the application process is running and responding.",
)
async def health_live() -> HealthLiveResponse:
    return HealthLiveResponse(
        status="ok",
        service="stocksense",
        environment=settings.ENVIRONMENT,
    )


@router.get(
    "/ready",
    response_model=HealthReadyResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Database connection failed or unavailable",
            "content": {
                "application/json": {
                    "example": {"status": "unavailable", "database": "disconnected"}
                }
            },
        }
    },
    summary="Readiness Probe",
    description="Verifies that the required database is reachable and accepting queries.",
)
def health_ready(db: Session = Depends(get_db)):
    try:
        # Execute minimal connectivity probe
        db.execute(text("SELECT 1"))
        return HealthReadyResponse(
            status="ready",
            database="connected",
        )
    except Exception as exc:
        logger.error("Readiness check failed: %s", str(exc))
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable", "database": "disconnected"},
        )
