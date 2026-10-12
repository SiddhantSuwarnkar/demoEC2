import csv
import io
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.anomaly import AnomalyReview
from app.models.data_import import DataImport, ImportRowError
from app.models.demand import DemandRecord
from app.models.forecast import ForecastRun
from app.models.product import Product
from app.models.recommendation import Recommendation
from app.models.scenario import ScenarioRun
from app.schemas.import_job import (
    ImportConfirmResponse,
    ImportDetailResponse,
    ImportPreviewResponse,
    RowDiagnostic,
)

# Canonical field mappings for Products CSV
PRODUCT_REQUIRED_COLUMNS = ["sku", "product_name", "unit_of_measure", "on_hand_qty"]
PRODUCT_COLUMN_ALIASES = {
    "name": "product_name",
    "current_stock": "on_hand_qty",
    "moq": "minimum_order_qty",
    "average_lead_time_days": "lead_time_days",
}
PRODUCT_KNOWN_COLUMNS = {
    "sku",
    "product_name",
    "name",
    "category",
    "unit_of_measure",
    "on_hand_qty",
    "current_stock",
    "committed_qty",
    "unit_cost",
    "ordering_cost",
    "holding_cost_per_unit_year",
    "holding_cost_rate",
    "target_service_level",
    "minimum_order_qty",
    "moq",
    "pack_size",
    "lead_time_days",
    "average_lead_time_days",
    "lead_time_stddev_days",
}

# Canonical field mappings for Daily Demand CSV
DEMAND_REQUIRED_COLUMNS = ["sku", "date", "units_sold"]
DEMAND_COLUMN_ALIASES = {
    "quantity": "units_sold",
    "demand_qty": "units_sold",
}
DEMAND_KNOWN_COLUMNS = {
    "sku",
    "date",
    "units_sold",
    "quantity",
    "demand_qty",
    "stockout_flag",
    "promotion_flag",
    "notes",
}

# Canonical field mappings for Supplier Settings CSV
SUPPLIER_REQUIRED_COLUMNS = ["sku", "average_lead_time_days"]
SUPPLIER_COLUMN_ALIASES = {
    "lead_time_days": "average_lead_time_days",
}
SUPPLIER_KNOWN_COLUMNS = {
    "sku",
    "average_lead_time_days",
    "lead_time_days",
    "lead_time_stddev_days",
}


def parse_boolean_value(val: Any) -> Optional[bool]:
    """Parse string or boolean to strict boolean, or None if empty."""
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s == "":
        return None
    if s in {"true", "1", "t", "yes", "y"}:
        return True
    if s in {"false", "0", "f", "no", "n"}:
        return False
    raise ValueError(f"Invalid boolean value '{val}'. Expected true/false or 1/0.")


def parse_date_iso(val: str) -> date:
    """Parse strict ISO YYYY-MM-DD date."""
    clean = str(val).strip()
    # Enforce strict length YYYY-MM-DD
    if len(clean) != 10 or clean[4] != "-" or clean[7] != "-":
        raise ValueError(f"Date '{val}' must be formatted as YYYY-MM-DD.")
    return datetime.strptime(clean, "%Y-%m-%d").date()


class ImportService:
    @staticmethod
    def decode_csv_bytes(file_bytes: bytes, filename: str) -> str:
        """Decode CSV bytes handling BOM and size limit."""
        if len(file_bytes) > settings.MAX_UPLOAD_SIZE_BYTES:
            status_413 = getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413)
            raise HTTPException(
                status_code=status_413,
                detail={
                    "code": "FILE_TOO_LARGE",
                    "message": f"File size {len(file_bytes)} bytes exceeds maximum limit of {settings.MAX_UPLOAD_SIZE_BYTES} bytes.",
                },
            )
        try:
            # utf-8-sig automatically removes the UTF-8 BOM if present
            return file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                return file_bytes.decode("latin-1")
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_ENCODING",
                        "message": f"Could not decode CSV file '{filename}'. Expected UTF-8 encoding: {str(exc)}",
                    },
                )

    @classmethod
    def preview_products_csv(
        cls,
        csv_text: str,
        filename: str,
        db: Session,
    ) -> ImportPreviewResponse:
        """Parse, validate, and preview products CSV without committing business data."""
        import_id = str(uuid.uuid4())
        diagnostics: List[RowDiagnostic] = []
        accepted_records: List[Dict[str, Any]] = []

        # 1. Parse CSV structure
        lines = csv_text.splitlines()
        if not lines or not csv_text.strip():
            diag = RowDiagnostic(
                row_number=0,
                column_name=None,
                error_code="EMPTY_FILE",
                message="Uploaded CSV file is empty.",
                is_warning=False,
            )
            return cls._persist_and_return_preview(
                import_id=import_id,
                file_type="products",
                filename=filename,
                total_rows=0,
                accepted_records=[],
                diagnostics=[diag],
                rejected_rows=0,
                db=db,
            )

        try:
            reader = csv.reader(io.StringIO(csv_text))
            raw_headers = next(reader, None)
        except Exception as exc:
            diag = RowDiagnostic(
                row_number=0,
                column_name=None,
                error_code="MALFORMED_CSV",
                message=f"Failed to parse CSV header: {str(exc)}",
                is_warning=False,
            )
            return cls._persist_and_return_preview(
                import_id=import_id,
                file_type="products",
                filename=filename,
                total_rows=0,
                accepted_records=[],
                diagnostics=[diag],
                rejected_rows=0,
                db=db,
            )

        if not raw_headers:
            diag = RowDiagnostic(
                row_number=0,
                column_name=None,
                error_code="EMPTY_FILE",
                message="Uploaded CSV contains no header row.",
                is_warning=False,
            )
            return cls._persist_and_return_preview(
                import_id=import_id,
                file_type="products",
                filename=filename,
                total_rows=0,
                accepted_records=[],
                diagnostics=[diag],
                rejected_rows=0,
                db=db,
            )

        # Clean and alias headers
        headers = [h.strip() for h in raw_headers]
        normalized_headers = [PRODUCT_COLUMN_ALIASES.get(h, h) for h in headers]

        # Check required columns
        header_set = set(normalized_headers)
        missing_required = [col for col in PRODUCT_REQUIRED_COLUMNS if col not in header_set]
        for col in missing_required:
            diagnostics.append(
                RowDiagnostic(
                    row_number=0,
                    column_name=col,
                    error_code="MISSING_REQUIRED_COLUMN",
                    message=f"Required column '{col}' is missing from CSV header.",
                    is_warning=False,
                )
            )

        # Check unexpected columns
        for col in headers:
            norm_col = PRODUCT_COLUMN_ALIASES.get(col, col)
            if norm_col not in PRODUCT_KNOWN_COLUMNS:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=0,
                        column_name=col,
                        error_code="UNEXPECTED_COLUMN",
                        message=f"Unexpected column '{col}' found in CSV header. It will not be mapped.",
                        is_warning=True,
                    )
                )

        if missing_required:
            return cls._persist_and_return_preview(
                import_id=import_id,
                file_type="products",
                filename=filename,
                total_rows=0,
                accepted_records=[],
                diagnostics=diagnostics,
                rejected_rows=0,
                db=db,
            )

        # Query existing SKUs in DB
        db_skus: Set[str] = {sku for (sku,) in db.query(Product.sku).all()}
        seen_skus_in_file: Dict[str, int] = {}

        total_data_rows = 0
        rejected_rows_count = 0

        for row_idx, raw_row in enumerate(reader, start=1):
            total_data_rows += 1
            if total_data_rows > settings.MAX_IMPORT_ROWS:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name=None,
                        error_code="ROW_COUNT_LIMIT_EXCEEDED",
                        message=f"Import exceeds maximum allowed limit of {settings.MAX_IMPORT_ROWS} data rows.",
                        is_warning=False,
                    )
                )
                break

            # Handle blank lines
            if not raw_row or all(cell.strip() == "" for cell in raw_row):
                continue

            row_has_blocking_error = False

            if len(raw_row) != len(headers):
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name=None,
                        error_code="MALFORMED_ROW",
                        message=f"Row has {len(raw_row)} columns, expected {len(headers)}.",
                        is_warning=False,
                    )
                )
                rejected_rows_count += 1
                continue

            row_dict = {
                normalized_headers[i]: (raw_row[i].strip() if raw_row[i] is not None else "")
                for i in range(len(headers))
            }

            # 1. SKU validation
            sku = row_dict.get("sku", "")
            if not sku:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="sku",
                        error_code="MISSING_REQUIRED_FIELD",
                        message="SKU is required and cannot be empty.",
                        is_warning=False,
                    )
                )
                row_has_blocking_error = True
            elif sku in seen_skus_in_file:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="sku",
                        error_code="DUPLICATE_SKU_IN_FILE",
                        message=f"Duplicate SKU '{sku}' in file; already defined in row {seen_skus_in_file[sku]}.",
                        is_warning=False,
                    )
                )
                row_has_blocking_error = True
            elif sku in db_skus:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="sku",
                        error_code="SKU_ALREADY_EXISTS",
                        message=f"Product with SKU '{sku}' already exists in database; catalogue attributes will be updated on confirm.",
                        is_warning=True,
                    )
                )
                seen_skus_in_file[sku] = row_idx
            else:
                seen_skus_in_file[sku] = row_idx

            # 2. product_name
            name = row_dict.get("product_name", "")
            if not name:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="product_name",
                        error_code="MISSING_REQUIRED_FIELD",
                        message="product_name is required and cannot be empty.",
                        is_warning=False,
                    )
                )
                row_has_blocking_error = True

            # 3. unit_of_measure
            uom = row_dict.get("unit_of_measure", "")
            if not uom:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="unit_of_measure",
                        error_code="MISSING_REQUIRED_FIELD",
                        message="unit_of_measure is required and cannot be empty.",
                        is_warning=False,
                    )
                )
                row_has_blocking_error = True

            # 4. on_hand_qty
            on_hand_str = row_dict.get("on_hand_qty", "")
            on_hand_val: int = 0
            if on_hand_str == "":
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="on_hand_qty",
                        error_code="MISSING_REQUIRED_FIELD",
                        message="on_hand_qty is required.",
                        is_warning=False,
                    )
                )
                row_has_blocking_error = True
            else:
                try:
                    on_hand_float = float(on_hand_str)
                    if on_hand_float < 0:
                        raise ValueError("Quantity cannot be negative.")
                    on_hand_val = int(on_hand_float)
                except Exception:
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="on_hand_qty",
                            error_code="INVALID_NON_NEGATIVE_NUMBER",
                            message=f"on_hand_qty '{on_hand_str}' must be a non-negative number.",
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True

            # 5. committed_qty (optional, disclosed default 0)
            committed_str = row_dict.get("committed_qty", "")
            committed_val: int = 0
            if committed_str != "":
                try:
                    committed_float = float(committed_str)
                    if committed_float < 0:
                        raise ValueError("Committed quantity cannot be negative.")
                    committed_val = int(committed_float)
                except Exception:
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="committed_qty",
                            error_code="INVALID_NON_NEGATIVE_NUMBER",
                            message=f"committed_qty '{committed_str}' must be a non-negative number.",
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True
            else:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="committed_qty",
                        error_code="DEFAULT_COMMITTED_QTY_APPLIED",
                        message="committed_qty omitted; applying explicit disclosed assumption of 0.",
                        is_warning=True,
                    )
                )

            # 6. unit_cost (optional, NULL when omitted)
            unit_cost_str = row_dict.get("unit_cost", "")
            unit_cost_val: Optional[Decimal] = None
            if unit_cost_str != "":
                try:
                    dec = Decimal(unit_cost_str)
                    if dec < Decimal("0.00"):
                        raise ValueError("Unit cost cannot be negative.")
                    unit_cost_val = dec
                except (InvalidOperation, ValueError):
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="unit_cost",
                            error_code="INVALID_NON_NEGATIVE_NUMBER",
                            message=f"unit_cost '{unit_cost_str}' must be a non-negative decimal.",
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True

            # 7. ordering_cost (optional, NULL when omitted)
            ordering_cost_str = row_dict.get("ordering_cost", "")
            ordering_cost_val: Optional[Decimal] = None
            if ordering_cost_str != "":
                try:
                    dec = Decimal(ordering_cost_str)
                    if dec < Decimal("0.00"):
                        raise ValueError("Ordering cost cannot be negative.")
                    ordering_cost_val = dec
                except (InvalidOperation, ValueError):
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="ordering_cost",
                            error_code="INVALID_NON_NEGATIVE_NUMBER",
                            message=f"ordering_cost '{ordering_cost_str}' must be a non-negative decimal.",
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True

            # 8. holding_cost_per_unit_year (optional, NULL when omitted)
            holding_cost_str = row_dict.get("holding_cost_per_unit_year", "")
            holding_cost_val: Optional[Decimal] = None
            holding_rate_val: Optional[Decimal] = None
            if holding_cost_str != "":
                try:
                    dec = Decimal(holding_cost_str)
                    if dec < Decimal("0.00"):
                        raise ValueError("Holding cost cannot be negative.")
                    holding_cost_val = dec
                    if unit_cost_val is not None and unit_cost_val > Decimal("0.00"):
                        computed_rate = holding_cost_val / unit_cost_val
                        holding_rate_val = min(Decimal("1.0000"), round(computed_rate, 4))
                except (InvalidOperation, ValueError):
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="holding_cost_per_unit_year",
                            error_code="INVALID_NON_NEGATIVE_NUMBER",
                            message=f"holding_cost_per_unit_year '{holding_cost_str}' must be a non-negative decimal.",
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True

            # 9. target_service_level (optional, default 0.95 with visible warning)
            tsl_str = row_dict.get("target_service_level", "")
            tsl_val: float = settings.DEFAULT_SERVICE_LEVEL
            if tsl_str != "":
                try:
                    val = float(tsl_str)
                    if not (0.0 < val < 1.0):
                        raise ValueError("target_service_level must be between 0 and 1 exclusive.")
                    tsl_val = val
                except Exception:
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="target_service_level",
                            error_code="INVALID_SERVICE_LEVEL",
                            message=f"target_service_level '{tsl_str}' must be strictly between 0 and 1 exclusive (e.g. 0.95).",
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True
            else:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="target_service_level",
                        error_code="DEFAULT_SERVICE_LEVEL_APPLIED",
                        message=f"target_service_level omitted; applying documented default of {settings.DEFAULT_SERVICE_LEVEL}.",
                        is_warning=True,
                    )
                )

            # 10. minimum_order_qty (optional, NULL when omitted)
            moq_str = row_dict.get("minimum_order_qty", "")
            moq_val: Optional[int] = None
            if moq_str != "":
                try:
                    moq_float = float(moq_str)
                    if moq_float < 1:
                        raise ValueError("MOQ must be >= 1.")
                    moq_val = int(moq_float)
                except Exception:
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="minimum_order_qty",
                            error_code="INVALID_POSITIVE_INTEGER",
                            message=f"minimum_order_qty '{moq_str}' must be an integer >= 1.",
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True

            # 11. pack_size (optional, NULL when omitted)
            pack_str = row_dict.get("pack_size", "")
            pack_val: Optional[int] = None
            if pack_str != "":
                try:
                    pack_float = float(pack_str)
                    if pack_float < 1:
                        raise ValueError("pack_size must be >= 1.")
                    pack_val = int(pack_float)
                except Exception:
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="pack_size",
                            error_code="INVALID_POSITIVE_INTEGER",
                            message=f"pack_size '{pack_str}' must be an integer >= 1.",
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True

            # 12. lead_time_days (optional extension in products.csv, NULL when omitted)
            lead_days_str = row_dict.get("lead_time_days", "")
            lead_days_val: Optional[int] = None
            if lead_days_str != "":
                try:
                    ld_float = float(lead_days_str)
                    if ld_float < 1:
                        raise ValueError("lead_time_days must be >= 1.")
                    lead_days_val = int(ld_float)
                except Exception:
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="lead_time_days",
                            error_code="INVALID_POSITIVE_INTEGER",
                            message=f"lead_time_days '{lead_days_str}' must be an integer >= 1.",
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True

            # 13. lead_time_stddev_days (optional extension in products.csv, NULL when omitted)
            lead_std_str = row_dict.get("lead_time_stddev_days", "")
            lead_std_val: Optional[float] = None
            if lead_std_str != "":
                try:
                    ls_float = float(lead_std_str)
                    if ls_float < 0:
                        raise ValueError("lead_time_stddev_days cannot be negative.")
                    lead_std_val = ls_float
                except Exception:
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="lead_time_stddev_days",
                            error_code="INVALID_NON_NEGATIVE_NUMBER",
                            message=f"lead_time_stddev_days '{lead_std_str}' must be a non-negative number.",
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True

            # Category
            category_val = row_dict.get("category", "") or None

            if row_has_blocking_error:
                rejected_rows_count += 1
            else:
                accepted_records.append(
                    {
                        "sku": sku,
                        "product_name": name,
                        "category": category_val,
                        "unit_of_measure": uom,
                        "on_hand_qty": on_hand_val,
                        "committed_qty": committed_val,
                        "unit_cost": str(unit_cost_val) if unit_cost_val is not None else None,
                        "ordering_cost": str(ordering_cost_val)
                        if ordering_cost_val is not None
                        else None,
                        "holding_cost_rate": str(holding_rate_val)
                        if holding_rate_val is not None
                        else None,
                        "holding_cost_per_unit_year": str(holding_cost_val)
                        if holding_cost_val is not None
                        else None,
                        "target_service_level": tsl_val,
                        "minimum_order_qty": moq_val,
                        "pack_size": pack_val,
                        "lead_time_days": lead_days_val,
                        "lead_time_stddev_days": lead_std_val,
                    }
                )

        staged_new_skus = [s for s in seen_skus_in_file.keys() if s not in db_skus]
        return cls._persist_and_return_preview(
            import_id=import_id,
            file_type="products",
            filename=filename,
            total_rows=total_data_rows,
            accepted_records=accepted_records,
            diagnostics=diagnostics,
            rejected_rows=rejected_rows_count,
            db=db,
            extra_summary={"staged_new_skus": staged_new_skus},
        )

    @classmethod
    def preview_demand_csv(
        cls,
        csv_text: str,
        filename: str,
        db: Session,
    ) -> ImportPreviewResponse:
        """Parse, validate, and preview daily demand CSV without committing business data."""
        import_id = str(uuid.uuid4())
        diagnostics: List[RowDiagnostic] = []
        accepted_records: List[Dict[str, Any]] = []

        # 1. Parse CSV structure
        lines = csv_text.splitlines()
        if not lines or not csv_text.strip():
            diag = RowDiagnostic(
                row_number=0,
                column_name=None,
                error_code="EMPTY_FILE",
                message="Uploaded CSV file is empty.",
                is_warning=False,
            )
            return cls._persist_and_return_preview(
                import_id=import_id,
                file_type="demand",
                filename=filename,
                total_rows=0,
                accepted_records=[],
                diagnostics=[diag],
                rejected_rows=0,
                db=db,
            )

        try:
            reader = csv.reader(io.StringIO(csv_text))
            raw_headers = next(reader, None)
        except Exception as exc:
            diag = RowDiagnostic(
                row_number=0,
                column_name=None,
                error_code="MALFORMED_CSV",
                message=f"Failed to parse CSV header: {str(exc)}",
                is_warning=False,
            )
            return cls._persist_and_return_preview(
                import_id=import_id,
                file_type="demand",
                filename=filename,
                total_rows=0,
                accepted_records=[],
                diagnostics=[diag],
                rejected_rows=0,
                db=db,
            )

        if not raw_headers:
            diag = RowDiagnostic(
                row_number=0,
                column_name=None,
                error_code="EMPTY_FILE",
                message="Uploaded CSV contains no header row.",
                is_warning=False,
            )
            return cls._persist_and_return_preview(
                import_id=import_id,
                file_type="demand",
                filename=filename,
                total_rows=0,
                accepted_records=[],
                diagnostics=[diag],
                rejected_rows=0,
                db=db,
            )

        headers = [h.strip() for h in raw_headers]
        normalized_headers = [DEMAND_COLUMN_ALIASES.get(h, h) for h in headers]

        # Check required columns
        header_set = set(normalized_headers)
        missing_required = [col for col in DEMAND_REQUIRED_COLUMNS if col not in header_set]
        for col in missing_required:
            diagnostics.append(
                RowDiagnostic(
                    row_number=0,
                    column_name=col,
                    error_code="MISSING_REQUIRED_COLUMN",
                    message=f"Required column '{col}' is missing from CSV header.",
                    is_warning=False,
                )
            )

        # Check unexpected columns
        for col in headers:
            norm_col = DEMAND_COLUMN_ALIASES.get(col, col)
            if norm_col not in DEMAND_KNOWN_COLUMNS:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=0,
                        column_name=col,
                        error_code="UNEXPECTED_COLUMN",
                        message=f"Unexpected column '{col}' found in CSV header. It will not be mapped.",
                        is_warning=True,
                    )
                )

        if missing_required:
            return cls._persist_and_return_preview(
                import_id=import_id,
                file_type="demand",
                filename=filename,
                total_rows=0,
                accepted_records=[],
                diagnostics=diagnostics,
                rejected_rows=0,
                db=db,
            )

        # Query existing products and existing demand in DB
        db_products: Dict[str, int] = {
            sku: pid for (sku, pid) in db.query(Product.sku, Product.id).all()
        }
        # Also recognize SKUs from staged product imports pending confirmation
        staged_prod_imports = (
            db.query(DataImport)
            .filter(
                DataImport.file_type == "products",
                DataImport.status == "previewed",
                DataImport.can_confirm == True,
            )
            .all()
        )
        for spi in staged_prod_imports:
            if spi.staged_data:
                for srec in spi.staged_data:
                    staged_sku = srec.get("sku")
                    if staged_sku and staged_sku not in db_products:
                        db_products[staged_sku] = 0

        existing_demand_keys: Set[Tuple[str, str]] = {
            (sku, d.isoformat())
            for (sku, d) in db.query(Product.sku, DemandRecord.date)
            .join(Product, DemandRecord.product_id == Product.id)
            .all()
        }

        seen_demand_keys_in_file: Dict[Tuple[str, str], int] = {}
        total_data_rows = 0
        rejected_rows_count = 0

        for row_idx, raw_row in enumerate(reader, start=1):
            total_data_rows += 1
            if total_data_rows > settings.MAX_IMPORT_ROWS:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name=None,
                        error_code="ROW_COUNT_LIMIT_EXCEEDED",
                        message=f"Import exceeds maximum allowed limit of {settings.MAX_IMPORT_ROWS} data rows.",
                        is_warning=False,
                    )
                )
                break

            if not raw_row or all(cell.strip() == "" for cell in raw_row):
                continue

            row_has_blocking_error = False

            if len(raw_row) != len(headers):
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name=None,
                        error_code="MALFORMED_ROW",
                        message=f"Row has {len(raw_row)} columns, expected {len(headers)}.",
                        is_warning=False,
                    )
                )
                rejected_rows_count += 1
                continue

            row_dict = {
                normalized_headers[i]: (raw_row[i].strip() if raw_row[i] is not None else "")
                for i in range(len(headers))
            }

            # 1. SKU validation
            sku = row_dict.get("sku", "")
            if not sku:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="sku",
                        error_code="MISSING_REQUIRED_FIELD",
                        message="SKU is required and cannot be empty.",
                        is_warning=False,
                    )
                )
                row_has_blocking_error = True
            elif sku not in db_products:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="sku",
                        error_code="UNKNOWN_SKU",
                        message=f"SKU '{sku}' does not exist in the product catalog. Demand records cannot be ingested for unknown products.",
                        is_warning=False,
                    )
                )
                row_has_blocking_error = True

            # 2. Date validation
            date_str = row_dict.get("date", "")
            date_val: Optional[date] = None
            if not date_str:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="date",
                        error_code="MISSING_REQUIRED_FIELD",
                        message="date is required and cannot be empty.",
                        is_warning=False,
                    )
                )
                row_has_blocking_error = True
            else:
                try:
                    date_val = parse_date_iso(date_str)
                except Exception as exc:
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="date",
                            error_code="INVALID_DATE_FORMAT",
                            message=f"Invalid date '{date_str}': {str(exc)}",
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True

            # 3. Duplicate checks
            if sku and date_val:
                demand_key = (sku, date_val.isoformat())
                if demand_key in seen_demand_keys_in_file:
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="date",
                            error_code="DUPLICATE_DEMAND_IN_FILE",
                            message=f"Duplicate demand record for SKU '{sku}' on date '{date_val.isoformat()}' found in row {row_idx}; already present in row {seen_demand_keys_in_file[demand_key]}. Silent summing is prohibited.",
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True
                elif demand_key in existing_demand_keys:
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="date",
                            error_code="DEMAND_ALREADY_EXISTS",
                            message=f"Demand record for SKU '{sku}' on date '{date_val.isoformat()}' already exists in database; observation will be updated on confirm.",
                            is_warning=True,
                        )
                    )
                    seen_demand_keys_in_file[demand_key] = row_idx
                else:
                    seen_demand_keys_in_file[demand_key] = row_idx

            # 4. units_sold validation
            units_str = row_dict.get("units_sold", "")
            units_val: float = 0.0
            if units_str == "":
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="units_sold",
                        error_code="MISSING_REQUIRED_FIELD",
                        message="units_sold is required.",
                        is_warning=False,
                    )
                )
                row_has_blocking_error = True
            else:
                try:
                    units_val = float(units_str)
                    if units_val < 0.0:
                        diagnostics.append(
                            RowDiagnostic(
                                row_number=row_idx,
                                column_name="units_sold",
                                error_code="NEGATIVE_QUANTITY",
                                message=f"units_sold cannot be negative (got '{units_str}').",
                                is_warning=False,
                            )
                        )
                        row_has_blocking_error = True
                except Exception:
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="units_sold",
                            error_code="INVALID_NUMERIC",
                            message=f"units_sold '{units_str}' must be a valid number.",
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True

            # 5. stockout_flag validation
            stockout_str = row_dict.get("stockout_flag", "")
            stockout_val = False
            if stockout_str != "":
                try:
                    parsed_bool = parse_boolean_value(stockout_str)
                    stockout_val = parsed_bool if parsed_bool is not None else False
                except ValueError as exc:
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="stockout_flag",
                            error_code="INVALID_BOOLEAN",
                            message=str(exc),
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True
            else:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="stockout_flag",
                        error_code="DEFAULT_STOCKOUT_FLAG_APPLIED",
                        message="stockout_flag omitted; defaulted to false.",
                        is_warning=True,
                    )
                )

            # 6. promotion_flag validation
            promotion_str = row_dict.get("promotion_flag", "")
            promotion_val = False
            if promotion_str != "":
                try:
                    parsed_bool = parse_boolean_value(promotion_str)
                    promotion_val = parsed_bool if parsed_bool is not None else False
                except ValueError as exc:
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="promotion_flag",
                            error_code="INVALID_BOOLEAN",
                            message=str(exc),
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True
            else:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="promotion_flag",
                        error_code="DEFAULT_PROMOTION_FLAG_APPLIED",
                        message="promotion_flag omitted; defaulted to false.",
                        is_warning=True,
                    )
                )

            # 7. notes (optional)
            notes_val = row_dict.get("notes", "") or None

            if row_has_blocking_error:
                rejected_rows_count += 1
            else:
                accepted_records.append(
                    {
                        "sku": sku,
                        "date": date_val.isoformat() if date_val else "",
                        "units_sold": units_val,
                        "stockout_flag": stockout_val,
                        "promotion_flag": promotion_val,
                        "notes": notes_val,
                    }
                )

        return cls._persist_and_return_preview(
            import_id=import_id,
            file_type="demand",
            filename=filename,
            total_rows=total_data_rows,
            accepted_records=accepted_records,
            diagnostics=diagnostics,
            rejected_rows=rejected_rows_count,
            db=db,
        )

    @classmethod
    def preview_suppliers_csv(
        cls,
        csv_text: str,
        filename: str,
        db: Session,
    ) -> ImportPreviewResponse:
        """Parse, validate, and preview supplier lead-time CSV without committing business data."""
        import_id = str(uuid.uuid4())
        diagnostics: List[RowDiagnostic] = []
        accepted_records: List[Dict[str, Any]] = []

        lines = csv_text.splitlines()
        if not lines or not csv_text.strip():
            diag = RowDiagnostic(
                row_number=0,
                column_name=None,
                error_code="EMPTY_FILE",
                message="Uploaded CSV file is empty.",
                is_warning=False,
            )
            return cls._persist_and_return_preview(
                import_id=import_id,
                file_type="suppliers",
                filename=filename,
                total_rows=0,
                accepted_records=[],
                diagnostics=[diag],
                rejected_rows=0,
                db=db,
            )

        reader = csv.reader(io.StringIO(csv_text))
        raw_headers = next(reader, None)
        if not raw_headers:
            diag = RowDiagnostic(
                row_number=0,
                column_name=None,
                error_code="EMPTY_FILE",
                message="Uploaded CSV contains no header row.",
                is_warning=False,
            )
            return cls._persist_and_return_preview(
                import_id=import_id,
                file_type="suppliers",
                filename=filename,
                total_rows=0,
                accepted_records=[],
                diagnostics=[diag],
                rejected_rows=0,
                db=db,
            )

        headers = [h.strip() for h in raw_headers]
        normalized_headers = [SUPPLIER_COLUMN_ALIASES.get(h, h) for h in headers]

        header_set = set(normalized_headers)
        missing_required = [col for col in SUPPLIER_REQUIRED_COLUMNS if col not in header_set]
        for col in missing_required:
            diagnostics.append(
                RowDiagnostic(
                    row_number=0,
                    column_name=col,
                    error_code="MISSING_REQUIRED_COLUMN",
                    message=f"Required column '{col}' is missing from CSV header.",
                    is_warning=False,
                )
            )

        for col in headers:
            norm_col = SUPPLIER_COLUMN_ALIASES.get(col, col)
            if norm_col not in SUPPLIER_KNOWN_COLUMNS:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=0,
                        column_name=col,
                        error_code="UNEXPECTED_COLUMN",
                        message=f"Unexpected column '{col}' found in CSV header.",
                        is_warning=True,
                    )
                )

        if missing_required:
            return cls._persist_and_return_preview(
                import_id=import_id,
                file_type="suppliers",
                filename=filename,
                total_rows=0,
                accepted_records=[],
                diagnostics=diagnostics,
                rejected_rows=0,
                db=db,
            )

        db_products: Dict[str, int] = {
            sku: pid for (sku, pid) in db.query(Product.sku, Product.id).all()
        }
        # Also recognize SKUs from staged product imports pending confirmation
        staged_prod_imports = (
            db.query(DataImport)
            .filter(
                DataImport.file_type == "products",
                DataImport.status == "previewed",
                DataImport.can_confirm == True,
            )
            .all()
        )
        for spi in staged_prod_imports:
            if spi.staged_data:
                for srec in spi.staged_data:
                    staged_sku = srec.get("sku")
                    if staged_sku and staged_sku not in db_products:
                        db_products[staged_sku] = 0

        seen_skus_in_file: Dict[str, int] = {}
        total_data_rows = 0
        rejected_rows_count = 0

        for row_idx, raw_row in enumerate(reader, start=1):
            total_data_rows += 1
            if total_data_rows > settings.MAX_IMPORT_ROWS:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name=None,
                        error_code="ROW_COUNT_LIMIT_EXCEEDED",
                        message=f"Import exceeds maximum allowed limit of {settings.MAX_IMPORT_ROWS} data rows.",
                        is_warning=False,
                    )
                )
                break

            if not raw_row or all(cell.strip() == "" for cell in raw_row):
                continue

            row_has_blocking_error = False
            if len(raw_row) != len(headers):
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name=None,
                        error_code="MALFORMED_ROW",
                        message=f"Row has {len(raw_row)} columns, expected {len(headers)}.",
                        is_warning=False,
                    )
                )
                rejected_rows_count += 1
                continue

            row_dict = {
                normalized_headers[i]: (raw_row[i].strip() if raw_row[i] is not None else "")
                for i in range(len(headers))
            }

            sku = row_dict.get("sku", "")
            if not sku:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="sku",
                        error_code="MISSING_REQUIRED_FIELD",
                        message="SKU is required.",
                        is_warning=False,
                    )
                )
                row_has_blocking_error = True
            elif sku not in db_products:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="sku",
                        error_code="UNKNOWN_SKU",
                        message=f"SKU '{sku}' does not exist in the product catalog.",
                        is_warning=False,
                    )
                )
                row_has_blocking_error = True
            elif sku in seen_skus_in_file:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="sku",
                        error_code="DUPLICATE_SKU_IN_FILE",
                        message=f"Duplicate SKU '{sku}' found; already defined in row {seen_skus_in_file[sku]}.",
                        is_warning=False,
                    )
                )
                row_has_blocking_error = True
            else:
                seen_skus_in_file[sku] = row_idx

            lead_days_str = row_dict.get("average_lead_time_days", "")
            lead_days_val: int = 1
            if not lead_days_str:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="average_lead_time_days",
                        error_code="MISSING_REQUIRED_FIELD",
                        message="average_lead_time_days is required.",
                        is_warning=False,
                    )
                )
                row_has_blocking_error = True
            else:
                try:
                    ld_val = float(lead_days_str)
                    if ld_val < 1:
                        raise ValueError("Lead time must be >= 1.")
                    lead_days_val = int(ld_val)
                except Exception:
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="average_lead_time_days",
                            error_code="INVALID_POSITIVE_INTEGER",
                            message=f"average_lead_time_days '{lead_days_str}' must be >= 1.",
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True

            lead_std_str = row_dict.get("lead_time_stddev_days", "")
            lead_std_val: float = 0.0
            if lead_std_str != "":
                try:
                    ls_val = float(lead_std_str)
                    if ls_val < 0:
                        raise ValueError("Lead time stddev cannot be negative.")
                    lead_std_val = ls_val
                except Exception:
                    diagnostics.append(
                        RowDiagnostic(
                            row_number=row_idx,
                            column_name="lead_time_stddev_days",
                            error_code="INVALID_NON_NEGATIVE_NUMBER",
                            message=f"lead_time_stddev_days '{lead_std_str}' must be >= 0.",
                            is_warning=False,
                        )
                    )
                    row_has_blocking_error = True
            else:
                diagnostics.append(
                    RowDiagnostic(
                        row_number=row_idx,
                        column_name="lead_time_stddev_days",
                        error_code="DEFAULT_LEAD_TIME_STDDEV_APPLIED",
                        message="lead_time_stddev_days omitted; defaulted to 0.0 (deterministic lead time).",
                        is_warning=True,
                    )
                )

            if row_has_blocking_error:
                rejected_rows_count += 1
            else:
                accepted_records.append(
                    {
                        "sku": sku,
                        "lead_time_days": lead_days_val,
                        "lead_time_stddev_days": lead_std_val,
                    }
                )

        return cls._persist_and_return_preview(
            import_id=import_id,
            file_type="suppliers",
            filename=filename,
            total_rows=total_data_rows,
            accepted_records=accepted_records,
            diagnostics=diagnostics,
            rejected_rows=rejected_rows_count,
            db=db,
        )

    @classmethod
    def _persist_and_return_preview(
        cls,
        import_id: str,
        file_type: str,
        filename: str,
        total_rows: int,
        accepted_records: List[Dict[str, Any]],
        diagnostics: List[RowDiagnostic],
        rejected_rows: int,
        db: Session,
        extra_summary: Optional[Dict[str, Any]] = None,
    ) -> ImportPreviewResponse:
        """Persist import preview tracking records and format preview response."""
        errors = [d for d in diagnostics if not d.is_warning]
        warnings = [d for d in diagnostics if d.is_warning]

        can_confirm = len(errors) == 0 and len(accepted_records) > 0
        status_val = "previewed" if (can_confirm or len(accepted_records) > 0) else "rejected"

        summary_dict = {
            "has_blocking_errors": len(errors) > 0,
            "can_confirm": can_confirm,
            "error_count": len(errors),
            "warning_count": len(warnings),
        }
        if extra_summary:
            summary_dict.update(extra_summary)

        data_import = DataImport(
            id=import_id,
            file_type=file_type,
            filename=filename,
            status=status_val,
            total_rows=total_rows,
            accepted_rows=len(accepted_records) if can_confirm else 0,
            rejected_rows=rejected_rows
            if rejected_rows > 0
            else (total_rows if not can_confirm else 0),
            warning_rows=len(warnings),
            can_confirm=can_confirm,
            staged_data=accepted_records if can_confirm else [],
            summary=summary_dict,
        )
        db.add(data_import)
        db.flush()

        # Add row errors/warnings
        for diag in diagnostics:
            row_err = ImportRowError(
                import_id=import_id,
                row_number=diag.row_number,
                column_name=diag.column_name,
                error_code=diag.error_code,
                message=diag.message,
                is_warning=diag.is_warning,
            )
            db.add(row_err)

        db.commit()

        # Bounded preview of parsed records
        preview_sample = accepted_records[: settings.DEFAULT_PREVIEW_LIMIT]

        return ImportPreviewResponse(
            import_id=import_id,
            file_type=file_type,
            filename=filename,
            status=status_val,
            can_confirm=can_confirm,
            total_rows=total_rows,
            accepted_rows=len(accepted_records) if can_confirm else 0,
            rejected_rows=rejected_rows
            if rejected_rows > 0
            else (total_rows if not can_confirm else 0),
            warning_rows=len(warnings),
            preview_records=preview_sample,
            errors=errors,
            warnings=warnings,
            summary={
                "can_confirm": can_confirm,
                "preview_record_count": len(preview_sample),
                "total_accepted": len(accepted_records),
            },
        )

    @classmethod
    def reset_catalog_data(
        cls,
        db: Session,
        keep_import_ids: Optional[List[str]] = None,
        keep_staged_imports: bool = False,
    ) -> Dict[str, Any]:
        """
        Purges all existing products, demand records, and analytics runs
        to ensure a completely clean slate for newly imported datasets.
        """
        db.query(ScenarioRun).delete(synchronize_session=False)
        db.query(Recommendation).delete(synchronize_session=False)
        db.query(ForecastRun).delete(synchronize_session=False)
        db.query(AnomalyReview).delete(synchronize_session=False)
        db.query(DemandRecord).delete(synchronize_session=False)
        deleted_products = db.query(Product).delete(synchronize_session=False)

        preserve_ids = set(keep_import_ids or [])
        if keep_staged_imports:
            staged_ids = [
                d.id
                for d in db.query(DataImport.id)
                .filter(DataImport.status.in_(["previewed", "staged"]))
                .all()
            ]
            preserve_ids.update(staged_ids)

        if preserve_ids:
            db.query(ImportRowError).filter(
                ~ImportRowError.import_id.in_(list(preserve_ids))
            ).delete(synchronize_session=False)
            db.query(DataImport).filter(
                ~DataImport.id.in_(list(preserve_ids))
            ).delete(synchronize_session=False)
        else:
            db.query(ImportRowError).delete(synchronize_session=False)
            db.query(DataImport).delete(synchronize_session=False)

        db.commit()
        db.expire_all()
        return {
            "status": "success",
            "message": f"Successfully purged existing catalog ({deleted_products} products and all associated demand & analytics).",
            "deleted_products_count": deleted_products,
        }

    @classmethod
    def confirm_import(
        cls, import_id: str, db: Session, clear_existing: bool = False
    ) -> ImportConfirmResponse:
        """
        Revalidate and transactionally commit confirmed import records into the database.
        Safe against retries: if already confirmed, returns idempotent summary.
        Rolls back transaction on any failure without partial writes.
        """
        import_record = db.query(DataImport).filter(DataImport.id == import_id).first()
        if not import_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "IMPORT_NOT_FOUND",
                    "message": f"Import session '{import_id}' was not found.",
                },
            )

        # If clear_existing is requested on products import, purge old catalog first while preserving staged sessions
        if clear_existing and import_record.file_type == "products":
            cls.reset_catalog_data(db=db, keep_import_ids=[import_id], keep_staged_imports=True)
            import_record = db.query(DataImport).filter(DataImport.id == import_id).first()

        # Idempotent retry: if already confirmed, safely return existing confirmation
        if import_record.status == "confirmed":
            return ImportConfirmResponse(
                import_id=import_record.id,
                file_type=import_record.file_type,
                status="confirmed",
                persisted_rows=import_record.accepted_rows,
                confirmed_at=import_record.confirmed_at or datetime.now(timezone.utc),
                message="Import was already confirmed. Safe idempotent retry returned.",
            )

        # Cannot confirm if not allowed
        if not import_record.can_confirm or import_record.status != "previewed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "CANNOT_CONFIRM_IMPORT",
                    "message": f"Import '{import_id}' cannot be confirmed because status is '{import_record.status}' and can_confirm is {import_record.can_confirm}.",
                },
            )

        staged_rows = import_record.staged_data or []
        if not staged_rows:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "NO_STAGED_RECORDS",
                    "message": "No valid staged records found in import session to confirm.",
                },
            )

        # -------------------------------------------------------------
        # Revalidate against current database state before writing
        # -------------------------------------------------------------
        if import_record.file_type == "products":
            # Revalidate: if an SKU was new at preview time but created in database prior to confirmation, reject conflict
            staged_new_skus = (import_record.summary or {}).get("staged_new_skus", [])
            if staged_new_skus:
                conflicts = [
                    s[0]
                    for s in db.query(Product.sku).filter(Product.sku.in_(staged_new_skus)).all()
                ]
                if conflicts:
                    import_record.status = "failed"
                    db.commit()
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "REVALIDATION_FAILED",
                            "message": f"Revalidation failed: Product SKU(s) {conflicts} were created in database after preview.",
                        },
                    )
        elif import_record.file_type == "demand":
            staged_skus = list({r["sku"] for r in staged_rows})
            db_products = {
                p.sku: p.id
                for p in db.query(Product.sku, Product.id)
                .filter(Product.sku.in_(staged_skus))
                .all()
            }
            missing_skus = [s for s in staged_skus if s not in db_products]
            if missing_skus:
                import_record.status = "failed"
                db.commit()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "REVALIDATION_FAILED",
                        "message": f"Revalidation failed: Product SKU(s) {missing_skus} no longer exist in database.",
                    },
                )
        elif import_record.file_type == "suppliers":
            staged_skus = list({r["sku"] for r in staged_rows})
            db_products_map = {
                p.sku: p for p in db.query(Product).filter(Product.sku.in_(staged_skus)).all()
            }
            missing_skus = [s for s in staged_skus if s not in db_products_map]
            if missing_skus:
                import_record.status = "failed"
                db.commit()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "REVALIDATION_FAILED",
                        "message": f"Revalidation failed: Product SKU(s) {missing_skus} no longer exist in database.",
                    },
                )

        # -------------------------------------------------------------
        # Atomic Transactional Commit
        # -------------------------------------------------------------
        try:
            persisted_count = 0
            if import_record.file_type == "products":
                for r in staged_rows:
                    existing_prod = db.query(Product).filter(Product.sku == r["sku"]).first()
                    if existing_prod:
                        existing_prod.name = r["product_name"]
                        if r.get("category"):
                            existing_prod.category = r.get("category")
                        if r.get("unit_of_measure"):
                            existing_prod.unit_of_measure = r.get("unit_of_measure")
                        if r.get("on_hand_qty") is not None:
                            existing_prod.current_stock = r["on_hand_qty"]
                        if r.get("committed_qty") is not None:
                            existing_prod.committed_qty = r["committed_qty"]
                        if r.get("unit_cost") is not None:
                            existing_prod.unit_cost = Decimal(str(r["unit_cost"]))
                        if r.get("ordering_cost") is not None:
                            existing_prod.ordering_cost = Decimal(str(r["ordering_cost"]))
                        if r.get("holding_cost_rate") is not None:
                            existing_prod.holding_cost_rate = Decimal(str(r["holding_cost_rate"]))
                        if r.get("holding_cost_per_unit_year") is not None:
                            existing_prod.holding_cost_per_unit_year = Decimal(str(r["holding_cost_per_unit_year"]))
                        if r.get("target_service_level") is not None:
                            existing_prod.target_service_level = r["target_service_level"]
                        if r.get("minimum_order_qty") is not None:
                            existing_prod.minimum_order_qty = r["minimum_order_qty"]
                        if r.get("pack_size") is not None:
                            existing_prod.pack_size = r["pack_size"]
                        if r.get("lead_time_days") is not None:
                            existing_prod.lead_time_days = r["lead_time_days"]
                        if r.get("lead_time_stddev_days") is not None:
                            existing_prod.lead_time_stddev_days = r["lead_time_stddev_days"]
                        existing_prod.import_id = import_record.id
                        persisted_count += 1
                    else:
                        product = Product(
                            sku=r["sku"],
                            name=r["product_name"],
                            category=r.get("category"),
                            unit_of_measure=r.get("unit_of_measure", "piece"),
                            current_stock=r.get("on_hand_qty", 0),
                            committed_qty=r.get("committed_qty", 0),
                            unit_cost=Decimal(str(r["unit_cost"]))
                            if r.get("unit_cost") is not None
                            else None,
                            ordering_cost=Decimal(str(r["ordering_cost"]))
                            if r.get("ordering_cost") is not None
                            else None,
                            holding_cost_rate=Decimal(str(r["holding_cost_rate"]))
                            if r.get("holding_cost_rate") is not None
                            else None,
                            holding_cost_per_unit_year=Decimal(str(r["holding_cost_per_unit_year"]))
                            if r.get("holding_cost_per_unit_year") is not None
                            else None,
                            target_service_level=r.get(
                                "target_service_level", settings.DEFAULT_SERVICE_LEVEL
                            ),
                            minimum_order_qty=r.get("minimum_order_qty"),
                            pack_size=r.get("pack_size"),
                            lead_time_days=r.get("lead_time_days"),
                            lead_time_stddev_days=r.get("lead_time_stddev_days"),
                            import_id=import_record.id,
                        )
                        db.add(product)
                        persisted_count += 1

            elif import_record.file_type == "demand":
                db_products = {p.sku: p.id for p in db.query(Product.sku, Product.id).all()}
                for r in staged_rows:
                    p_id = db_products[r["sku"]]
                    d_date = date.fromisoformat(r["date"])
                    existing_demand = (
                        db.query(DemandRecord)
                        .filter(DemandRecord.product_id == p_id, DemandRecord.date == d_date)
                        .first()
                    )
                    if existing_demand:
                        existing_demand.quantity = float(r["units_sold"])
                        existing_demand.stockout_flag = bool(r.get("stockout_flag", False))
                        existing_demand.promotion_flag = bool(r.get("promotion_flag", False))
                        if r.get("notes") is not None:
                            existing_demand.notes = r.get("notes")
                        existing_demand.import_id = import_record.id
                        persisted_count += 1
                    else:
                        demand = DemandRecord(
                            product_id=p_id,
                            date=d_date,
                            quantity=float(r["units_sold"]),
                            stockout_flag=bool(r.get("stockout_flag", False)),
                            promotion_flag=bool(r.get("promotion_flag", False)),
                            notes=r.get("notes"),
                            import_id=import_record.id,
                        )
                        db.add(demand)
                        persisted_count += 1

            elif import_record.file_type == "suppliers":
                db_prods = {p.sku: p for p in db.query(Product).all()}
                for r in staged_rows:
                    prod = db_prods[r["sku"]]
                    prod.lead_time_days = r["lead_time_days"]
                    if r.get("lead_time_stddev_days") is not None:
                        prod.lead_time_stddev_days = r["lead_time_stddev_days"]
                    persisted_count += 1

            import_record.status = "confirmed"
            import_record.confirmed_at = datetime.now(timezone.utc)
            db.commit()

            return ImportConfirmResponse(
                import_id=import_record.id,
                file_type=import_record.file_type,
                status="confirmed",
                persisted_rows=persisted_count,
                confirmed_at=import_record.confirmed_at,
                message=f"Successfully persisted {persisted_count} records to database.",
            )

        except Exception as exc:
            db.rollback()
            try:
                import_record.status = "failed"
                db.commit()
            except Exception:
                pass
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "IMPORT_COMMIT_FAILED",
                    "message": f"Database transaction failed during import confirmation: {str(exc)}",
                },
            )

    @classmethod
    def get_import_details(cls, import_id: str, db: Session) -> ImportDetailResponse:
        """Retrieve durable import status, counts, and diagnostic details."""
        import_record = db.query(DataImport).filter(DataImport.id == import_id).first()
        if not import_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "IMPORT_NOT_FOUND",
                    "message": f"Import session '{import_id}' was not found.",
                },
            )

        row_errors = (
            db.query(ImportRowError)
            .filter(ImportRowError.import_id == import_id)
            .order_by(ImportRowError.row_number)
            .all()
        )

        errors = [RowDiagnostic.model_validate(e) for e in row_errors if not e.is_warning]
        warnings = [RowDiagnostic.model_validate(e) for e in row_errors if e.is_warning]
        staged = import_record.staged_data or []
        preview_records = staged[: settings.DEFAULT_PREVIEW_LIMIT] if staged else None

        return ImportDetailResponse(
            import_id=import_record.id,
            file_type=import_record.file_type,
            filename=import_record.filename,
            status=import_record.status,
            can_confirm=import_record.can_confirm,
            total_rows=import_record.total_rows,
            accepted_rows=import_record.accepted_rows,
            rejected_rows=import_record.rejected_rows,
            warning_rows=import_record.warning_rows,
            created_at=import_record.created_at,
            confirmed_at=import_record.confirmed_at,
            preview_records=preview_records,
            errors=errors,
            warnings=warnings,
            summary=import_record.summary,
        )
