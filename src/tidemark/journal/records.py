"""Append-only research record.

Every Section 1 evaluation — including every WAIT — is journaled. "No
setups found" is a successful run, not a failure, and is journaled the
same as any other outcome. This module builds the journal row shape from
an evaluated `ContextRecord`; persistence (insert-if-not-exists, fetch,
list) lives in `data/store.py`'s `TidemarkStore`, following the existing
pattern for candles, runs, and context records.
"""

from __future__ import annotations

import datetime as dt

from tidemark.data.models import ContextRecord, JournalEntry


def build_journal_entry(record: ContextRecord, recorded_at: dt.datetime) -> JournalEntry:
    """Build the journal row for one evaluation. Not yet persisted.

    `alert_sent`/`alert_reason` start unset — the change detector and any
    Telegram send happen after the journal write (evaluation -> journal
    -> change detector -> Telegram), and are recorded onto this same row
    afterward via `TidemarkStore.record_alert_outcome`, once known.
    """
    return JournalEntry(
        asset=record.asset,
        evaluated_at=record.evaluated_at,
        recorded_at=recorded_at,
        rule_version=record.rule_version,
        state=record.state,
        watch=record.watch,
        grade=record.grade,
        reason_code=record.reason_code,
        active_levels=record.active_levels,
        fib=record.fib,
        swings_used=record.swings_used,
        alert_sent=False,
        alert_reason=None,
    )
