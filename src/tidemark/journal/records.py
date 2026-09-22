"""Append-only observation log.

Every evaluation run — including runs that find no setups — is recorded.
"No setups found" is a successful run, not a failure, and is journaled the
same as any other outcome.
"""

from __future__ import annotations

from tidemark.data.models import ContextRecord, JournalEntry


class Journal:
    """Append-only log of rulebook evaluations.

    Not implemented in Phase 0.
    """

    def append(self, record: ContextRecord, note: str) -> JournalEntry:
        """Append an observation for a given evaluation record.

        Must never mutate or delete a prior entry.
        """
        raise NotImplementedError
