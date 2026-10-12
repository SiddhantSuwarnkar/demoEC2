from typing import List, Optional

from pydantic import BaseModel, Field


class ErrorDetailSchema(BaseModel):
    field: Optional[str] = None
    message: str


class ErrorBodySchema(BaseModel):
    code: str
    message: str
    details: Optional[List[ErrorDetailSchema]] = None


class StandardErrorResponse(BaseModel):
    error: ErrorBodySchema


class HealthLiveResponse(BaseModel):
    status: str = Field(default="ok", example="ok")
    service: str = Field(default="stocksense", example="stocksense")
    environment: str = Field(default="development", example="development")


class HealthReadyResponse(BaseModel):
    status: str = Field(default="ready", example="ready")
    database: str = Field(default="connected", example="connected")
