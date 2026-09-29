"""Builds `position_flow_classifier` inputs from an already-fetched
`MarketIntelSnapshot` - no fetching of its own, unlike `briefing_data.py`
for the BTC briefing, since `price_change`/`open_interest_change_pct`
are already on the snapshot (see `service.py`, docs/adr/0011-market-
intelligence-layer.md). Kept as its own small module rather than inlined
in `bot.py` so the mapping is unit-testable without a fake Telegram/
Coinalyze client.
"""

from __future__ import annotations

from tidemark.market_intel.models import MarketIntelSnapshot
from tidemark.market_intel.position_flow_classifier import (
    OpenInterestInput,
    PositionFlowResult,
    PriceInput,
    classify,
)


def classify_snapshot(snapshot: MarketIntelSnapshot) -> PositionFlowResult:
    price = PriceInput(
        status=snapshot.price_change.status,
        change_pct=snapshot.price_change.value,
        period_start=snapshot.price_change.period_start,
        period_close=snapshot.price_change.period_close,
        reason=snapshot.price_change.reason,
    )
    oi = OpenInterestInput(
        status=snapshot.open_interest_change_pct.status,
        change_pct=snapshot.open_interest_change_pct.value,
        period_start=snapshot.open_interest_change_pct.period_start,
        period_close=snapshot.open_interest_change_pct.period_close,
        reason=snapshot.open_interest_change_pct.reason,
    )
    return classify(price, oi)
