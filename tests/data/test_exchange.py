"""Exchange client is read-only and unimplemented in Phase 0."""

import pytest

from tidemark.data.exchange import ExchangeClient


def test_fetch_closed_candles_not_implemented() -> None:
    client = ExchangeClient()
    with pytest.raises(NotImplementedError):
        client.fetch_closed_candles("BTCUSDT", "4h", 10)


def test_client_has_no_order_placement_methods() -> None:
    forbidden = {"place_order", "create_order", "cancel_order", "submit_order"}
    assert forbidden.isdisjoint(dir(ExchangeClient))
