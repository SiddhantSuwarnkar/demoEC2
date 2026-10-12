import logging
from typing import List

import pandas as pd

from app.schemas.forecast import ForecastPredictionPoint, ModelMetrics
from app.services.forecasting.data_prep import ObservationPoint
from app.services.forecasting.metrics import calculate_forecast_metrics

logger = logging.getLogger("stocksense.forecasting.prophet")

try:
    import prophet
    from prophet import Prophet

    PROPHET_AVAILABLE = True
    PROPHET_VERSION = getattr(prophet, "__version__", "unknown")
except ImportError:
    PROPHET_AVAILABLE = False
    PROPHET_VERSION = None
    Prophet = None


def evaluate_prophet(
    train_observations: List[ObservationPoint],
    holdout_observations: List[ObservationPoint],
    growth: str = "linear",
    seasonality_mode: str = "additive",
    interval_width: float = 0.80,
) -> ModelMetrics:
    """Evaluates Prophet candidate model on the exact same chronological holdout.

    - Fits Prophet strictly on training observations (ds, y).
    - Withholds holdout observations completely to eliminate leakage.
    - Gracefully handles missing dependencies, convergence failures, or exceptions.
    - Enforces non-negative demand predictions with audit clipping.
    """
    config = {
        "model": "prophet",
        "version": PROPHET_VERSION,
        "growth": growth,
        "seasonality_mode": seasonality_mode,
        "interval_width": interval_width,
        "training_observations": len(train_observations),
        "holdout_observations": len(holdout_observations),
        "negative_clipping_applied": False,
    }

    if not PROPHET_AVAILABLE:
        return ModelMetrics(
            method="prophet",
            status="unavailable",
            failure_reason="Prophet package is not installed in the environment.",
            config=config,
            predictions=[],
        )

    if len(train_observations) < 5:
        return ModelMetrics(
            method="prophet",
            status="failed",
            failure_reason=f"Insufficient training observations ({len(train_observations)}) for Prophet fitting.",
            config=config,
            predictions=[],
        )

    try:
        # Prepare training dataframe with strictly train observations
        df_train = pd.DataFrame(
            {
                "ds": pd.to_datetime([obs.date for obs in train_observations]),
                "y": [float(obs.quantity) for obs in train_observations],
            }
        )

        # Configure seasonality sensibly based on sample size
        n_train = len(train_observations)
        weekly = "auto" if n_train >= 14 else False

        m = Prophet(
            growth=growth,
            seasonality_mode=seasonality_mode,
            interval_width=interval_width,
            daily_seasonality=False,
            weekly_seasonality=weekly,
            yearly_seasonality=False,
        )

        # Suppress cmdstanpy / prophet excessive stdout logging during web request
        import contextlib
        import io

        f = io.StringIO()
        with contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
            m.fit(df_train)

        # Prepare future dataframe for exact holdout dates
        holdout_dates = [obs.date for obs in holdout_observations]
        df_future = pd.DataFrame({"ds": pd.to_datetime(holdout_dates)})

        with contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
            forecast = m.predict(df_future)

        yhat_series = forecast["yhat"].tolist()

        predictions: List[ForecastPredictionPoint] = []
        any_clipped = False

        for h_obs, raw_val in zip(holdout_observations, yhat_series):
            raw_float = round(float(raw_val), 2)
            clipped_float = max(0.0, raw_float)
            if clipped_float != raw_float:
                any_clipped = True

            predictions.append(
                ForecastPredictionPoint(
                    date=h_obs.date,
                    actual=h_obs.quantity,
                    predicted=clipped_float,
                    raw_predicted=raw_float,
                )
            )

        config["negative_clipping_applied"] = any_clipped

        return calculate_forecast_metrics(
            method_name="prophet",
            predictions=predictions,
            config=config,
            status="completed",
        )

    except Exception as exc:
        logger.warning("Prophet fitting failed: %s", exc, exc_info=True)
        return ModelMetrics(
            method="prophet",
            status="failed",
            failure_reason=f"Prophet fitting or prediction error: {str(exc)}",
            config=config,
            predictions=[],
        )
