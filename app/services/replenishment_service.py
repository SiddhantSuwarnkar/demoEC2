"""StockSense - Replenishment Service.

This service connects database models (Product, DemandRecord, Recommendation)
with the pure Replenishment Calculation Engine.

Responsibilities:
- Retrieve product attributes and historical demand observations.
- Check eligibility and validate input completeness.
- Invoke pure calculation engine.
- Persist immutable recommendation snapshots for auditability.
- Provide retrieval endpoints for latest, historical, and overview recommendations.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Tuple

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.demand import DemandRecord
from app.models.product import Product
from app.models.recommendation import Recommendation
from app.services.replenishment_calculator import (
    DemandObservation,
    ReplenishmentCalculationResult,
    ReplenishmentInputs,
    calculate_replenishment,
)


class ReplenishmentService:
    """Service layer managing replenishment calculation workflow and database persistence."""

    @classmethod
    def calculate_for_product(
        cls,
        db: Session,
        product_id: int,
        target_service_level_override: Optional[float] = None,
        lead_time_days_override: Optional[int] = None,
        lead_time_stddev_days_override: Optional[float] = None,
    ) -> Tuple[Recommendation, ReplenishmentCalculationResult]:
        """Calculate and persist a new recommendation snapshot for a product.

        Raises:
            KeyError: if product does not exist.
            MissingSupplierSettingsError: if lead time is missing.
            InsufficientDemandHistoryError: if demand history is empty or < 2 records.
            InvalidReplenishmentParameterError: if parameters are mathematically invalid.
        """
        product = db.query(Product).filter(Product.id == product_id).first()
        if not product:
            raise KeyError(f"Product with ID {product_id} not found.")

        # Determine effective parameters (allowing explicit overrides if provided)
        effective_tsl = (
            target_service_level_override
            if target_service_level_override is not None
            else (
                float(product.target_service_level)
                if product.target_service_level is not None
                else 0.95
            )
        )

        effective_lead_days = (
            lead_time_days_override
            if lead_time_days_override is not None
            else product.lead_time_days
        )

        effective_lead_std = (
            lead_time_stddev_days_override
            if lead_time_stddev_days_override is not None
            else (
                float(product.lead_time_stddev_days)
                if product.lead_time_stddev_days is not None
                else None
            )
        )

        inputs = ReplenishmentInputs(
            product_id=product.id,
            sku=product.sku,
            product_name=product.name or product.sku,
            on_hand_qty=product.on_hand_qty or 0,
            committed_qty=product.committed_qty or 0,
            lead_time_days=effective_lead_days,
            lead_time_stddev_days=effective_lead_std,
            target_service_level=effective_tsl,
            unit_cost=product.unit_cost,
            ordering_cost=product.ordering_cost,
            holding_cost_per_unit_year=product.holding_cost_per_unit_year,
            holding_cost_rate=product.holding_cost_rate,
            minimum_order_qty=product.minimum_order_qty,
            pack_size=product.pack_size,
            formula_version="v1.0",
        )

        # Retrieve demand observations chronologically
        db_demands = (
            db.query(DemandRecord)
            .filter(DemandRecord.product_id == product_id)
            .order_by(DemandRecord.date.asc())
            .all()
        )

        observations = [
            DemandObservation(
                date=d.date,
                quantity=float(d.quantity),
                stockout_flag=bool(d.stockout_flag),
                promotion_flag=bool(d.promotion_flag),
            )
            for d in db_demands
        ]

        # Execute pure calculation
        calc_result = calculate_replenishment(inputs=inputs, demand_observations=observations)

        # Persist new recommendation snapshot
        recommendation = Recommendation(
            product_id=product.id,
            formula_version=calc_result.formula_version,
            input_snapshot=calc_result.input_snapshot,
            inventory_position=Decimal(str(calc_result.inventory_position)),
            mean_daily_demand=Decimal(str(calc_result.mean_daily_demand)),
            daily_demand_stddev=Decimal(str(calc_result.daily_demand_stddev)),
            average_lead_time_days=Decimal(str(calc_result.average_lead_time_days)),
            lead_time_stddev_days=Decimal(str(calc_result.lead_time_stddev_days)),
            target_service_level=Decimal(str(calc_result.target_service_level)),
            z_factor=Decimal(str(calc_result.z_factor)),
            expected_lead_time_demand=Decimal(str(calc_result.expected_lead_time_demand)),
            safety_stock=calc_result.safety_stock,
            reorder_point=calc_result.reorder_point,
            annual_demand=Decimal(str(calc_result.annual_demand)),
            raw_eoq=Decimal(str(calc_result.raw_eoq)) if calc_result.raw_eoq is not None else None,
            recommended_order_quantity=calc_result.recommended_order_quantity,
            current_stock=calc_result.on_hand_qty,
            urgency=calc_result.urgency,
            explanation=calc_result.explanation,
            assumptions=calc_result.assumptions,
            status="pending",
        )

        # Also update product cached replenishment metrics if helpful
        product.safety_stock = calc_result.safety_stock
        product.reorder_point = calc_result.reorder_point

        db.add(recommendation)
        db.commit()
        db.refresh(recommendation)

        return recommendation, calc_result

    @classmethod
    def get_latest_for_product(
        cls,
        db: Session,
        product_id: int,
    ) -> Optional[Recommendation]:
        """Retrieve the latest persisted recommendation snapshot for a given product."""
        return (
            db.query(Recommendation)
            .filter(Recommendation.product_id == product_id)
            .order_by(desc(Recommendation.calculated_at), desc(Recommendation.id))
            .first()
        )

    @classmethod
    def list_for_product(
        cls,
        db: Session,
        product_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Recommendation]:
        """Retrieve historical recommendation snapshots for a given product."""
        return (
            db.query(Recommendation)
            .filter(Recommendation.product_id == product_id)
            .order_by(desc(Recommendation.calculated_at), desc(Recommendation.id))
            .offset(offset)
            .limit(limit)
            .all()
        )

    @classmethod
    def list_recommendations(
        cls,
        db: Session,
        latest_only: bool = True,
        urgency: Optional[str] = None,
        status: Optional[str] = None,
        reorder_only: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Recommendation], int]:
        """List recommendations across products with optional filtering and pagination.

        If `latest_only` is True, returns only the most recent recommendation per product.
        """
        query = db.query(Recommendation)

        if urgency:
            query = query.filter(Recommendation.urgency == urgency)
        if status:
            query = query.filter(Recommendation.status == status)
        if reorder_only is True:
            query = query.filter(Recommendation.recommended_order_quantity > 0)
        elif reorder_only is False:
            query = query.filter(Recommendation.recommended_order_quantity == 0)

        if latest_only:
            # Subquery to find maximum ID per product
            from sqlalchemy import func, select

            subq = select(func.max(Recommendation.id)).group_by(Recommendation.product_id)
            query = query.filter(Recommendation.id.in_(subq))

        total_count = query.count()
        results = (
            query.order_by(desc(Recommendation.calculated_at), desc(Recommendation.id))
            .offset(offset)
            .limit(limit)
            .all()
        )

        return results, total_count
