"""ccxt <-> Coinalyze symbol mapping, including the CJK ticker in our
universe (Binance's own ticker, literally Chinese characters for
"lobster") - verified mechanical, not a lookup, against a real
`/future-markets` listing during the Coinalyze inspection.
"""

from __future__ import annotations

import urllib.parse

import pytest

from tidemark.market_intel.errors import UnsupportedVenueError
from tidemark.market_intel.symbols import to_coinalyze_symbol

CJK_LOBSTER = "龙虾"


@pytest.mark.parametrize(
    ("ccxt_symbol", "expected"),
    [
        ("BTC/USDT:USDT", "BTCUSDT_PERP.A"),
        ("ETH/USDT:USDT", "ETHUSDT_PERP.A"),
        ("SOL/USDT:USDT", "SOLUSDT_PERP.A"),
        ("1000PEPE/USDT:USDT", "1000PEPEUSDT_PERP.A"),
        (f"{CJK_LOBSTER}/USDT:USDT", f"{CJK_LOBSTER}USDT_PERP.A"),
    ],
)
def test_mechanical_mapping_for_binanceusdm(ccxt_symbol: str, expected: str) -> None:
    assert to_coinalyze_symbol(ccxt_symbol, "binanceusdm") == expected


def test_unmapped_venue_fails_closed() -> None:
    with pytest.raises(UnsupportedVenueError) as exc_info:
        to_coinalyze_symbol("BTC/USDT:USDT", "bitget")
    assert exc_info.value.venue == "bitget"


def test_cjk_symbol_percent_encodes_and_round_trips() -> None:
    """The Unicode ticker must be preserved verbatim through URL
    percent-encoding, never romanized or mangled."""
    mapped = to_coinalyze_symbol(f"{CJK_LOBSTER}/USDT:USDT", "binanceusdm")
    assert mapped == f"{CJK_LOBSTER}USDT_PERP.A"

    encoded = urllib.parse.urlencode({"symbols": mapped})
    assert "%" in encoded  # actually percent-encoded, not left raw
    round_tripped = urllib.parse.parse_qs(encoded)["symbols"][0]
    assert round_tripped == mapped
