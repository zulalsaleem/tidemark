"""universe_context_store.py: market_intel's own table, never a research
one, and never `market_intel_evaluations` either. Append-only: a repeat
write always inserts a new row, never updates an existing one.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import inspect
from sqlalchemy.orm import sessionmaker

from tidemark.market_intel.universe_context_store import (
    METRIC_NAMES,
    MetricSummaryFields,
    UniverseContextCache,
    UniverseContextRecord,
    init_universe_context_store,
    latest_universe_context,
    make_engine,
    record_universe_context,
)

T1 = dt.datetime(2026, 9, 28, 7, 0, tzinfo=dt.UTC)
T2 = dt.datetime(2026, 9, 28, 13, 0, tzinfo=dt.UTC)
PERIOD_START = dt.datetime(2026, 9, 28, 6, 0, tzinfo=dt.UTC)
PERIOD_END = dt.datetime(2026, 9, 28, 7, 0, tzinfo=dt.UTC)


def _summary(**overrides) -> MetricSummaryFields:
    defaults = dict(n=30, min=0.5, p25=1.2, median=1.7, p75=2.1, max=2.8, unavailable_count=0)
    defaults.update(overrides)
    return MetricSummaryFields(**defaults)


def _record(computed_at: dt.datetime, **overrides) -> UniverseContextRecord:
    defaults = dict(
        computed_at=computed_at,
        universe_snapshot_id="snap-1",
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        metrics={name: _summary() for name in METRIC_NAMES},
    )
    defaults.update(overrides)
    return UniverseContextRecord(**defaults)


def _engine(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'tidemark.db').as_posix()}")
    init_universe_context_store(engine)
    return engine


def test_the_table_is_named_distinctly_from_every_other_table(tmp_path) -> None:
    engine = _engine(tmp_path)
    tables = set(inspect(engine).get_table_names())

    assert "universe_context_cache" in tables
    for other_table in (
        "journal_entries",
        "observations",
        "candles",
        "market_intel_evaluations",
        "universe_snapshot",
    ):
        assert other_table not in tables


def test_record_persists_the_snapshot_id_and_period(tmp_path) -> None:
    engine = _engine(tmp_path)
    record_universe_context(engine, _record(T1, universe_snapshot_id="snap-abc"))

    row = latest_universe_context(engine)

    assert row is not None
    assert row.universe_snapshot_id == "snap-abc"
    assert row.period_start == PERIOD_START
    assert row.period_end == PERIOD_END
    assert row.computed_at == T1


def test_record_persists_every_metric_field(tmp_path) -> None:
    engine = _engine(tmp_path)
    record_universe_context(
        engine,
        _record(
            T1,
            metrics={
                "long_short_ratio": _summary(n=30, median=1.742, p75=2.125),
                "funding_rate": _summary(n=28, median=0.005, p75=0.01, unavailable_count=2),
                "oi_change_pct": _summary(n=30, median=0.5, p75=1.2),
                "buy_sell_ratio": _summary(n=30, median=0.797, p75=0.944),
            },
        ),
    )

    row = latest_universe_context(engine)

    assert row.long_short_ratio_n == 30
    assert row.long_short_ratio_median == 1.742
    assert row.long_short_ratio_p75 == 2.125
    assert row.funding_rate_n == 28
    assert row.funding_rate_unavailable_count == 2
    assert row.oi_change_pct_median == 0.5
    assert row.buy_sell_ratio_median == 0.797


def test_a_metric_with_zero_n_stores_none_not_zero(tmp_path) -> None:
    engine = _engine(tmp_path)
    empty = MetricSummaryFields(
        n=0, min=None, p25=None, median=None, p75=None, max=None, unavailable_count=30
    )
    record_universe_context(engine, _record(T1, metrics={name: empty for name in METRIC_NAMES}))

    row = latest_universe_context(engine)

    assert row.long_short_ratio_n == 0
    assert row.long_short_ratio_median is None  # never fabricated as 0
    assert row.long_short_ratio_unavailable_count == 30


def test_repeat_writes_insert_a_new_row_never_update(tmp_path) -> None:
    engine = _engine(tmp_path)
    record_universe_context(engine, _record(T1, universe_snapshot_id="snap-morning"))
    record_universe_context(engine, _record(T2, universe_snapshot_id="snap-afternoon"))

    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        count = session.query(UniverseContextCache).count()
    assert count == 2  # append-only, both rows kept


def test_latest_returns_the_most_recently_computed_row(tmp_path) -> None:
    engine = _engine(tmp_path)
    record_universe_context(engine, _record(T1, universe_snapshot_id="snap-morning"))
    record_universe_context(engine, _record(T2, universe_snapshot_id="snap-afternoon"))

    row = latest_universe_context(engine)

    assert row.universe_snapshot_id == "snap-afternoon"


def test_latest_returns_none_on_a_fresh_table(tmp_path) -> None:
    engine = _engine(tmp_path)
    assert latest_universe_context(engine) is None
