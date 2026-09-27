"""ccxt symbol <-> Coinalyze symbol mapping.

Coinalyze identifies a market as `<BASE><QUOTE>_PERP.<EXCHANGE_CODE>`. For
our canonical venue (ADR 0002: Binance USDT-M, ccxt id `binanceusdm`,
Coinalyze exchange code `"A"`) this is a mechanical concatenation of the
ccxt unified symbol's own base/quote assets - no lookup table of symbol
names is needed, verified live against Coinalyze's `/future-markets`
listing including a CJK-ticker meme coin in our universe (see the
Coinalyze inspection report and docs/adr/0011).

This mapping is Binance-specific: other exchanges spell the same market
differently (a hyphen, an underscore, or a fully romanized ticker) and
have their own exchange code. An unmapped venue fails closed via
`UnsupportedVenueError` rather than guessing a code.
"""

from __future__ import annotations

from tidemark.market_intel.errors import UnsupportedVenueError

# Coinalyze exchange codes for the venues ADR 0002 knows about. Only
# `binanceusdm` is mapped today - Tidemark's canonical venue.
VENUE_EXCHANGE_CODES: dict[str, str] = {
    "binanceusdm": "A",
}


def to_coinalyze_symbol(ccxt_symbol: str, venue: str) -> str:
    """Map a ccxt unified perpetual symbol (e.g. "BTC/USDT:USDT") to its
    Coinalyze market symbol (e.g. "BTCUSDT_PERP.A").

    Unicode base/quote assets (e.g. a CJK ticker) are preserved verbatim,
    never romanized or transliterated - percent-encoding for the HTTP
    query string happens later, in `client.py`, via `urlencode`.
    """
    if venue not in VENUE_EXCHANGE_CODES:
        raise UnsupportedVenueError(venue)

    base, _, rest = ccxt_symbol.partition("/")
    quote, _, _settlement = rest.partition(":")
    exchange_code = VENUE_EXCHANGE_CODES[venue]
    return f"{base}{quote}_PERP.{exchange_code}"
