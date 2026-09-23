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
