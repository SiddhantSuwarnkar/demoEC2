from typing import List

from app.core.errors import AppException
from app.schemas.forecast import ForecastPredictionPoint, ModelMetrics
from app.services.forecasting.data_prep import ObservationPoint
from app.services.forecasting.metrics import calculate_forecast_metrics


class InvalidBaselineConfigError(AppException):
    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="INVALID_BASELINE_CONFIG",
            status_code=422,
        )


def evaluate_moving_average(
    train_observations: List[ObservationPoint],
    holdout_observations: List[ObservationPoint],
    window_size: int = 7,
) -> ModelMetrics:
    """Evaluates a deterministic Moving Average baseline on the chronological holdout.

    - Fit/initialized strictly on training observations.
    - Fixed-origin multi-step forecast: actual holdout demand is strictly NOT consumed.
    - All predictions are non-negative.
    """
    n_train = len(train_observations)
    if n_train < 2:
        raise InvalidBaselineConfigError(
            f"Training observations count ({n_train}) is insufficient for Moving Average."
        )

    if window_size > n_train:
        raise InvalidBaselineConfigError(
            f"Requested Moving Average window_size ({window_size}) exceeds available training "
            f"observations ({n_train})."
        )

    # Use the last window_size observations of the training set
    window_values = [obs.quantity for obs in train_observations[-window_size:]]
    forecast_value = round(max(0.0, sum(window_values) / len(window_values)), 2)

    predictions: List[ForecastPredictionPoint] = []
    for h_obs in holdout_observations:
        predictions.append(
            ForecastPredictionPoint(
                date=h_obs.date,
                actual=h_obs.quantity,
                predicted=forecast_value,
                raw_predicted=forecast_value,
            )
        )

    config = {
        "window_size": window_size,
        "training_observations": n_train,
        "holdout_observations": len(holdout_observations),
        "method_type": "fixed_origin_multi_step",
    }

    return calculate_forecast_metrics(
        method_name="moving_average",
        predictions=predictions,
        config=config,
        status="completed",
    )
