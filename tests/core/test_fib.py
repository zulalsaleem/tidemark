"""Fib leg/zone logic is unimplemented in Phase 0 but importable and callable."""

import pytest

from tidemark.core.fib import find_valid_leg, in_fib_zone


def test_find_valid_leg_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        find_valid_leg([], atr_value=100.0)


def test_in_fib_zone_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        in_fib_zone(100.0, leg=None)
