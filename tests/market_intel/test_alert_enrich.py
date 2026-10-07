"""The alert enricher, end to end on real SQLite databases: the durable cursor
(first run, ordering, failure and retry, idempotency, missed runs), Coinalyze
outage behaviour, dry run, the composed text, and `tidemark alert enrich`
through the real CLI dispatch. No live network: Coinalyze and Telegram are
replaced by fakes. Journal rows are written with the production path.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session
from test_coin_market_context import _database, assert_no_banned_language
from test_market_context import BTC_SYMBOL, ETH_SYMBOL, VENUE, _cache, _FakeMarketClient, _reading
from typer.testing import CliRunner

from tidemark import cli as cli_module
from tidemark.cli import app
from tidemark.data.models import ContextRecord, JournalEntry
from tidemark.data.store import TidemarkStore, create_store_engine
from tidemark.enrich import alerts as enrich_alerts
from tidemark.enrich.alerts import (
    ALREADY_SENT,
    FAILED,
    NO_ALERT,
    SENT,
    WOULD_SEND,
    build_live_bundle,
    run_enrichment,
)
from tidemark.journal.records import build_journal_entry
from tidemark.market_intel import alert_cursor
from tidemark.market_intel.context_read import section1_from_entry
from tidemark.market_intel.market_context import ReferenceSnapshotCache, fetch_market_context
from tidemark.market_intel.service import fetch_market_intel

SOL = "SOL/USDT:USDT"
NOW = dt.datetime(2026, 9, 28, 8, 30, tzinfo=dt.UTC)
BASE = dt.datetime(2026, 9, 28, 0, 0, tzinfo=dt.UTC)
runner = CliRunner()


# -- helpers ---------------------------------------------------------------


def _record_at(index: int, *, state: str, watch: str, reason_code: str, grade) -> ContextRecord:
    return ContextRecord(
        asset=SOL,
        evaluated_at=BASE + dt.timedelta(hours=4 * index),
        rule_version="section-01-v1.1",
        state=state,
        watch=watch,
        grade=grade,
        reason_code=reason_code,
        active_levels=[],
        fib={},
        swings_used=[],
    )


def seed_entry(url: str, index: int, alert_reason: str | None = None, **kwargs) -> int:
    """Writes one journal entry exactly as `tidemark run` does, then records
    the change detector's decision on it. Returns the journal entry id.
    """
    defaults = dict(state="NEUTRAL", watch="WAIT", reason_code="NEUTRAL_STRUCTURE", grade=None)
    defaults.update(kwargs)
    record = _record_at(index, **defaults)
    engine = create_store_engine(url)
    try:
        store = TidemarkStore(engine)
        result = store.save_journal_entry(
            build_journal_entry(record, recorded_at=record.evaluated_at)
        )
        if alert_reason is not None:
            store.record_alert_outcome(result.entry.id, alert_sent=False, alert_reason=alert_reason)
        return result.entry.id
    finally:
        engine.dispose()


def seed_watch_opened(url: str, index: int) -> int:
    return seed_entry(
        url,
        index,
        alert_reason="WATCH_OPENED",
        state="BULLISH",
        watch="LONG_WATCH",
        reason_code="MAJOR_SUPPORT",
        grade="B",
    )


def seed_quiet(url: str, index: int) -> int:
    return seed_entry(url, index)


def _readings() -> dict:
    return {
        SOL: _reading(price_pct=-0.4),
        BTC_SYMBOL: _reading(price_pct=0.1),
        ETH_SYMBOL: _reading(),
    }


def live_builder(url: str, readings: dict | None = None, fail_for: tuple[str, ...] = ()):
    """A bundle builder that runs the real market-context path against a fake
    Coinalyze client, so the composed alert uses the real renderers.
    """

    def build(entry, now):
        client = _FakeMarketClient(readings or _readings(), fail_for=fail_for)
        cache = _cache(client)
        snapshot = fetch_market_intel(client, cache, entry.asset, VENUE, now)
        return fetch_market_context(
            client,
            cache,
            snapshot,
            VENUE,
            now,
            url,
            coin_section1=section1_from_entry(entry),
        )

    return build


class Sender:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.texts: list[str] = []

    def __call__(self, text: str) -> bool:
        self.texts.append(text)
        return self.ok


@pytest.fixture
def world(tmp_path):
    url = _database(tmp_path)
    cursor = alert_cursor.make_engine(url)
    yield url, cursor
    cursor.dispose()


def run(url, cursor, *, send: bool, builder=None, sender=None, now=NOW):
    return run_enrichment(
        database_url=url,
        cursor_engine=cursor,
        now=now,
        send=send,
        build_bundle=builder or live_builder(url),
        send_text=sender if sender is not None else (Sender() if send else None),
    )


def _baseline(world):
    """A cursor already initialised past everything seeded so far."""
    url, cursor = world
    seed_quiet(url, 0)
    run(url, cursor, send=True)  # first run: records the latest entry, sends nothing
    return alert_cursor.get_checkpoint(cursor)


# -- the durable cursor ----------------------------------------------------


def test_first_run_records_the_latest_entry_and_sends_nothing(world) -> None:
    url, cursor = world
    seed_watch_opened(url, 0)
    latest = seed_watch_opened(url, 1)
    sender = Sender()

    outcome = run(url, cursor, send=True, sender=sender)

    assert outcome.first_run
    assert outcome.first_run_baseline.id == latest
    assert outcome.decisions == ()
    assert sender.texts == []  # the earlier WATCH is history, never replayed
    assert alert_cursor.get_checkpoint(cursor) == latest


def test_entries_after_the_checkpoint_are_processed_in_order(world) -> None:
    url, cursor = world
    checkpoint = _baseline(world)
    opened = seed_watch_opened(url, 1)
    quiet = seed_quiet(url, 2)
    closed = seed_entry(url, 3, alert_reason="WATCH_CLOSED", state="NEUTRAL", watch="WAIT")
    sender = Sender()

    outcome = run(url, cursor, send=True, sender=sender)

    assert [d.journal_entry_id for d in outcome.decisions] == [opened, quiet, closed]
    assert [d.decision for d in outcome.decisions] == [SENT, NO_ALERT, SENT]
    assert len(sender.texts) == 2
    assert outcome.failure is None
    assert alert_cursor.get_checkpoint(cursor) == closed
    assert checkpoint < opened


def test_a_successful_send_is_recorded_against_its_journal_entry(world) -> None:
    url, cursor = world
    _baseline(world)
    opened = seed_watch_opened(url, 1)

    run(url, cursor, send=True)

    assert alert_cursor.is_sent(cursor, opened)
    sent = alert_cursor.list_sent(cursor)
    assert [s.journal_entry_id for s in sent] == [opened]
    assert sent[0].alert_reason == "WATCH_OPENED"
    assert sent[0].asset == SOL


def test_a_failed_send_leaves_the_checkpoint_and_the_next_run_retries(world) -> None:
    url, cursor = world
    _baseline(world)
    quiet = seed_quiet(url, 1)
    opened = seed_watch_opened(url, 2)

    failing = run(url, cursor, send=True, sender=Sender(ok=False))

    assert failing.failure is not None
    assert failing.decisions[-1].decision == FAILED
    # The quiet entry before the failed alert moved the checkpoint; the alert did not.
    assert alert_cursor.get_checkpoint(cursor) == quiet
    assert not alert_cursor.is_sent(cursor, opened)

    retry = Sender()
    recovered = run(url, cursor, send=True, sender=retry)

    assert recovered.failure is None
    assert len(retry.texts) == 1
    assert alert_cursor.is_sent(cursor, opened)
    assert alert_cursor.get_checkpoint(cursor) == opened


def test_running_twice_sends_nothing_the_second_time(world) -> None:
    url, cursor = world
    _baseline(world)
    seed_watch_opened(url, 1)
    first = Sender()
    second = Sender()

    run(url, cursor, send=True, sender=first)
    again = run(url, cursor, send=True, sender=second)

    assert len(first.texts) == 1
    assert second.texts == []
    assert again.decisions == ()


def test_a_missed_run_picks_up_several_entries_in_one_go(world) -> None:
    url, cursor = world
    _baseline(world)
    ids = [seed_watch_opened(url, i) for i in range(1, 5)]
    sender = Sender()

    outcome = run(url, cursor, send=True, sender=sender)

    assert [d.journal_entry_id for d in outcome.decisions] == ids
    assert len(sender.texts) == 4
    assert alert_cursor.get_checkpoint(cursor) == ids[-1]


def test_an_entry_already_recorded_as_sent_is_never_sent_again(world) -> None:
    url, cursor = world
    checkpoint = _baseline(world)
    opened = seed_watch_opened(url, 1)
    # Simulate a sent record that exists without the checkpoint having moved
    # past it - the state a re-run would find after an interrupted cursor write.
    with Session(cursor) as session, session.begin():
        session.add(
            alert_cursor.SentAlert(
                journal_entry_id=opened,
                asset=SOL,
                evaluated_at=BASE + dt.timedelta(hours=4),
                alert_reason="WATCH_OPENED",
                sent_at=NOW,
            )
        )
    sender = Sender()

    outcome = run(url, cursor, send=True, sender=sender)

    assert checkpoint < opened
    assert [d.decision for d in outcome.decisions] == [ALREADY_SENT]
    assert sender.texts == []
    assert alert_cursor.get_checkpoint(cursor) == opened


def test_the_checkpoint_never_moves_backward(world) -> None:
    url, cursor = world
    checkpoint = _baseline(world)
    alert_cursor.advance_checkpoint(cursor, checkpoint - 1, NOW)
    assert alert_cursor.get_checkpoint(cursor) == checkpoint


# -- Coinalyze outage ------------------------------------------------------


def test_a_missing_api_key_still_sends_the_section1_facts(world) -> None:
    url, cursor = world
    _baseline(world)
    opened = seed_watch_opened(url, 1)
    sender = Sender()

    outcome = run(
        url,
        cursor,
        send=True,
        sender=sender,
        builder=lambda entry, now: build_live_bundle(
            entry, now, database_url=url, venue=VENUE, api_key=None
        ),
    )

    assert outcome.failure is None
    assert alert_cursor.is_sent(cursor, opened)
    text = sender.texts[0]
    assert "LONG WATCH" in text
    assert "4H: BULLISH" in text
    assert "Coinalyze API key is not configured" in text
    assert "UNAVAILABLE" in text
    assert "Relative strength vs BTC: NO_MATCH" in text


def test_a_rate_limited_coin_fetch_still_sends_with_unavailable_context(world, monkeypatch) -> None:
    url, cursor = world
    _baseline(world)
    seed_watch_opened(url, 1)
    monkeypatch.setattr(
        enrich_alerts,
        "CoinalyzeClient",
        lambda api_key: _FakeMarketClient(_readings(), fail_for=(SOL,)),
    )
    sender = Sender()

    outcome = run(
        url,
        cursor,
        send=True,
        sender=sender,
        builder=lambda entry, now: build_live_bundle(
            entry, now, database_url=url, venue=VENUE, api_key="unused"
        ),
    )

    assert outcome.failure is None
    assert "rate limit reached this minute" in sender.texts[0]
    assert "Section 1 (4H): BULLISH" in sender.texts[0]


def test_btc_and_eth_context_come_from_their_stored_section1_records(world) -> None:
    url, cursor = world
    _baseline(world)
    seed_entry(
        url,
        1,
        alert_reason="WATCH_OPENED",
        state="BULLISH",
        watch="LONG_WATCH",
        reason_code="MAJOR_SUPPORT",
        grade="B",
    )
    # BTC's latest stored record is fresh and BEARISH, so the coin is AGAINST BTC.
    btc = ContextRecord(
        asset=BTC_SYMBOL,
        evaluated_at=NOW - dt.timedelta(hours=1),
        rule_version="section-01-v1.1",
        state="BEARISH",
        watch="SHORT_WATCH",
        grade="A",
        reason_code="MAJOR_RESISTANCE",
        active_levels=[],
        fib={},
        swings_used=[],
    )
    engine = create_store_engine(url)
    try:
        TidemarkStore(engine).save_journal_entry(build_journal_entry(btc, recorded_at=NOW))
    finally:
        engine.dispose()
    sender = Sender()

    run(url, cursor, send=True, sender=sender)

    assert "BTC alignment: AGAINST BTC" in sender.texts[0]


# -- dry run ---------------------------------------------------------------


def test_a_dry_run_composes_what_would_be_sent_and_writes_nothing(tmp_path) -> None:
    url = _database(tmp_path)
    cursor = alert_cursor.make_engine(url)
    try:
        seed_quiet(url, 0)
        seed_watch_opened(url, 1)

        outcome = run(url, cursor, send=False)

        assert outcome.first_run  # no checkpoint yet, and a dry run writes none
        assert outcome.decisions == ()
        assert not inspect(cursor).has_table(alert_cursor.CHECKPOINT_TABLE)
        assert not inspect(cursor).has_table(alert_cursor.SENT_TABLE)
    finally:
        cursor.dispose()


def test_a_dry_run_after_the_first_run_leaves_the_checkpoint_alone(world) -> None:
    url, cursor = world
    checkpoint = _baseline(world)
    opened = seed_watch_opened(url, 1)

    outcome = run(url, cursor, send=False)

    assert [d.decision for d in outcome.decisions] == [WOULD_SEND]
    assert outcome.decisions[0].text is not None
    assert alert_cursor.get_checkpoint(cursor) == checkpoint
    assert not alert_cursor.is_sent(cursor, opened)
    assert alert_cursor.list_sent(cursor) == []


# -- the composed text -----------------------------------------------------


def test_the_composed_alert_has_no_banned_or_deferred_language(world) -> None:
    url, cursor = world
    _baseline(world)
    seed_watch_opened(url, 1)

    live = run(url, cursor, send=False).decisions[0].text
    assert_no_banned_language(live)

    outage = (
        run(
            url,
            cursor,
            send=False,
            builder=lambda entry, now: build_live_bundle(
                entry, now, database_url=url, venue=VENUE, api_key=None
            ),
        )
        .decisions[0]
        .text
    )
    assert_no_banned_language(outage)


def test_the_composed_alert_carries_the_section1_facts_then_the_context(world) -> None:
    url, cursor = world
    _baseline(world)
    seed_watch_opened(url, 1)

    text = run(url, cursor, send=False).decisions[0].text

    assert text.index("LONG WATCH") < text.index("4H: BULLISH") < text.index("MARKET CONTEXT")
    assert text.index("MARKET CONTEXT") < text.index("WHAT TO WATCH") < text.index("Rulebooks:")
    assert "Market information only. Context only, not a trade signal." in text


# -- the CLI ---------------------------------------------------------------


@pytest.fixture
def cli_world(world, monkeypatch):
    url, cursor = world
    monkeypatch.setenv("TIDEMARK_DATABASE_URL", url)
    monkeypatch.setattr(
        enrich_alerts, "CoinalyzeClient", lambda api_key: _FakeMarketClient(_readings())
    )
    return url, cursor


def test_alert_enrich_defaults_to_a_dry_run_and_refuses_both_flags(cli_world) -> None:
    url, cursor = cli_world
    seed_quiet(url, 0)

    default = runner.invoke(app, ["alert", "enrich"])
    both = runner.invoke(app, ["alert", "enrich", "--send", "--dry-run"])

    assert default.exit_code == 0, default.output
    assert "mode: DRY RUN" in default.output
    assert "No checkpoint yet" in default.output
    assert both.exit_code == 2
    assert "not both" in both.output


def test_alert_enrich_dry_run_prints_the_example_alert(cli_world, world) -> None:
    url, cursor = cli_world
    _baseline(world)
    seed_watch_opened(url, 1)

    result = runner.invoke(app, ["alert", "enrich", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "WOULD SEND" in result.output
    assert "--- composed alert for journal entry" in result.output
    assert "LONG WATCH" in result.output
    assert "Dry run: nothing sent, nothing written" in result.output


def test_alert_enrich_send_sends_and_advances_then_says_so(cli_world, world, monkeypatch) -> None:
    url, cursor = cli_world
    _baseline(world)
    opened = seed_watch_opened(url, 1)
    sender = Sender()

    class _Notifier:
        def __init__(self, settings) -> None:
            self.send_text = sender

    monkeypatch.setattr(cli_module, "_notifier", lambda settings: _Notifier(settings))

    result = runner.invoke(app, ["alert", "enrich", "--send"])

    assert result.exit_code == 0, result.output
    assert "SENT" in result.output
    assert len(sender.texts) == 1
    assert alert_cursor.is_sent(cursor, opened)
    assert alert_cursor.get_checkpoint(cursor) == opened


def test_alert_enrich_send_reports_a_failed_send_and_exits_nonzero(
    cli_world, world, monkeypatch
) -> None:
    url, cursor = cli_world
    _baseline(world)
    seed_watch_opened(url, 1)

    class _Notifier:
        def __init__(self, settings) -> None:
            self.send_text = Sender(ok=False)

    monkeypatch.setattr(cli_module, "_notifier", lambda settings: _Notifier(settings))

    result = runner.invoke(app, ["alert", "enrich", "--send"])

    assert result.exit_code == 1
    assert "FAILED: Telegram send failed" in result.output
    assert alert_cursor.list_sent(cursor) == []


def test_journal_entry_is_not_written_by_the_enricher(world) -> None:
    url, cursor = world
    _baseline(world)
    opened = seed_watch_opened(url, 1)
    before = _journal_snapshot(url)

    run(url, cursor, send=True)

    assert _journal_snapshot(url) == before
    assert opened in before


def _journal_snapshot(url: str) -> dict[int, tuple]:
    engine = create_store_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(JournalEntry.__table__.select()).fetchall()
        return {row.id: (row.alert_sent, row.alert_reason, row.state) for row in rows}
    finally:
        engine.dispose()


# -- one reference cache per run ------------------------------------------


def test_three_entries_in_one_period_fetch_btc_and_eth_once(world, monkeypatch) -> None:
    """A catch-up of N entries in the same closed period costs 14 + 7N, not 21N:
    the coin is fetched per alert, BTC and ETH once for the whole run.
    """
    url, cursor = world
    _baseline(world)
    ids = [seed_watch_opened(url, i) for i in (1, 2, 3)]
    fake = _FakeMarketClient(_readings())
    monkeypatch.setattr(enrich_alerts, "CoinalyzeClient", lambda api_key: fake)
    reference_cache = ReferenceSnapshotCache()

    outcome = run(
        url,
        cursor,
        send=True,
        builder=lambda entry, now: build_live_bundle(
            entry,
            now,
            database_url=url,
            venue=VENUE,
            api_key="unused",
            reference_cache=reference_cache,
        ),
    )

    assert [d.journal_entry_id for d in outcome.decisions] == ids
    assert [d.decision for d in outcome.decisions] == [SENT, SENT, SENT]
    # One metric-call set per snapshot: 7 calls each. Coin 3x, BTC once, ETH once.
    assert fake.requested.count(SOL) == 3 * 7
    assert fake.requested.count(BTC_SYMBOL) == 7
    assert fake.requested.count(ETH_SYMBOL) == 7


# -- journal alerts reads the enricher's sent records ---------------------


def test_an_alert_recorded_by_the_enricher_appears_in_journal_alerts(
    cli_world, world, monkeypatch
) -> None:
    url, cursor = cli_world
    _baseline(world)
    opened = seed_watch_opened(url, 1)
    # No alert decision on this entry: it must never appear in the listing.
    seed_quiet(url, 2)

    class _Notifier:
        def __init__(self, settings) -> None:
            self.send_text = Sender()

    monkeypatch.setattr(cli_module, "_notifier", lambda settings: _Notifier(settings))
    sent = runner.invoke(app, ["alert", "enrich", "--send"])
    assert sent.exit_code == 0, sent.output

    listing = runner.invoke(app, ["journal", "alerts", "--days", "36500"])

    assert listing.exit_code == 0, listing.output
    assert "alert_reason=WATCH_OPENED" in listing.output
    assert "sent_at=" in listing.output
    assert len(listing.output.strip().splitlines()) == 1
    assert alert_cursor.is_sent(cursor, opened)


def test_journal_alerts_says_so_when_the_enricher_has_sent_nothing(cli_world) -> None:
    result = runner.invoke(app, ["journal", "alerts", "--days", "36500"])
    assert result.exit_code == 0, result.output
    assert "No alerts sent." in result.output
