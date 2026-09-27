"""Asset-class domain constraint (Phase 6, UNIV-08).

The Tidemark rulebook is a crypto scalping/intraday rulebook; its 4H/1H
structural behaviour is not validated for tokenised equities, commodities,
FX, or crypto-basket indices. The Phase 6 asset-class inspection
(2026-09-25) found that ccxt's unified market fields carry no such
signal, but Binance's own raw `market['info']['underlyingType']` — already
present in the same `load_markets()` response `data/discover.py` already
consumes, no new endpoint or API call — reliably distinguishes them:
`COIN` for every crypto contract tested (including all 5 non-ASCII
CJK-ticker meme-coin perpetuals), a fixed, enumerable set of TradFi values
for every named non-crypto contract, and `INDEX` for crypto-basket
products (`BTCDOM`, `ALL`) that are crypto-related but not a single
underlying coin.

Classification is deliberately mechanical and narrow: `underlyingType`
only. Symbol name, ticker pattern, price behaviour, volume,
`underlyingSubType`, and `contractType` are never used to decide asset
class (`contractType` agreed with `underlyingType` on every symbol the
inspection checked, but corroboration is not substitution — see the
inspection report). A `underlyingType` this module has not been told
about is `UNKNOWN`, never guessed into `NON_CRYPTO` — see
docs/adr/0009-universe-selection-architecture.md's UNIV-08.
"""

from __future__ import annotations

from dataclasses import dataclass

CLASSIFICATION_SOURCE = "BINANCE_MARKET_METADATA"
CLASSIFICATION_METHODOLOGY_VERSION = "asset-class-v1"

# asset_class values.
CRYPTO = "CRYPTO"
NON_CRYPTO = "NON_CRYPTO"
NON_ELIGIBLE_INDEX = "NON_ELIGIBLE_INDEX"
UNKNOWN = "UNKNOWN"

# Exclusion reasons (UniverseSnapshotRow.exclusion_reason). NON_ELIGIBLE_INDEX
# is deliberately both the asset_class value and its own exclusion reason -
# there is only one reason a symbol ever gets that asset_class.
NON_CRYPTO_UNDERLYING = "NON_CRYPTO_UNDERLYING"
UNKNOWN_UNDERLYING_TYPE = "UNKNOWN_UNDERLYING_TYPE"

_CRYPTO_UNDERLYING_TYPE = "COIN"
_INDEX_UNDERLYING_TYPE = "INDEX"

# Every non-crypto `underlyingType` value the Phase 6 inspection found on
# binanceusdm (727 ACTIVE symbols, full scan, 2026-09-25). A value outside
# this set - a typo, a future Binance category this module was never told
# about - is UNKNOWN, not silently absorbed into NON_CRYPTO.
_KNOWN_NON_CRYPTO_UNDERLYING_TYPES = frozenset(
    {"EQUITY", "KR_EQUITY", "HK_EQUITY", "CN_EQUITY", "COMMODITY", "FX", "PREMARKET"}
)


@dataclass(frozen=True)
class AssetClassification:
    """One symbol's asset-class classification outcome."""

    asset_class: str
    exclusion_reason: str | None


def classify_underlying_type(underlying_type: str | None) -> AssetClassification:
    """Classify a symbol from Binance's raw `underlyingType` field alone.

        underlyingType == "COIN"        -> CRYPTO, eligible (no reason)
        underlyingType == "INDEX"       -> NON_ELIGIBLE_INDEX, excluded
        a known non-crypto TradFi type  -> NON_CRYPTO, excluded
        missing or unrecognised         -> UNKNOWN, excluded (fail closed)

    Deterministic and total: every input maps to exactly one of the four
    asset_class values above. Never inspects the symbol string, price,
    volume, or `underlyingSubType` - see the module docstring.
    """
    if underlying_type == _CRYPTO_UNDERLYING_TYPE:
        return AssetClassification(CRYPTO, None)
    if underlying_type == _INDEX_UNDERLYING_TYPE:
        return AssetClassification(NON_ELIGIBLE_INDEX, NON_ELIGIBLE_INDEX)
    if underlying_type in _KNOWN_NON_CRYPTO_UNDERLYING_TYPES:
        return AssetClassification(NON_CRYPTO, NON_CRYPTO_UNDERLYING)
    return AssetClassification(UNKNOWN, UNKNOWN_UNDERLYING_TYPE)
