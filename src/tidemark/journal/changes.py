"""Change detector: decides whether an evaluation is worth alerting on.

Pure function, no I/O — `detect_change` takes the previous journal row
for an asset and the current evaluation and returns either `None` or an
alert reason. It never touches the database, the network, or the clock;
the caller is responsible for fetching the previous row and for anything
that happens with the returned reason (Telegram, logging, etc).
"""

from __future__ import annotations

from tidemark.context.htf import (
    LONG_WATCH,
    SHORT_WATCH,
    STRUCTURE_BROKEN_BEAR,
    STRUCTURE_BROKEN_BULL,
    WAIT,
)
from tidemark.data.models import ContextRecord, JournalEntry

_BROKEN_STATES = (STRUCTURE_BROKEN_BULL, STRUCTURE_BROKEN_BEAR)

WATCH_OPENED = "WATCH_OPENED"
GRADE_UPGRADED = "GRADE_UPGRADED"
GRADE_DOWNGRADED = "GRADE_DOWNGRADED"
WATCH_FLIPPED = "WATCH_FLIPPED"
WATCH_CLOSED = "WATCH_CLOSED"
STRUCTURE_BROKEN = "STRUCTURE_BROKEN"
STRUCTURE_RESOLVED = "STRUCTURE_RESOLVED"

_WATCH_STATES = (LONG_WATCH, SHORT_WATCH)


def detect_change(previous: JournalEntry | None, current: ContextRecord) -> str | None:
    """Return an alert reason for `current` relative to `previous`, or None.

    `previous` is the most recent journal row for this asset at the same
    rule_version (never across a rule_version change — see
    `journal/records.py`). `None` means this is the first evaluation on
    record for this asset/rule_version: journal-only, never an alert.

    Only compares state, watch, and grade. A level's price or a fib
    leg's values changing while state/watch/grade hold steady is not a
    change worth alerting on.
    """
    if previous is None:
        return None

    prev_state, prev_watch, prev_grade = previous.state, previous.watch, previous.grade
    cur_state, cur_watch, cur_grade = current.state, current.watch, current.grade

    if prev_state == cur_state and prev_watch == cur_watch and prev_grade == cur_grade:
        return None

    # State-level transitions take priority over watch-level ones: a
    # break happens to always carry watch=WAIT, so without this order a
    # break out of an open watch would look like a mere WATCH_CLOSED.
    #
    # cur_state in _BROKEN_STATES also covers STRUCTURE_BROKEN_BULL ->
    # STRUCTURE_BROKEN_BEAR (and the mirror): a different broken state is
    # still reported as STRUCTURE_BROKEN, not STRUCTURE_RESOLVED. This
    # transition is possible because context/htf.py's state machine can
    # recompute bias and immediately re-break within the same candle
    # (broken -> a new post-break swing confirms -> bias recomputes ->
    # the same candle's close also breaches the newly-computed watched
    # level). "Any state -> STRUCTURE_BROKEN_BULL/_BEAR" reads as "which
    # structure is broken right now", not "structure just resolved" -
    # structure never stopped being broken here, it just broke in the
    # other direction, so STRUCTURE_RESOLVED (which means "no longer
    # broken") would misdescribe it. The all-unchanged check above
    # already excludes the case where cur_state equals prev_state, so by
    # construction this branch is only reached when the broken direction
    # actually changed.
    if cur_state in _BROKEN_STATES:
        return STRUCTURE_BROKEN
    if prev_state in _BROKEN_STATES:
        return STRUCTURE_RESOLVED

    if prev_watch == WAIT and cur_watch in _WATCH_STATES:
        return WATCH_OPENED
    if prev_watch in _WATCH_STATES and cur_watch == WAIT:
        return WATCH_CLOSED
    if prev_watch in _WATCH_STATES and cur_watch in _WATCH_STATES and prev_watch != cur_watch:
        return WATCH_FLIPPED
    if prev_watch in _WATCH_STATES and cur_watch in _WATCH_STATES:
        if prev_grade == "B" and cur_grade == "A":
            return GRADE_UPGRADED
        if prev_grade == "A" and cur_grade == "B":
            return GRADE_DOWNGRADED

    return None
