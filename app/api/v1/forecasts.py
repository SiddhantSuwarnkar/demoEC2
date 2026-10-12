from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.forecast import (
    ForecastBenchmarkRequest,
    ForecastMetricsDetailsResponse,
    ForecastRunListResponse,
    ForecastRunRead,
)
from app.services.forecast_service import ForecastService

router = APIRouter(tags=["Forecasting"])


@router.post(
    "/products/{product_id}/forecast-benchmark",
    response_model=ForecastRunRead,
    status_code=status.HTTP_200_OK,
    summary="Execute Forecast Benchmark",
    description=(
        "Executes a reproducible chronological benchmark comparing a Moving Average baseline "
        "against Prophet on identical holdout dates. Withholds holdout actuals from model fitting "
        "to prevent leakage. Persists an immutable benchmark run snapshot."
    ),
)
def run_forecast_benchmark(
    product_id: int,
    request: Optional[ForecastBenchmarkRequest] = None,
    db: Session = Depends(get_db),
) -> ForecastRunRead:
    req = request or ForecastBenchmarkRequest()
    return ForecastService.run_benchmark_for_product(
        db=db,
        product_id=product_id,
        request=req,
    )


@router.get(
    "/forecast-runs/{run_id}",
    response_model=ForecastRunRead,
    summary="Get Forecast Run",
    description="Retrieves a persisted forecast run snapshot including split, diagnostics, metrics, and interpretation.",
)
def get_forecast_run(
    run_id: int,
    db: Session = Depends(get_db),
) -> ForecastRunRead:
    return ForecastService.get_forecast_run(db=db, run_id=run_id)


@router.get(
    "/forecast-runs",
    response_model=ForecastRunListResponse,
    summary="List Forecast Runs",
    description="Returns paginated historical forecast benchmark runs across all products or filtered by product_id.",
)
def list_forecast_runs(
    product_id: Optional[int] = Query(default=None, description="Optional product ID filter"),
    page: int = Query(default=1, ge=1, description="Page number"),
    limit: int = Query(default=20, ge=1, le=100, description="Page size limit"),
    db: Session = Depends(get_db),
) -> ForecastRunListResponse:
    return ForecastService.list_forecast_runs(
        db=db,
        product_id=product_id,
        page=page,
        limit=limit,
    )


@router.get(
    "/products/{product_id}/forecast-runs",
    response_model=ForecastRunListResponse,
    summary="List Forecast Runs for Product",
    description="Returns paginated historical forecast benchmark runs for a specified product.",
)
def list_forecast_runs_for_product(
    product_id: int,
    page: int = Query(default=1, ge=1, description="Page number"),
    limit: int = Query(default=20, ge=1, le=100, description="Page size limit"),
    db: Session = Depends(get_db),
) -> ForecastRunListResponse:
    return ForecastService.list_forecast_runs(
        db=db,
        product_id=product_id,
        page=page,
        limit=limit,
    )


@router.get(
    "/forecast-runs/{run_id}/metrics",
    response_model=ForecastMetricsDetailsResponse,
    summary="Get Forecast Run Metrics & Predictions",
    description="Retrieves detailed point predictions, MAE, WAPE, and comparative evaluation for a run.",
)
def get_forecast_metrics_details(
    run_id: int,
    db: Session = Depends(get_db),
) -> ForecastMetricsDetailsResponse:
    return ForecastService.get_forecast_metrics_details(db=db, run_id=run_id)
