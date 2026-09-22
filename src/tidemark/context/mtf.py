"""Section 2 — 1H behavior (placeholder, PENDING).

`docs/rulebook/section-02-1h-behavior-DRAFT.md` is still in extraction —
every state is PENDING and two parameters are open (1H swing fractal N,
and the definition of "at/around the HTF area" on 1H). This module must
not guess those values. It stays unimplemented until Section 2 is
registered and locked.
"""

from __future__ import annotations


def evaluate(asset: str, htf_context, candles_1h):
    """Evaluate Section 2 behavior states against the current HTF context.

    Not implemented: Section 2 of the rulebook is still a draft with
    PENDING states and open parameters.
    """
    raise NotImplementedError(
        "Section 2 (1H behavior) is DRAFT / PENDING — see "
        "docs/rulebook/section-02-1h-behavior-DRAFT.md"
    )
