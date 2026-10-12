from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class RowDiagnostic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_number: int = Field(..., description="1-indexed row number in the CSV data")
    column_name: Optional[str] = Field(
        None, description="Column name associated with the diagnostic"
    )
    error_code: str = Field(..., description="Machine-readable error/warning code")
    message: str = Field(..., description="Human-readable explanation")
    is_warning: bool = Field(
        default=False, description="Whether this diagnostic is a non-blocking warning"
    )


class ImportPreviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    import_id: str = Field(..., description="Unique server-controlled import session identifier")
    file_type: str = Field(..., description="Type of imported file: products, demand, or suppliers")
    filename: str = Field(..., description="Original uploaded filename")
    status: str = Field(..., description="Import state: previewed, rejected, or failed")
    can_confirm: bool = Field(
        ..., description="Indicates whether the import is valid and ready to confirm"
    )
    total_rows: int = Field(..., ge=0, description="Total data rows parsed from CSV")
    accepted_rows: int = Field(..., ge=0, description="Number of validated rows ready for import")
    rejected_rows: int = Field(
        ..., ge=0, description="Number of rows rejected due to validation errors"
    )
    warning_rows: int = Field(..., ge=0, description="Number of rows with non-blocking warnings")
    preview_records: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Bounded sample of parsed and accepted records",
    )
    errors: List[RowDiagnostic] = Field(
        default_factory=list,
        description="List of blocking validation errors",
    )
    warnings: List[RowDiagnostic] = Field(
        default_factory=list,
        description="List of non-blocking validation warnings",
    )
    summary: Optional[Dict[str, Any]] = Field(
        None, description="Additional contextual information and statistics"
    )


class ImportConfirmResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    import_id: str = Field(..., description="Unique import session identifier")
    file_type: str = Field(..., description="Type of imported file")
    status: str = Field(..., description="Final status: confirmed")
    persisted_rows: int = Field(
        ..., ge=0, description="Number of business records persisted to database"
    )
    confirmed_at: datetime = Field(..., description="Timestamp of confirmation")
    message: str = Field(..., description="Human-readable confirmation summary")


class ImportDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    import_id: str = Field(..., description="Unique import session identifier")
    file_type: str = Field(..., description="Type of imported file")
    filename: str = Field(..., description="Original filename")
    status: str = Field(..., description="Import status")
    can_confirm: bool = Field(..., description="Whether confirmation is allowed")
    total_rows: int = Field(..., ge=0)
    accepted_rows: int = Field(..., ge=0)
    rejected_rows: int = Field(..., ge=0)
    warning_rows: int = Field(..., ge=0)
    created_at: datetime
    confirmed_at: Optional[datetime] = None
    preview_records: Optional[List[Dict[str, Any]]] = None
    errors: List[RowDiagnostic] = Field(default_factory=list)
    warnings: List[RowDiagnostic] = Field(default_factory=list)
    summary: Optional[Dict[str, Any]] = None
