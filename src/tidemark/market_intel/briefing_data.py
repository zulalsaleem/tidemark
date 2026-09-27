"""Fetches exactly the three inputs the derivatives-context classifier
needs for BTC: closed-1H price % change, closed-1H OI % change, and the
current + previous closed-1H funding readings.

Deliberately independent from `service.py`'s `fetch_market_intel`: that
function builds the broader `/coin`/`intel market` snapshot (open
interest, liquidations, volumes, long/short ratio - none of which the
classifier needs), and extending its `MarketIntelSnapshot` shape just to
carry a price reading it was never meant to have would entangle two
different consumers' concerns. This costs a couple of extra Coinalyze
calls (price and OI are re-fetched rather than reusing a snapshot) - a
few units against the documented 40/minute budget, once an hour, is not
worth the coupling.
"""

from __future__ import annotations

import datetime as dt

from tidemark.market_intel.clamping import closed_period
from tidemark.market_intel.client import CoinalyzeClient
from tidemark.market_intel.derivatives_classifier import FundingInput, OpenInterestInput, PriceInput
from tidemark.market_intel.future_markets import FutureMarketsCache
from tidemark.market_intel.models import MARKET_NOT_FOUND, NO_DATA, OK
from tidemark.market_intel.symbols import to_coinalyze_symbol

BTC_CCXT_SYMBOL = "BTC/USDT:USDT"
INTERVAL = "1hour"

_EMPTY_RESPONSE = "Coinalyze returned no data for this period"
_ZERO_OPEN = "cannot compute a percentage change: the period's opening value was 0"


def _find_bucket(history_response: list[dict], period_start_epoch: int) -> dict | None:
    if not history_response:
        return None
    for bucket in history_response[0].get("history", []):
        if bucket["t"] == period_start_epoch:
            return bucket
    return None


def _percent_change(bucket: dict) -> float | None:
    if bucket["o"] == 0:
        return None
    return (bucket["c"] - bucket["o"]) / bucket["o"] * 100


def fetch_classifier_inputs(
    client: CoinalyzeClient,
    cache: FutureMarketsCache,
    venue: str,
    now: dt.datetime,
    interval: str = INTERVAL,
) -> tuple[PriceInput, OpenInterestInput, FundingInput]:
    coinalyze_symbol = to_coinalyze_symbol(BTC_CCXT_SYMBOL, venue)
    info = cache.lookup(coinalyze_symbol)
    if info is None:
        reason = f"{coinalyze_symbol!r} is not listed in Coinalyze's /future-markets"
        return (
            PriceInput(MARKET_NOT_FOUND, None, None, None, reason),
            OpenInterestInput(MARKET_NOT_FOUND, None, None, None, reason),
            FundingInput(MARKET_NOT_FOUND, None, None, None, None, reason),
        )

    period = closed_period(now, interval)
    from_ts = int(period.start.timestamp())
    to_ts = int(period.close.timestamp()) - 1  # exclude the in-progress next bucket

    price = _fetch_price(client, coinalyze_symbol, interval, from_ts, to_ts, period)
    oi = _fetch_oi(client, coinalyze_symbol, interval, from_ts, to_ts, period)
    funding = _fetch_funding(client, coinalyze_symbol, interval, period)

    return price, oi, funding


def _fetch_price(client, symbol, interval, from_ts, to_ts, period) -> PriceInput:
    ohlcv = client.ohlcv_history(symbol, interval, from_ts, to_ts)
    bucket = _find_bucket(ohlcv, from_ts)
    if bucket is None:
        return PriceInput(NO_DATA, None, None, None, _EMPTY_RESPONSE)
    change_pct = _percent_change(bucket)
    if change_pct is None:
        return PriceInput(NO_DATA, None, None, None, _ZERO_OPEN)
    return PriceInput(OK, change_pct, period.start, period.close)


def _fetch_oi(client, symbol, interval, from_ts, to_ts, period) -> OpenInterestInput:
    history = client.open_interest_history(symbol, interval, from_ts, to_ts, convert_to_usd=True)
    bucket = _find_bucket(history, from_ts)
    if bucket is None:
        return OpenInterestInput(NO_DATA, None, None, None, _EMPTY_RESPONSE)
    change_pct = _percent_change(bucket)
    if change_pct is None:
        return OpenInterestInput(NO_DATA, None, None, None, _ZERO_OPEN)
    return OpenInterestInput(OK, change_pct, period.start, period.close)


def _fetch_funding(client, symbol, interval, period) -> FundingInput:
    # Need both the current and the previous closed 1H reading (for
    # D5/D6's rising/falling), fetched in one range query.
    previous_start_ts = int((period.start - dt.timedelta(hours=1)).timestamp())
    current_start_ts = int(period.start.timestamp())
    to_ts = int(period.close.timestamp()) - 1

    history = client.funding_rate_history(symbol, interval, previous_start_ts, to_ts)
    current_bucket = _find_bucket(history, current_start_ts)
    if current_bucket is None:
        return FundingInput(NO_DATA, None, None, None, None, _EMPTY_RESPONSE)

    previous_bucket = _find_bucket(history, previous_start_ts)
    previous_value = previous_bucket["c"] if previous_bucket is not None else None

    return FundingInput(OK, current_bucket["c"], previous_value, period.start, period.close)
