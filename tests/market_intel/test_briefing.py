"""evaluate_briefing: the full hourly cycle - classify, read BTC
structure (real database, real journal_entries table - the table
`tidemark run` actually writes, per ADR 0011's Merge 3 addendum), decide
send, record every evaluation whether or not it sends, and never touch a
research table beyond the one permitted read.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from tidemark.data.models import ContextRecord, JournalEntry
from tidemark.data.store import TidemarkStore, create_store_engine, init_db
from tidemark.journal.records import build_journal_entry
from tidemark.market_intel.briefing import (
    CLASSIFICATION_CHANGED,
    STRUCTURAL_CHANGE,
    evaluate_briefing,
    mark_sent,
)
from tidemark.market_intel.evaluation_store import (
    init_evaluation_store,
    latest_evaluation_before,
    make_engine,
)
from tidemark.market_intel.future_markets import FutureMarketsCache

VENUE = "binanceusdm"
ASSET = "BTC/USDT:USDT"
COINALYZE_SYMBOL = "BTCUSDT_PERP.A"

HOUR1 = dt.datetime(2026, 9, 27, 19, 5, tzinfo=dt.UTC)  # closed period 18:00-19:00
HOUR2 = dt.datetime(2026, 9, 27, 20, 5, tzinfo=dt.UTC)  # closed period 19:00-20:00


def _market_row(**overrides) -> dict:
    base = {
        "symbol": COINALYZE_SYMBOL,
        "exchange": "A",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "is_perpetual": True,
        "has_long_short_ratio_data": True,
        "has_ohlcv_data": True,
        "has_buy_sell_data": True,
        "oi_lq_vol_denominated_in": "BASE_ASSET",
    }
    base.update(overrides)
    return base


def _history_response(period_start_epoch: int, **fields) -> list[dict]:
    return [{"symbol": COINALYZE_SYMBOL, "history": [{"t": period_start_epoch, **fields}]}]


class _FakeClient:
    """Drives D1 (price up, OI up, funding positive) by default; override
    per-test via the constructor args."""

    def __init__(
        self,
        listed: bool = True,
        price_ohlcv: dict | None = None,
        oi_bucket: dict | None = None,
        funding_history: list[dict] | None = None,
    ) -> None:
        self._listed = listed
        self._price_ohlcv = price_ohlcv
        self._oi_bucket = oi_bucket
        self._funding_history = funding_history

    def future_markets(self):
        return [_market_row()] if self._listed else []

    def ohlcv_history(self, symbol, interval, from_ts, to_ts):
        bucket = self._price_ohlcv or {"o": 100.0, "h": 101.0, "l": 99.0, "c": 101.0}  # +1%
        return _history_response(from_ts, **bucket)

    def open_interest_history(self, symbol, interval, from_ts, to_ts, convert_to_usd=True):
        bucket = self._oi_bucket or {"o": 1000.0, "h": 1010.0, "l": 995.0, "c": 1010.0}  # +1%
        return _history_response(from_ts, **bucket)

    def funding_rate_history(self, symbol, interval, from_ts, to_ts):
        if self._funding_history is not None:
            return self._funding_history
        return [
            {
                "symbol": COINALYZE_SYMBOL,
                "history": [
                    {"t": from_ts, "o": 0.005, "h": 0.006, "l": 0.004, "c": 0.005},
                    {"t": to_ts + 1 - 3600, "o": 0.005, "h": 0.011, "l": 0.005, "c": 0.01},
                ],
            }
        ]


def _cache(client: _FakeClient) -> FutureMarketsCache:
    return FutureMarketsCache(client)


def _dbs(tmp_path):
    db_path = (tmp_path / "tidemark.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    research_engine = create_store_engine(database_url)
    init_db(research_engine)
    evaluation_engine = make_engine(database_url)
    init_evaluation_store(evaluation_engine)
    return database_url, research_engine, evaluation_engine


def _save_structure(research_engine, evaluated_at, **overrides) -> None:
    """Writes a Section 1 result the way `tidemark run` actually does:
    an in-memory `ContextRecord` from `htf.evaluate`, converted via
    `build_journal_entry` and persisted with `save_journal_entry` - never
    a direct `context_records` write, which `tidemark run` never performs
    (see ADR 0011's Merge 3 addendum for the investigation that found
    this).
    """
    defaults = dict(
        asset=ASSET,
        evaluated_at=evaluated_at,
        rule_version="section-01-v1.1",
        state="BULLISH",
        watch="LONG_WATCH",
        grade="A",
        reason_code="X",
        active_levels=[],
        fib={},
        swings_used=[],
    )
    defaults.update(overrides)
    record = ContextRecord(**defaults)
    entry = build_journal_entry(record, recorded_at=evaluated_at)
    TidemarkStore(research_engine).save_journal_entry(entry)


# -- first evaluation ever: never sends, even for a real D-match -------------


def test_first_evaluation_ever_never_sends(tmp_path) -> None:
    database_url, research_engine, evaluation_engine = _dbs(tmp_path)
    client = _FakeClient()  # drives D1

    result = evaluate_briefing(
        client, _cache(client), evaluation_engine, database_url, VENUE, HOUR1
    )

    assert result.classification.result == "D1"
    assert result.should_send is False
    assert result.send_reason is None


def test_first_evaluation_is_still_recorded(tmp_path) -> None:
    database_url, research_engine, evaluation_engine = _dbs(tmp_path)
    client = _FakeClient()

    result = evaluate_briefing(
        client, _cache(client), evaluation_engine, database_url, VENUE, HOUR1
    )

    row = latest_evaluation_before(evaluation_engine, ASSET, HOUR2)
    assert row is not None
    assert row.classification == "D1"
    assert row.sent is False
    assert row.evaluated_at == result.evaluated_at


# -- classification-changed trigger --------------------------------------


def test_unchanged_classification_does_not_send(tmp_path) -> None:
    database_url, research_engine, evaluation_engine = _dbs(tmp_path)
    client = _FakeClient()  # D1 both hours
    evaluate_briefing(client, _cache(client), evaluation_engine, database_url, VENUE, HOUR1)

    result = evaluate_briefing(
        client, _cache(client), evaluation_engine, database_url, VENUE, HOUR2
    )

    assert result.classification.result == "D1"
    assert result.should_send is False


def test_changed_classification_sends(tmp_path) -> None:
    database_url, research_engine, evaluation_engine = _dbs(tmp_path)
    d1_client = _FakeClient()  # price+1%, OI+1%, funding positive -> D1
    evaluate_briefing(d1_client, _cache(d1_client), evaluation_engine, database_url, VENUE, HOUR1)

    d4_client = _FakeClient(  # price-1%, OI-1%, funding positive -> D4
        price_ohlcv={"o": 100.0, "h": 100.0, "l": 99.0, "c": 99.0},
        oi_bucket={"o": 1000.0, "h": 1000.0, "l": 985.0, "c": 985.0},
    )
    result = evaluate_briefing(
        d4_client, _cache(d4_client), evaluation_engine, database_url, VENUE, HOUR2
    )

    assert result.classification.result == "D4"
    assert result.should_send is True
    assert result.send_reason == CLASSIFICATION_CHANGED


# -- NO_MATCH never sends, changed or not ------------------------------------


def test_no_match_never_sends_even_though_it_differs_from_the_previous(tmp_path) -> None:
    database_url, research_engine, evaluation_engine = _dbs(tmp_path)
    d1_client = _FakeClient()
    evaluate_briefing(d1_client, _cache(d1_client), evaluation_engine, database_url, VENUE, HOUR1)

    flat_client = _FakeClient(  # FLAT price, FLAT OI -> no rulebook match
        price_ohlcv={"o": 100.0, "h": 100.0, "l": 100.0, "c": 100.0},
        oi_bucket={"o": 1000.0, "h": 1000.0, "l": 1000.0, "c": 1000.0},
    )
    result = evaluate_briefing(
        flat_client, _cache(flat_client), evaluation_engine, database_url, VENUE, HOUR2
    )

    assert result.classification.result == "NO_MATCH"
    assert result.should_send is False
    assert result.send_reason is None


def test_two_consecutive_no_match_evaluations_never_send(tmp_path) -> None:
    database_url, research_engine, evaluation_engine = _dbs(tmp_path)
    flat_client = _FakeClient(
        price_ohlcv={"o": 100.0, "h": 100.0, "l": 100.0, "c": 100.0},
        oi_bucket={"o": 1000.0, "h": 1000.0, "l": 1000.0, "c": 1000.0},
    )
    evaluate_briefing(
        flat_client, _cache(flat_client), evaluation_engine, database_url, VENUE, HOUR1
    )
    result = evaluate_briefing(
        flat_client, _cache(flat_client), evaluation_engine, database_url, VENUE, HOUR2
    )

    assert result.classification.result == "NO_MATCH"
    assert result.should_send is False


# -- structural-change trigger (compared against the last SENT briefing) ----


def test_structural_change_sends_even_with_an_unchanged_classification(tmp_path) -> None:
    database_url, research_engine, evaluation_engine = _dbs(tmp_path)
    client = _FakeClient()  # D1 every hour in this test

    # Hour 1: BULLISH/LONG_WATCH, D1, and mark it as sent (simulating a
    # real prior delivery) so there is a "last sent" baseline to compare.
    _save_structure(research_engine, dt.datetime(2026, 9, 27, 16, 0, tzinfo=dt.UTC))
    result1 = evaluate_briefing(
        client, _cache(client), evaluation_engine, database_url, VENUE, HOUR1
    )
    mark_sent(evaluation_engine, result1, HOUR1)

    # Hour 2: classification is STILL D1 (unchanged), but structure has
    # moved to BEARISH/SHORT_WATCH.
    _save_structure(
        research_engine,
        dt.datetime(2026, 9, 27, 20, 0, tzinfo=dt.UTC),
        state="BEARISH",
        watch="SHORT_WATCH",
    )
    result2 = evaluate_briefing(
        client, _cache(client), evaluation_engine, database_url, VENUE, HOUR2
    )

    assert result2.classification.result == "D1"  # unchanged
    assert result2.should_send is True
    assert result2.send_reason == STRUCTURAL_CHANGE


def test_structure_ticking_between_two_unsent_evaluations_does_not_send(tmp_path) -> None:
    """The structural trigger is scoped to "differs from the last SENT
    briefing", not "differs from the immediately prior evaluation" - a
    structure change between two evaluations nobody was ever told about
    must not fire on its own.
    """
    database_url, research_engine, evaluation_engine = _dbs(tmp_path)
    client = _FakeClient()

    _save_structure(research_engine, dt.datetime(2026, 9, 27, 16, 0, tzinfo=dt.UTC))
    evaluate_briefing(client, _cache(client), evaluation_engine, database_url, VENUE, HOUR1)
    # (never marked sent)

    _save_structure(
        research_engine,
        dt.datetime(2026, 9, 27, 20, 0, tzinfo=dt.UTC),
        state="BEARISH",
        watch="SHORT_WATCH",
    )
    result = evaluate_briefing(
        client, _cache(client), evaluation_engine, database_url, VENUE, HOUR2
    )

    assert result.classification.result == "D1"
    assert result.should_send is False  # no prior SENT record to compare against


# -- missing/stale Section 1 result: UNAVAILABLE, never recomputed ----------


def test_missing_journal_entry_renders_structure_unavailable(tmp_path) -> None:
    database_url, research_engine, evaluation_engine = _dbs(tmp_path)
    client = _FakeClient()

    result = evaluate_briefing(
        client, _cache(client), evaluation_engine, database_url, VENUE, HOUR1
    )

    assert result.structure.available is False
    assert result.structure.stale is False
    assert "UNAVAILABLE" in result.message


def test_stale_journal_entry_renders_structure_unavailable(tmp_path) -> None:
    database_url, research_engine, evaluation_engine = _dbs(tmp_path)
    client = _FakeClient()
    # Section 1 last ran 20 hours ago - well beyond the staleness grace window.
    _save_structure(research_engine, HOUR1 - dt.timedelta(hours=20))

    result = evaluate_briefing(
        client, _cache(client), evaluation_engine, database_url, VENUE, HOUR1
    )

    assert result.structure.available is False
    assert result.structure.stale is True


def test_a_real_stored_section1_result_renders_structure_instead_of_unavailable(tmp_path) -> None:
    """The regression this fix addresses: a fresh, real journal_entries
    row (written the way `tidemark run` actually writes one) must show up
    as real BTC STRUCTURE content, not UNAVAILABLE. Before this fix,
    briefing.py read `context_records` - a table `tidemark run` never
    writes - so this exact scenario silently always showed UNAVAILABLE
    even with a perfectly healthy, freshly-run Section 1 result on
    record. See ADR 0011's Merge 3 addendum.
    """
    database_url, research_engine, evaluation_engine = _dbs(tmp_path)
    client = _FakeClient()
    _save_structure(
        research_engine,
        HOUR1 - dt.timedelta(hours=1),
        state="BULLISH",
        watch="LONG_WATCH",
        grade="A",
    )

    result = evaluate_briefing(
        client, _cache(client), evaluation_engine, database_url, VENUE, HOUR1
    )

    assert result.structure.available is True
    assert result.structure.stale is False
    assert result.structure.state == "BULLISH"
    assert result.structure.watch == "LONG_WATCH"
    assert result.structure.grade == "A"
    assert "UNAVAILABLE" not in result.message
    assert "BTC STRUCTURE" in result.message
    assert "State: BULLISH / Watch: LONG_WATCH, grade A" in result.message


# -- no research table is ever written ---------------------------------------


def test_evaluate_briefing_writes_no_research_table(tmp_path) -> None:
    database_url, research_engine, evaluation_engine = _dbs(tmp_path)
    _save_structure(research_engine, dt.datetime(2026, 9, 27, 16, 0, tzinfo=dt.UTC))
    client = _FakeClient()

    def _counts():
        with research_engine.connect() as conn:
            return {
                "journal_entries": conn.execute(
                    select(func.count()).select_from(JournalEntry)
                ).scalar(),
                "context_records": conn.execute(
                    select(func.count()).select_from(ContextRecord)
                ).scalar(),
            }

    before = _counts()
    assert before == {"journal_entries": 1, "context_records": 0}  # the one setup row only

    evaluate_briefing(client, _cache(client), evaluation_engine, database_url, VENUE, HOUR1)

    after = _counts()
    assert after == before  # unchanged: reading journal_entries never writes to it or anywhere else


# -- unavailable classifier inputs: NO_MATCH, never a guess ------------------


def test_btc_not_listed_gives_no_match_and_records_unavailable_inputs(tmp_path) -> None:
    database_url, research_engine, evaluation_engine = _dbs(tmp_path)
    client = _FakeClient(listed=False)

    result = evaluate_briefing(
        client, _cache(client), evaluation_engine, database_url, VENUE, HOUR1
    )

    assert result.classification.result == "NO_MATCH"
    row = latest_evaluation_before(evaluation_engine, ASSET, HOUR2)
    assert row.price_status != "OK"


# -- mark_sent updates only sent/send_reason ---------------------------------


def test_mark_sent_updates_the_already_recorded_row(tmp_path) -> None:
    database_url, research_engine, evaluation_engine = _dbs(tmp_path)
    client = _FakeClient()
    result = evaluate_briefing(
        client, _cache(client), evaluation_engine, database_url, VENUE, HOUR1
    )

    row_before = latest_evaluation_before(evaluation_engine, ASSET, HOUR2)
    assert row_before.sent is False

    mark_sent(evaluation_engine, result, HOUR1)

    row_after = latest_evaluation_before(evaluation_engine, ASSET, HOUR2)
    assert row_after.sent is True
    assert row_after.classification == row_before.classification  # untouched
