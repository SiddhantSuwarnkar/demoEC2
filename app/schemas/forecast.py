from datetime import date, datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ForecastPredictionPoint(BaseModel):
    date: date
    actual: Optional[float] = None
    predicted: float
    raw_predicted: Optional[float] = None  # Stored if prediction was clipped to non-negative


class ModelMetrics(BaseModel):
    method: str
    status: str = "completed"  # "completed", "failed", "unavailable"
    mae: Optional[float] = None
    wape: Optional[float] = None
    wape_unavailable_reason: Optional[str] = None
    failure_reason: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)
    predictions: List[ForecastPredictionPoint] = Field(default_factory=list)


class ModelComparison(BaseModel):
    winning_model: Optional[str] = None  # "moving_average", "prophet", "tie", "none"
    mae_difference: Optional[float] = None  # candidate_mae - baseline_mae
    wape_difference: Optional[float] = None
    relative_improvement_pct: Optional[float] = None
    comparison_valid: bool = True
    invalid_reason: Optional[str] = None
    interpretation: str


class SeriesDiagnostics(BaseModel):
    total_observations: int
    first_date: Optional[date] = None
    last_date: Optional[date] = None
    calendar_span_days: int
    missing_calendar_dates: int
    max_calendar_gap_days: int
    zero_demand_count: int
    reviewed_anomalies_count: int
    data_quality_warnings: List[str] = Field(default_factory=list)
    series_type_note: str = (
        "Observed recorded demand dates only. Missing calendar dates are not imputed as zero; "
        "averages represent recorded-day rates rather than full calendar-day rates."
    )


class SplitConfig(BaseModel):
    method: str = "chronological_holdout"
    test_size: float = 0.2
    test_days: Optional[int] = None
    min_train_size: int = 7
    min_test_size: int = 3
    training_observation_count: int
    evaluation_observation_count: int


class ForecastBenchmarkRequest(BaseModel):
    test_size: float = Field(
        default=0.2,
        ge=0.05,
        le=0.5,
        description="Fraction of chronological observations reserved for evaluation holdout",
    )
    test_days: Optional[int] = Field(
        default=None,
        ge=1,
        description="Explicit number of holdout observations (overrides test_size if provided)",
    )
    window_size: int = Field(
        default=7,
        ge=2,
        le=90,
        description="Moving Average window size (must not exceed training observation count)",
    )
    prophet_growth: str = Field(
        default="linear",
        pattern="^(linear|flat)$",
        description="Prophet growth trend specification",
    )
    prophet_seasonality_mode: str = Field(
        default="additive",
        pattern="^(additive|multiplicative)$",
        description="Prophet seasonality mode",
    )
    min_train_size: int = Field(
        default=7,
        ge=5,
        description="Minimum training observations required",
    )
    min_test_size: int = Field(
        default=3,
        ge=2,
        description="Minimum holdout observations required",
    )


class ForecastRunBase(BaseModel):
    product_id: int
    baseline_method: str = Field(default="moving_average", max_length=64)
    candidate_method: str = Field(default="prophet", max_length=64)
    mae_baseline: Optional[float] = None
    mae_candidate: Optional[float] = None
    wape_baseline: Optional[float] = None
    wape_candidate: Optional[float] = None
    holdout_start_date: Optional[date] = None
    holdout_end_date: Optional[date] = None
    status: str = Field(default="completed", max_length=32)


class ForecastRunCreate(ForecastRunBase):
    pass


class ForecastRunRead(ForecastRunBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: Optional[str] = None
    product_name: Optional[str] = None
    training_start_date: Optional[date] = None
    training_end_date: Optional[date] = None
    data_version: Optional[str] = None
    failure_reason: Optional[str] = None
    created_at: datetime
    benchmark_version: str = "1.0"

    split_config: Optional[SplitConfig] = None
    diagnostics: Optional[SeriesDiagnostics] = None
    baseline_metrics: Optional[ModelMetrics] = None
    candidate_metrics: Optional[ModelMetrics] = None
    comparison: Optional[ModelComparison] = None
    assumptions_and_limitations: List[str] = Field(default_factory=list)


class ForecastRunListResponse(BaseModel):
    total: int
    page: int
    limit: int
    items: List[ForecastRunRead]


class ForecastMetricsDetailsResponse(BaseModel):
    run_id: int
    product_id: int
    sku: str
    status: str
    baseline_metrics: ModelMetrics
    candidate_metrics: ModelMetrics
    comparison: ModelComparison
