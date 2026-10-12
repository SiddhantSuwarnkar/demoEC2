from datetime import date, timedelta
from typing import List
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.anomaly import AnomalyReview
from app.models.demand import DemandRecord
from app.models.product import Product
from app.schemas.forecast import ForecastPredictionPoint
from app.services.forecasting.baseline import (
    InvalidBaselineConfigError,
    evaluate_moving_average,
)
from app.services.forecasting.data_prep import (
    InsufficientDemandHistoryError,
    ObservationPoint,
    ProductNotFoundError,
    prepare_demand_series,
)
from app.services.forecasting.metrics import calculate_forecast_metrics, compare_models
from app.services.forecasting.prophet_adapter import evaluate_prophet
from app.services.forecasting.split import (
    InvalidSplitConfigError,
    perform_chronological_split,
)


def _create_sample_product(db: Session, sku: str = "SKU-FC-001") -> Product:
    prod = Product(
        sku=sku,
        product_name=f"Forecast Test Product {sku}",
        unit_of_measure="piece",
        on_hand_qty=100,
        safety_stock=20,
        reorder_point=40,
        unit_cost=15.0,
        lead_time_days=7,
    )
    db.add(prod)
    db.commit()
    db.refresh(prod)
    return prod


def _create_demand_series(
    db: Session,
    product_id: int,
    start_date: date,
    quantities: List[int],
    step_days: int = 1,
) -> List[DemandRecord]:
    records = []
    current_date = start_date
    for qty in quantities:
        rec = DemandRecord(
            product_id=product_id,
            date=current_date,
            quantity=float(qty),
            promotion_flag=False,
            stockout_flag=False,
        )
        db.add(rec)
        records.append(rec)
        current_date += timedelta(days=step_days)
    db.commit()
    return records


# -----------------------------------------------------------------------------
# 1. Chronological Split Ordering, No Shuffling & Leakage Prevention
# -----------------------------------------------------------------------------


def test_chronological_split_ordering_and_no_shuffling():
    start = date(2026, 1, 1)
    obs = [
        ObservationPoint(
            date=start + timedelta(days=i),
            quantity=10.0 + i,
            record_id=i + 1,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
        for i in range(20)
    ]

    split = perform_chronological_split(
        observations=obs,
        test_size=0.25,
        min_train_size=7,
        min_test_size=3,
    )

    # 20 observations, 25% holdout = 5 observations holdout, 15 training
    assert len(split.train_observations) == 15
    assert len(split.holdout_observations) == 5

    # Check strict chronological ordering (no shuffling)
    for i in range(len(split.train_observations) - 1):
        assert split.train_observations[i].date < split.train_observations[i + 1].date

    for i in range(len(split.holdout_observations) - 1):
        assert split.holdout_observations[i].date < split.holdout_observations[i + 1].date

    # Training end must precede holdout start
    assert split.training_end_date < split.holdout_start_date
    assert split.train_observations[-1].date == date(2026, 1, 15)
    assert split.holdout_observations[0].date == date(2026, 1, 16)
    assert split.holdout_observations[-1].date == date(2026, 1, 20)


def test_no_leakage_from_holdout_into_fitting():
    """Verify that train_observations passed to models contain zero holdout records."""
    start = date(2026, 1, 1)
    obs = [
        ObservationPoint(
            date=start + timedelta(days=i),
            quantity=float(i * 10),
            record_id=i + 1,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
        for i in range(12)
    ]

    split = perform_chronological_split(obs, test_size=0.25, min_train_size=7, min_test_size=3)
    # Train: 9, Holdout: 3
    assert len(split.train_observations) == 9
    assert len(split.holdout_observations) == 3

    train_dates = {o.date for o in split.train_observations}
    holdout_dates = {o.date for o in split.holdout_observations}

    assert train_dates.isdisjoint(holdout_dates)
    assert max(train_dates) < min(holdout_dates)


# -----------------------------------------------------------------------------
# 2. Moving Average Baseline Tests
# -----------------------------------------------------------------------------


def test_moving_average_predictions_for_known_dataset():
    start = date(2026, 1, 1)
    # Train quantities: 10, 20, 30, 40, 50, 60, 70 (length 7). Window = 3 -> mean of (50, 60, 70) = 60.0
    train_obs = [
        ObservationPoint(
            date=start + timedelta(days=i),
            quantity=float((i + 1) * 10),
            record_id=i + 1,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
        for i in range(7)
    ]
    holdout_obs = [
        ObservationPoint(
            date=start + timedelta(days=7 + i),
            quantity=65.0,
            record_id=8 + i,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
        for i in range(3)
    ]

    metrics = evaluate_moving_average(train_obs, holdout_obs, window_size=3)
    assert metrics.status == "completed"
    assert len(metrics.predictions) == 3

    for p in metrics.predictions:
        assert p.predicted == 60.0  # mean of (50, 60, 70)
        assert p.actual == 65.0

    # MAE should be mean(abs(65 - 60)) = 5.0
    assert metrics.mae == 5.0
    # WAPE = sum(abs(5 + 5 + 5)) / sum(65 + 65 + 65) * 100 = 15 / 195 * 100 = 7.6923%
    assert metrics.wape == pytest.approx(7.6923, abs=0.001)


def test_fixed_origin_multi_step_baseline_behavior():
    """Verify that multi-step forecast does NOT update its prediction based on holdout actuals."""
    start = date(2026, 1, 1)
    train_obs = [
        ObservationPoint(
            date=start + timedelta(days=i),
            quantity=20.0,
            record_id=i + 1,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
        for i in range(8)
    ]
    # Wild fluctuations in holdout
    holdout_obs = [
        ObservationPoint(
            date=start + timedelta(days=8 + i),
            quantity=float(100 * (i + 1)),
            record_id=9 + i,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
        for i in range(4)
    ]

    metrics = evaluate_moving_average(train_obs, holdout_obs, window_size=5)
    # The fixed-origin prediction remains constant at 20.0 regardless of holdout actuals
    for p in metrics.predictions:
        assert p.predicted == 20.0


def test_deterministic_repeated_baseline_evaluations():
    start = date(2026, 1, 1)
    train_obs = [
        ObservationPoint(
            date=start + timedelta(days=i),
            quantity=float(i % 5 + 10),
            record_id=i + 1,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
        for i in range(10)
    ]
    holdout_obs = [
        ObservationPoint(
            date=start + timedelta(days=10 + i),
            quantity=12.0,
            record_id=11 + i,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
        for i in range(3)
    ]

    run1 = evaluate_moving_average(train_obs, holdout_obs, window_size=7)
    run2 = evaluate_moving_average(train_obs, holdout_obs, window_size=7)

    assert run1.mae == run2.mae
    assert run1.wape == run2.wape
    assert [p.predicted for p in run1.predictions] == [p.predicted for p in run2.predictions]


# -----------------------------------------------------------------------------
# 3. Prophet Candidate Model Tests
# -----------------------------------------------------------------------------


def test_prophet_predictions_on_exact_same_holdout_dates():
    start = date(2026, 1, 1)
    train_obs = [
        ObservationPoint(
            date=start + timedelta(days=i),
            quantity=float(10 + (i % 7)),
            record_id=i + 1,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
        for i in range(15)
    ]
    holdout_obs = [
        ObservationPoint(
            date=start + timedelta(days=15 + i),
            quantity=12.0,
            record_id=16 + i,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
        for i in range(4)
    ]

    p_metrics = evaluate_prophet(train_obs, holdout_obs)
    b_metrics = evaluate_moving_average(train_obs, holdout_obs, window_size=7)

    assert p_metrics.status == "completed"
    assert b_metrics.status == "completed"

    p_dates = [p.date for p in p_metrics.predictions]
    b_dates = [p.date for p in b_metrics.predictions]
    h_dates = [o.date for o in holdout_obs]

    assert p_dates == h_dates
    assert b_dates == h_dates
    assert len(p_metrics.predictions) == len(b_metrics.predictions)


def test_prophet_negative_clipping_preserves_raw_prediction():
    """Verify that if Prophet predicts negative demand, it is clipped to 0.0 with raw stored."""
    start = date(2026, 1, 1)
    train_obs = [
        ObservationPoint(
            date=start + timedelta(days=i),
            quantity=1.0,
            record_id=i + 1,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
        for i in range(10)
    ]
    holdout_obs = [
        ObservationPoint(
            date=start + timedelta(days=10),
            quantity=0.0,
            record_id=11,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
    ]

    # Mock Prophet predict to return a negative yhat
    with patch("app.services.forecasting.prophet_adapter.Prophet") as mock_prophet_cls:
        import pandas as pd

        mock_instance = mock_prophet_cls.return_value
        mock_instance.predict.return_value = pd.DataFrame(
            {"ds": [pd.to_datetime(holdout_obs[0].date)], "yhat": [-3.75]}
        )

        metrics = evaluate_prophet(train_obs, holdout_obs)
        assert metrics.status == "completed"
        assert metrics.predictions[0].predicted == 0.0  # clipped
        assert metrics.predictions[0].raw_predicted == -3.75  # raw preserved
        assert metrics.config["negative_clipping_applied"] is True


def test_prophet_failure_does_not_lose_baseline_metrics():
    """Verify that when Prophet fails or is unavailable, baseline still succeeds."""
    start = date(2026, 1, 1)
    train_obs = [
        ObservationPoint(
            date=start + timedelta(days=i),
            quantity=20.0,
            record_id=i + 1,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
        for i in range(10)
    ]
    holdout_obs = [
        ObservationPoint(
            date=start + timedelta(days=10 + i),
            quantity=20.0,
            record_id=11 + i,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
        for i in range(3)
    ]

    b_metrics = evaluate_moving_average(train_obs, holdout_obs, window_size=5)
    assert b_metrics.status == "completed"
    assert b_metrics.mae == 0.0

    with patch("app.services.forecasting.prophet_adapter.PROPHET_AVAILABLE", False):
        p_metrics = evaluate_prophet(train_obs, holdout_obs)
        assert p_metrics.status == "unavailable"
        assert "not installed" in p_metrics.failure_reason

        comp = compare_models(b_metrics, p_metrics)
        assert comp.winning_model == "moving_average"
        assert comp.comparison_valid is True


# -----------------------------------------------------------------------------
# 4. Evaluation Metrics Tests (MAE, WAPE, Zero-Actuals, Non-Finite)
# -----------------------------------------------------------------------------


def test_mae_and_wape_calculation_accuracy():
    pts = [
        ForecastPredictionPoint(date=date(2026, 1, 1), actual=10.0, predicted=12.0),  # error = 2
        ForecastPredictionPoint(date=date(2026, 1, 2), actual=20.0, predicted=16.0),  # error = 4
        ForecastPredictionPoint(date=date(2026, 1, 3), actual=30.0, predicted=30.0),  # error = 0
    ]
    metrics = calculate_forecast_metrics("test_model", pts, config={})
    # MAE = (2 + 4 + 0) / 3 = 2.0
    assert metrics.mae == 2.0
    # WAPE = (2 + 4 + 0) / (10 + 20 + 30) * 100 = 6 / 60 * 100 = 10.0%
    assert metrics.wape == 10.0
    assert metrics.wape_unavailable_reason is None


def test_wape_unavailable_when_total_actual_demand_is_zero():
    pts = [
        ForecastPredictionPoint(date=date(2026, 1, 1), actual=0.0, predicted=5.0),
        ForecastPredictionPoint(date=date(2026, 1, 2), actual=0.0, predicted=2.0),
    ]
    metrics = calculate_forecast_metrics("test_model", pts, config={})
    # MAE = (5 + 2) / 2 = 3.5
    assert metrics.mae == 3.5
    # WAPE must be None because sum(actual) == 0
    assert metrics.wape is None
    assert metrics.wape_unavailable_reason is not None
    assert "zero denominator" in metrics.wape_unavailable_reason


def test_zero_demand_observations_handling():
    pts = [
        ForecastPredictionPoint(date=date(2026, 1, 1), actual=0.0, predicted=0.0),
        ForecastPredictionPoint(date=date(2026, 1, 2), actual=10.0, predicted=10.0),
    ]
    metrics = calculate_forecast_metrics("test_model", pts, config={})
    assert metrics.mae == 0.0
    assert metrics.wape == 0.0  # 0 / 10 * 100 = 0.0%


def test_non_finite_prediction_handling():
    pts = [
        ForecastPredictionPoint(date=date(2026, 1, 1), actual=10.0, predicted=float("nan")),
    ]
    metrics = calculate_forecast_metrics("test_model", pts, config={})
    assert metrics.status == "failed"
    assert "non-finite" in (metrics.failure_reason or "")
    assert metrics.mae is None


# -----------------------------------------------------------------------------
# 5. Data Prep: Missing Dates, Gaps & Anomalies
# -----------------------------------------------------------------------------


def test_missing_calendar_dates_not_imputed_as_zero(db_session: Session):
    prod = _create_sample_product(db_session, sku="SKU-GAPS-001")
    # Insert 10 observations with 3-day gaps between them
    _create_demand_series(
        db_session,
        prod.id,
        start_date=date(2026, 1, 1),
        quantities=[10, 15, 20, 25, 30, 35, 40, 45, 50, 55],
        step_days=3,
    )

    prepared = prepare_demand_series(db_session, prod.id, min_observations=10)
    assert prepared.diagnostics.total_observations == 10
    assert prepared.diagnostics.calendar_span_days == 28  # 1 to 28
    assert prepared.diagnostics.missing_calendar_dates == 18
    assert prepared.diagnostics.max_calendar_gap_days == 3
    # Verify no zero-demand synthetic records were created
    assert len(prepared.observations) == 10
    assert all(obs.quantity >= 10 for obs in prepared.observations)
    assert any("missing calendar days" in w for w in prepared.diagnostics.data_quality_warnings)


def test_reviewed_anomalies_traceability_without_modifying_raw_data(db_session: Session):
    prod = _create_sample_product(db_session, sku="SKU-ANOM-001")
    recs = _create_demand_series(
        db_session,
        prod.id,
        start_date=date(2026, 1, 1),
        quantities=[10, 10, 10, 10, 10, 10, 500, 10, 10, 10],  # 500 is anomaly
    )

    # Add an anomaly review for the 500 spike
    spike_rec = recs[6]
    rev = AnomalyReview(
        product_id=prod.id,
        demand_record_id=spike_rec.id,
        affected_start_date=spike_rec.date,
        affected_end_date=spike_rec.date,
        original_quantity=500.0,
        anomaly_score=15.0,
        classification="promotion",
        review_status="reviewed",
    )
    db_session.add(rev)
    db_session.commit()

    prepared = prepare_demand_series(db_session, prod.id, min_observations=10)
    assert prepared.diagnostics.reviewed_anomalies_count == 1
    # Verify the observation kept raw 500.0 without automatic imputation or modification
    spike_obs = prepared.observations[6]
    assert spike_obs.quantity == 500.0
    assert spike_obs.has_anomaly_review is True
    assert spike_obs.reviewed_classification == "promotion"


# -----------------------------------------------------------------------------
# 6. Validation & Error Handling Tests
# -----------------------------------------------------------------------------


def test_insufficient_training_and_holdout_history(db_session: Session):
    prod = _create_sample_product(db_session, sku="SKU-SHORT-001")
    _create_demand_series(
        db_session,
        prod.id,
        start_date=date(2026, 1, 1),
        quantities=[10, 20, 30],  # only 3 records
    )

    with pytest.raises(InsufficientDemandHistoryError):
        prepare_demand_series(db_session, prod.id, min_observations=10)


def test_invalid_split_configuration():
    start = date(2026, 1, 1)
    obs = [
        ObservationPoint(
            date=start + timedelta(days=i),
            quantity=10.0,
            record_id=i + 1,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
        for i in range(12)
    ]

    # test_size > 0.5
    with pytest.raises(InvalidSplitConfigError):
        perform_chronological_split(obs, test_size=0.8)

    # test_days exceeds total
    with pytest.raises(InvalidSplitConfigError):
        perform_chronological_split(obs, test_days=10, min_train_size=7)


def test_invalid_baseline_window_configuration():
    start = date(2026, 1, 1)
    train_obs = [
        ObservationPoint(
            date=start + timedelta(days=i),
            quantity=10.0,
            record_id=i + 1,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
        for i in range(5)
    ]
    holdout_obs = [
        ObservationPoint(
            date=start + timedelta(days=5),
            quantity=10.0,
            record_id=6,
            is_promotion=False,
            is_stockout=False,
            has_anomaly_review=False,
        )
    ]

    # window_size 10 > train_obs 5
    with pytest.raises(InvalidBaselineConfigError):
        evaluate_moving_average(train_obs, holdout_obs, window_size=10)


def test_missing_product_handling(db_session: Session):
    with pytest.raises(ProductNotFoundError):
        prepare_demand_series(db_session, product_id=999999, min_observations=10)


# -----------------------------------------------------------------------------
# 7. End-to-End Persistence, Immutability & API Tests
# -----------------------------------------------------------------------------


def test_full_benchmark_run_persistence_and_retrieval(client: TestClient, db_session: Session):
    prod = _create_sample_product(db_session, sku="SKU-API-E2E-001")
    _create_demand_series(
        db_session,
        prod.id,
        start_date=date(2026, 1, 1),
        quantities=[10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36, 38],  # 15 records
    )

    # Execute benchmark via API
    resp = client.post(
        f"/api/v1/products/{prod.id}/forecast-benchmark",
        json={"test_size": 0.2, "window_size": 5},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["product_id"] == prod.id
    assert data["sku"] == "SKU-API-E2E-001"
    assert data["status"] == "completed"
    assert data["baseline_method"] == "moving_average"
    assert data["candidate_method"] == "prophet"
    assert data["mae_baseline"] is not None
    assert data["mae_candidate"] is not None
    run_id = data["id"]

    # Verify retrieval via GET /forecast-runs/{id}
    get_resp = client.get(f"/api/v1/forecast-runs/{run_id}")
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["id"] == run_id
    assert get_data["split_config"]["evaluation_observation_count"] == 3
    assert get_data["split_config"]["training_observation_count"] == 12
    assert get_data["comparison"]["winning_model"] in ["moving_average", "prophet", "tie"]

    # Verify listing via GET /products/{id}/forecast-runs
    list_resp = client.get(f"/api/v1/products/{prod.id}/forecast-runs")
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert list_data["total"] >= 1
    assert any(r["id"] == run_id for r in list_data["items"])

    # Verify detailed metrics via GET /forecast-runs/{id}/metrics
    metrics_resp = client.get(f"/api/v1/forecast-runs/{run_id}/metrics")
    assert metrics_resp.status_code == 200
    metrics_data = metrics_resp.json()
    assert len(metrics_data["baseline_metrics"]["predictions"]) == 3
    assert len(metrics_data["candidate_metrics"]["predictions"]) == 3


def test_historical_run_immutability_after_new_imports(client: TestClient, db_session: Session):
    prod = _create_sample_product(db_session, sku="SKU-IMMUTABLE-001")
    _create_demand_series(
        db_session,
        prod.id,
        start_date=date(2026, 1, 1),
        quantities=[10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21],  # 12 records
    )

    # Run benchmark
    resp = client.post(
        f"/api/v1/products/{prod.id}/forecast-benchmark",
        json={"test_size": 0.25, "window_size": 5},
    )
    assert resp.status_code == 200
    run_id = resp.json()["id"]
    original_baseline_mae = resp.json()["mae_baseline"]
    original_candidate_mae = resp.json()["mae_candidate"]

    # Import 10 new demand records into the database
    _create_demand_series(
        db_session,
        prod.id,
        start_date=date(2026, 1, 13),
        quantities=[50, 60, 70, 80, 90, 100, 110, 120, 130, 140],
    )

    # Retrieve the historical run and verify its metrics and snapshot did not change
    get_resp = client.get(f"/api/v1/forecast-runs/{run_id}")
    assert get_resp.status_code == 200
    historical = get_resp.json()
    assert historical["mae_baseline"] == original_baseline_mae
    assert historical["mae_candidate"] == original_candidate_mae
    assert historical["split_config"]["training_observation_count"] == 9
    assert historical["split_config"]["evaluation_observation_count"] == 3


def test_get_nonexistent_forecast_run(client: TestClient):
    resp = client.get("/api/v1/forecast-runs/999999")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "FORECAST_RUN_NOT_FOUND"


def test_list_forecast_runs_nonexistent_product(client: TestClient):
    resp = client.get("/api/v1/products/999999/forecast-runs")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PRODUCT_NOT_FOUND"


def test_full_benchmark_run_partial_status_when_prophet_fails(
    client: TestClient, db_session: Session
):
    """Verify that when Prophet fails during benchmark execution, status is 'partial' and baseline metrics survive."""
    prod = _create_sample_product(db_session, sku="SKU-PARTIAL-001")
    _create_demand_series(
        db_session,
        prod.id,
        start_date=date(2026, 1, 1),
        quantities=[10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32],
    )

    with patch("app.services.forecast_service.evaluate_prophet") as mock_eval_prophet:
        from app.schemas.forecast import ModelMetrics

        mock_eval_prophet.return_value = ModelMetrics(
            method="prophet",
            status="failed",
            failure_reason="Simulated Prophet divergence failure",
            predictions=[],
        )

        resp = client.post(
            f"/api/v1/products/{prod.id}/forecast-benchmark",
            json={"test_size": 0.25, "window_size": 5},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["status"] == "partial"
        assert "Candidate model failed" in (data["failure_reason"] or "")
        assert data["mae_baseline"] is not None
        assert data["mae_candidate"] is None
        assert data["comparison"]["winning_model"] == "moving_average"
        assert data["comparison"]["comparison_valid"] is True


def test_forecast_benchmark_transaction_rollback_on_db_error(db_session: Session):
    """Verify that database transaction rolls back cleanly if commit fails."""
    prod = _create_sample_product(db_session, sku="SKU-ROLLBACK-001")
    _create_demand_series(
        db_session,
        prod.id,
        start_date=date(2026, 1, 1),
        quantities=[10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32],
    )

    from app.schemas.forecast import ForecastBenchmarkRequest
    from app.services.forecast_service import ForecastService

    req = ForecastBenchmarkRequest(test_size=0.25, window_size=5)

    with patch.object(db_session, "commit", side_effect=Exception("Database lock failure")):
        with pytest.raises(Exception, match="Database lock failure"):
            ForecastService.run_benchmark_for_product(
                db=db_session,
                product_id=prod.id,
                request=req,
            )


def test_list_all_forecast_runs_global_and_filtered(client: TestClient, db_session: Session):
    """Verify GET /forecast-runs returns paginated runs globally or filtered by product_id."""
    prod1 = _create_sample_product(db_session, sku="SKU-GLOBAL-FC-1")
    prod2 = _create_sample_product(db_session, sku="SKU-GLOBAL-FC-2")
    _create_demand_series(
        db_session,
        prod1.id,
        start_date=date(2026, 1, 1),
        quantities=[10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32],
    )
    _create_demand_series(
        db_session,
        prod2.id,
        start_date=date(2026, 1, 1),
        quantities=[5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16],
    )

    client.post(f"/api/v1/products/{prod1.id}/forecast-benchmark", json={"test_size": 0.25})
    client.post(f"/api/v1/products/{prod2.id}/forecast-benchmark", json={"test_size": 0.25})

    # Global list
    resp_all = client.get("/api/v1/forecast-runs")
    assert resp_all.status_code == 200
    data_all = resp_all.json()
    assert data_all["total"] >= 2
    skus = {item["sku"] for item in data_all["items"]}
    assert "SKU-GLOBAL-FC-1" in skus
    assert "SKU-GLOBAL-FC-2" in skus

    # Filtered by product_id
    resp_p1 = client.get(f"/api/v1/forecast-runs?product_id={prod1.id}")
    assert resp_p1.status_code == 200
    data_p1 = resp_p1.json()
    assert data_p1["total"] >= 1
    assert all(item["product_id"] == prod1.id for item in data_p1["items"])
