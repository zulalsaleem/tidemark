"""Market-data client (placeholder).

Read-only. This client fetches closed candles only — it never places
orders, never holds private keys, and is never given trading permissions
on the exchange account it reads from.
"""

from __future__ import annotations


class ExchangeClient:
    """Fetches closed OHLCV candles for a configured exchange.

    Not implemented in Phase 0.
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self._base_url = base_url
        self._api_key = api_key

    def fetch_closed_candles(self, asset: str, timeframe: str, limit: int) -> list:
        """Fetch the most recent *closed* candles for an asset/timeframe.

        Must never return an in-progress candle.
        """
        raise NotImplementedError
