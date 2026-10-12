"""StockSense - Anomaly Service.

Database-backed operations for:
- Deterministic anomaly detection and idempotent persistence.
- Filtering and paginated listing of anomaly candidates.
- Reviewer workflows (confirming classifications, dismissing candidates, notes).
- Summary statistics for dashboard insights.
- Immutable data guarantees: Raw demand records and CSV source data are NEVER mutated.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.anomaly import AnomalyReview
from app.models.demand import DemandRecord
from app.models.product import Product
from app.schemas.anomaly import (
    AnomalyDetectorConfigSchema,
    AnomalyReviewDetailRead,
    AnomalyReviewRead,
    AnomalyReviewUpdateRequest,
    AnomalySummaryResponse,
)
from app.services.anomaly_detector import (
    AnomalyDetectorConfig,
    DetectionResult,
    detect_anomalies,
)


class AnomalyService:
    """Service handling anomaly detection persistence, review updates, and queries."""

    @classmethod
    def _map_to_schema(
        cls,
        review: AnomalyReview,
        demand: DemandRecord,
        product: Product,
    ) -> AnomalyReviewRead:
        """Map ORM entities to AnomalyReviewRead schema."""
        user_confirmed = review.classification if review.review_status == "reviewed" else None
        return AnomalyReviewRead(
            id=review.id,
            demand_record_id=review.demand_record_id or demand.id,
            product_id=review.product_id or product.id,
            sku=product.sku,
            product_name=product.name,
            observation_date=demand.date,
            original_quantity=float(review.original_quantity),
            adjusted_quantity=(
                float(review.adjusted_quantity) if review.adjusted_quantity is not None else None
            ),
            detector_version=review.detector_version,
            anomaly_type=review.anomaly_type,
            anomaly_score=(
                float(review.anomaly_score) if review.anomaly_score is not None else None
            ),
            affected_start_date=review.affected_start_date or demand.date,
            affected_end_date=review.affected_end_date or demand.date,
            detector_explanation=review.detector_explanation,
            suggested_classification=review.ai_suggestion or "unknown",
            user_confirmed_classification=user_confirmed,
            review_status=review.review_status,
            classification=review.classification,
            user_treatment=review.user_treatment,
            reviewer_notes=review.reviewer_notes,
            stockout_flag=bool(demand.stockout_flag),
            promotion_flag=bool(demand.promotion_flag),
            reviewed_at=review.reviewed_at,
            created_at=review.created_at,
        )

    @classmethod
    def detect_for_product(
        cls,
        db: Session,
        product_id: int,
        config: Optional[AnomalyDetectorConfigSchema] = None,
    ) -> Tuple[DetectionResult, List[AnomalyReviewRead], int, int]:
        """Run anomaly detection for a product and persist candidates idempotently.

        Guarantees:
        - Deterministic output.
        - Idempotent rerun: does not duplicate candidates for the same observation.
        - Preserves user-confirmed classifications and notes across reruns.
        - Never mutates historical demand records or source quantities.
        """
        product = db.query(Product).filter(Product.id == product_id).first()
        if not product:
            raise KeyError(f"Product with ID {product_id} not found.")

        # Retrieve demand observations chronologically
        demand_records = (
            db.query(DemandRecord)
            .filter(DemandRecord.product_id == product_id)
            .order_by(DemandRecord.date.asc())
            .all()
        )

        if not demand_records:
            raise ValueError(
                f"Product '{product.sku}' has no demand records for anomaly detection."
            )

        cfg = AnomalyDetectorConfig(
            threshold_multiplier=config.threshold_multiplier if config else 3.0,
            min_history_records=config.min_history_records if config else 5,
        )

        if len(demand_records) < cfg.min_history_records:
            raise ValueError(
                f"Insufficient demand history: product '{product.sku}' has {len(demand_records)} records, "
                f"minimum required is {cfg.min_history_records} for statistical anomaly detection."
            )

        # Run pure detector
        det_result = detect_anomalies(demand_records, cfg)

        # Query existing reviews for this product
        existing_reviews = (
            db.query(AnomalyReview).filter(AnomalyReview.product_id == product_id).all()
        )
        existing_by_record_id: Dict[int, AnomalyReview] = {
            r.demand_record_id: r for r in existing_reviews if r.demand_record_id is not None
        }

        demand_by_id: Dict[int, DemandRecord] = {d.id: d for d in demand_records}

        new_created = 0
        existing_preserved = 0
        active_reviews: List[AnomalyReview] = []

        for candidate in det_result.candidates:
            rec_id = candidate.demand_record_id
            demand_rec = demand_by_id.get(rec_id)
            if not demand_rec:
                continue

            existing = existing_by_record_id.get(rec_id)
            if existing is None:
                # Brand new anomaly review candidate
                new_review = AnomalyReview(
                    product_id=product.id,
                    demand_record_id=rec_id,
                    detector_version=candidate.to_dict().get(
                        "detector_version", cfg.detector_version
                    ),
                    anomaly_type=candidate.anomaly_type,
                    anomaly_score=candidate.score,
                    affected_start_date=candidate.observation_date,
                    affected_end_date=candidate.observation_date,
                    detector_explanation=candidate.explanation,
                    ai_suggestion=candidate.suggested_classification,
                    review_status="pending",
                    classification="unknown",
                    original_quantity=candidate.observed_quantity,
                    reviewed_at=datetime.now(timezone.utc),
                )
                db.add(new_review)
                demand_rec.is_anomaly = True
                active_reviews.append(new_review)
                new_created += 1
            else:
                # Existing candidate: idempotency and preservation rule
                existing_preserved += 1
                if existing.review_status == "pending":
                    # Pending review: refresh detector evidence
                    existing.detector_version = cfg.detector_version
                    existing.anomaly_type = candidate.anomaly_type
                    existing.anomaly_score = candidate.score
                    existing.detector_explanation = candidate.explanation
                    existing.ai_suggestion = candidate.suggested_classification
                    existing.classification = "unknown"
                else:
                    # User-confirmed ('reviewed' or 'dismissed'):
                    # PRESERVE user classification, review_status, and reviewer_notes!
                    existing.detector_version = cfg.detector_version
                    existing.anomaly_score = candidate.score
                    existing.detector_explanation = candidate.explanation
                    existing.ai_suggestion = candidate.suggested_classification

                demand_rec.is_anomaly = True
                active_reviews.append(existing)

        db.commit()

        # Refresh entities to ensure all IDs and relationships are populated
        for r in active_reviews:
            db.refresh(r)

        response_candidates = [
            cls._map_to_schema(
                r,
                demand_by_id[r.demand_record_id],
                product,
            )
            for r in active_reviews
            if r.demand_record_id in demand_by_id
        ]

        return det_result, response_candidates, new_created, existing_preserved

    @classmethod
    def list_anomalies(
        cls,
        db: Session,
        product_id: Optional[int] = None,
        sku: Optional[str] = None,
        review_status: Optional[str] = None,
        classification: Optional[str] = None,
        suggested_classification: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[AnomalyReviewRead], int]:
        """List anomaly reviews with optional filters and pagination."""
        if start_date and end_date and start_date > end_date:
            raise ValueError("start_date must be less than or equal to end_date.")

        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100.")
        if skip < 0:
            raise ValueError("skip must be non-negative.")

        query = (
            db.query(AnomalyReview, DemandRecord, Product)
            .join(DemandRecord, AnomalyReview.demand_record_id == DemandRecord.id)
            .join(Product, AnomalyReview.product_id == Product.id)
        )

        if product_id is not None:
            query = query.filter(AnomalyReview.product_id == product_id)
        if sku is not None:
            query = query.filter(Product.sku == sku)
        if review_status is not None:
            if review_status not in ("pending", "reviewed", "dismissed"):
                raise ValueError(f"Invalid review_status filter: '{review_status}'.")
            query = query.filter(AnomalyReview.review_status == review_status)
        if classification is not None:
            if classification not in ("promotion", "data_error", "unknown"):
                raise ValueError(f"Invalid classification filter: '{classification}'.")
            query = query.filter(AnomalyReview.classification == classification)
        if suggested_classification is not None:
            if suggested_classification not in ("promotion", "data_error", "unknown"):
                raise ValueError(
                    f"Invalid suggested_classification filter: '{suggested_classification}'."
                )
            query = query.filter(AnomalyReview.ai_suggestion == suggested_classification)
        if start_date is not None:
            query = query.filter(DemandRecord.date >= start_date)
        if end_date is not None:
            query = query.filter(DemandRecord.date <= end_date)

        total_count = query.count()

        results = (
            query.order_by(DemandRecord.date.desc(), AnomalyReview.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

        items = [cls._map_to_schema(review, demand, prod) for review, demand, prod in results]

        return items, total_count

    @classmethod
    def get_anomaly_by_id(cls, db: Session, anomaly_id: int) -> AnomalyReviewDetailRead:
        """Retrieve full details of a single anomaly review."""
        result = (
            db.query(AnomalyReview, DemandRecord, Product)
            .join(DemandRecord, AnomalyReview.demand_record_id == DemandRecord.id)
            .join(Product, AnomalyReview.product_id == Product.id)
            .filter(AnomalyReview.id == anomaly_id)
            .first()
        )

        if not result:
            raise KeyError(f"Anomaly review with ID {anomaly_id} not found.")

        review, demand, product = result
        base_schema = cls._map_to_schema(review, demand, product)

        # Parse baseline stats if available in detector explanation
        baseline_stats: Dict[str, Any] = {}
        if review.detector_explanation:
            score_match = re.search(r"score=([\d\.]+)", review.detector_explanation)
            bound_match = re.search(r"threshold \(([\d\.]+)", review.detector_explanation)
            if score_match:
                baseline_stats["score"] = float(score_match.group(1))
            if bound_match:
                baseline_stats["threshold"] = float(bound_match.group(1))

        return AnomalyReviewDetailRead(
            **base_schema.model_dump(),
            baseline_statistics=baseline_stats if baseline_stats else None,
        )

    @classmethod
    def update_anomaly_review(
        cls,
        db: Session,
        anomaly_id: int,
        update: AnomalyReviewUpdateRequest,
    ) -> AnomalyReviewRead:
        """Update or confirm an anomaly review classification/status.

        Enforces:
        - Valid classification ('promotion', 'data_error', 'unknown').
        - Valid review_status ('pending', 'reviewed', 'dismissed').
        - Non-negative adjusted_quantity.
        - Immutability of raw demand record.
        """
        result = (
            db.query(AnomalyReview, DemandRecord, Product)
            .join(DemandRecord, AnomalyReview.demand_record_id == DemandRecord.id)
            .join(Product, AnomalyReview.product_id == Product.id)
            .filter(AnomalyReview.id == anomaly_id)
            .first()
        )

        if not result:
            raise KeyError(f"Anomaly review with ID {anomaly_id} not found.")

        review, demand, product = result

        # Validate classification
        if update.classification is not None:
            if update.classification not in ("promotion", "data_error", "unknown"):
                raise ValueError(
                    f"Invalid classification '{update.classification}'. "
                    "Allowed values: 'promotion', 'data_error', 'unknown'."
                )
            review.classification = update.classification
            # Setting classification confirms the review unless explicitly marked otherwise
            if update.review_status is None and review.review_status == "pending":
                review.review_status = "reviewed"

        # Validate review status
        if update.review_status is not None:
            if update.review_status not in ("pending", "reviewed", "dismissed"):
                raise ValueError(
                    f"Invalid review_status '{update.review_status}'. "
                    "Allowed values: 'pending', 'reviewed', 'dismissed'."
                )
            review.review_status = update.review_status

        # Reviewer notes
        if update.reviewer_notes is not None:
            if update.reviewer:
                review.reviewer_notes = f"[{update.reviewer}] {update.reviewer_notes}"
            else:
                review.reviewer_notes = update.reviewer_notes

        # Treatment & adjusted quantity
        if update.user_treatment is not None:
            review.user_treatment = update.user_treatment

        if update.adjusted_quantity is not None:
            if update.adjusted_quantity < 0.0:
                raise ValueError("adjusted_quantity must be non-negative.")
            review.adjusted_quantity = update.adjusted_quantity

        review.reviewed_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(review)

        return cls._map_to_schema(review, demand, product)

    @classmethod
    def get_anomaly_summary(
        cls,
        db: Session,
        product_id: Optional[int] = None,
    ) -> AnomalySummaryResponse:
        """Compute summary counts across anomaly reviews."""
        sku: Optional[str] = None
        query = db.query(AnomalyReview)
        if product_id is not None:
            product = db.query(Product).filter(Product.id == product_id).first()
            if not product:
                raise KeyError(f"Product with ID {product_id} not found.")
            sku = product.sku
            query = query.filter(AnomalyReview.product_id == product_id)

        reviews = query.all()
        total = len(reviews)
        pending = sum(1 for r in reviews if r.review_status == "pending")
        reviewed = sum(1 for r in reviews if r.review_status == "reviewed")
        dismissed = sum(1 for r in reviews if r.review_status == "dismissed")

        by_classification: Dict[str, int] = {
            "promotion": sum(1 for r in reviews if r.classification == "promotion"),
            "data_error": sum(1 for r in reviews if r.classification == "data_error"),
            "unknown": sum(1 for r in reviews if r.classification == "unknown"),
        }

        by_suggested_classification: Dict[str, int] = {
            "promotion": sum(1 for r in reviews if r.ai_suggestion == "promotion"),
            "data_error": sum(1 for r in reviews if r.ai_suggestion == "data_error"),
            "unknown": sum(
                1 for r in reviews if (r.ai_suggestion == "unknown" or r.ai_suggestion is None)
            ),
        }

        by_type: Dict[str, int] = {}
        for r in reviews:
            atype = r.anomaly_type or "unknown"
            by_type[atype] = by_type.get(atype, 0) + 1

        return AnomalySummaryResponse(
            product_id=product_id,
            sku=sku,
            total_anomalies=total,
            pending_count=pending,
            reviewed_count=reviewed,
            dismissed_count=dismissed,
            by_classification=by_classification,
            by_suggested_classification=by_suggested_classification,
            by_type=by_type,
        )
