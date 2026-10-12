from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.product import Product
from app.models.recommendation import Recommendation
from app.schemas.recommendation import (
    RecommendationCalculateRequest,
    RecommendationListResponse,
    RecommendationRead,
    RecommendationStatusUpdateRequest,
)
from app.services.replenishment_calculator import (
    InsufficientDemandHistoryError,
    InvalidReplenishmentParameterError,
    MissingSupplierSettingsError,
)
from app.services.replenishment_service import ReplenishmentService

router = APIRouter(tags=["Recommendations"])


@router.post(
    "/products/{product_id}/recommendations/calculate",
    response_model=RecommendationRead,
    status_code=status.HTTP_200_OK,
    summary="Calculate Replenishment Recommendation",
    description=(
        "Executes the explainable deterministic replenishment engine for an eligible product. "
        "Calculates demand metrics, safety stock, reorder point, inventory position, EOQ, "
        "and order proposals with supplier constraints. Persists an immutable calculation snapshot."
    ),
)
def calculate_recommendation(
    product_id: int,
    request: Optional[RecommendationCalculateRequest] = None,
    db: Session = Depends(get_db),
) -> RecommendationRead:
    tsl = request.target_service_level if request else None
    lt = request.lead_time_days if request else None
    lt_std = request.lead_time_stddev_days if request else None

    try:
        rec, _ = ReplenishmentService.calculate_for_product(
            db=db,
            product_id=product_id,
            target_service_level_override=tsl,
            lead_time_days_override=lt,
            lead_time_stddev_days_override=lt_std,
        )
        return rec
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found.",
        )
    except (
        MissingSupplierSettingsError,
        InsufficientDemandHistoryError,
        InvalidReplenishmentParameterError,
    ) as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


@router.get(
    "/products/{product_id}/recommendations/latest",
    response_model=RecommendationRead,
    summary="Get Latest Product Recommendation",
    description="Retrieves the most recent replenishment recommendation snapshot for a product.",
)
def get_latest_recommendation(
    product_id: int,
    db: Session = Depends(get_db),
) -> RecommendationRead:
    prod = db.query(Product).filter(Product.id == product_id).first()
    if not prod:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found.",
        )

    rec = ReplenishmentService.get_latest_for_product(db, product_id)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No replenishment recommendations found for product ID {product_id}.",
        )
    return rec


@router.get(
    "/products/{product_id}/recommendations",
    response_model=List[RecommendationRead],
    summary="Get Product Recommendation History",
    description="Retrieves historical replenishment recommendation snapshots for a product.",
)
def get_product_recommendation_history(
    product_id: int,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> List[RecommendationRead]:
    prod = db.query(Product).filter(Product.id == product_id).first()
    if not prod:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found.",
        )

    return ReplenishmentService.list_for_product(db, product_id, limit=limit, offset=offset)


@router.get(
    "/recommendations",
    response_model=RecommendationListResponse,
    summary="List Replenishment Recommendations Overview",
    description="Retrieves recommendations across inventory items with filtering and pagination.",
)
def list_recommendations_overview(
    latest_only: bool = Query(
        True, description="Return only the most recent recommendation per product"
    ),
    urgency: Optional[Literal["low", "medium", "high", "critical"]] = Query(None),
    status_filter: Optional[Literal["pending", "approved", "rejected"]] = Query(
        None, alias="status"
    ),
    reorder_only: Optional[bool] = Query(
        None, description="Filter for recommended_order_quantity > 0"
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> RecommendationListResponse:
    items, total = ReplenishmentService.list_recommendations(
        db=db,
        latest_only=latest_only,
        urgency=urgency,
        status=status_filter,
        reorder_only=reorder_only,
        limit=limit,
        offset=offset,
    )
    return RecommendationListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/recommendations/{recommendation_id}",
    response_model=RecommendationRead,
    summary="Get Recommendation by ID",
    description="Retrieves a specific recommendation snapshot by primary key ID.",
)
def get_recommendation_by_id(
    recommendation_id: int,
    db: Session = Depends(get_db),
) -> RecommendationRead:
    rec = db.query(Recommendation).filter(Recommendation.id == recommendation_id).first()
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recommendation with ID {recommendation_id} not found.",
        )
    return rec


@router.patch(
    "/recommendations/{recommendation_id}/status",
    response_model=RecommendationRead,
    summary="Update Recommendation Status",
    description="Updates the review status of a recommendation snapshot (pending, approved, rejected).",
)
def update_recommendation_status(
    recommendation_id: int,
    request: RecommendationStatusUpdateRequest,
    db: Session = Depends(get_db),
) -> RecommendationRead:
    rec = db.query(Recommendation).filter(Recommendation.id == recommendation_id).first()
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recommendation with ID {recommendation_id} not found.",
        )

    rec.status = request.status
    db.commit()
    db.refresh(rec)
    return rec
