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

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all Tidemark ORM models."""


class Candle(Base):
    """A single closed OHLCV candle for one asset/timeframe.

    Tidemark only ever stores and evaluates closed candles — there is no
    field for an in-progress candle, by design.
    """

    __tablename__ = "candles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset: Mapped[str] = mapped_column(String, nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String, nullable=False, index=True)
    open_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_time: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)


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
