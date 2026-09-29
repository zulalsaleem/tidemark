"""Gathers what `/coin` needs to show each metric's current value beside
the cached universe median/p75 - the data-gathering half; formatting
lives in `telegram_render.py`, exactly like every other `/coin` value.

All four "current" values now come straight off the existing
`MarketIntelSnapshot` - zero extra Coinalyze calls. OI change is
`snapshot.open_interest_change_pct` (added alongside `price_change` for
`position_flow_classifier.py` - see docs/adr/0011-market-intelligence-
layer.md): before that field existed, this module fetched its own
OI-history bucket independently, since `open_interest_change` is an
absolute USD difference, a different quantity from the percentage the
cache measures. That extra fetch is gone now that the percentage is
computed once, in `service.py`, from a bucket already fetched there.
Long/short ratio and buy/sell ratio (computed here from the two volume
values already on the snapshot) needed no extra fetch either way.
Funding rate reuses the snapshot's existing LIVE `funding_rate` value,
not a second closed-period fetch, unlike the hourly BTC briefing's
classifier - a deliberate, documented tradeoff: see the ADR for why a
live-vs-closed-period pairing is acceptable here.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from tidemark.market_intel.models import OK, MarketIntelSnapshot
from tidemark.market_intel.universe_context_store import (
    UniverseContextCache,
    latest_universe_context,
)


@dataclass(frozen=True)
class MetricContext:
    current: float
    median: float
    p75: float
    computed_at: dt.datetime
    age: dt.timedelta
    is_stale: bool


@dataclass(frozen=True)
class CoinUniverseContext:
    long_short_ratio: MetricContext | None
    funding_rate: MetricContext | None
    oi_change_pct: MetricContext | None
    buy_sell_ratio: MetricContext | None


def _metric_context(
    current: float | None,
    median: float | None,
    p75: float | None,
    computed_at: dt.datetime,
    age: dt.timedelta,
    is_stale: bool,
) -> MetricContext | None:
    if current is None or median is None or p75 is None:
        return None
    return MetricContext(
        current=current, median=median, p75=p75, computed_at=computed_at, age=age, is_stale=is_stale
    )


def gather_coin_universe_context(
    context_engine,
    snapshot: MarketIntelSnapshot,
    now: dt.datetime,
    stale_after: dt.timedelta,
) -> CoinUniverseContext | None:
    """`None` when the cache has never been refreshed - /coin renders
    exactly as before, no universe lines at all. Otherwise, each of the
    four metrics is independently `None` (omitted) when this symbol has
    no current value for it, even though the cache itself has data -
    never a fabricated current value paired with a real median/p75.
    """
    cached: UniverseContextCache | None = latest_universe_context(context_engine)
    if cached is None:
        return None

    age = now - cached.computed_at
    is_stale = age > stale_after

    ls_current = snapshot.long_short_ratio.ratio if snapshot.long_short_ratio.status == OK else None
    funding_current = snapshot.funding_rate.value if snapshot.funding_rate.status == OK else None
    oi_current = (
        snapshot.open_interest_change_pct.value
        if snapshot.open_interest_change_pct.status == OK
        else None
    )

    bs_current = None
    if (
        snapshot.buy_volume.status == OK
        and snapshot.sell_volume.status == OK
        and snapshot.sell_volume.value != 0
    ):
        bs_current = snapshot.buy_volume.value / snapshot.sell_volume.value

    return CoinUniverseContext(
        long_short_ratio=_metric_context(
            ls_current,
            cached.long_short_ratio_median,
            cached.long_short_ratio_p75,
            cached.computed_at,
            age,
            is_stale,
        ),
        funding_rate=_metric_context(
            funding_current,
            cached.funding_rate_median,
            cached.funding_rate_p75,
            cached.computed_at,
            age,
            is_stale,
        ),
        oi_change_pct=_metric_context(
            oi_current,
            cached.oi_change_pct_median,
            cached.oi_change_pct_p75,
            cached.computed_at,
            age,
            is_stale,
        ),
        buy_sell_ratio=_metric_context(
            bs_current,
            cached.buy_sell_ratio_median,
            cached.buy_sell_ratio_p75,
            cached.computed_at,
            age,
            is_stale,
        ),
    )
