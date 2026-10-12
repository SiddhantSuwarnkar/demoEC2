from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class RecommendationBase(BaseModel):
    product_id: int
    recommended_order_quantity: int = Field(..., ge=0)
    current_stock: int
    safety_stock: int
    reorder_point: int
    urgency: Literal["low", "medium", "high", "critical"] = "medium"
    explanation: Optional[str] = None
    assumptions: Optional[Dict[str, Any]] = None
    status: Literal["pending", "approved", "rejected"] = "pending"


class RecommendationCreate(RecommendationBase):
    pass


class RecommendationCalculateRequest(BaseModel):
    """Optional overrides when requesting a replenishment calculation."""

    target_service_level: Optional[float] = Field(
        None,
        gt=0.0,
        lt=1.0,
        description="Optional override for target cycle service level (0.0 to 1.0 exclusive)",
    )
    lead_time_days: Optional[int] = Field(
        None,
        gt=0,
        description="Optional override for average supplier lead time in days (> 0)",
    )
    lead_time_stddev_days: Optional[float] = Field(
        None,
        ge=0.0,
        description="Optional override for supplier lead time standard deviation (>= 0)",
    )


class RecommendationStatusUpdateRequest(BaseModel):
    status: Literal["pending", "approved", "rejected"]


class RecommendationRead(RecommendationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    formula_version: str = "v1.0"
    calculated_at: Optional[datetime] = None
    input_snapshot: Optional[Dict[str, Any]] = None
    inventory_position: Optional[Decimal] = None
    mean_daily_demand: Optional[Decimal] = None
    daily_demand_stddev: Optional[Decimal] = None
    average_lead_time_days: Optional[Decimal] = None
    lead_time_stddev_days: Optional[Decimal] = None
    target_service_level: Optional[Decimal] = None
    z_factor: Optional[Decimal] = None
    expected_lead_time_demand: Optional[Decimal] = None
    annual_demand: Optional[Decimal] = None
    raw_eoq: Optional[Decimal] = None
    created_at: datetime


class RecommendationListResponse(BaseModel):
    items: List[RecommendationRead]
    total: int
    limit: int
    offset: int
