"""Asset-class domain constraint (Phase 6, UNIV-08). Pure function, no
store/network - see the Phase 6 asset-class inspection report for the
live-data evidence this classification is built from.
"""

from __future__ import annotations

from tidemark.data.asset_class import (
    CRYPTO,
    NON_CRYPTO,
    NON_CRYPTO_UNDERLYING,
    NON_ELIGIBLE_INDEX,
    UNKNOWN,
    UNKNOWN_UNDERLYING_TYPE,
    classify_underlying_type,
)


def test_coin_is_crypto_and_eligible() -> None:
    result = classify_underlying_type("COIN")
    assert result.asset_class == CRYPTO
    assert result.exclusion_reason is None


def test_index_is_excluded_as_non_eligible_index() -> None:
    result = classify_underlying_type("INDEX")
    assert result.asset_class == NON_ELIGIBLE_INDEX
    assert result.exclusion_reason == NON_ELIGIBLE_INDEX


def test_premarket_is_excluded_as_non_crypto() -> None:
    result = classify_underlying_type("PREMARKET")
    assert result.asset_class == NON_CRYPTO
    assert result.exclusion_reason == NON_CRYPTO_UNDERLYING


def test_equity_is_excluded_as_non_crypto() -> None:
    result = classify_underlying_type("EQUITY")
    assert result.asset_class == NON_CRYPTO
    assert result.exclusion_reason == NON_CRYPTO_UNDERLYING


def test_commodity_is_excluded_as_non_crypto() -> None:
    result = classify_underlying_type("COMMODITY")
    assert result.asset_class == NON_CRYPTO
    assert result.exclusion_reason == NON_CRYPTO_UNDERLYING


def test_fx_is_excluded_as_non_crypto() -> None:
    result = classify_underlying_type("FX")
    assert result.asset_class == NON_CRYPTO
    assert result.exclusion_reason == NON_CRYPTO_UNDERLYING


def test_kr_equity_is_excluded_as_non_crypto() -> None:
    result = classify_underlying_type("KR_EQUITY")
    assert result.asset_class == NON_CRYPTO
    assert result.exclusion_reason == NON_CRYPTO_UNDERLYING


def test_hk_equity_is_excluded_as_non_crypto() -> None:
    result = classify_underlying_type("HK_EQUITY")
    assert result.asset_class == NON_CRYPTO
    assert result.exclusion_reason == NON_CRYPTO_UNDERLYING


def test_cn_equity_is_excluded_as_non_crypto() -> None:
    result = classify_underlying_type("CN_EQUITY")
    assert result.asset_class == NON_CRYPTO
    assert result.exclusion_reason == NON_CRYPTO_UNDERLYING


def test_missing_underlying_type_gives_unknown_and_is_excluded() -> None:
    result = classify_underlying_type(None)
    assert result.asset_class == UNKNOWN
    assert result.exclusion_reason == UNKNOWN_UNDERLYING_TYPE


def test_unrecognised_underlying_type_gives_unknown_and_is_excluded() -> None:
    """A value this module has never been told about - a Binance typo, a
    future category - must fail closed to UNKNOWN, never be silently
    absorbed into NON_CRYPTO."""
    result = classify_underlying_type("SOMETHING_NEW_BINANCE_ADDS_LATER")
    assert result.asset_class == UNKNOWN
    assert result.exclusion_reason == UNKNOWN_UNDERLYING_TYPE


def test_empty_string_underlying_type_gives_unknown() -> None:
    result = classify_underlying_type("")
    assert result.asset_class == UNKNOWN
    assert result.exclusion_reason == UNKNOWN_UNDERLYING_TYPE


def test_non_ascii_ticker_with_coin_underlying_type_is_eligible() -> None:
    """The 5 real CJK-ticker meme-coin perpetuals found in Merge 2A all
    carry underlyingType=COIN - the classifier must not special-case or
    drop them. The classifier itself takes no symbol argument at all, so
    this also proves it cannot possibly look at the ticker string."""
    result = classify_underlying_type("COIN")
    assert result.asset_class == CRYPTO
    assert result.exclusion_reason is None


def test_classifier_signature_takes_no_symbol_name() -> None:
    """The classifier ignores symbol names entirely - enforced structurally,
    not just by convention: its only parameter is underlying_type."""
    import inspect

    params = list(inspect.signature(classify_underlying_type).parameters)
    assert params == ["underlying_type"]


def test_classification_is_deterministic() -> None:
    first = classify_underlying_type("COIN")
    second = classify_underlying_type("COIN")
    assert first == second
