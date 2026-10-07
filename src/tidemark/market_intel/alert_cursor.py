"""The alert enricher's durable cursor: the last journal entry it has
finished with, and which journal entry each sent alert came from.

Two tables in market_intel's own declarative base, never a research table
(`journal_entries`, `observations`, `candles`, ... are never written here).
They live in the same SQLite file as everything else because the database
URL is shared infrastructure, not because the research engine owns them.

Semantics, in full in docs/adr/0012-alert-enricher.md:
- The checkpoint is the id of the last journal entry processed. The
  enricher processes entries with a larger id, in order.
- The checkpoint moves forward only as part of a successful outcome: a
  quiet entry (no alert_reason) moves it past itself, and a sent alert
  moves it past itself in the same transaction that records the send.
  A failed send never moves it.
- Each sent alert is recorded against its journal entry id, unique, so a
  re-run can never send the same alert twice even if the checkpoint were
  somehow lost.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import DateTime, Engine, Integer, String, create_engine, inspect, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.types import TypeDecorator

CHECKPOINT_TABLE = "alert_enrich_checkpoint"
SENT_TABLE = "alert_enrich_sent"
_CHECKPOINT_ROW_ID = 1


class AlertCursorBase(DeclarativeBase):
    """Deliberately separate from `tidemark.data.models.Base`."""


class _UTCDateTime(TypeDecorator):
    """An independent copy of the naive-UTC round-trip used by the other
    market_intel stores, so this module never imports `tidemark.data`.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime given; all stored timestamps must be UTC-aware")
        return value.astimezone(dt.UTC).replace(tzinfo=None)

    def process_result_value(self, value: dt.datetime | None, dialect) -> dt.datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=dt.UTC)


class AlertCheckpoint(AlertCursorBase):
    """A single row (id always 1): the last journal entry id finished with."""

    __tablename__ = CHECKPOINT_TABLE

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_journal_entry_id: Mapped[int] = mapped_column(Integer, nullable=False)
    initialised_at: Mapped[dt.datetime] = mapped_column(_UTCDateTime, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(_UTCDateTime, nullable=False)


class SentAlert(AlertCursorBase):
    """One row per alert actually delivered, keyed to the journal entry it
    describes. `journal_entry_id` is unique: that is the no-double-send
    guarantee, enforced by the database rather than by the caller.
    """

    __tablename__ = SENT_TABLE

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    journal_entry_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    asset: Mapped[str] = mapped_column(String, nullable=False)
    evaluated_at: Mapped[dt.datetime] = mapped_column(_UTCDateTime, nullable=False)
    alert_reason: Mapped[str] = mapped_column(String, nullable=False)
    sent_at: Mapped[dt.datetime] = mapped_column(_UTCDateTime, nullable=False)


@dataclass(frozen=True)
class SentRecord:
    journal_entry_id: int
    asset: str
    evaluated_at: dt.datetime
    alert_reason: str
    sent_at: dt.datetime


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url)


def init_alert_cursor_store(engine: Engine) -> None:
    """Creates this module's two tables and nothing else. Only the send
    path calls this - a dry run must leave the database untouched.
    """
    AlertCursorBase.metadata.create_all(engine)


def _has_table(engine: Engine, name: str) -> bool:
    return inspect(engine).has_table(name)


def get_checkpoint(engine: Engine) -> int | None:
    """The last journal entry id finished with, or None if the enricher has
    never run (no checkpoint row, or no table at all yet).
    """
    if not _has_table(engine, CHECKPOINT_TABLE):
        return None
    with Session(engine) as session:
        row = session.get(AlertCheckpoint, _CHECKPOINT_ROW_ID)
        return None if row is None else row.last_journal_entry_id


def initialise_checkpoint(engine: Engine, journal_entry_id: int, at: dt.datetime) -> None:
    with Session(engine) as session, session.begin():
        session.add(
            AlertCheckpoint(
                id=_CHECKPOINT_ROW_ID,
                last_journal_entry_id=journal_entry_id,
                initialised_at=at,
                updated_at=at,
            )
        )


def advance_checkpoint(engine: Engine, journal_entry_id: int, at: dt.datetime) -> None:
    """Move the checkpoint forward. It never moves backward: an id at or
    below the current checkpoint is a no-op, so a stale caller cannot make
    the enricher re-process history.
    """
    with Session(engine) as session, session.begin():
        row = session.get(AlertCheckpoint, _CHECKPOINT_ROW_ID)
        if row is None:
            raise RuntimeError("alert cursor has no checkpoint; initialise it first")
        if journal_entry_id > row.last_journal_entry_id:
            row.last_journal_entry_id = journal_entry_id
            row.updated_at = at


def is_sent(engine: Engine, journal_entry_id: int) -> bool:
    if not _has_table(engine, SENT_TABLE):
        return False
    with Session(engine) as session:
        stmt = select(SentAlert.id).where(SentAlert.journal_entry_id == journal_entry_id)
        return session.scalars(stmt).first() is not None


def record_sent(
    engine: Engine,
    journal_entry_id: int,
    asset: str,
    evaluated_at: dt.datetime,
    alert_reason: str,
    at: dt.datetime,
) -> None:
    """Record a delivered alert and advance the checkpoint past it, in one
    transaction. Call only after the send has succeeded.
    """
    with Session(engine) as session, session.begin():
        session.add(
            SentAlert(
                journal_entry_id=journal_entry_id,
                asset=asset,
                evaluated_at=evaluated_at,
                alert_reason=alert_reason,
                sent_at=at,
            )
        )
        row = session.get(AlertCheckpoint, _CHECKPOINT_ROW_ID)
        if row is None:
            raise RuntimeError("alert cursor has no checkpoint; initialise it first")
        if journal_entry_id > row.last_journal_entry_id:
            row.last_journal_entry_id = journal_entry_id
            row.updated_at = at


def list_sent(engine: Engine) -> list[SentRecord]:
    if not _has_table(engine, SENT_TABLE):
        return []
    with Session(engine) as session:
        stmt = select(SentAlert).order_by(SentAlert.journal_entry_id)
        return [
            SentRecord(
                journal_entry_id=row.journal_entry_id,
                asset=row.asset,
                evaluated_at=row.evaluated_at,
                alert_reason=row.alert_reason,
                sent_at=row.sent_at,
            )
            for row in session.scalars(stmt)
        ]
