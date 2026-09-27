"""FutureMarketsCache: TTL'd `/future-markets` listing and lookup."""

from __future__ import annotations

import datetime as dt

from tidemark.market_intel.future_markets import FutureMarketsCache


class _FakeClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls = 0

    def future_markets(self) -> list[dict]:
        self.calls += 1
        return self.rows


def _row(symbol: str, **overrides) -> dict:
    base = {
        "symbol": symbol,
        "exchange": "A",
        "base_asset": symbol.split("USDT")[0],
        "quote_asset": "USDT",
        "is_perpetual": True,
        "has_long_short_ratio_data": True,
        "has_ohlcv_data": True,
        "has_buy_sell_data": True,
        "oi_lq_vol_denominated_in": "BASE_ASSET",
    }
    base.update(overrides)
    return base


def test_lookup_returns_none_for_a_symbol_not_in_the_listing() -> None:
    client = _FakeClient([_row("BTCUSDT_PERP.A")])
    cache = FutureMarketsCache(client)

    assert cache.lookup("NOPE_PERP.A") is None


def test_lookup_returns_info_for_a_listed_symbol() -> None:
    client = _FakeClient([_row("BTCUSDT_PERP.A", has_long_short_ratio_data=False)])
    cache = FutureMarketsCache(client)

    info = cache.lookup("BTCUSDT_PERP.A")

    assert info is not None
    assert info.symbol == "BTCUSDT_PERP.A"
    assert info.has_long_short_ratio_data is False
    assert info.has_ohlcv_data is True


def test_listing_is_fetched_once_within_the_ttl() -> None:
    client = _FakeClient([_row("BTCUSDT_PERP.A")])
    now = [dt.datetime(2026, 1, 1, tzinfo=dt.UTC)]
    cache = FutureMarketsCache(client, ttl=dt.timedelta(hours=24), clock=lambda: now[0])

    cache.lookup("BTCUSDT_PERP.A")
    now[0] += dt.timedelta(hours=1)
    cache.lookup("ETHUSDT_PERP.A")

    assert client.calls == 1


def test_listing_refreshes_after_the_ttl_expires() -> None:
    client = _FakeClient([_row("BTCUSDT_PERP.A")])
    now = [dt.datetime(2026, 1, 1, tzinfo=dt.UTC)]
    cache = FutureMarketsCache(client, ttl=dt.timedelta(hours=24), clock=lambda: now[0])

    cache.lookup("BTCUSDT_PERP.A")
    now[0] += dt.timedelta(hours=25)
    cache.lookup("BTCUSDT_PERP.A")

    assert client.calls == 2


def test_cjk_symbol_is_looked_up_by_exact_unicode_key() -> None:
    client = _FakeClient([_row("龙虾USDT_PERP.A", base_asset="龙虾")])
    cache = FutureMarketsCache(client)

    info = cache.lookup("龙虾USDT_PERP.A")

    assert info is not None
    assert info.base_asset == "龙虾"
