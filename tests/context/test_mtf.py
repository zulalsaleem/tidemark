"""Section 2 is DRAFT/PENDING and must refuse to guess, not just be unwritten."""

import pytest

from tidemark.context.mtf import evaluate


def test_evaluate_refuses_because_rulebook_is_draft() -> None:
    with pytest.raises(NotImplementedError, match="DRAFT"):
        evaluate("BTCUSDT", htf_context=None, candles_1h=None)
