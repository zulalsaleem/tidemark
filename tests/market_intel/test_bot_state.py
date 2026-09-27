"""BotStateStore: the flat-file offset persistence that keeps a restart
from replaying old messages or skipping new ones.
"""

from __future__ import annotations

from tidemark.market_intel.bot_state import BotStateStore


def test_load_offset_returns_none_when_no_file_exists(tmp_path) -> None:
    store = BotStateStore(tmp_path / "offset.json")
    assert store.load_offset() is None


def test_save_then_load_round_trips(tmp_path) -> None:
    store = BotStateStore(tmp_path / "offset.json")
    store.save_offset(42)

    assert store.load_offset() == 42


def test_save_overwrites_the_previous_offset(tmp_path) -> None:
    store = BotStateStore(tmp_path / "offset.json")
    store.save_offset(1)
    store.save_offset(2)

    assert store.load_offset() == 2


def test_a_new_store_instance_sees_the_persisted_offset(tmp_path) -> None:
    path = tmp_path / "offset.json"
    BotStateStore(path).save_offset(7)

    assert BotStateStore(path).load_offset() == 7


def test_corrupt_file_is_treated_as_no_offset_rather_than_crashing(tmp_path) -> None:
    path = tmp_path / "offset.json"
    path.write_text("not valid json{{{", encoding="utf-8")

    assert BotStateStore(path).load_offset() is None


def test_save_creates_missing_parent_directories(tmp_path) -> None:
    path = tmp_path / "nested" / "dir" / "offset.json"
    store = BotStateStore(path)
    store.save_offset(3)

    assert store.load_offset() == 3


def test_save_does_not_leave_a_temp_file_behind(tmp_path) -> None:
    path = tmp_path / "offset.json"
    BotStateStore(path).save_offset(1)

    assert not path.with_suffix(".json.tmp").exists()
    assert path.exists()
