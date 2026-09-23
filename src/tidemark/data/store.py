"""SQLite persistence via SQLAlchemy.

Candle upserts and idempotent context-record writes, per the standing
rules: only closed candles are ever stored, and re-evaluating the same
(asset, evaluated_at, rule_version) overwrites rather than duplicates.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from tidemark.data.models import Base, Candle, ContextRecord

_CANDLE_COLUMNS = ["open_time", "close_time", "open", "high", "low", "close", "volume"]


def _as_utc(value: dt.datetime) -> dt.datetime:
    """Normalize a datetime to UTC-aware.

    SQLite has no native datetime type, so SQLAlchemy's
    `DateTime(timezone=True)` round-trips values as naive on read even
    though Tidemark only ever writes UTC (standing rule: closed candles
    only, UTC). This restores the UTC tzinfo SQLite dropped.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def create_store_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine for the given database URL."""
    return create_engine(database_url)


def init_db(engine: Engine) -> None:
    """Create all tables defined in `tidemark.data.models` if missing."""
    Base.metadata.create_all(engine)


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory bound to the given engine."""
    return sessionmaker(bind=engine)


class TidemarkStore:
    """Read/write access to candles and context records."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session_factory = get_session_factory(engine)

    def save_candles(self, candles: list[Candle]) -> None:
        """Persist a batch of closed candles.

        Idempotent: upserts by (asset, timeframe, close_time), so
        re-fetching an overlapping range never duplicates a candle.
        """
        with self._session_factory() as session:
            for candle in candles:
                existing = (
                    session.query(Candle)
                    .filter_by(
                        asset=candle.asset,
                        timeframe=candle.timeframe,
                        close_time=candle.close_time,
                    )
                    .one_or_none()
                )
                if existing is not None:
                    existing.open_time = candle.open_time
                    existing.open = candle.open
                    existing.high = candle.high
                    existing.low = candle.low
                    existing.close = candle.close
                    existing.volume = candle.volume
                else:
                    session.add(
                        Candle(
                            asset=candle.asset,
                            timeframe=candle.timeframe,
                            open_time=candle.open_time,
                            close_time=candle.close_time,
                            open=candle.open,
                            high=candle.high,
                            low=candle.low,
                            close=candle.close,
                            volume=candle.volume,
                        )
                    )
            session.commit()

    def get_candles(
        self,
        asset: str,
        timeframe: str,
        limit: int | None = None,
        as_of: dt.datetime | None = None,
    ) -> pd.DataFrame:
        """Fetch closed candles for an asset/timeframe, oldest to newest.

        `as_of`, when given, excludes any candle closing after it — this
        is what lets `--as-of` reproduce exactly what was known at that
        moment. `limit`, when given, keeps only the most recent rows
        after that filter.
        """
        with self._session_factory() as session:
            query = session.query(Candle).filter_by(asset=asset, timeframe=timeframe)
            if as_of is not None:
                query = query.filter(Candle.close_time <= as_of)
            query = query.order_by(Candle.close_time.asc())
            rows = query.all()
            if limit is not None:
                rows = rows[-limit:]
            data = [
                {
                    "open_time": _as_utc(row.open_time),
                    "close_time": _as_utc(row.close_time),
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                }
                for row in rows
            ]
        return pd.DataFrame(data, columns=_CANDLE_COLUMNS)

    def save_context_record(self, record: ContextRecord) -> None:
        """Persist a Section 1 output record.

        Idempotent: re-evaluating the same (asset, evaluated_at,
        rule_version) overwrites the existing row rather than duplicating
        it.
        """
        with self._session_factory() as session:
            existing = (
                session.query(ContextRecord)
                .filter_by(
                    asset=record.asset,
                    evaluated_at=record.evaluated_at,
                    rule_version=record.rule_version,
                )
                .one_or_none()
            )
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
            record = (
                session.query(ContextRecord)
                .filter_by(asset=asset)
                .order_by(ContextRecord.evaluated_at.desc())
                .first()
            )
            if record is not None:
                record.evaluated_at = _as_utc(record.evaluated_at)
            return record

    def context_history(self, asset: str, since: dt.datetime | None = None) -> list[ContextRecord]:
        """Fetch context records for an asset, newest first."""
        with self._session_factory() as session:
            query = session.query(ContextRecord).filter_by(asset=asset)
            if since is not None:
                query = query.filter(ContextRecord.evaluated_at >= since)
            records = query.order_by(ContextRecord.evaluated_at.desc()).all()
            for record in records:
                record.evaluated_at = _as_utc(record.evaluated_at)
            return records
