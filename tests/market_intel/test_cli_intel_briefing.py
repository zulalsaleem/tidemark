"""`tidemark intel briefing [--send] [--json]`: missing settings exit
cleanly; without --send nothing is ever delivered; --json is valid and
matches the text render's decision. No live network: `_coinalyze_client`/
`_telegram_bot_client` are monkeypatched, exactly like elsewhere in this
suite.
"""

from __future__ import annotations

import datetime as dt
import json as json_module

from typer.testing import CliRunner

from tidemark import cli as cli_module
from tidemark.cli import app
from tidemark.market_intel.evaluation_store import (
    EvaluationRecord,
    init_evaluation_store,
    make_engine,
    record_evaluation,
)

runner = CliRunner()

COINALYZE_SYMBOL = "BTCUSDT_PERP.A"


def _market_row(**overrides) -> dict:
    base = {
        "symbol": COINALYZE_SYMBOL,
        "exchange": "A",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "is_perpetual": True,
        "has_long_short_ratio_data": True,
        "has_ohlcv_data": True,
        "has_buy_sell_data": True,
        "oi_lq_vol_denominated_in": "BASE_ASSET",
    }
    base.update(overrides)
    return base


def _history_response(period_start_epoch: int, **fields) -> list[dict]:
    return [{"symbol": COINALYZE_SYMBOL, "history": [{"t": period_start_epoch, **fields}]}]


class _FakeCoinalyze:
    """Drives D1 (price up, OI up, funding positive)."""

    def future_markets(self):
        return [_market_row()]

    def ohlcv_history(self, symbol, interval, from_ts, to_ts):
        return _history_response(from_ts, o=100.0, h=101.0, l=99.0, c=101.0)

    def open_interest_history(self, symbol, interval, from_ts, to_ts, convert_to_usd=True):
        return _history_response(from_ts, o=1000.0, h=1010.0, l=995.0, c=1010.0)

    def funding_rate_history(self, symbol, interval, from_ts, to_ts):
        return [
            {
                "symbol": COINALYZE_SYMBOL,
                "history": [
                    {"t": from_ts, "o": 0.005, "h": 0.006, "l": 0.004, "c": 0.005},
                    {"t": to_ts + 1 - 3600, "o": 0.005, "h": 0.011, "l": 0.005, "c": 0.01},
                ],
            }
        ]


class _FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    def send_message(self, chat_id, text) -> None:
        self.sent.append((chat_id, text))


def _use_temp_db(tmp_path, monkeypatch) -> None:
    db_path = (tmp_path / "tidemark.db").as_posix()
    monkeypatch.setenv("TIDEMARK_DATABASE_URL", f"sqlite:///{db_path}")


# -- missing settings exit cleanly --------------------------------------------


def test_missing_coinalyze_key_exits_cleanly(monkeypatch, tmp_path) -> None:
    _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("TIDEMARK_COINALYZE_API_KEY", "")

    result = runner.invoke(app, ["intel", "briefing"])

    assert result.exit_code == 1
    assert "TIDEMARK_COINALYZE_API_KEY is not set" in result.output


# -- without --send, nothing is ever delivered --------------------------------


def test_without_send_flag_never_calls_telegram_even_when_it_would_send(
    monkeypatch, tmp_path
) -> None:
    _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(cli_module, "_coinalyze_client", lambda settings: _FakeCoinalyze())
    fake_telegram = _FakeTelegram()
    monkeypatch.setattr(cli_module, "_telegram_bot_client", lambda settings: fake_telegram)

    result = runner.invoke(app, ["intel", "briefing"])

    assert result.exit_code == 0
    assert fake_telegram.sent == []  # never contacted, --send was not given
    assert "sent=False" in result.output


def test_first_run_prints_the_briefing_text(monkeypatch, tmp_path) -> None:
    _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(cli_module, "_coinalyze_client", lambda settings: _FakeCoinalyze())

    result = runner.invoke(app, ["intel", "briefing"])

    assert result.exit_code == 0
    assert "BTC STRUCTURE" in result.output
    assert "DERIVATIVES CONTEXT" in result.output
    assert "D1:" in result.output
    assert "rulebook: derivatives-context-v0.1" in result.output


# -- --json is valid and matches the decision ---------------------------------


def test_json_output_is_valid_and_matches_the_text_decision(monkeypatch, tmp_path) -> None:
    _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(cli_module, "_coinalyze_client", lambda settings: _FakeCoinalyze())

    result = runner.invoke(app, ["intel", "briefing", "--json"])

    assert result.exit_code == 0
    payload = json_module.loads(result.output)
    assert payload["classification"]["result"] == "D1"
    assert payload["should_send"] is False  # first evaluation ever
    assert payload["sent"] is False
    assert payload["structure"]["available"] is False


# -- --send actually delivers when the evaluation decides to ------------------


def test_send_flag_delivers_when_the_evaluation_decides_to_send(monkeypatch, tmp_path) -> None:
    _use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("TIDEMARK_TELEGRAM_ALLOWED_CHAT_ID", "111")

    # Deterministically seed a real "previous hour, D1, sent" evaluation
    # so this run's D4 classification is a genuine classification-change,
    # without depending on two CLI invocations landing in different
    # closed-1H windows by wall-clock luck.
    db_path = (tmp_path / "tidemark.db").as_posix()
    database_url = f"sqlite:///{db_path}"
    evaluation_engine = make_engine(database_url)
    init_evaluation_store(evaluation_engine)
    previous_hour = dt.datetime.now(dt.UTC).replace(
        minute=0, second=0, microsecond=0
    ) - dt.timedelta(hours=1)
    record_evaluation(
        evaluation_engine,
        EvaluationRecord(
            asset="BTC/USDT:USDT",
            evaluated_at=previous_hour,
            recorded_at=previous_hour,
            rulebook_version="derivatives-context-v0.1",
            classification="D1",
            classification_reason=None,
            price_status="OK",
            price_change_pct=1.0,
            price_period_start=previous_hour - dt.timedelta(hours=1),
            price_period_close=previous_hour,
            oi_status="OK",
            oi_change_pct=1.0,
            oi_period_start=previous_hour - dt.timedelta(hours=1),
            oi_period_close=previous_hour,
            funding_status="OK",
            funding_value=0.01,
            funding_previous_value=0.005,
            funding_period_start=previous_hour - dt.timedelta(hours=1),
            funding_period_close=previous_hour,
            structure_available=False,
            structure_state=None,
            structure_watch=None,
            structure_grade=None,
            structure_rule_version=None,
            structure_evaluated_at=None,
            sent=True,
            send_reason=None,
        ),
    )

    # Current hour drives D4 (price down, OI down, funding positive).
    class _D4Coinalyze(_FakeCoinalyze):
        def ohlcv_history(self, symbol, interval, from_ts, to_ts):
            return _history_response(from_ts, o=100.0, h=100.0, l=99.0, c=99.0)

        def open_interest_history(self, symbol, interval, from_ts, to_ts, convert_to_usd=True):
            return _history_response(from_ts, o=1000.0, h=1000.0, l=985.0, c=985.0)

    fake_telegram = _FakeTelegram()
    monkeypatch.setattr(cli_module, "_telegram_bot_client", lambda settings: fake_telegram)
    monkeypatch.setattr(cli_module, "_coinalyze_client", lambda settings: _D4Coinalyze())

    result = runner.invoke(app, ["intel", "briefing", "--send"])

    assert result.exit_code == 0
    assert "should_send=True" in result.output
    assert "sent=True" in result.output
    assert len(fake_telegram.sent) == 1
    chat_id, text = fake_telegram.sent[0]
    assert chat_id == 111
    assert "D4" in text
