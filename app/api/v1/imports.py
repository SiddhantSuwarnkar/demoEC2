from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.import_job import (
    ImportConfirmResponse,
    ImportDetailResponse,
    ImportPreviewResponse,
)
from app.services.import_service import ImportService

router = APIRouter(prefix="/imports", tags=["Imports"])


def _validate_csv_extension(filename: str) -> None:
    if not filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_FILE_EXTENSION",
                "message": f"File '{filename}' must have a .csv extension.",
            },
        )


@router.post(
    "/products/preview",
    response_model=ImportPreviewResponse,
    summary="Preview Products CSV",
    description="Upload and validate a products CSV file. Returns parsed preview, row errors, and warnings without writing business records.",
)
async def preview_products_csv(
    file: UploadFile = File(..., description="Products CSV file"),
    db: Session = Depends(get_db),
) -> ImportPreviewResponse:
    filename = file.filename or "products.csv"
    _validate_csv_extension(filename)
    raw_bytes = await file.read()
    csv_text = ImportService.decode_csv_bytes(raw_bytes, filename)
    return ImportService.preview_products_csv(csv_text=csv_text, filename=filename, db=db)


@router.post(
    "/demand/preview",
    response_model=ImportPreviewResponse,
    summary="Preview Daily Demand CSV",
    description="Upload and validate a daily demand CSV file. Returns parsed preview, row errors, and warnings without writing business records.",
)
async def preview_demand_csv(
    file: UploadFile = File(..., description="Daily Demand CSV file"),
    db: Session = Depends(get_db),
) -> ImportPreviewResponse:
    filename = file.filename or "daily_demand.csv"
    _validate_csv_extension(filename)
    raw_bytes = await file.read()
    csv_text = ImportService.decode_csv_bytes(raw_bytes, filename)
    return ImportService.preview_demand_csv(csv_text=csv_text, filename=filename, db=db)


@router.post(
    "/suppliers/preview",
    response_model=ImportPreviewResponse,
    summary="Preview Supplier Settings CSV",
    description="Upload and validate a supplier settings CSV file. Returns parsed preview without writing business records.",
)
async def preview_suppliers_csv(
    file: UploadFile = File(..., description="Supplier Settings CSV file"),
    db: Session = Depends(get_db),
) -> ImportPreviewResponse:
    filename = file.filename or "supplier_settings.csv"
    _validate_csv_extension(filename)
    raw_bytes = await file.read()
    csv_text = ImportService.decode_csv_bytes(raw_bytes, filename)
    return ImportService.preview_suppliers_csv(csv_text=csv_text, filename=filename, db=db)


@router.post(
    "/preview",
    response_model=ImportPreviewResponse,
    summary="General CSV Preview Endpoint",
    description="Upload and preview a CSV file with explicit file_type (products, demand, or suppliers).",
)
async def preview_generic_csv(
    file: UploadFile = File(..., description="CSV file to preview"),
    file_type: str = Query(
        "products",
        description="Type of CSV being uploaded: 'products', 'demand', or 'suppliers'",
    ),
    db: Session = Depends(get_db),
) -> ImportPreviewResponse:
    filename = file.filename or f"{file_type}.csv"
    _validate_csv_extension(filename)
    raw_bytes = await file.read()
    csv_text = ImportService.decode_csv_bytes(raw_bytes, filename)

    normalized_type = file_type.strip().lower()
    if normalized_type == "products":
        return ImportService.preview_products_csv(csv_text=csv_text, filename=filename, db=db)
    elif normalized_type == "demand":
        return ImportService.preview_demand_csv(csv_text=csv_text, filename=filename, db=db)
    elif normalized_type == "suppliers":
        return ImportService.preview_suppliers_csv(csv_text=csv_text, filename=filename, db=db)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_FILE_TYPE",
                "message": f"Unsupported file_type '{file_type}'. Supported types: 'products', 'demand', 'suppliers'.",
            },
        )


@router.post(
    "/{import_id}/confirm",
    response_model=ImportConfirmResponse,
    summary="Confirm CSV Import",
    description="Revalidate the import against current database state and transactionally persist records. Optionally purges previous catalog data.",
)
def confirm_import(
    import_id: str,
    clear_existing: bool = Query(
        False,
        description="Whether to purge previous catalog and demand data before persisting this import",
    ),
    db: Session = Depends(get_db),
) -> ImportConfirmResponse:
    return ImportService.confirm_import(
        import_id=import_id, db=db, clear_existing=clear_existing
    )


@router.post(
    "/reset",
    summary="Reset / Clear Catalog & Analytics Data",
    description="Purges all existing products, demand records, recommendations, and analytics so a new dataset can be analyzed in complete isolation.",
)
def reset_database_data(
    db: Session = Depends(get_db),
) -> dict:
    return ImportService.reset_catalog_data(db=db)


@router.get(
    "/{import_id}",
    response_model=ImportDetailResponse,
    summary="Get Import Details",
    description="Retrieve durable import status, summary counts, diagnostics, and preview records.",
)
def get_import_details(
    import_id: str,
    db: Session = Depends(get_db),
) -> ImportDetailResponse:
    return ImportService.get_import_details(import_id=import_id, db=db)
