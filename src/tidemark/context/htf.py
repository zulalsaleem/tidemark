"""Section 1 — HTF context (4H).

Implements `docs/rulebook/section-01-htf-context-v1.0.md`, which is
LOCKED. This module must implement exactly what that document specifies —
nothing more, nothing tuned, nothing inferred. If a future rulebook change
is needed, a new version file is added; this module then targets that new
version rather than editing the old one in place.
"""

from __future__ import annotations

from tidemark.data.models import ContextRecord

RULE_VERSION = "1.0"


def evaluate(asset: str, candles_4h, atr_value: float) -> ContextRecord:
    """Evaluate Section 1's decision matrix against closed 4H candles.

    Recalculated at every 4H close. `candles_4h` must contain closed
    candles only, ordered oldest to newest.

    Not implemented in Phase 0.
    """
    raise NotImplementedError
