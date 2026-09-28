"""market_intel's own persistence for `tidemark intel refresh-context`:
one row per refresh, holding the whole universe's distribution summary
for long/short ratio, funding rate, OI 1H % change, and buy/sell volume
ratio - exactly what `intel distributions` measures, cached so `/coin`
can show a fast local comparison instead of a ~189s live universe scan.

A brand-new table (`universe_context_cache`), in its own declarative
Base - deliberately never `tidemark.data.models.Base`, and deliberately
its own Base rather than reusing `evaluation_store.MarketIntelBase`, so
this module stays exactly as self-contained as every other market_intel
storage module (see `evaluation_store.py`'s own `_UTCDateTime` comment
for why duplication is preferred over cross-module coupling here). It
lives in the same SQLite file as `tidemark.db`, but never reads or
writes `journal_entries`, `observations`, `context_records`, or
`universe_snapshot`/`universe_snapshot_row` - it only ever records the
`universe_snapshot_id` string `intel distributions`/`universe_read.py`
already produced, never a foreign key or a join back to that table.

Append-only, unlike `MarketIntelEvaluation`: a refresh always inserts a
new row, never updates an existing one. `tidemark intel refresh-context`
is the only writer - `intel distributions` itself stays fully read-only
and unchanged, per its own standing constraint.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import DateTime, Engine, Float, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

METRIC_NAMES = ("long_short_ratio", "funding_rate", "oi_change_pct", "buy_sell_ratio")


class UniverseContextBase(DeclarativeBase):
    """market_intel's own declarative base for this table - deliberately
    separate from both `tidemark.data.models.Base` and
    `evaluation_store.MarketIntelBase`."""


class _UTCDateTime(TypeDecorator):
    """A third, independent copy of `data.models.UTCDateTime`'s naive-UTC
    round-tripping - see `evaluation_store._UTCDateTime`'s docstring for
    why this is duplicated rather than shared.
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


class UniverseContextCache(UniverseContextBase):
    """One `intel distributions` measurement, cached. `universe_snapshot_id`
    is recorded (not joined - see module docstring) so a cached summary
    can always be traced to the cohort it was computed from, since
    universe membership changes daily.
    """

    __tablename__ = "universe_context_cache"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    computed_at: Mapped[dt.datetime] = mapped_column(_UTCDateTime, nullable=False, index=True)
    universe_snapshot_id: Mapped[str] = mapped_column(String, nullable=False)
    period_start: Mapped[dt.datetime] = mapped_column(_UTCDateTime, nullable=False)
    period_end: Mapped[dt.datetime] = mapped_column(_UTCDateTime, nullable=False)

    long_short_ratio_n: Mapped[int] = mapped_column(Integer, nullable=False)
    long_short_ratio_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    long_short_ratio_p25: Mapped[float | None] = mapped_column(Float, nullable=True)
    long_short_ratio_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    long_short_ratio_p75: Mapped[float | None] = mapped_column(Float, nullable=True)
    long_short_ratio_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    long_short_ratio_unavailable_count: Mapped[int] = mapped_column(Integer, nullable=False)

    funding_rate_n: Mapped[int] = mapped_column(Integer, nullable=False)
    funding_rate_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding_rate_p25: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding_rate_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding_rate_p75: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding_rate_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding_rate_unavailable_count: Mapped[int] = mapped_column(Integer, nullable=False)

    oi_change_pct_n: Mapped[int] = mapped_column(Integer, nullable=False)
    oi_change_pct_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi_change_pct_p25: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi_change_pct_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi_change_pct_p75: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi_change_pct_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi_change_pct_unavailable_count: Mapped[int] = mapped_column(Integer, nullable=False)

    buy_sell_ratio_n: Mapped[int] = mapped_column(Integer, nullable=False)
    buy_sell_ratio_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_sell_ratio_p25: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_sell_ratio_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_sell_ratio_p75: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_sell_ratio_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_sell_ratio_unavailable_count: Mapped[int] = mapped_column(Integer, nullable=False)


def init_universe_context_store(engine: Engine) -> None:
    """Create `universe_context_cache` if it doesn't exist yet. Never
    touches any table in `tidemark.data.models.Base` or
    `evaluation_store.MarketIntelBase` - a disjoint metadata registry by
    construction.
    """
    UniverseContextBase.metadata.create_all(engine)


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url)


@dataclass(frozen=True)
class MetricSummaryFields:
    n: int
    min: float | None
    p25: float | None
    median: float | None
    p75: float | None
    max: float | None
    unavailable_count: int


@dataclass(frozen=True)
class UniverseContextRecord:
    """Plain data in; `record_universe_context` is the only place this
    becomes a row. `metrics` keys are exactly `METRIC_NAMES`.
    """

    computed_at: dt.datetime
    universe_snapshot_id: str
    period_start: dt.datetime
    period_end: dt.datetime
    metrics: dict[str, MetricSummaryFields]


def record_universe_context(engine: Engine, record: UniverseContextRecord) -> None:
    """Insert one new row. Always an insert - this table is append-only,
    never updated or de-duplicated; a scheduled refresh that runs twice
    in the same hour simply produces two rows.
    """
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        fields = {
            "computed_at": record.computed_at,
            "universe_snapshot_id": record.universe_snapshot_id,
            "period_start": record.period_start,
            "period_end": record.period_end,
        }
        for name in METRIC_NAMES:
            summary = record.metrics[name]
            fields[f"{name}_n"] = summary.n
            fields[f"{name}_min"] = summary.min
            fields[f"{name}_p25"] = summary.p25
            fields[f"{name}_median"] = summary.median
            fields[f"{name}_p75"] = summary.p75
            fields[f"{name}_max"] = summary.max
            fields[f"{name}_unavailable_count"] = summary.unavailable_count
        session.add(UniverseContextCache(**fields))
        session.commit()


def latest_universe_context(engine: Engine) -> UniverseContextCache | None:
    """The most recently computed row, or `None` if the table is empty
    (including a fresh database that has never been refreshed) - a fast
    local read, no Coinalyze call.
    """
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        stmt = (
            select(UniverseContextCache).order_by(UniverseContextCache.computed_at.desc()).limit(1)
        )
        return session.scalars(stmt).first()
