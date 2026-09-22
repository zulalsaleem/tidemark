"""Average True Range.

Section 1 of the rulebook fixes the distance unit as ATR(14) on the 4H
timeframe. All tolerance/threshold parameters (level tolerance, minimum Fib
leg, swing cluster distance, reaction test) are expressed in units of this
ATR.
"""

from __future__ import annotations

import pandas as pd


def atr(candles: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Average True Range over closed candles only.

    `candles` must contain only closed candles, ordered oldest to newest,
    with `high`, `low`, and `close` columns.

    Not implemented in Phase 0.
    """
    raise NotImplementedError
