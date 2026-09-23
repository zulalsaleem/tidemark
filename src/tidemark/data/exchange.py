"""Market-data client (placeholder).

Read-only. This client fetches closed candles only — it never places
orders, never holds private keys, and is never given trading permissions
on the exchange account it reads from.
"""

from __future__ import annotations

import datetime as dt

import ccxt

from tidemark.data.models import Candle

DEFAULT_EXCHANGE_ID = "binanceusdm"


class ExchangeClient:
    """Fetches closed OHLCV candles for a configured exchange via ccxt.

    Market-data endpoints only — this client never calls an order,
    balance, or trading endpoint, and public OHLCV data needs no API key.
    `exchange_id` is a ccxt exchange id (default: Binance USDⓈ-M futures,
    matching ccxt's unified perpetual symbol format e.g. "BTC/USDT:USDT").
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        exchange_id: str = DEFAULT_EXCHANGE_ID,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._exchange_id = exchange_id
        exchange_class = getattr(ccxt, exchange_id)
        config: dict = {"enableRateLimit": True}
        if api_key:
            config["apiKey"] = api_key
        self._exchange = exchange_class(config)
        if base_url:
            self._exchange.urls["api"] = base_url

    def fetch_closed_candles(
        self,
        asset: str,
        timeframe: str,
        limit: int = 500,
        since: dt.datetime | None = None,
    ) -> list[Candle]:
        """Fetch closed candles for an asset/timeframe, oldest to newest.

        Must never return an in-progress candle: the exchange's most
        recent kline can still be forming, so any row whose close time is
        not yet in the past is dropped.
        """
        since_ms = int(since.timestamp() * 1000) if since is not None else None
        raw = self._exchange.fetch_ohlcv(asset, timeframe=timeframe, limit=limit, since=since_ms)
        timeframe_ms = self._exchange.parse_timeframe(timeframe) * 1000
        now_ms = self._exchange.milliseconds()

        candles = []
        for open_ms, open_, high, low, close, volume in raw:
            close_ms = open_ms + timeframe_ms
            if close_ms > now_ms:
                continue
            candles.append(
                Candle(
                    asset=asset,
                    timeframe=timeframe,
                    open_time=dt.datetime.fromtimestamp(open_ms / 1000, tz=dt.UTC),
                    close_time=dt.datetime.fromtimestamp(close_ms / 1000, tz=dt.UTC),
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )
            )
        return candles
