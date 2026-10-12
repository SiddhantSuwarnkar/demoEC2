from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field


class DemandRecordBase(BaseModel):
    product_id: int
    date: date
    quantity: float = Field(..., ge=0)
    stockout_flag: bool = False
    promotion_flag: bool = False
    is_anomaly: bool = False
    notes: Optional[str] = None


class DemandRecordCreate(DemandRecordBase):
    pass


class DemandRecordRead(DemandRecordBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime

    @computed_field
    def units_sold(self) -> float:
        return self.quantity
