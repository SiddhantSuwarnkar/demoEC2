"""StockSense - Anomaly Pydantic Schemas.

Schemas for:
- Anomaly review creation, updates, and reads.
- Statistical detector invocation and configuration.
- Paginated listing and summary metrics.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AnomalyDetectorConfigSchema(BaseModel):
    """Optional configuration for the anomaly detection run."""

    threshold_multiplier: float = Field(
        default=3.0,
        gt=0.0,
        le=20.0,
        description="Modified Z-score threshold for flagging outliers.",
    )
    min_history_records: int = Field(
        default=5,
        ge=2,
        le=1000,
        description="Minimum observations required before statistical detector runs.",
    )


class AnomalyReviewBase(BaseModel):
    demand_record_id: int
    classification: Literal["promotion", "data_error", "unknown"]
    reviewer_notes: Optional[str] = None
    original_quantity: float = Field(..., ge=0)
    adjusted_quantity: Optional[float] = Field(None, ge=0)


class AnomalyReviewCreate(AnomalyReviewBase):
    pass


class AnomalyReviewRead(AnomalyReviewBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: Optional[int] = None
    sku: Optional[str] = None
    product_name: Optional[str] = None
    observation_date: Optional[date] = None
    detector_version: Optional[str] = None
    anomaly_type: Optional[str] = None
    anomaly_score: Optional[float] = None
    affected_start_date: Optional[date] = None
    affected_end_date: Optional[date] = None
    detector_explanation: Optional[str] = None
    suggested_classification: Optional[str] = None
    user_confirmed_classification: Optional[str] = None
    review_status: str = "pending"
    user_treatment: Optional[str] = None
    stockout_flag: Optional[bool] = None
    promotion_flag: Optional[bool] = None
    reviewed_at: datetime
    created_at: datetime


class AnomalyReviewDetailRead(AnomalyReviewRead):
    """Detailed view including baseline statistics and context."""

    baseline_statistics: Optional[Dict[str, Any]] = None


class AnomalyDetectionResponse(BaseModel):
    """Response returned when triggering anomaly detection."""

    product_id: int
    sku: str
    total_records_analyzed: int
    detector_version: str
    detected_anomalies_count: int
    new_candidates_created: int
    existing_candidates_preserved: int
    candidates: List[AnomalyReviewRead]
    notes: List[str] = Field(default_factory=list)


class AnomalyReviewUpdateRequest(BaseModel):
    """Request payload to confirm classification or update status."""

    classification: Optional[Literal["promotion", "data_error", "unknown"]] = Field(
        None, description="User-confirmed classification."
    )
    review_status: Optional[Literal["pending", "reviewed", "dismissed"]] = Field(
        None, description="Updated review status."
    )
    reviewer_notes: Optional[str] = Field(
        None, description="Reviewer rationale or explanation notes."
    )
    reviewer: Optional[str] = Field(None, description="Reviewer username or identifier.")
    user_treatment: Optional[str] = Field(
        None, description="Action taken, e.g., 'keep', 'exclude', 'clamp'."
    )
    adjusted_quantity: Optional[float] = Field(
        None, ge=0.0, description="Optional adjusted quantity for downstream analysis."
    )


class AnomalySummaryResponse(BaseModel):
    """Concise anomaly metrics for dashboards and product overviews."""

    product_id: Optional[int] = None
    sku: Optional[str] = None
    total_anomalies: int
    pending_count: int
    reviewed_count: int
    dismissed_count: int
    by_classification: Dict[str, int]
    by_suggested_classification: Optional[Dict[str, int]] = None
    by_type: Dict[str, int]


class AnomalyListResponse(BaseModel):
    """Paginated list of anomaly candidates."""

    items: List[AnomalyReviewRead]
    total: int
    skip: int
    limit: int
