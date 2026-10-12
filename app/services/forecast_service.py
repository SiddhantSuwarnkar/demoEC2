import logging
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppException
from app.models.forecast import ForecastRun
from app.models.product import Product
from app.schemas.forecast import (
    ForecastBenchmarkRequest,
    ForecastMetricsDetailsResponse,
    ForecastRunListResponse,
    ForecastRunRead,
    ModelComparison,
    ModelMetrics,
    SeriesDiagnostics,
    SplitConfig,
)
from app.services.forecasting.baseline import evaluate_moving_average
from app.services.forecasting.data_prep import prepare_demand_series
from app.services.forecasting.metrics import compare_models
from app.services.forecasting.prophet_adapter import evaluate_prophet
from app.services.forecasting.split import perform_chronological_split

logger = logging.getLogger("stocksense.forecasting.service")


class ForecastRunNotFoundError(AppException):
    def __init__(self, run_id: int):
        super().__init__(
            message=f"Forecast run with ID {run_id} not found.",
            code="FORECAST_RUN_NOT_FOUND",
            status_code=404,
        )


class ForecastService:
    @staticmethod
    def run_benchmark_for_product(
        db: Session,
        product_id: int,
        request: ForecastBenchmarkRequest,
    ) -> ForecastRunRead:
        """Executes a reproducible chronological benchmark between Moving Average and Prophet."""
        min_total = request.min_train_size + request.min_test_size
        prepared = prepare_demand_series(db=db, product_id=product_id, min_observations=min_total)

        split = perform_chronological_split(
            observations=prepared.observations,
            test_size=request.test_size,
            test_days=request.test_days,
            min_train_size=request.min_train_size,
            min_test_size=request.min_test_size,
        )

        # Baseline evaluation
        baseline_metrics = evaluate_moving_average(
            train_observations=split.train_observations,
            holdout_observations=split.holdout_observations,
            window_size=request.window_size,
        )

        # Candidate Prophet evaluation
        candidate_metrics = evaluate_prophet(
            train_observations=split.train_observations,
            holdout_observations=split.holdout_observations,
            growth=request.prophet_growth,
            seasonality_mode=request.prophet_seasonality_mode,
        )

        # Comparison
        comparison = compare_models(
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            holdout_dates_match=True,
        )

        # Assumptions and limitations documentation
        assumptions: List[str] = [
            "Fixed-origin multi-step forecast: models were evaluated strictly without consuming actual holdout demand.",
            "Demand observations represent recorded transaction days; missing calendar days are not imputed as zero.",
            "Evaluation metrics reflect historical performance on the selected holdout period and do not guarantee future accuracy.",
        ]
        if prepared.diagnostics.data_quality_warnings:
            assumptions.extend(prepared.diagnostics.data_quality_warnings)

        # Determine run status
        if baseline_metrics.status == "completed" and candidate_metrics.status == "completed":
            overall_status = "completed"
            failure_reason = None
        elif baseline_metrics.status == "completed":
            overall_status = "partial"
            failure_reason = f"Candidate model failed: {candidate_metrics.failure_reason}"
        else:
            overall_status = "failed"
            failure_reason = (
                f"Baseline failed: {baseline_metrics.failure_reason or 'unknown failure'}"
            )

        # Candidate config bundle containing audit details and predictions
        candidate_payload = {
            **candidate_metrics.model_dump(mode="json"),
            "split_config": split.split_config.model_dump(mode="json"),
            "diagnostics": prepared.diagnostics.model_dump(mode="json"),
            "comparison": comparison.model_dump(mode="json"),
            "assumptions_and_limitations": assumptions,
            "benchmark_version": "1.0",
        }

        data_version = (
            f"obs_count={len(prepared.observations)};"
            f"first={prepared.diagnostics.first_date};last={prepared.diagnostics.last_date}"
        )

        forecast_run = ForecastRun(
            product_id=product_id,
            baseline_method="moving_average",
            candidate_method="prophet",
            training_start_date=split.training_start_date,
            training_end_date=split.training_end_date,
            holdout_start_date=split.holdout_start_date,
            holdout_end_date=split.holdout_end_date,
            baseline_config=baseline_metrics.model_dump(mode="json"),
            candidate_config=candidate_payload,
            mae_baseline=baseline_metrics.mae,
            mae_candidate=candidate_metrics.mae,
            wape_baseline=baseline_metrics.wape,
            wape_candidate=candidate_metrics.wape,
            data_version=data_version,
            status=overall_status,
            failure_reason=failure_reason,
        )

        try:
            db.add(forecast_run)
            db.commit()
            db.refresh(forecast_run)
        except Exception:
            db.rollback()
            raise

        return ForecastService._map_to_schema(
            run=forecast_run,
            product=prepared.product,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            split_config=split.split_config,
            diagnostics=prepared.diagnostics,
            comparison=comparison,
            assumptions=assumptions,
        )

    @staticmethod
    def get_forecast_run(db: Session, run_id: int) -> ForecastRunRead:
        """Retrieves an immutable persisted forecast run with complete benchmark snapshot."""
        run = db.execute(select(ForecastRun).where(ForecastRun.id == run_id)).scalar_one_or_none()
        if not run:
            raise ForecastRunNotFoundError(run_id)

        product = db.execute(select(Product).where(Product.id == run.product_id)).scalar_one()
        return ForecastService._map_from_db(run, product)

    @staticmethod
    def list_forecast_runs(
        db: Session,
        product_id: Optional[int] = None,
        page: int = 1,
        limit: int = 20,
    ) -> ForecastRunListResponse:
        """Lists paginated forecast runs across all products or for a specific product."""
        query = select(ForecastRun)
        count_query = select(func.count(ForecastRun.id))

        if product_id is not None:
            product = db.execute(
                select(Product).where(Product.id == product_id)
            ).scalar_one_or_none()
            if not product:
                raise AppException(
                    message=f"Product with ID {product_id} not found.",
                    code="PRODUCT_NOT_FOUND",
                    status_code=404,
                )
            query = query.where(ForecastRun.product_id == product_id)
            count_query = count_query.where(ForecastRun.product_id == product_id)

        total = db.scalar(count_query) or 0

        runs = (
            db.execute(
                query.order_by(ForecastRun.id.desc()).offset((page - 1) * limit).limit(limit)
            )
            .scalars()
            .all()
        )

        prod_ids = list({r.product_id for r in runs})
        products_map = {}
        if prod_ids:
            prods = db.execute(select(Product).where(Product.id.in_(prod_ids))).scalars().all()
            products_map = {p.id: p for p in prods}

        items = []
        for r in runs:
            p = products_map.get(r.product_id)
            if p:
                items.append(ForecastService._map_from_db(r, p))

        return ForecastRunListResponse(
            total=total,
            page=page,
            limit=limit,
            items=items,
        )

    @staticmethod
    def get_forecast_metrics_details(db: Session, run_id: int) -> ForecastMetricsDetailsResponse:
        """Returns detailed prediction points, metrics, and comparisons for inspection."""
        run = ForecastService.get_forecast_run(db, run_id)
        return ForecastMetricsDetailsResponse(
            run_id=run.id,
            product_id=run.product_id,
            sku=run.sku or "",
            status=run.status,
            baseline_metrics=run.baseline_metrics or ModelMetrics(method=run.baseline_method),
            candidate_metrics=run.candidate_metrics or ModelMetrics(method=run.candidate_method),
            comparison=run.comparison
            or ModelComparison(
                winning_model="none",
                comparison_valid=False,
                interpretation="Comparison details unavailable.",
            ),
        )

    @staticmethod
    def _map_from_db(run: ForecastRun, product: Product) -> ForecastRunRead:
        """Reconstructs the full benchmark response schema from persisted database columns and JSON."""
        cand_payload = run.candidate_config or {}
        base_payload = run.baseline_config or {}

        # Reconstruct components from JSON snapshot
        split_data = cand_payload.get("split_config")
        split_cfg = SplitConfig(**split_data) if split_data else None

        diag_data = cand_payload.get("diagnostics")
        diag_cfg = SeriesDiagnostics(**diag_data) if diag_data else None

        comp_data = cand_payload.get("comparison")
        comp_cfg = ModelComparison(**comp_data) if comp_data else None

        assumptions = cand_payload.get("assumptions_and_limitations", [])

        # Reconstruct base metrics
        base_metrics = ModelMetrics(**base_payload) if base_payload else None

        # Reconstruct candidate metrics from payload
        cand_metrics = None
        if cand_payload:
            cand_metrics = ModelMetrics(
                method=cand_payload.get("method", run.candidate_method),
                status=cand_payload.get("status", "completed"),
                mae=cand_payload.get("mae", run.mae_candidate),
                wape=cand_payload.get("wape", run.wape_candidate),
                wape_unavailable_reason=cand_payload.get("wape_unavailable_reason"),
                failure_reason=cand_payload.get("failure_reason"),
                config=cand_payload.get("config", {}),
                predictions=cand_payload.get("predictions", []),
            )

        return ForecastRunRead(
            id=run.id,
            product_id=run.product_id,
            sku=product.sku,
            product_name=product.product_name,
            baseline_method=run.baseline_method,
            candidate_method=run.candidate_method,
            training_start_date=run.training_start_date,
            training_end_date=run.training_end_date,
            holdout_start_date=run.holdout_start_date,
            holdout_end_date=run.holdout_end_date,
            mae_baseline=run.mae_baseline,
            mae_candidate=run.mae_candidate,
            wape_baseline=run.wape_baseline,
            wape_candidate=run.wape_candidate,
            data_version=run.data_version,
            status=run.status,
            failure_reason=run.failure_reason,
            created_at=run.created_at,
            benchmark_version=cand_payload.get("benchmark_version", "1.0"),
            split_config=split_cfg,
            diagnostics=diag_cfg,
            baseline_metrics=base_metrics,
            candidate_metrics=cand_metrics,
            comparison=comp_cfg,
            assumptions_and_limitations=assumptions,
        )

    @staticmethod
    def _map_to_schema(
        run: ForecastRun,
        product: Product,
        baseline_metrics: ModelMetrics,
        candidate_metrics: ModelMetrics,
        split_config: SplitConfig,
        diagnostics: SeriesDiagnostics,
        comparison: ModelComparison,
        assumptions: List[str],
    ) -> ForecastRunRead:
        return ForecastRunRead(
            id=run.id,
            product_id=run.product_id,
            sku=product.sku,
            product_name=product.product_name,
            baseline_method=run.baseline_method,
            candidate_method=run.candidate_method,
            training_start_date=run.training_start_date,
            training_end_date=run.training_end_date,
            holdout_start_date=run.holdout_start_date,
            holdout_end_date=run.holdout_end_date,
            mae_baseline=run.mae_baseline,
            mae_candidate=run.mae_candidate,
            wape_baseline=run.wape_baseline,
            wape_candidate=run.wape_candidate,
            data_version=run.data_version,
            status=run.status,
            failure_reason=run.failure_reason,
            created_at=run.created_at,
            benchmark_version="1.0",
            split_config=split_config,
            diagnostics=diagnostics,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            comparison=comparison,
            assumptions_and_limitations=assumptions,
        )
