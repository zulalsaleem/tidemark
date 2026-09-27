"""Persists the Telegram `getUpdates` offset so a restart never replays
an already-processed message and never skips a new one.

A flat JSON file, not a database table - this has no relationship
whatsoever to `tidemark.db`'s research tables, which is a stronger form
of the market_intel isolation boundary than merely using a different
table in the same database would be. Written atomically (temp file +
rename) so a crash mid-write can never corrupt the last known-good
offset.
"""

from __future__ import annotations

import json
from pathlib import Path


class BotStateStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load_offset(self) -> int | None:
        if not self._path.exists():
            return None
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        offset = payload.get("offset")
        return int(offset) if offset is not None else None

    def save_offset(self, offset: int) -> None:
        if self._path.parent != Path():
            self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(json.dumps({"offset": offset}), encoding="utf-8")
        tmp_path.replace(self._path)
