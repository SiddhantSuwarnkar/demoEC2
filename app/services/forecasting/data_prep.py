from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppException
from app.models.anomaly import AnomalyReview
from app.models.demand import DemandRecord
from app.models.product import Product
from app.schemas.forecast import SeriesDiagnostics


class ProductNotFoundError(AppException):
    def __init__(self, product_id: int):
        super().__init__(
            message=f"Product with ID {product_id} not found.",
            code="PRODUCT_NOT_FOUND",
            status_code=404,
        )


class InsufficientDemandHistoryError(AppException):
    def __init__(self, message: str, details: Optional[List[dict]] = None):
        super().__init__(
            message=message,
            code="INSUFFICIENT_DEMAND_HISTORY",
            status_code=422,
        )


@dataclass(frozen=True)
class ObservationPoint:
    date: date
    quantity: float
    record_id: int
    is_promotion: bool
    is_stockout: bool
    has_anomaly_review: bool
    reviewed_classification: Optional[str] = None


@dataclass(frozen=True)
class PreparedSeries:
    product: Product
    observations: List[ObservationPoint]
    diagnostics: SeriesDiagnostics


def prepare_demand_series(
    db: Session,
    product_id: int,
    min_observations: int = 10,
) -> PreparedSeries:
    """Prepares and validates chronological demand series without modifying raw records.

    - Sorts chronologically.
    - Preserves explicit zeros.
    - Never imputes missing calendar dates.
    - Computes calendar gap diagnostics and quality warnings.
    - Traces anomaly review status without altering raw observations.
    """
    product = db.execute(select(Product).where(Product.id == product_id)).scalar_one_or_none()
    if not product:
        raise ProductNotFoundError(product_id)

    # Fetch raw demand records in strict ascending chronological order
    demand_records = (
        db.execute(
            select(DemandRecord)
            .where(DemandRecord.product_id == product_id)
            .order_by(DemandRecord.date.asc())
        )
        .scalars()
        .all()
    )

    total_obs = len(demand_records)
    if total_obs < min_observations:
        raise InsufficientDemandHistoryError(
            f"Product {product.sku} has only {total_obs} recorded demand observations. "
            f"At least {min_observations} observations are required for benchmark evaluation."
        )

    # Fetch anomaly reviews for this product to maintain audit traceability
    reviews = (
        db.execute(select(AnomalyReview).where(AnomalyReview.product_id == product_id))
        .scalars()
        .all()
    )
    reviews_by_rec_id = {r.demand_record_id: r for r in reviews if r.demand_record_id is not None}
    reviews_by_date = {
        r.affected_start_date: r for r in reviews if r.affected_start_date is not None
    }

    observations: List[ObservationPoint] = []
    zero_count = 0
    warnings: List[str] = []

    prev_date: Optional[date] = None
    max_gap = 0
    reviewed_anomaly_count = 0

    for rec in demand_records:
        qty = float(rec.quantity)
        if qty == 0.0:
            zero_count += 1

        rec_date = rec.date
        rev = reviews_by_rec_id.get(rec.id) or reviews_by_date.get(rec_date)
        has_rev = rev is not None
        rev_class = rev.classification if rev and rev.review_status == "reviewed" else None
        if has_rev:
            reviewed_anomaly_count += 1

        if prev_date is not None:
            gap = (rec_date - prev_date).days
            if gap > max_gap:
                max_gap = gap
        prev_date = rec_date

        observations.append(
            ObservationPoint(
                date=rec_date,
                quantity=qty,
                record_id=rec.id,
                is_promotion=bool(getattr(rec, "promotion_flag", False)),
                is_stockout=bool(getattr(rec, "stockout_flag", False)),
                has_anomaly_review=has_rev,
                reviewed_classification=rev_class,
            )
        )

    first_date = observations[0].date
    last_date = observations[-1].date
    calendar_span = (last_date - first_date).days + 1
    missing_dates = max(0, calendar_span - total_obs)

    if missing_dates > 0:
        warnings.append(
            f"Series has {missing_dates} missing calendar days between {first_date} and {last_date} "
            f"(max interval between records: {max_gap} days). Observations are evaluated on recorded dates only."
        )

    if zero_count > (total_obs * 0.5):
        warnings.append(
            f"High sparsity: {zero_count} of {total_obs} observations ({round(zero_count / total_obs * 100, 1)}%) "
            "have zero demand."
        )

    if reviewed_anomaly_count > 0:
        warnings.append(
            f"Series contains {reviewed_anomaly_count} reviewed anomaly candidates. "
            "Raw recorded demand is evaluated without modification as per default benchmark policy."
        )

    diagnostics = SeriesDiagnostics(
        total_observations=total_obs,
        first_date=first_date,
        last_date=last_date,
        calendar_span_days=calendar_span,
        missing_calendar_dates=missing_dates,
        max_calendar_gap_days=max_gap,
        zero_demand_count=zero_count,
        reviewed_anomalies_count=reviewed_anomaly_count,
        data_quality_warnings=warnings,
    )

    return PreparedSeries(
        product=product,
        observations=observations,
        diagnostics=diagnostics,
    )
