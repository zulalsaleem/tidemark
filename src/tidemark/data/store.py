"""SQLite persistence via SQLAlchemy.

Candle ingestion (Part C/D of Phase 1): idempotent upserts, per-candle
sanity checks, rejected-candle recording, gap detection, and run
bookkeeping. Section 1 context-record persistence (Phase 2): idempotent
upsert keyed on (asset, evaluated_at, rule_version), so re-evaluating the
same 4H close overwrites rather than duplicates.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from tidemark.data.exchange import RawCandle
from tidemark.data.models import (
    Base,
    Candle,
    ContextRecord,
    JournalEntry,
    Observation,
    RejectedCandle,
    Run,
    RunSymbolStat,
)
from tidemark.data.timeframes import TIMEFRAME_DURATIONS

RUNNING_STATUS = "RUNNING"
VALID_RUN_STATUSES = {"COMPLETED", "PARTIAL", "FAILED"}


def create_store_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine for the given database URL."""
    return create_engine(database_url)


def init_db(engine: Engine) -> None:
    """Create all tables defined in `tidemark.data.models` if missing."""
    Base.metadata.create_all(engine)


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory bound to the given engine."""
    return sessionmaker(bind=engine)


@dataclass(frozen=True)
class CandleUpsertResult:
    """Outcome of upserting one batch of candles for a symbol/timeframe."""

    fetched: int
    inserted: int
    duplicates_skipped: int
    rejected: int


@dataclass(frozen=True)
class ObservationWriteResult:
    """Outcome of writing one Section 2 observation row.

    Append-only, mirroring `JournalWriteResult`: `inserted=False` means
    this (asset, evaluated_at, rule_version) was already observed — a
    no-op, and `observation` is the existing row untouched, not a second
    row and not an update.
    """

    observation: Observation
    inserted: bool


@dataclass(frozen=True)
class JournalWriteResult:
    """Outcome of writing one journal entry.

    `inserted=False` means this (asset, evaluated_at, rule_version) was
    already journaled — a no-op, not an error, and `entry` is the
    existing row untouched. Callers use `inserted` to decide whether to
    run the change detector at all: a repeat must trigger neither a new
    row nor a re-alert.
    """

    entry: JournalEntry
    inserted: bool


def _validate_candle(candle: RawCandle) -> str | None:
    """Return a rejection reason, or None if the candle is sane.

    Never "fixes" a bad row — only classifies it.
    """
    reasons = []
    if not (candle.open > 0 and candle.high > 0 and candle.low > 0 and candle.close > 0):
        reasons.append("non-positive price")
    if candle.high < max(candle.open, candle.close):
        reasons.append("high < max(open, close)")
    if candle.low > min(candle.open, candle.close):
        reasons.append("low > min(open, close)")
    if candle.volume < 0:
        reasons.append("volume < 0")
    return "; ".join(reasons) if reasons else None


class TidemarkStore:
    """Read/write access to candles, runs, and (later) rulebook records."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session_factory = get_session_factory(engine)

    # -- candles ----------------------------------------------------------

    def upsert_candles(
        self,
        venue: str,
        symbol: str,
        timeframe: str,
        candles: Sequence[RawCandle],
        fetched_at: dt.datetime,
    ) -> CandleUpsertResult:
        """Validate and idempotently insert a batch of closed candles.

        Invalid rows are rejected and recorded in `rejected_candles`
        instead of being stored or "fixed". Running the same fetch twice
        inserts zero new rows the second time.
        """
        fetched = len(candles)
        rejected = 0
        valid_rows = []

        with self._session_factory() as session:
            for candle in candles:
                reason = _validate_candle(candle)
                if reason is not None:
                    rejected += 1
                    session.add(
                        RejectedCandle(
                            venue=venue,
                            symbol=symbol,
                            timeframe=timeframe,
                            open_time=candle.open_time,
                            close_time=candle.close_time,
                            open=candle.open,
                            high=candle.high,
                            low=candle.low,
                            close=candle.close,
                            volume=candle.volume,
                            reason=reason,
                            rejected_at=fetched_at,
                        )
                    )
                    continue
                valid_rows.append(
                    {
                        "venue": venue,
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "open_time": candle.open_time,
                        "close_time": candle.close_time,
                        "open": candle.open,
                        "high": candle.high,
                        "low": candle.low,
                        "close": candle.close,
                        "volume": candle.volume,
                        "fetched_at": fetched_at,
                    }
                )

            inserted = 0
            if valid_rows:
                stmt = sqlite_insert(Candle).values(valid_rows)
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["venue", "symbol", "timeframe", "open_time"]
                )
                result = session.execute(stmt)
                inserted = result.rowcount if result.rowcount and result.rowcount > 0 else 0

            session.commit()

        return CandleUpsertResult(
            fetched=fetched,
            inserted=inserted,
            duplicates_skipped=len(valid_rows) - inserted,
            rejected=rejected,
        )

    def get_candles(
        self,
        venue: str,
        symbol: str,
        timeframe: str,
        start: dt.datetime | None = None,
        end: dt.datetime | None = None,
    ) -> list[Candle]:
        """Fetch stored closed candles ordered oldest to newest.

        `start` is inclusive, `end` is exclusive, both compare on
        `open_time`.
        """
        with self._session_factory() as session:
            stmt = select(Candle).where(
                Candle.venue == venue, Candle.symbol == symbol, Candle.timeframe == timeframe
            )
            if start is not None:
                stmt = stmt.where(Candle.open_time >= start)
            if end is not None:
                stmt = stmt.where(Candle.open_time < end)
            stmt = stmt.order_by(Candle.open_time)
            return list(session.scalars(stmt))

    def latest_candle(self, venue: str, symbol: str, timeframe: str) -> Candle | None:
        """Fetch the most recently closed stored candle, if any."""
        with self._session_factory() as session:
            stmt = (
                select(Candle)
                .where(
                    Candle.venue == venue, Candle.symbol == symbol, Candle.timeframe == timeframe
                )
                .order_by(Candle.open_time.desc())
                .limit(1)
            )
            return session.scalars(stmt).first()

    def count_candles(self, venue: str, symbol: str, timeframe: str) -> int:
        """Count stored candles for a venue/symbol/timeframe."""
        with self._session_factory() as session:
            stmt = (
                select(func.count())
                .select_from(Candle)
                .where(
                    Candle.venue == venue, Candle.symbol == symbol, Candle.timeframe == timeframe
                )
            )
            return session.scalar(stmt) or 0

    # -- gap detection ------------------------------------------------------

    def find_gaps(self, venue: str, symbol: str, timeframe: str) -> list[dt.datetime]:
        """Report missing `open_time`s between the first and last stored
        candle. Never fabricates or interpolates candles — only reports.
        """
        duration = TIMEFRAME_DURATIONS[timeframe]
        candles = self.get_candles(venue, symbol, timeframe)
        if len(candles) < 2:
            return []

        existing = {c.open_time for c in candles}
        last = candles[-1].open_time
        expected = candles[0].open_time
        missing: list[dt.datetime] = []
        while expected < last:
            if expected not in existing:
                missing.append(expected)
            expected += duration
        return missing

    # -- runs ---------------------------------------------------------------

    def start_run(self, run_id: str, command: str, started_at: dt.datetime) -> None:
        """Insert a run row in the transient RUNNING status.

        Callers must always follow this with `finish_run` — including on
        the exception path, via try/finally — so a crash never leaves a
        run with no row at all.
        """
        with self._session_factory() as session:
            session.add(
                Run(
                    run_id=run_id,
                    command=command,
                    started_at=started_at,
                    finished_at=None,
                    status=RUNNING_STATUS,
                )
            )
            session.commit()

    def finish_run(
        self,
        run_id: str,
        finished_at: dt.datetime,
        status: str,
        stats: dict[tuple[str, str], CandleUpsertResult],
    ) -> None:
        """Update a RUNNING row with its final status and per
        symbol/timeframe counts. `status` must be COMPLETED, PARTIAL, or
        FAILED — RUNNING is only ever set by `start_run`.
        """
        if status not in VALID_RUN_STATUSES:
            raise ValueError(f"invalid run status: {status!r}")

        with self._session_factory() as session:
            run = session.execute(select(Run).where(Run.run_id == run_id)).scalar_one()
            run.finished_at = finished_at
            run.status = status
            for (symbol, timeframe), result in stats.items():
                session.add(
                    RunSymbolStat(
                        run_id=run_id,
                        symbol=symbol,
                        timeframe=timeframe,
                        fetched=result.fetched,
                        inserted=result.inserted,
                        duplicates_skipped=result.duplicates_skipped,
                        rejected=result.rejected,
                    )
                )
            session.commit()

    def latest_run(self) -> Run | None:
        """Fetch the most recently started run, if any (any status)."""
        with self._session_factory() as session:
            stmt = select(Run).order_by(Run.started_at.desc()).limit(1)
            return session.scalars(stmt).first()

    def running_runs(self) -> list[Run]:
        """Fetch all runs still in the transient RUNNING status.

        A RUNNING row that has been sitting for a long time did not
        reach `finish_run` — most likely the process that started it
        crashed or was killed.
        """
        with self._session_factory() as session:
            stmt = select(Run).where(Run.status == RUNNING_STATUS).order_by(Run.started_at)
            return list(session.scalars(stmt))

    def run_symbol_stats(self, run_id: str) -> list[RunSymbolStat]:
        """Fetch per symbol/timeframe stats for one run."""
        with self._session_factory() as session:
            stmt = select(RunSymbolStat).where(RunSymbolStat.run_id == run_id)
            return list(session.scalars(stmt))

    def latest_runs_by_command(self) -> dict[str, Run]:
        """The most recent run row for each distinct command that has
        ever run (e.g. "backfill", "update", "run"), keyed by command.

        A command that has never run is simply absent from the result —
        callers should not treat that as a failure on its own, since a
        deployment may legitimately never use every command.
        """
        with self._session_factory() as session:
            stmt = select(Run).order_by(Run.started_at.desc())
            rows = list(session.scalars(stmt))
        latest: dict[str, Run] = {}
        for row in rows:
            latest.setdefault(row.command, row)
        return latest

    # -- context records (Section 1) -----------------------------------------

    def save_context_record(self, record: ContextRecord) -> None:
        """Persist a Section 1 output record.

        Idempotent: re-evaluating the same (asset, evaluated_at,
        rule_version) overwrites the existing row rather than duplicating
        it. Query-then-write rather than a DB-level upsert, since
        `ContextRecord` (unlike `Candle`) carries no unique constraint for
        that key — this only ever runs from a single CLI invocation, so
        there is no concurrent-writer race to guard against.
        """
        with self._session_factory() as session:
            existing = session.scalars(
                select(ContextRecord).where(
                    ContextRecord.asset == record.asset,
                    ContextRecord.evaluated_at == record.evaluated_at,
                    ContextRecord.rule_version == record.rule_version,
                )
            ).one_or_none()
            if existing is not None:
                existing.state = record.state
                existing.watch = record.watch
                existing.grade = record.grade
                existing.reason_code = record.reason_code
                existing.active_levels = record.active_levels
                existing.fib = record.fib
                existing.swings_used = record.swings_used
            else:
                session.add(
                    ContextRecord(
                        asset=record.asset,
                        evaluated_at=record.evaluated_at,
                        rule_version=record.rule_version,
                        state=record.state,
                        watch=record.watch,
                        grade=record.grade,
                        reason_code=record.reason_code,
                        active_levels=record.active_levels,
                        fib=record.fib,
                        swings_used=record.swings_used,
                    )
                )
            session.commit()

    def latest_context_record(self, asset: str) -> ContextRecord | None:
        """Fetch the most recent context record for an asset, if any."""
        with self._session_factory() as session:
            stmt = (
                select(ContextRecord)
                .where(ContextRecord.asset == asset)
                .order_by(ContextRecord.evaluated_at.desc())
                .limit(1)
            )
            return session.scalars(stmt).first()

    def context_history(self, asset: str, since: dt.datetime | None = None) -> list[ContextRecord]:
        """Fetch context records for an asset, newest first."""
        with self._session_factory() as session:
            stmt = select(ContextRecord).where(ContextRecord.asset == asset)
            if since is not None:
                stmt = stmt.where(ContextRecord.evaluated_at >= since)
            stmt = stmt.order_by(ContextRecord.evaluated_at.desc())
            return list(session.scalars(stmt))

    # -- journal (Phase 3) ----------------------------------------------------

    def save_journal_entry(self, entry: JournalEntry) -> JournalWriteResult:
        """Append one journal row. Append-only: unlike `save_context_record`,
        an existing (asset, evaluated_at, rule_version) row is never
        overwritten — re-evaluating the same candle is a no-op that
        returns the existing row, not a second row and not an update.
        """
        with self._session_factory() as session:
            existing = session.scalars(
                select(JournalEntry).where(
                    JournalEntry.asset == entry.asset,
                    JournalEntry.evaluated_at == entry.evaluated_at,
                    JournalEntry.rule_version == entry.rule_version,
                )
            ).one_or_none()
            if existing is not None:
                return JournalWriteResult(entry=existing, inserted=False)

            new_entry = JournalEntry(
                asset=entry.asset,
                evaluated_at=entry.evaluated_at,
                recorded_at=entry.recorded_at,
                rule_version=entry.rule_version,
                state=entry.state,
                watch=entry.watch,
                grade=entry.grade,
                reason_code=entry.reason_code,
                active_levels=entry.active_levels,
                fib=entry.fib,
                swings_used=entry.swings_used,
                alert_sent=entry.alert_sent,
                alert_reason=entry.alert_reason,
            )
            session.add(new_entry)
            session.commit()
            session.refresh(new_entry)
            return JournalWriteResult(entry=new_entry, inserted=True)

    def record_alert_outcome(self, entry_id: int, alert_sent: bool, alert_reason: str) -> None:
        """Set `alert_sent`/`alert_reason` on an already-inserted journal
        row — the one exception to "journal rows are never updated after
        insert". Runs once, immediately after the change detector and any
        Telegram send for that same evaluation; nothing else on the row
        is touched.
        """
        with self._session_factory() as session:
            row = session.execute(
                select(JournalEntry).where(JournalEntry.id == entry_id)
            ).scalar_one()
            row.alert_sent = alert_sent
            row.alert_reason = alert_reason
            session.commit()

    def latest_journal_entry(self, asset: str, rule_version: str) -> JournalEntry | None:
        """Fetch the most recent journal row for an asset at a given
        rule_version, if any. Never crosses a rule_version boundary — a
        rule_version change must not by itself look like a state/watch/
        grade change to the change detector.
        """
        with self._session_factory() as session:
            stmt = (
                select(JournalEntry)
                .where(JournalEntry.asset == asset, JournalEntry.rule_version == rule_version)
                .order_by(JournalEntry.evaluated_at.desc())
                .limit(1)
            )
            return session.scalars(stmt).first()

    def journal_history(self, asset: str, since: dt.datetime | None = None) -> list[JournalEntry]:
        """Fetch journal rows for an asset, newest first."""
        with self._session_factory() as session:
            stmt = select(JournalEntry).where(JournalEntry.asset == asset)
            if since is not None:
                stmt = stmt.where(JournalEntry.evaluated_at >= since)
            stmt = stmt.order_by(JournalEntry.evaluated_at.desc())
            return list(session.scalars(stmt))

    def journal_alerts(self, since: dt.datetime | None = None) -> list[JournalEntry]:
        """Fetch journal rows where an alert was actually sent, across all
        assets, newest first.
        """
        with self._session_factory() as session:
            stmt = select(JournalEntry).where(JournalEntry.alert_sent.is_(True))
            if since is not None:
                stmt = stmt.where(JournalEntry.evaluated_at >= since)
            stmt = stmt.order_by(JournalEntry.evaluated_at.desc())
            return list(session.scalars(stmt))

    def latest_journal_entry_overall(self) -> JournalEntry | None:
        """Fetch the single most recently recorded journal row across all
        assets (by `recorded_at` — when the write actually happened, not
        the candle's own close time), if any. Used to answer "did the
        system journal anything recently", independent of which asset.
        """
        with self._session_factory() as session:
            stmt = select(JournalEntry).order_by(JournalEntry.recorded_at.desc()).limit(1)
            return session.scalars(stmt).first()

    def count_journal_entries(self) -> int:
        """Count all journal rows, across every asset."""
        with self._session_factory() as session:
            stmt = select(func.count()).select_from(JournalEntry)
            return session.scalar(stmt) or 0

    # -- observations (Section 2, Phase 5) -------------------------------------

    def save_observation(self, observation: Observation) -> ObservationWriteResult:
        """Append one Section 2 observation row. Append-only, mirroring
        `save_journal_entry`: an existing (asset, evaluated_at,
        rule_version) row is never overwritten — re-evaluating the same
        1H close is a no-op that returns the existing row.
        """
        with self._session_factory() as session:
            existing = session.scalars(
                select(Observation).where(
                    Observation.asset == observation.asset,
                    Observation.evaluated_at == observation.evaluated_at,
                    Observation.rule_version == observation.rule_version,
                )
            ).one_or_none()
            if existing is not None:
                return ObservationWriteResult(observation=existing, inserted=False)

            new_observation = Observation(
                asset=observation.asset,
                evaluated_at=observation.evaluated_at,
                rule_version=observation.rule_version,
                section_1_state=observation.section_1_state,
                section_1_watch=observation.section_1_watch,
                section_1_grade=observation.section_1_grade,
                section_1_level_price=observation.section_1_level_price,
                interaction_detected=observation.interaction_detected,
                reaction_tier=observation.reaction_tier,
                reaction_condition_matched=observation.reaction_condition_matched,
                reaction_started_at=observation.reaction_started_at,
                structure_reference_price=observation.structure_reference_price,
                structure_reference_confirmed_at=observation.structure_reference_confirmed_at,
                structure_change=observation.structure_change,
                failure=observation.failure,
                expiry=observation.expiry,
                state=observation.state,
                reason_code=observation.reason_code,
                swings_used=observation.swings_used,
            )
            session.add(new_observation)
            session.commit()
            session.refresh(new_observation)
            return ObservationWriteResult(observation=new_observation, inserted=True)

    def observation_history(
        self, asset: str, since: dt.datetime | None = None
    ) -> list[Observation]:
        """Fetch observation rows for an asset, newest first."""
        with self._session_factory() as session:
            stmt = select(Observation).where(Observation.asset == asset)
            if since is not None:
                stmt = stmt.where(Observation.evaluated_at >= since)
            stmt = stmt.order_by(Observation.evaluated_at.desc())
            return list(session.scalars(stmt))

    def all_observations(self, since: dt.datetime | None = None) -> list[Observation]:
        """Fetch observation rows across every asset, newest first — the
        query `observe stats` aggregates over.
        """
        with self._session_factory() as session:
            stmt = select(Observation)
            if since is not None:
                stmt = stmt.where(Observation.evaluated_at >= since)
            stmt = stmt.order_by(Observation.evaluated_at.desc())
            return list(session.scalars(stmt))
