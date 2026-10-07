"""Phase A market context: the derived comparisons (relative strength,
BTC alignment, liquidation imbalance), BTC/ETH reference handling, WATCH
summary, and the rendered /coin reply through the REAL bot dispatch path.

No live network: a fake Coinalyze client supplies per-symbol readings.
Section 1 records are written with the production path
(`build_journal_entry` + `TidemarkStore.save_journal_entry`), never a bespoke
row insert. See docs/adr/0011.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tidemark.market_intel.context_read import Section1Read
from tidemark.market_intel.errors import RateLimitedError
from tidemark.market_intel.future_markets import FutureMarketsCache
from tidemark.market_intel.market_context import (
    AGAINST_BTC,
    ALIGNED,
    BTC_SYMBOL,
    ETH_SYMBOL,
    LIQUIDATIONS_BALANCED,
    LONG_LIQUIDATIONS_GREATER,
    NO_DIRECTIONAL_ALIGNMENT,
    NO_MATCH,
    RELATIVE_IN_LINE,
    RELATIVE_STRONGER,
    RELATIVE_WEAKER,
    SHORT_LIQUIDATIONS_GREATER,
    UNAVAILABLE,
    AssetContext,
    asset_context_from_snapshot,
    btc_alignment,
    build_market_context,
    fetch_market_context,
    liquidation_imbalance,
    reference_symbols_to_fetch,
    relative_strength,
    section1_direction,
    unavailable_asset_context,
)
from tidemark.market_intel.service import fetch_market_intel

NOW = dt.datetime(2026, 9, 28, 8, 30, tzinfo=dt.UTC)
PERIOD_START = dt.datetime(2026, 9, 28, 7, 0, tzinfo=dt.UTC)
PERIOD_START_EPOCH = int(PERIOD_START.timestamp())
FRESH_EVALUATED_AT = dt.datetime(2026, 9, 28, 8, 0, tzinfo=dt.UTC)
STALE_EVALUATED_AT = dt.datetime(2026, 9, 27, 8, 0, tzinfo=dt.UTC)
VENUE = "binanceusdm"
SOL = "SOL/USDT:USDT"
ALLOWED_CHAT_ID = 111

# -- fakes ----------------------------------------------------------------------


def _coinalyze(ccxt_symbol: str) -> str:
    return f"{ccxt_symbol.split('/')[0]}USDT_PERP.A"


def _ccxt(coinalyze_symbol: str) -> str:
    base = coinalyze_symbol.split("USDT_PERP")[0]
    return f"{base}/USDT:USDT"


def _reading(**overrides) -> dict:
    base = dict(
        price_pct=0.0,
        oi_pct=0.0,
        funding=0.001,
        ls_ratio=1.2,
        liq_long=1000.0,
        liq_short=500.0,
        has_ohlcv=True,
        liq_missing=False,
    )
    base.update(overrides)
    return base


def _market_row(ccxt_symbol: str, reading: dict) -> dict:
    return {
        "symbol": _coinalyze(ccxt_symbol),
        "exchange": "A",
        "base_asset": ccxt_symbol.split("/")[0],
        "quote_asset": "USDT",
        "is_perpetual": True,
        "has_long_short_ratio_data": True,
        "has_ohlcv_data": reading["has_ohlcv"],
        "has_buy_sell_data": True,
        "oi_lq_vol_denominated_in": "BASE_ASSET",
    }


class _FakeMarketClient:
    """Enough of CoinalyzeClient for `fetch_market_intel`. `readings` maps a
    ccxt symbol to its closed-1H values; `fail_for` raises RateLimitedError
    for those symbols' metric calls, and `requested` logs every symbol asked.
    """

    def __init__(
        self,
        readings: dict[str, dict],
        *,
        calls_in_last_minute: int = 0,
        fail_for: tuple[str, ...] = (),
    ) -> None:
        self._readings = readings
        self.calls_in_last_minute = calls_in_last_minute
        self._fail_for = set(fail_for)
        self.requested: list[str] = []

    def _reading(self, coinalyze_symbol: str) -> dict:
        ccxt = _ccxt(coinalyze_symbol)
        self.requested.append(ccxt)
        if ccxt in self._fail_for:
            raise RateLimitedError(3, 1.0)
        return self._readings[ccxt]

    def future_markets(self):
        return [_market_row(ccxt, reading) for ccxt, reading in self._readings.items()]

    def open_interest(self, symbols, convert_to_usd=True):
        self._reading(symbols[0])
        return [{"symbol": symbols[0], "value": 1e9, "update": int(NOW.timestamp() * 1000)}]

    def funding_rate(self, symbols):
        reading = self._reading(symbols[0])
        return [
            {
                "symbol": symbols[0],
                "value": reading["funding"],
                "update": int(NOW.timestamp() * 1000),
            }
        ]

    def predicted_funding_rate(self, symbols):
        self._reading(symbols[0])
        return [{"symbol": symbols[0], "value": 0.0, "update": int(NOW.timestamp() * 1000)}]

    def open_interest_history(self, symbol, interval, from_ts, to_ts, convert_to_usd=True):
        reading = self._reading(symbol)
        close = 100.0 * (1 + reading["oi_pct"] / 100)
        return [
            {
                "symbol": symbol,
                "history": [{"t": from_ts, "o": 100.0, "h": close, "l": 100.0, "c": close}],
            }
        ]

    def long_short_ratio_history(self, symbol, interval, from_ts, to_ts):
        reading = self._reading(symbol)
        return [
            {
                "symbol": symbol,
                "history": [{"t": from_ts, "r": reading["ls_ratio"], "l": 55.0, "s": 45.0}],
            }
        ]

    def liquidation_history(self, symbol, interval, from_ts, to_ts, convert_to_usd=True):
        reading = self._reading(symbol)
        if reading["liq_missing"]:
            return [{"symbol": symbol, "history": []}]
        return [
            {
                "symbol": symbol,
                "history": [{"t": from_ts, "l": reading["liq_long"], "s": reading["liq_short"]}],
            }
        ]

    def ohlcv_history(self, symbol, interval, from_ts, to_ts):
        reading = self._reading(symbol)
        close = 100.0 * (1 + reading["price_pct"] / 100)
        return [
            {
                "symbol": symbol,
                "history": [
                    {
                        "t": from_ts,
                        "o": 100.0,
                        "h": max(100.0, close),
                        "l": min(100.0, close),
                        "c": close,
                        "v": 1000.0,
                        "bv": 600.0,
                    }
                ],
            }
        ]


def _cache(client: _FakeMarketClient) -> FutureMarketsCache:
    return FutureMarketsCache(client)


def _asset(ccxt: str, section1: Section1Read, now: dt.datetime = NOW, **overrides) -> AssetContext:
    client = _FakeMarketClient({ccxt: _reading(**overrides)})
    snapshot = fetch_market_intel(client, _cache(client), ccxt, VENUE, now)
    return asset_context_from_snapshot(snapshot, section1)


def _s1(
    asset: str,
    state: str | None = "BULLISH",
    watch: str = "LONG_WATCH",
    *,
    available: bool = True,
    active_levels: tuple[dict, ...] = (),
    grade: str | None = "B",
) -> Section1Read:
    if not available:
        return Section1Read(asset=asset, available=False, reason="stored record is stale")
    return Section1Read(
        asset=asset,
        available=True,
        state=state,
        watch=watch,
        grade=grade,
        rule_version="section-01-v1.1",
        evaluated_at=FRESH_EVALUATED_AT,
        active_levels=active_levels,
    )


# -- relative strength ----------------------------------------------------------


def test_coin_stronger_than_btc_over_the_same_closed_period() -> None:
    coin = _asset(SOL, _s1(SOL), price_pct=0.6)
    btc = _asset(BTC_SYMBOL, _s1(BTC_SYMBOL), price_pct=0.1)
    assert relative_strength(coin, btc).label == RELATIVE_STRONGER


def test_coin_weaker_than_btc_over_the_same_closed_period() -> None:
    coin = _asset(SOL, _s1(SOL), price_pct=-0.4)
    btc = _asset(BTC_SYMBOL, _s1(BTC_SYMBOL), price_pct=0.1)
    assert relative_strength(coin, btc).label == RELATIVE_WEAKER


def test_equal_price_change_is_in_line_with_btc() -> None:
    coin = _asset(SOL, _s1(SOL), price_pct=0.3)
    btc = _asset(BTC_SYMBOL, _s1(BTC_SYMBOL), price_pct=0.3)
    assert relative_strength(coin, btc).label == RELATIVE_IN_LINE


def test_missing_coin_price_change_is_no_match_with_a_reason() -> None:
    coin = _asset(SOL, _s1(SOL), has_ohlcv=False)
    btc = _asset(BTC_SYMBOL, _s1(BTC_SYMBOL), price_pct=0.1)
    result = relative_strength(coin, btc)
    assert result.label == NO_MATCH
    assert "SOL/USDT:USDT price change NO_DATA" in result.reason


def test_missing_btc_price_change_is_no_match_with_a_reason() -> None:
    coin = _asset(SOL, _s1(SOL), price_pct=0.6)
    btc = _asset(BTC_SYMBOL, _s1(BTC_SYMBOL), has_ohlcv=False)
    result = relative_strength(coin, btc)
    assert result.label == NO_MATCH
    assert "BTC/USDT:USDT price change NO_DATA" in result.reason


def test_unavailable_btc_context_is_no_match_never_a_guess() -> None:
    coin = _asset(SOL, _s1(SOL), price_pct=0.6)
    btc = unavailable_asset_context(BTC_SYMBOL, "rate limited", _s1(BTC_SYMBOL, available=False))
    result = relative_strength(coin, btc)
    assert result.label == NO_MATCH
    assert "UNAVAILABLE" in result.reason


def test_price_changes_from_different_periods_are_no_match() -> None:
    coin = _asset(SOL, _s1(SOL), price_pct=0.6)
    btc = _asset(BTC_SYMBOL, _s1(BTC_SYMBOL), now=NOW + dt.timedelta(hours=1), price_pct=0.1)
    result = relative_strength(coin, btc)
    assert result.label == NO_MATCH
    assert result.reason == "price changes cover different closed periods"


def test_btc_itself_is_no_match_against_btc() -> None:
    btc = _asset(BTC_SYMBOL, _s1(BTC_SYMBOL), price_pct=0.1)
    result = relative_strength(btc, btc)
    assert result.label == NO_MATCH
    assert "BTC reference itself" in result.reason


# -- BTC alignment --------------------------------------------------------------


@pytest.mark.parametrize(
    ("coin_state", "btc_state", "expected"),
    [
        ("BULLISH", "BULLISH", ALIGNED),
        ("BEARISH", "BEARISH", ALIGNED),
        ("BULLISH", "BEARISH", AGAINST_BTC),
        ("BEARISH", "BULLISH", AGAINST_BTC),
        ("NEUTRAL", "BULLISH", NO_DIRECTIONAL_ALIGNMENT),
        ("BULLISH", "NEUTRAL", NO_DIRECTIONAL_ALIGNMENT),
        ("NEUTRAL", "NEUTRAL", NO_DIRECTIONAL_ALIGNMENT),
        ("INSUFFICIENT_STRUCTURE", "BEARISH", NO_DIRECTIONAL_ALIGNMENT),
        ("STRUCTURE_BROKEN_BEAR", "BULLISH", NO_DIRECTIONAL_ALIGNMENT),
        ("BEARISH", "STRUCTURE_BROKEN_BULL", NO_DIRECTIONAL_ALIGNMENT),
    ],
)
def test_alignment_for_every_stored_state_combination(coin_state, btc_state, expected) -> None:
    coin = _asset(SOL, _s1(SOL, coin_state))
    btc = _asset(BTC_SYMBOL, _s1(BTC_SYMBOL, btc_state))
    assert btc_alignment(coin, btc).label == expected


def test_alignment_with_coin_section1_unavailable_is_no_directional_alignment() -> None:
    coin = _asset(SOL, _s1(SOL, available=False))
    btc = _asset(BTC_SYMBOL, _s1(BTC_SYMBOL, "BULLISH"))
    result = btc_alignment(coin, btc)
    assert result.label == NO_DIRECTIONAL_ALIGNMENT
    assert "SOL/USDT:USDT Section 1 UNAVAILABLE" in result.reason


def test_alignment_with_btc_context_unavailable_is_no_directional_alignment() -> None:
    coin = _asset(SOL, _s1(SOL, "BULLISH"))
    btc = unavailable_asset_context(BTC_SYMBOL, "rate limited", _s1(BTC_SYMBOL, available=False))
    assert btc_alignment(coin, btc).label == NO_DIRECTIONAL_ALIGNMENT


def test_btc_itself_has_no_alignment_with_itself() -> None:
    btc = _asset(BTC_SYMBOL, _s1(BTC_SYMBOL, "BULLISH"))
    assert btc_alignment(btc, btc).label == NO_MATCH


def test_only_bullish_and_bearish_map_to_a_direction() -> None:
    assert section1_direction(_s1(SOL, "BULLISH")) == "UP"
    assert section1_direction(_s1(SOL, "BEARISH", "SHORT_WATCH")) == "DOWN"
    assert section1_direction(_s1(SOL, "NEUTRAL", "WAIT")) is None
    assert section1_direction(_s1(SOL, available=False)) is None


# -- liquidation imbalance ------------------------------------------------------


@pytest.mark.parametrize(
    ("liq_long", "liq_short", "expected"),
    [
        (2000.0, 1000.0, LONG_LIQUIDATIONS_GREATER),
        (1000.0, 2000.0, SHORT_LIQUIDATIONS_GREATER),
        (1500.0, 1500.0, LIQUIDATIONS_BALANCED),
    ],
)
def test_liquidation_imbalance_three_directional_cases(liq_long, liq_short, expected) -> None:
    coin = _asset(SOL, _s1(SOL), liq_long=liq_long, liq_short=liq_short)
    assert liquidation_imbalance(coin).label == expected


def test_liquidation_imbalance_missing_is_unavailable_with_a_reason() -> None:
    coin = _asset(SOL, _s1(SOL), liq_missing=True)
    result = liquidation_imbalance(coin)
    assert result.label == UNAVAILABLE
    assert "NO_DATA" in result.reason


def test_liquidation_imbalance_with_no_coin_context_is_unavailable() -> None:
    coin = unavailable_asset_context(SOL, "rate limited", _s1(SOL, available=False))
    assert liquidation_imbalance(coin).label == UNAVAILABLE


# -- reference symbols and bundle assembly -------------------------------------


def test_reference_symbols_exclude_the_coin_itself() -> None:
    assert reference_symbols_to_fetch(SOL) == (BTC_SYMBOL, ETH_SYMBOL)
    assert reference_symbols_to_fetch(BTC_SYMBOL) == (ETH_SYMBOL,)
    assert reference_symbols_to_fetch(ETH_SYMBOL) == (BTC_SYMBOL,)


def test_btc_failure_renders_unavailable_without_failing_the_bundle() -> None:
    readings = {SOL: _reading(price_pct=0.6), BTC_SYMBOL: _reading(), ETH_SYMBOL: _reading()}
    client = _FakeMarketClient(readings, fail_for=(BTC_SYMBOL,))
    cache = _cache(client)
    coin_snapshot = fetch_market_intel(client, cache, SOL, VENUE, NOW)

    bundle = fetch_market_context(
        client, cache, coin_snapshot, VENUE, NOW, "sqlite:///does-not-matter.db"
    )

    btc = bundle.market.btc
    assert btc.status == UNAVAILABLE
    assert "rate limit" in btc.reason
    # Not recomputed: no position flow, no metrics, nothing derived from it.
    assert btc.position_flow is None
    assert btc.price_change is None
    assert bundle.derived.relative_strength.label == NO_MATCH
    # The other reference is unaffected.
    assert bundle.market.eth.status == "OK"


def test_only_the_references_not_already_in_hand_are_fetched() -> None:
    readings = {SOL: _reading(), BTC_SYMBOL: _reading(), ETH_SYMBOL: _reading()}
    client = _FakeMarketClient(readings)
    cache = _cache(client)
    coin_snapshot = fetch_market_intel(client, cache, BTC_SYMBOL, VENUE, NOW)
    client.requested.clear()

    fetch_market_context(client, cache, coin_snapshot, VENUE, NOW, "sqlite:///x.db")

    assert set(client.requested) == {ETH_SYMBOL}


def test_watch_summary_names_the_held_level_with_most_touches() -> None:
    levels = (
        {"price": 100.0, "role": "support", "held": True, "touches": 2},
        {"price": 95.0, "role": "support", "held": True, "touches": 4},
        {"price": 120.0, "role": "resistance", "held": True, "touches": 9},
    )
    coin = _asset(SOL, _s1(SOL, "BULLISH", "LONG_WATCH", active_levels=levels))
    btc = _asset(BTC_SYMBOL, _s1(BTC_SYMBOL, "BULLISH"))
    eth = _asset(ETH_SYMBOL, _s1(ETH_SYMBOL, "BULLISH"))

    watch = build_market_context(coin, btc, eth).watch

    assert watch.active
    assert watch.watch == "LONG_WATCH"
    assert watch.level_price == 95.0
    assert watch.level_touches == 4
    assert watch.btc_alignment == ALIGNED


def test_no_held_level_leaves_the_watch_level_unnamed() -> None:
    coin = _asset(SOL, _s1(SOL, "BULLISH", "LONG_WATCH", active_levels=()))
    btc = _asset(BTC_SYMBOL, _s1(BTC_SYMBOL, "BULLISH"))
    eth = _asset(ETH_SYMBOL, _s1(ETH_SYMBOL, "BULLISH"))
    watch = build_market_context(coin, btc, eth).watch
    assert watch.active
    assert watch.level_price is None


def test_a_wait_watch_is_not_active() -> None:
    coin = _asset(SOL, _s1(SOL, "NEUTRAL", "WAIT"))
    btc = _asset(BTC_SYMBOL, _s1(BTC_SYMBOL, "BULLISH"))
    eth = _asset(ETH_SYMBOL, _s1(ETH_SYMBOL, "BULLISH"))
    assert not build_market_context(coin, btc, eth).watch.active


def test_rulebook_versions_list_every_version_a_message_used() -> None:
    coin = _asset(SOL, _s1(SOL, "BULLISH"))
    btc = _asset(BTC_SYMBOL, _s1(BTC_SYMBOL, "BULLISH"))
    eth = _asset(ETH_SYMBOL, _s1(ETH_SYMBOL, available=False))
    bundle = build_market_context(coin, btc, eth)
    assert bundle.rulebook_versions == ("section-01-v1.1", "position-flow-v0.1")
