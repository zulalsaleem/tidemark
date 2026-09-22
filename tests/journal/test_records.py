"""Journal is unimplemented in Phase 0 but importable and callable."""

import pytest

from tidemark.journal.records import Journal


def test_append_not_implemented() -> None:
    journal = Journal()
    with pytest.raises(NotImplementedError):
        journal.append(record=None, note="no setups found")
