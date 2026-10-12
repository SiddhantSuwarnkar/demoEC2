"""StockSense - Anomaly Review API Endpoints.

Implements Phase 4 REST API endpoints for:
- Triggering deterministic anomaly detection.
- Listing anomaly candidates with filtering and pagination.
- Human review workflows: confirm classification, dismiss false positives, record notes.
- Summary metrics for dashboard views.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.product import Product
from app.schemas.anomaly import (
    AnomalyDetectionResponse,
    AnomalyDetectorConfigSchema,
    AnomalyListResponse,
    AnomalyReviewDetailRead,
    AnomalyReviewRead,
    AnomalyReviewUpdateRequest,
    AnomalySummaryResponse,
)
from app.services.anomaly_service import AnomalyService

router = APIRouter(tags=["Anomalies"])


@router.post(
    "/products/{product_id}/anomalies/detect",
    response_model=AnomalyDetectionResponse,
    status_code=status.HTTP_200_OK,
    summary="Detect Demand Anomalies for Product",
    description=(
        "Executes the deterministic statistical anomaly detector (Median/MAD) on a product's historical demand. "
        "Flags candidate spikes, drops, promotion-correlated surges, and potential data errors. "
        "Persists candidates idempotently and preserves existing user review decisions."
    ),
)
def detect_product_anomalies(
    product_id: int,
    config: Optional[AnomalyDetectorConfigSchema] = None,
    db: Session = Depends(get_db),
) -> AnomalyDetectionResponse:
    try:
        det_result, candidates, new_created, existing_preserved = AnomalyService.detect_for_product(
            db=db, product_id=product_id, config=config
        )
        product = db.query(Product).filter(Product.id == product_id).first()
        sku = product.sku if product else f"ID-{product_id}"

        return AnomalyDetectionResponse(
            product_id=product_id,
            sku=sku,
            total_records_analyzed=det_result.total_records_analyzed,
            detector_version=det_result.detector_version,
            detected_anomalies_count=len(candidates),
            new_candidates_created=new_created,
            existing_candidates_preserved=existing_preserved,
            candidates=candidates,
            notes=det_result.notes,
        )
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e).strip("'"),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get(
    "/products/{product_id}/anomalies",
    response_model=AnomalyListResponse,
    summary="List Anomaly Candidates for Product",
    description="Lists anomaly candidates for a specific product with filtering and pagination.",
)
def list_product_anomalies(
    product_id: int,
    review_status: Optional[str] = Query(
        None, description="Filter by review status: pending, reviewed, dismissed"
    ),
    classification: Optional[str] = Query(
        None, description="Filter by classification: promotion, data_error, unknown"
    ),
    suggested_classification: Optional[str] = Query(
        None,
        description="Filter by detector suggested classification: promotion, data_error, unknown",
    ),
    start_date: Optional[date] = Query(None, description="Start date filter"),
    end_date: Optional[date] = Query(None, description="End date filter"),
    skip: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(50, ge=1, le=100, description="Page limit"),
    db: Session = Depends(get_db),
) -> AnomalyListResponse:
    # Verify product exists
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found.",
        )

    try:
        items, total = AnomalyService.list_anomalies(
            db=db,
            product_id=product_id,
            review_status=review_status,
            classification=classification,
            suggested_classification=suggested_classification,
            start_date=start_date,
            end_date=end_date,
            skip=skip,
            limit=limit,
        )
        return AnomalyListResponse(items=items, total=total, skip=skip, limit=limit)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get(
    "/products/{product_id}/anomalies/summary",
    response_model=AnomalySummaryResponse,
    summary="Get Anomaly Summary for Product",
    description="Returns aggregate counts of pending, reviewed, and dismissed anomaly candidates for a product.",
)
def get_product_anomaly_summary(
    product_id: int,
    db: Session = Depends(get_db),
) -> AnomalySummaryResponse:
    try:
        return AnomalyService.get_anomaly_summary(db=db, product_id=product_id)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e).strip("'"),
        )


@router.get(
    "/anomalies",
    response_model=AnomalyListResponse,
    summary="List All Anomaly Reviews",
    description="Global overview list of anomaly reviews across all products with filtering and pagination.",
)
def list_all_anomalies(
    product_id: Optional[int] = Query(None, description="Filter by product ID"),
    sku: Optional[str] = Query(None, description="Filter by product SKU"),
    review_status: Optional[str] = Query(
        None, description="Filter by review status: pending, reviewed, dismissed"
    ),
    classification: Optional[str] = Query(
        None, description="Filter by classification: promotion, data_error, unknown"
    ),
    suggested_classification: Optional[str] = Query(
        None,
        description="Filter by detector suggested classification: promotion, data_error, unknown",
    ),
    start_date: Optional[date] = Query(None, description="Filter by start date"),
    end_date: Optional[date] = Query(None, description="Filter by end date"),
    skip: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(50, ge=1, le=100, description="Page limit"),
    db: Session = Depends(get_db),
) -> AnomalyListResponse:
    try:
        items, total = AnomalyService.list_anomalies(
            db=db,
            product_id=product_id,
            sku=sku,
            review_status=review_status,
            classification=classification,
            suggested_classification=suggested_classification,
            start_date=start_date,
            end_date=end_date,
            skip=skip,
            limit=limit,
        )
        return AnomalyListResponse(items=items, total=total, skip=skip, limit=limit)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get(
    "/anomalies/summary",
    response_model=AnomalySummaryResponse,
    summary="Get Global Anomaly Summary",
    description="Returns aggregate counts of anomalies across the entire catalog.",
)
def get_global_anomaly_summary(
    db: Session = Depends(get_db),
) -> AnomalySummaryResponse:
    return AnomalyService.get_anomaly_summary(db=db, product_id=None)


@router.get(
    "/anomalies/{anomaly_id}",
    response_model=AnomalyReviewDetailRead,
    summary="Get Anomaly Review Detail",
    description="Retrieves complete information for a single anomaly candidate including detector evidence.",
)
def get_anomaly_detail(
    anomaly_id: int,
    db: Session = Depends(get_db),
) -> AnomalyReviewDetailRead:
    try:
        return AnomalyService.get_anomaly_by_id(db=db, anomaly_id=anomaly_id)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e).strip("'"),
        )


@router.patch(
    "/anomalies/{anomaly_id}/review",
    response_model=AnomalyReviewRead,
    summary="Confirm or Update Anomaly Review",
    description="Submit user-confirmed classification, review notes, treatment, or status update.",
)
def review_anomaly(
    anomaly_id: int,
    update: AnomalyReviewUpdateRequest,
    db: Session = Depends(get_db),
) -> AnomalyReviewRead:
    try:
        return AnomalyService.update_anomaly_review(db=db, anomaly_id=anomaly_id, update=update)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e).strip("'"),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post(
    "/anomalies/{anomaly_id}/reviews",
    response_model=AnomalyReviewRead,
    summary="Submit Classification (Spec Alias)",
    description="Alias endpoint matching Backend Plan Section 9.3 for submitting classification decisions.",
)
def submit_anomaly_review_alias(
    anomaly_id: int,
    update: AnomalyReviewUpdateRequest,
    db: Session = Depends(get_db),
) -> AnomalyReviewRead:
    return review_anomaly(anomaly_id=anomaly_id, update=update, db=db)


@router.post(
    "/anomalies/{anomaly_id}/dismiss",
    response_model=AnomalyReviewRead,
    summary="Dismiss Anomaly Candidate",
    description="Dismisses an anomaly candidate as a false positive or acceptable normal observation.",
)
def dismiss_anomaly(
    anomaly_id: int,
    notes: Optional[str] = Query(None, description="Optional dismissal rationale"),
    reviewer: Optional[str] = Query(None, description="Optional reviewer name"),
    db: Session = Depends(get_db),
) -> AnomalyReviewRead:
    update = AnomalyReviewUpdateRequest(
        review_status="dismissed",
        reviewer_notes=notes or "Candidate dismissed as normal variance/false positive.",
        reviewer=reviewer,
    )
    try:
        return AnomalyService.update_anomaly_review(db=db, anomaly_id=anomaly_id, update=update)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e).strip("'"),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
