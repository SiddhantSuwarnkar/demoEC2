from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ProductBase(BaseModel):
    sku: str = Field(..., max_length=64, description="Stock Keeping Unit identifier")
    name: str = Field(..., max_length=255, description="Product display name")
    category: Optional[str] = Field(None, max_length=100)
    unit_of_measure: str = Field(default="piece", max_length=32)
    current_stock: int = Field(default=0, ge=0)
    committed_qty: int = Field(default=0, ge=0)
    safety_stock: int = Field(default=0, ge=0)
    reorder_point: int = Field(default=0, ge=0)
    lead_time_days: Optional[int] = Field(default=None, ge=1)
    lead_time_stddev_days: Optional[float] = Field(default=None, ge=0)
    unit_cost: Optional[Decimal] = Field(default=None, ge=0)
    ordering_cost: Optional[Decimal] = Field(None, ge=0)
    holding_cost_rate: Optional[Decimal] = Field(default=None, ge=0)
    holding_cost_per_unit_year: Optional[Decimal] = Field(default=None, ge=0)
    stockout_cost: Optional[Decimal] = Field(None, ge=0)
    target_service_level: Optional[float] = Field(default=0.95, gt=0, lt=1)
    minimum_order_qty: Optional[int] = Field(default=None, ge=1)
    pack_size: Optional[int] = Field(default=None, ge=1)
    is_active: bool = True


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    sku: Optional[str] = Field(None, max_length=64)
    name: Optional[str] = Field(None, max_length=255)
    category: Optional[str] = Field(None, max_length=100)
    unit_of_measure: Optional[str] = Field(None, max_length=32)
    current_stock: Optional[int] = Field(None, ge=0)
    committed_qty: Optional[int] = Field(None, ge=0)
    safety_stock: Optional[int] = Field(None, ge=0)
    reorder_point: Optional[int] = Field(None, ge=0)
    lead_time_days: Optional[int] = Field(None, ge=1)
    lead_time_stddev_days: Optional[float] = Field(None, ge=0)
    unit_cost: Optional[Decimal] = Field(None, ge=0)
    ordering_cost: Optional[Decimal] = Field(None, ge=0)
    holding_cost_rate: Optional[Decimal] = Field(None, ge=0)
    holding_cost_per_unit_year: Optional[Decimal] = Field(None, ge=0)
    stockout_cost: Optional[Decimal] = Field(None, ge=0)
    target_service_level: Optional[float] = Field(None, gt=0, lt=1)
    minimum_order_qty: Optional[int] = Field(None, ge=1)
    pack_size: Optional[int] = Field(None, ge=1)
    is_active: Optional[bool] = None



class ProductRead(ProductBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime

    @computed_field
    def product_name(self) -> str:
        return self.name

    @computed_field
    def on_hand_qty(self) -> int:
        return self.current_stock

    @computed_field
    def average_lead_time_days(self) -> Optional[int]:
        return self.lead_time_days
