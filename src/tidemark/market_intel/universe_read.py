"""A second narrow, explicitly-permitted exception to the market_intel
isolation boundary, alongside `context_read.py` - see
docs/adr/0011-market-intelligence-layer.md's "Addendum: intel
distributions". `tidemark intel distributions` needs to measure the same
symbol universe `tidemark run` operates over, so it needs read access to
the latest `UniverseSnapshot`/`UniverseSnapshotRow` the research engine's
universe-selection pipeline wrote. This module is the only place in
`market_intel` allowed to read it, and it does exactly that and nothing
else:

- imports ONLY `tidemark.data.models.UniverseSnapshot` and
  `tidemark.data.models.UniverseSnapshotRow` - the same plain ORM data
  module `context_read.py` already imports `JournalEntry` from, with the
  same zero-imports-back-into-the-research-engine property. The import-
  boundary test allows `tidemark.data` imports ONLY from
  `tidemark.data.models`, from ONLY these two files - never
  `tidemark.data.store` or `tidemark.data.symbol_source`, whose helpers
  transitively pull in `tidemark.data.exchange`.
- never imports `tidemark.data.symbol_source` and never reproduces its
  `TIDEMARK_SYMBOLS` fallback or staleness handling - `intel
  distributions` is an explicit, read-only measurement tool, and the
  task it exists for asks for the snapshot's selection "not
  TIDEMARK_SYMBOLS". Silently substituting a fallback list here would
  make a distributions run silently measure a different universe than
  the one it reports, so a missing or empty snapshot is returned as
  such (`None`, `[]`) for the caller to report plainly, never masked.
- builds its own SQLAlchemy engine directly from a database URL string,
  exactly like `context_read.py`, for the same reason: avoiding
  `data.store.create_store_engine` avoids importing `data.store` at all.
- never calls `init_db`/`create_all` - a fresh database with no
  `universe_snapshot` table yet is exactly the "no snapshot" case this
  already has to handle.
- never writes anything.
"""

from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from tidemark.data.models import UniverseSnapshot, UniverseSnapshotRow


def read_latest_selected_symbols(database_url: str, venue: str) -> tuple[str | None, list[str]]:
    """The latest `UniverseSnapshot` for `venue` and its SELECTED symbols,
    in rank order - or `(None, [])` if no snapshot exists for `venue` yet
    (including a fresh database with no `universe_snapshot` table at all).
    """
    engine = create_engine(database_url)
    try:
        session_factory = sessionmaker(bind=engine)
        try:
            with session_factory() as session:
                header_stmt = (
                    select(UniverseSnapshot)
                    .where(UniverseSnapshot.venue == venue)
                    .order_by(UniverseSnapshot.snapshot_at.desc())
                    .limit(1)
                )
                latest = session.scalars(header_stmt).first()
                if latest is None:
                    return None, []

                rows_stmt = (
                    select(UniverseSnapshotRow)
                    .where(UniverseSnapshotRow.snapshot_id == latest.snapshot_id)
                    .where(UniverseSnapshotRow.selected.is_(True))
                    .order_by(UniverseSnapshotRow.rank)
                )
                selected = [row.symbol for row in session.scalars(rows_stmt)]
                return latest.snapshot_id, selected
        except OperationalError:
            return None, []
    finally:
        engine.dispose()
