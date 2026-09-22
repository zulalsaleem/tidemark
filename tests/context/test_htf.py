"""Section 1 evaluator is unimplemented in Phase 0 but importable and callable."""

import pytest

from tidemark.context.htf import RULE_VERSION, evaluate


def test_rule_version_matches_locked_section() -> None:
    assert RULE_VERSION == "1.0"


def test_evaluate_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        evaluate("BTCUSDT", candles_4h=None, atr_value=100.0)
