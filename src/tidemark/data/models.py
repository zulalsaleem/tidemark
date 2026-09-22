"""Persistence models.

These are data structures, not strategy logic — the fields mirror what the
rulebook (`docs/rulebook/`) says must be recorded, not decisions about how
to compute them.

Standing rules encoded here:
  - Every structural point (`Swing`) stores `formed_at` and `confirmed_at`
    separately, since a swing is usable only some time after it forms.
  - Every emitted record (`ContextRecord`) stores the `rule_version` that
    produced it.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    """Declarative base for all Tidemark ORM models."""


class UTCDateTime(TypeDecorator):
    """A datetime column that is always UTC-aware on the Python side.

    SQLite has no native timezone-aware datetime type — values would
    otherwise round-trip as naive datetimes. This stores naive UTC and
    re-attaches `tzinfo=UTC` on read, and refuses to bind a naive input,
    so every timestamp that comes out of the store is UTC-aware.
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


class Candle(Base):
    """A single closed OHLCV candle for one venue/symbol/timeframe.

    Tidemark only ever stores and evaluates closed candles — there is no
    field for an in-progress candle, by design. Mixing venues within one
    symbol's history is not allowed; see
    docs/adr/0002-canonical-market-data-venue.md.
    """

    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint(
            "venue", "symbol", "timeframe", "open_time", name="uq_candles_venue_symbol_tf_open"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venue: Mapped[str] = mapped_column(String, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String, nullable=False, index=True)
    open_time: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    close_time: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    fetched_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)


class RejectedCandle(Base):
    """A candle row that failed sanity checks and was never stored as a
    `Candle`. Tidemark never silently "fixes" bad data — it records the
    rejection with a reason instead.
    """

    __tablename__ = "rejected_candles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venue: Mapped[str] = mapped_column(String, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String, nullable=False, index=True)
    open_time: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    close_time: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    rejected_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)


class Run(Base):
    """One execution of a data-layer command (backfill/update).

    `status` is exactly one of COMPLETED, PARTIAL, or FAILED. A run that
    fetched nothing new because the data was already current is
    COMPLETED, not FAILED.
    """

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    command: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)


class RunSymbolStat(Base):
    """Per symbol/timeframe counts for one `Run`."""

    __tablename__ = "run_symbol_stats"
    __table_args__ = (
        UniqueConstraint("run_id", "symbol", "timeframe", name="uq_run_symbol_stats_run_symbol_tf"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("runs.run_id"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    timeframe: Mapped[str] = mapped_column(String, nullable=False)
    fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicates_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Swing(Base):
    """A fractal swing point (structural high or low).

    `formed_at` is when the fractal pattern completed on the chart;
    `confirmed_at` is when the swing becomes usable per the rulebook's
    confirmation lag (e.g. "usable 8h after formation" in Section 1).
    """

    __tablename__ = "swings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset: Mapped[str] = mapped_column(String, nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # e.g. HH, HL, LH, LL
    price: Mapped[float] = mapped_column(Float, nullable=False)
    formed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fractal_n: Mapped[int] = mapped_column(Integer, nullable=False)


class Level(Base):
    """A horizontal support/resistance level or zone.

    May originate from a swing cluster or from a fixed reference (e.g.
    previous day/week high/low) as defined by the rulebook.
    """

    __tablename__ = "levels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset: Mapped[str] = mapped_column(String, nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # support, resistance
    price: Mapped[float] = mapped_column(Float, nullable=False)
    zone_low: Mapped[float] = mapped_column(Float, nullable=False)
    zone_high: Mapped[float] = mapped_column(Float, nullable=False)
    touches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_major: Mapped[bool] = mapped_column(nullable=False, default=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    formed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ContextRecord(Base):
    """An emitted rulebook evaluation output (Section 1 OUTPUT RECORD).

    Recalculated at every 4H close and consumed by Section 2. Always tagged
    with the `rule_version` that produced it, per the standing rules.
    """

    __tablename__ = "context_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset: Mapped[str] = mapped_column(String, nullable=False, index=True)
    evaluated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    rule_version: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    watch: Mapped[str] = mapped_column(String, nullable=False)
    grade: Mapped[str | None] = mapped_column(String, nullable=True)
    reason_code: Mapped[str] = mapped_column(String, nullable=False)

    # Structured sub-records stored as JSON: active_levels[], fib{...},
    # swings_used[]{formed_at, confirmed_at} — see Section 1 OUTPUT RECORD.
    active_levels: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    fib: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    swings_used: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class JournalEntry(Base):
    """An append-only observation log entry (see `journal/records.py`)."""

    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    context_record_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("context_records.id"), nullable=False
    )
    recorded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rule_version: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str] = mapped_column(String, nullable=False)
