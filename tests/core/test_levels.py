"""Level clustering is unimplemented in Phase 0 but importable and callable."""

import pytest

from tidemark.core.levels import cluster_swings_into_levels, prev_period_high_low


def test_cluster_swings_into_levels_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        cluster_swings_into_levels([], atr_value=100.0)


def test_prev_period_high_low_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        prev_period_high_low(None, period="day")
