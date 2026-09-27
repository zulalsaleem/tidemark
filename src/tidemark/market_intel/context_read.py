"""A single, narrow, explicitly-permitted exception to the market_intel
isolation boundary - see docs/adr/0011-market-intelligence-layer.md's
"Addendum: Merge 3" for the full reasoning. The hourly BTC briefing needs
BTC's stored Section 1 (structure) result; this module is the only place
in `market_intel` allowed to read it, and it does exactly that and
nothing else:

- imports ONLY `tidemark.data.models.ContextRecord` - a plain ORM data
  class with zero imports of its own back into `context`/`journal`/
  `replay`/`data.exchange` (this is what makes it safe to read at all;
  the import-boundary test enforces this by allowing `tidemark.data`
  imports ONLY from `tidemark.data.models`, nothing else under
  `tidemark.data` - in particular never `tidemark.data.store`, whose
  `TidemarkStore` transitively imports `tidemark.data.exchange` to
  type-hint candle fetching, which would smuggle a forbidden import in
  through the back door).
- never imports `tidemark.context` (the Section 1 evaluation logic) and
  never recomputes or duplicates any of it - this is a read of an
  already-stored result, nothing more.
- builds its own SQLAlchemy engine directly from a database URL string,
  rather than reusing `data.store.create_store_engine` - a one-line
  wrapper around `sqlalchemy.create_engine` this module has its own
  trivial copy of, for the same reason as above.
- never calls `init_db`/`create_all`, mirroring `health.checks`'s own
  database check: it must see the research database exactly as it is,
  and "no context_records table yet" is exactly the "no context record"
  case this already has to handle.
- never writes anything. Derivatives data never flows back into Section
  1 through this module or any other - there is no write path here at
  all.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from tidemark.data.models import ContextRecord

# One missed 4H evaluation cycle of grace before the briefing treats a
# stored Section 1 record as too old to show. This is an operational
# freshness guard `market_intel` owns for its own display purposes, not
# a rulebook parameter - Section 1 itself defines no "staleness" concept
# for its own output, and this module invents no Section 1 behavior.
CONTEXT_RECORD_STALE_AFTER = dt.timedelta(hours=8)


def read_latest_context_record(database_url: str, asset: str) -> ContextRecord | None:
    """The most recent Section 1 `ContextRecord` for `asset`, or `None`
    if there isn't one - including a fresh database with no
    `context_records` table at all yet.
    """
    engine = create_engine(database_url)
    try:
        session_factory = sessionmaker(bind=engine)
        try:
            with session_factory() as session:
                stmt = (
                    select(ContextRecord)
                    .where(ContextRecord.asset == asset)
                    .order_by(ContextRecord.evaluated_at.desc())
                    .limit(1)
                )
                return session.scalars(stmt).first()
        except OperationalError:
            return None
    finally:
        engine.dispose()


def is_stale(record: ContextRecord, now: dt.datetime) -> bool:
    return now - record.evaluated_at > CONTEXT_RECORD_STALE_AFTER
