"""market_intel's own persistence: one row per hourly derivatives-context
evaluation, sent or not.

A brand-new table (`market_intel_evaluations`), in its own declarative
Base - deliberately never `tidemark.data.models.Base` - so this
package's schema can never be confused with, or accidentally coupled
to, a research table. It lives in the same SQLite file as
`tidemark.db` (the database URL is shared infrastructure, not a
research table itself), but `journal_entries`, `observations`, and
`context_records` never gain a row from this module, and this module
never reads or writes any of them either - see
docs/adr/0011-market-intelligence-layer.md.

Idempotent like `JournalEntry`: a repeat write for the same
`(asset, evaluated_at)` is a no-op for every classification/input field
- never recomputed, never overwritten - with one narrow exception,
mirroring `JournalEntry.alert_sent`/`alert_reason` exactly: `sent` and
`send_reason` may be updated after the fact, so a dry-run recorded
`sent=False` can later be corrected to `sent=True` once the same hour's
briefing is actually delivered, without ever touching what was
classified.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import DateTime, Engine, Float, String, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator


class MarketIntelBase(DeclarativeBase):
    """market_intel's own declarative base - deliberately separate from
    `tidemark.data.models.Base`."""


class _UTCDateTime(TypeDecorator):
    """A second, independent copy of `data.models.UTCDateTime`'s naive-
    UTC round-tripping - not imported from there, to keep this module's
    only dependency on the research engine the one explicit read in
    `context_read.py`, nothing more.
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


class MarketIntelEvaluation(MarketIntelBase):
    """One hourly evaluation: the classification, the inputs that
    produced it, the BTC structure snapshot used for the structural-
    change check, and whether/why it was sent.
    """

    __tablename__ = "market_intel_evaluations"
    __table_args__ = (
        UniqueConstraint("asset", "evaluated_at", name="uq_market_intel_eval_asset_evaluated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset: Mapped[str] = mapped_column(String, nullable=False, index=True)
    evaluated_at: Mapped[dt.datetime] = mapped_column(_UTCDateTime, nullable=False, index=True)
    recorded_at: Mapped[dt.datetime] = mapped_column(_UTCDateTime, nullable=False)
    rulebook_version: Mapped[str] = mapped_column(String, nullable=False)

    classification: Mapped[str] = mapped_column(String, nullable=False)
    classification_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    price_status: Mapped[str] = mapped_column(String, nullable=False)
    price_change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_period_start: Mapped[dt.datetime | None] = mapped_column(_UTCDateTime, nullable=True)
    price_period_close: Mapped[dt.datetime | None] = mapped_column(_UTCDateTime, nullable=True)

    oi_status: Mapped[str] = mapped_column(String, nullable=False)
    oi_change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi_period_start: Mapped[dt.datetime | None] = mapped_column(_UTCDateTime, nullable=True)
    oi_period_close: Mapped[dt.datetime | None] = mapped_column(_UTCDateTime, nullable=True)

    funding_status: Mapped[str] = mapped_column(String, nullable=False)
    funding_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding_previous_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding_period_start: Mapped[dt.datetime | None] = mapped_column(_UTCDateTime, nullable=True)
    funding_period_close: Mapped[dt.datetime | None] = mapped_column(_UTCDateTime, nullable=True)

    structure_available: Mapped[bool] = mapped_column(nullable=False, default=False)
    structure_state: Mapped[str | None] = mapped_column(String, nullable=True)
    structure_watch: Mapped[str | None] = mapped_column(String, nullable=True)
    structure_grade: Mapped[str | None] = mapped_column(String, nullable=True)
    structure_rule_version: Mapped[str | None] = mapped_column(String, nullable=True)
    structure_evaluated_at: Mapped[dt.datetime | None] = mapped_column(_UTCDateTime, nullable=True)

    sent: Mapped[bool] = mapped_column(nullable=False, default=False)
    send_reason: Mapped[str | None] = mapped_column(String, nullable=True)


def init_evaluation_store(engine: Engine) -> None:
    """Create `market_intel_evaluations` if it doesn't exist yet. Never
    touches any table in `tidemark.data.models.Base` - a disjoint
    metadata registry by construction.
    """
    MarketIntelBase.metadata.create_all(engine)


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url)


@dataclass(frozen=True)
class EvaluationRecord:
    """Plain data in; `record_evaluation` is the only place this becomes a row."""

    asset: str
    evaluated_at: dt.datetime
    recorded_at: dt.datetime
    rulebook_version: str
    classification: str
    classification_reason: str | None
    price_status: str
    price_change_pct: float | None
    price_period_start: dt.datetime | None
    price_period_close: dt.datetime | None
    oi_status: str
    oi_change_pct: float | None
    oi_period_start: dt.datetime | None
    oi_period_close: dt.datetime | None
    funding_status: str
    funding_value: float | None
    funding_previous_value: float | None
    funding_period_start: dt.datetime | None
    funding_period_close: dt.datetime | None
    structure_available: bool
    structure_state: str | None
    structure_watch: str | None
    structure_grade: str | None
    structure_rule_version: str | None
    structure_evaluated_at: dt.datetime | None
    sent: bool
    send_reason: str | None


def record_evaluation(engine: Engine, record: EvaluationRecord) -> None:
    """Insert a new evaluation, or - if one is already recorded for this
    `(asset, evaluated_at)` - update only `sent`/`send_reason`. Every
    other field is fixed at first insert, exactly like
    `JournalEntry.alert_sent`/`alert_reason`.
    """
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        existing = session.scalars(
            select(MarketIntelEvaluation).where(
                MarketIntelEvaluation.asset == record.asset,
                MarketIntelEvaluation.evaluated_at == record.evaluated_at,
            )
        ).first()
        if existing is not None:
            existing.sent = record.sent
            existing.send_reason = record.send_reason
            session.commit()
            return

        session.add(
            MarketIntelEvaluation(
                asset=record.asset,
                evaluated_at=record.evaluated_at,
                recorded_at=record.recorded_at,
                rulebook_version=record.rulebook_version,
                classification=record.classification,
                classification_reason=record.classification_reason,
                price_status=record.price_status,
                price_change_pct=record.price_change_pct,
                price_period_start=record.price_period_start,
                price_period_close=record.price_period_close,
                oi_status=record.oi_status,
                oi_change_pct=record.oi_change_pct,
                oi_period_start=record.oi_period_start,
                oi_period_close=record.oi_period_close,
                funding_status=record.funding_status,
                funding_value=record.funding_value,
                funding_previous_value=record.funding_previous_value,
                funding_period_start=record.funding_period_start,
                funding_period_close=record.funding_period_close,
                structure_available=record.structure_available,
                structure_state=record.structure_state,
                structure_watch=record.structure_watch,
                structure_grade=record.structure_grade,
                structure_rule_version=record.structure_rule_version,
                structure_evaluated_at=record.structure_evaluated_at,
                sent=record.sent,
                send_reason=record.send_reason,
            )
        )
        session.commit()


def latest_evaluation_before(
    engine: Engine, asset: str, before: dt.datetime
) -> MarketIntelEvaluation | None:
    """The most recent evaluation strictly before `before` - used to
    decide whether the classification "changed" from the immediately
    prior evaluation (sent or not), mirroring `journal.changes`'s own
    "compare against the previous recorded row" pattern.
    """
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        stmt = (
            select(MarketIntelEvaluation)
            .where(
                MarketIntelEvaluation.asset == asset, MarketIntelEvaluation.evaluated_at < before
            )
            .order_by(MarketIntelEvaluation.evaluated_at.desc())
            .limit(1)
        )
        return session.scalars(stmt).first()


def latest_sent_evaluation_before(
    engine: Engine, asset: str, before: dt.datetime
) -> MarketIntelEvaluation | None:
    """The most recent SENT evaluation strictly before `before` - used
    for the structural-change comparison, which the merge scopes to
    "what the last SENT briefing carried", not the last evaluation.
    """
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        stmt = (
            select(MarketIntelEvaluation)
            .where(
                MarketIntelEvaluation.asset == asset,
                MarketIntelEvaluation.evaluated_at < before,
                MarketIntelEvaluation.sent.is_(True),
            )
            .order_by(MarketIntelEvaluation.evaluated_at.desc())
            .limit(1)
        )
        return session.scalars(stmt).first()
