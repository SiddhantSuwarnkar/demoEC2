import math
from typing import List, Optional

from app.schemas.forecast import (
    ForecastPredictionPoint,
    ModelComparison,
    ModelMetrics,
)


def calculate_forecast_metrics(
    method_name: str,
    predictions: List[ForecastPredictionPoint],
    config: dict,
    status: str = "completed",
    failure_reason: Optional[str] = None,
) -> ModelMetrics:
    """Calculates MAE and WAPE with zero-denominator safety and non-negative constraints."""
    if status != "completed" or not predictions:
        return ModelMetrics(
            method=method_name,
            status=status,
            mae=None,
            wape=None,
            wape_unavailable_reason="Model did not produce valid predictions."
            if status != "completed"
            else "No prediction points provided.",
            failure_reason=failure_reason,
            config=config,
            predictions=predictions,
        )

    abs_errors: List[float] = []
    actuals: List[float] = []

    for pt in predictions:
        if pt.actual is None:
            continue
        if math.isnan(pt.predicted) or math.isinf(pt.predicted):
            return ModelMetrics(
                method=method_name,
                status="failed",
                mae=None,
                wape=None,
                failure_reason=f"Model produced non-finite prediction value ({pt.predicted}) on date {pt.date}.",
                config=config,
                predictions=predictions,
            )
        abs_errors.append(abs(pt.actual - pt.predicted))
        actuals.append(abs(pt.actual))

    if not abs_errors:
        return ModelMetrics(
            method=method_name,
            status="failed",
            mae=None,
            wape=None,
            failure_reason="No holdout observation actuals available to evaluate error.",
            config=config,
            predictions=predictions,
        )

    mae_val = round(sum(abs_errors) / len(abs_errors), 4)

    sum_actuals = sum(actuals)
    if sum_actuals == 0.0:
        wape_val = None
        wape_reason = (
            "Total actual demand across the holdout period is zero; "
            "WAPE percentage is undefined (zero denominator)."
        )
    else:
        wape_val = round((sum(abs_errors) / sum_actuals) * 100.0, 4)
        wape_reason = None

    return ModelMetrics(
        method=method_name,
        status="completed",
        mae=mae_val,
        wape=wape_val,
        wape_unavailable_reason=wape_reason,
        failure_reason=None,
        config=config,
        predictions=predictions,
    )


def compare_models(
    baseline_metrics: ModelMetrics,
    candidate_metrics: ModelMetrics,
    holdout_dates_match: bool = True,
) -> ModelComparison:
    """Produces an objective comparison between baseline and candidate models."""
    if not holdout_dates_match:
        return ModelComparison(
            winning_model="none",
            mae_difference=None,
            wape_difference=None,
            relative_improvement_pct=None,
            comparison_valid=False,
            invalid_reason="Evaluation dates differed between baseline and candidate models.",
            interpretation="Comparison is invalid because models were evaluated on different dates.",
        )

    base_ok = baseline_metrics.status == "completed" and baseline_metrics.mae is not None
    cand_ok = candidate_metrics.status == "completed" and candidate_metrics.mae is not None

    if not base_ok and not cand_ok:
        return ModelComparison(
            winning_model="none",
            mae_difference=None,
            wape_difference=None,
            relative_improvement_pct=None,
            comparison_valid=False,
            invalid_reason="Both baseline and candidate models failed evaluation.",
            interpretation="Neither model produced valid evaluation metrics on the holdout period.",
        )

    if base_ok and not cand_ok:
        return ModelComparison(
            winning_model="moving_average",
            mae_difference=None,
            wape_difference=None,
            relative_improvement_pct=None,
            comparison_valid=True,
            invalid_reason=None,
            interpretation=(
                f"Moving Average baseline completed successfully (MAE: {baseline_metrics.mae}), "
                f"whereas {candidate_metrics.method} failed ({candidate_metrics.failure_reason or 'unavailable'})."
            ),
        )

    if cand_ok and not base_ok:
        return ModelComparison(
            winning_model=candidate_metrics.method,
            mae_difference=None,
            wape_difference=None,
            relative_improvement_pct=None,
            comparison_valid=True,
            invalid_reason=None,
            interpretation=f"{candidate_metrics.method} completed successfully, whereas Moving Average baseline failed.",
        )

    # Both succeeded
    mae_base = baseline_metrics.mae
    mae_cand = candidate_metrics.mae
    mae_diff = round(mae_cand - mae_base, 4)

    wape_diff: Optional[float] = None
    if baseline_metrics.wape is not None and candidate_metrics.wape is not None:
        wape_diff = round(candidate_metrics.wape - baseline_metrics.wape, 4)

    rel_improvement: Optional[float] = None
    if mae_base > 0:
        rel_improvement = round(((mae_base - mae_cand) / mae_base) * 100.0, 2)

    if abs(mae_diff) < 0.0001:
        winner = "tie"
        interpretation = (
            f"Both models achieved equivalent accuracy on the holdout period "
            f"(Moving Average MAE: {mae_base}, Candidate MAE: {mae_cand})."
        )
    elif mae_cand < mae_base:
        winner = candidate_metrics.method
        pct_text = (
            f" ({rel_improvement}% reduction in error)" if rel_improvement is not None else ""
        )
        interpretation = (
            f"Candidate model ({candidate_metrics.method}) outperformed Moving Average baseline by "
            f"{abs(mae_diff)} MAE units{pct_text} on the chronological holdout."
        )
    else:
        winner = "moving_average"
        interpretation = (
            f"Moving Average baseline outperformed candidate model ({candidate_metrics.method}) by "
            f"{abs(mae_diff)} MAE units on the chronological holdout."
        )

    return ModelComparison(
        winning_model=winner,
        mae_difference=mae_diff,
        wape_difference=wape_diff,
        relative_improvement_pct=rel_improvement,
        comparison_valid=True,
        invalid_reason=None,
        interpretation=interpretation,
    )
