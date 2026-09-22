"""Swing detection is unimplemented in Phase 0 but importable and callable."""

import pandas as pd
import pytest

from tidemark.core.swings import find_swings


def test_find_swings_not_implemented() -> None:
    candles = pd.DataFrame({"high": [1.0], "low": [0.5], "close": [0.8]})
    with pytest.raises(NotImplementedError):
        find_swings(candles, fractal_n=2)
