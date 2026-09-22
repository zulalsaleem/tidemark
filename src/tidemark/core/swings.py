"""Fractal swing detection.

Section 1 parameters: swing fractal N = 2, usable 8h after formation. Every
detected swing must record `formed_at` (when the fractal completed) and
`confirmed_at` (when it becomes usable) separately, per the standing rules.
"""

from __future__ import annotations

import pandas as pd

from tidemark.data.models import Swing


def find_swings(candles: pd.DataFrame, fractal_n: int) -> list[Swing]:
    """Detect fractal swing highs/lows over closed candles only.

    `candles` must contain only closed candles, ordered oldest to newest.
    `fractal_n` is the number of candles required on each side of a
    pivot for it to qualify as a swing point.

    Not implemented in Phase 0.
    """
    raise NotImplementedError
