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

    `status` starts as the transient RUNNING and ends at exactly one of
    COMPLETED, PARTIAL, or FAILED. A run that fetched nothing new because
    the data was already current is COMPLETED, not FAILED. `finished_at`
    is null while a run is still RUNNING.
    """

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    command: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
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
    formed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    confirmed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    fractal_n: Mapped[int] = mapped_column(Integer, nullable=False)


class Level(Base):
    """A horizontal level or zone.

    May originate from a swing cluster or from a fixed reference (e.g.
    previous day/week high/low) as defined by the rulebook. `source`
    records that origin (e.g. `swing_high_cluster`, `prev_week_low`) — a
    permanent fact about the level. There is no support/resistance
    column: Section 1 v1.1 (RULE 1.7a) makes a level's role dynamic,
    evaluated fresh from its price and the current close at every
    evaluation (`core.levels.level_role`), and explicitly never stored
    from formation.
    """

    __tablename__ = "levels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset: Mapped[str] = mapped_column(String, nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String, nullable=False, index=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    zone_low: Mapped[float] = mapped_column(Float, nullable=False)
    zone_high: Mapped[float] = mapped_column(Float, nullable=False)
    touches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_major: Mapped[bool] = mapped_column(nullable=False, default=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    formed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)


class ContextRecord(Base):
    """An emitted rulebook evaluation output (Section 1 OUTPUT RECORD).

    Recalculated at every 4H close and consumed by Section 2. Always tagged
    with the `rule_version` that produced it, per the standing rules.
    """

    __tablename__ = "context_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset: Mapped[str] = mapped_column(String, nullable=False, index=True)
    evaluated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
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
    """An append-only research record: one row per Section 1 evaluation,
    including every WAIT (see `journal/records.py`).

    Never updated after insert, with one narrow exception: `alert_sent`
    and `alert_reason`, set once the change detector and Telegram send
    (if any) have run for this evaluation — never the evaluation fields
    themselves (state, watch, grade, reason_code, active_levels, fib,
    swings_used), which are fixed at insert time.
    """

    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint(
            "asset", "evaluated_at", "rule_version", name="uq_journal_asset_evaluated_at_rule"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset: Mapped[str] = mapped_column(String, nullable=False, index=True)
    evaluated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    recorded_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    rule_version: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    watch: Mapped[str] = mapped_column(String, nullable=False)
    grade: Mapped[str | None] = mapped_column(String, nullable=True)
    reason_code: Mapped[str] = mapped_column(String, nullable=False)
    active_levels: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    fib: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    swings_used: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    alert_sent: Mapped[bool] = mapped_column(nullable=False, default=False)
    alert_reason: Mapped[str | None] = mapped_column(String, nullable=True)


class Observation(Base):
    """An append-only Section 2 (1H) observation row — see
    `docs/rulebook/section-02-1h-behaviour-v0.1.md`, PROVISIONAL /
    OBSERVATION ONLY, and `context/mtf.py`.

    One row per closed 1H candle per asset while under an active Section
    1 WATCH, including every no-reaction evaluation — the negative cases
    are the measurement. Distinct from `journal_entries` (the Section 1
    research record): this is a separate table for a separate, purely
    observational purpose, never read by the change detector or
    notifier.
    """

    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint(
            "asset", "evaluated_at", "rule_version", name="uq_observations_asset_evaluated_at_rule"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset: Mapped[str] = mapped_column(String, nullable=False, index=True)
    evaluated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    rule_version: Mapped[str] = mapped_column(String, nullable=False)

    section_1_state: Mapped[str] = mapped_column(String, nullable=False)
    section_1_watch: Mapped[str] = mapped_column(String, nullable=False)
    section_1_grade: Mapped[str | None] = mapped_column(String, nullable=True)
    section_1_level_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    interaction_detected: Mapped[bool] = mapped_column(nullable=False, default=False)
    reaction_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    reaction_condition_matched: Mapped[str | None] = mapped_column(String, nullable=True)
    reaction_started_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)

    structure_reference_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    structure_reference_confirmed_at: Mapped[dt.datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )

    structure_change: Mapped[str | None] = mapped_column(String, nullable=True)
    failure: Mapped[str | None] = mapped_column(String, nullable=True)
    expiry: Mapped[bool] = mapped_column(nullable=False, default=False)

    state: Mapped[str] = mapped_column(String, nullable=False)
    reason_code: Mapped[str] = mapped_column(String, nullable=False)

    # swings_used[]{kind, price, formed_at, confirmed_at} - mirrors the
    # Section 1 OUTPUT RECORD's swings_used shape.
    swings_used: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
