from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from app.core.errors import AppException
from app.schemas.forecast import SplitConfig
from app.services.forecasting.data_prep import ObservationPoint


class InvalidSplitConfigError(AppException):
    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="INVALID_SPLIT_CONFIG",
            status_code=422,
        )


@dataclass(frozen=True)
class ChronologicalSplit:
    train_observations: List[ObservationPoint]
    holdout_observations: List[ObservationPoint]
    split_config: SplitConfig
    training_start_date: date
    training_end_date: date
    holdout_start_date: date
    holdout_end_date: date


def perform_chronological_split(
    observations: List[ObservationPoint],
    test_size: float = 0.2,
    test_days: Optional[int] = None,
    min_train_size: int = 7,
    min_test_size: int = 3,
) -> ChronologicalSplit:
    """Performs a strict chronological train/test holdout split.

    - Earlier observations are used exclusively for training.
    - Later observations are used exclusively for holdout evaluation.
    - Shuffling is strictly disallowed.
    - Prevents holdout data leakage.
    """
    total = len(observations)
    if total < (min_train_size + min_test_size):
        raise InvalidSplitConfigError(
            f"Total observations ({total}) is less than required sum of min_train_size ({min_train_size}) "
            f"and min_test_size ({min_test_size})."
        )

    if test_days is not None:
        if test_days < min_test_size:
            raise InvalidSplitConfigError(
                f"Requested test_days ({test_days}) is less than min_test_size ({min_test_size})."
            )
        holdout_count = test_days
    else:
        if not (0.05 <= test_size <= 0.5):
            raise InvalidSplitConfigError(f"test_size ({test_size}) must be between 0.05 and 0.5.")
        holdout_count = max(min_test_size, round(total * test_size))

    train_count = total - holdout_count
    if train_count < min_train_size:
        raise InvalidSplitConfigError(
            f"Split produces only {train_count} training observations, which is less than "
            f"min_train_size ({min_train_size}). Total observations: {total}, holdout: {holdout_count}."
        )

    # Slice strictly chronologically
    train_obs = observations[:train_count]
    holdout_obs = observations[train_count:]

    training_start = train_obs[0].date
    training_end = train_obs[-1].date
    holdout_start = holdout_obs[0].date
    holdout_end = holdout_obs[-1].date

    # Sanity check chronological ordering
    if training_end >= holdout_start:
        raise InvalidSplitConfigError(
            f"Chronological ordering violation: training_end ({training_end}) is not strictly before "
            f"holdout_start ({holdout_start})."
        )

    config = SplitConfig(
        method="chronological_holdout",
        test_size=test_size,
        test_days=test_days,
        min_train_size=min_train_size,
        min_test_size=min_test_size,
        training_observation_count=train_count,
        evaluation_observation_count=holdout_count,
    )

    return ChronologicalSplit(
        train_observations=train_obs,
        holdout_observations=holdout_obs,
        split_config=config,
        training_start_date=training_start,
        training_end_date=training_end,
        holdout_start_date=holdout_start,
        holdout_end_date=holdout_end,
    )
