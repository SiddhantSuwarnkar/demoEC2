import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.scenario import (
    ScenarioComparisonRequest,
    ScenarioComparisonResponse,
    ScenarioListResponse,
    ScenarioRunRead,
    ScenarioRunRequest,
)
from app.services.scenario_service import ScenarioService

logger = logging.getLogger("stocksense.scenarios.api")

router = APIRouter(tags=["What-If Simulator"])


@router.post(
    "/products/{product_id}/scenarios",
    response_model=ScenarioRunRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create & Execute What-If Scenario",
    description=(
        "Simulates inventory replenishment dynamics under user-defined stress conditions "
        "(demand surge percentage and/or additional supplier delay in calendar days). "
        "Evaluates daily inventory flow, stockouts, replenishment events, and costs. "
        "Persists an immutable scenario run snapshot for decision support. "
        "Never alters actual physical stock or creates real purchase orders."
    ),
)
def create_scenario_run(
    product_id: int,
    request: ScenarioRunRequest,
    db: Session = Depends(get_db),
) -> ScenarioRunRead:
    return ScenarioService.run_scenario_for_product(
        db=db,
        product_id=product_id,
        request=request,
    )


@router.get(
    "/scenarios",
    response_model=ScenarioListResponse,
    summary="List Scenario Simulation Runs",
    description="Returns a paginated list of historical scenario simulation runs across all products, or filtered by product_id.",
)
def list_scenarios(
    product_id: Optional[int] = Query(default=None, description="Optional product ID filter"),
    page: int = Query(default=1, ge=1, description="Page number"),
    limit: int = Query(default=20, ge=1, le=100, description="Page size limit"),
    db: Session = Depends(get_db),
) -> ScenarioListResponse:
    return ScenarioService.list_scenarios(
        db=db,
        product_id=product_id,
        page=page,
        limit=limit,
    )


@router.get(
    "/scenarios/{scenario_id}",
    response_model=ScenarioRunRead,
    summary="Retrieve Persisted Scenario Run",
    description="Retrieves a persisted scenario run snapshot including parameters, baseline metrics, stressed metrics, daily timeline, and replenishment events.",
)
def get_scenario_run(
    scenario_id: int,
    db: Session = Depends(get_db),
) -> ScenarioRunRead:
    return ScenarioService.get_scenario(db=db, scenario_id=scenario_id)


@router.get(
    "/products/{product_id}/scenarios",
    response_model=ScenarioListResponse,
    summary="List Scenarios for Product",
    description="Returns a paginated list of historical scenario simulation runs for a specified product.",
)
def list_scenarios_for_product(
    product_id: int,
    page: int = Query(default=1, ge=1, description="Page number"),
    limit: int = Query(default=20, ge=1, le=100, description="Page size limit"),
    db: Session = Depends(get_db),
) -> ScenarioListResponse:
    return ScenarioService.list_scenarios_for_product(
        db=db,
        product_id=product_id,
        page=page,
        limit=limit,
    )


@router.post(
    "/products/{product_id}/scenarios/compare",
    response_model=ScenarioComparisonResponse,
    status_code=status.HTTP_200_OK,
    summary="Compare What-If Scenarios vs. Baseline",
    description=(
        "Executes a multi-scenario comparison evaluating a baseline policy (0% surge, 0d delay) "
        "against one or more stressed operating scenarios over the same horizon and demand profile. "
        "Calculates delta unmet demand, delta stockout days, and delta inventory costs."
    ),
)
def compare_scenarios(
    product_id: int,
    request: ScenarioComparisonRequest,
    db: Session = Depends(get_db),
) -> ScenarioComparisonResponse:
    return ScenarioService.compare_scenarios(
        db=db,
        product_id=product_id,
        request=request,
    )
