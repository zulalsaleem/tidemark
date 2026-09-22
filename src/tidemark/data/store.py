"""SQLite persistence via SQLAlchemy.

Engine/session construction is plumbing and is implemented here. Read/write
operations against the store are strategy-adjacent and are left as
`NotImplementedError` until the corresponding rulebook-driven logic exists.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from tidemark.data.models import Base


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
    """Read/write access to candles, swings, levels, and context records.

    Not implemented in Phase 0 — this class defines the surface that later
    phases will fill in once the corresponding rulebook logic exists.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save_candles(self, candles: list) -> None:
        """Persist a batch of closed candles."""
        raise NotImplementedError

    def get_candles(self, asset: str, timeframe: str, limit: int) -> list:
        """Fetch the most recent closed candles for an asset/timeframe."""
        raise NotImplementedError

    def save_context_record(self, record: object) -> None:
        """Persist a Section 1 output record."""
        raise NotImplementedError

    def latest_context_record(self, asset: str) -> object | None:
        """Fetch the most recent context record for an asset, if any."""
        raise NotImplementedError
