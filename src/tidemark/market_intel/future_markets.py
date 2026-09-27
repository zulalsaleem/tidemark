"""Cached `/future-markets` listing and per-symbol availability lookup.

Coinalyze returns `200 []` for a symbol that isn't listed at all, for one
that's listed but has no data flowing, and for a symbol name that's
simply wrong - all three look identical on the wire (verified live: a
real zero-coverage symbol and a fabricated garbage symbol both produced
the same empty array). This module is what lets the rest of the package
tell those apart:

- not present in `/future-markets` at all -> `MARKET_NOT_FOUND`
- present, but the relevant metric's own `has_*_data` flag is `False`,
  or a live call to that metric still comes back empty -> `NO_DATA`
- present and data flows -> `OK`

The listing is fetched at most once per `ttl` (default 24h, per the
merge spec) and is a single flat API call regardless of how many symbols
are looked up against it.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from tidemark.market_intel.client import CoinalyzeClient

DEFAULT_TTL = dt.timedelta(hours=24)


@dataclass(frozen=True)
class FutureMarketInfo:
    """The fields of `future_market_info` this package actually uses."""

    symbol: str
    exchange: str
    base_asset: str
    quote_asset: str
    is_perpetual: bool
    has_long_short_ratio_data: bool
    has_ohlcv_data: bool
    has_buy_sell_data: bool
    oi_lq_vol_denominated_in: str

    @classmethod
    def from_json(cls, payload: dict) -> FutureMarketInfo:
        return cls(
            symbol=payload["symbol"],
            exchange=payload["exchange"],
            base_asset=payload["base_asset"],
            quote_asset=payload["quote_asset"],
            is_perpetual=bool(payload.get("is_perpetual", False)),
            has_long_short_ratio_data=bool(payload.get("has_long_short_ratio_data", False)),
            has_ohlcv_data=bool(payload.get("has_ohlcv_data", False)),
            has_buy_sell_data=bool(payload.get("has_buy_sell_data", False)),
            oi_lq_vol_denominated_in=payload.get("oi_lq_vol_denominated_in", "BASE_ASSET"),
        )


class FutureMarketsCache:
    """Fetches and caches `/future-markets`, refreshing at most once per `ttl`."""

    def __init__(
        self,
        client: CoinalyzeClient,
        ttl: dt.timedelta = DEFAULT_TTL,
        clock: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self._client = client
        self._ttl = ttl
        self._clock = clock or (lambda: dt.datetime.now(dt.UTC))
        self._by_symbol: dict[str, FutureMarketInfo] = {}
        self._fetched_at: dt.datetime | None = None

    def _refresh_if_stale(self) -> None:
        now = self._clock()
        if self._fetched_at is not None and now - self._fetched_at < self._ttl:
            return
        rows = self._client.future_markets()
        self._by_symbol = {row["symbol"]: FutureMarketInfo.from_json(row) for row in rows}
        self._fetched_at = now

    def lookup(self, coinalyze_symbol: str) -> FutureMarketInfo | None:
        """The cached listing entry for `coinalyze_symbol`, or `None` if
        it is not present in `/future-markets` at all (`MARKET_NOT_FOUND`).
        """
        self._refresh_if_stale()
        return self._by_symbol.get(coinalyze_symbol)
