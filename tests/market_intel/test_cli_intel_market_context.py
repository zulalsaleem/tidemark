"""`tidemark intel alert` and the market-context section of `intel market`,
through the real CLI dispatch. No live network: `_coinalyze_client` is
replaced by the same fake client the bot tests use.
"""

from __future__ import annotations

import datetime as dt

import pytest
from test_coin_market_context import _database, assert_no_banned_language, seed_section1
from test_market_context import BTC_SYMBOL, ETH_SYMBOL, _FakeMarketClient, _reading
from typer.testing import CliRunner

from tidemark import cli as cli_module
from tidemark.cli import app

runner = CliRunner()
TRUMP = "TRUMP/USDT:USDT"


def _fresh() -> dt.datetime:
    """The CLI reads the real clock, so a 'fresh' record is one from the last hour."""
    return dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    database_url = _database(tmp_path)
    monkeypatch.setenv("TIDEMARK_DATABASE_URL", database_url)
    client = _FakeMarketClient(
        {
            TRUMP: _reading(price_pct=-0.4, liq_long=2000.0, liq_short=500.0),
            BTC_SYMBOL: _reading(price_pct=0.1),
            ETH_SYMBOL: _reading(price_pct=0.05),
        }
    )
    monkeypatch.setattr(cli_module, "_coinalyze_client", lambda settings: client)
    return database_url


def test_intel_alert_prints_the_watch_alert_when_a_watch_is_active(cli_env) -> None:
    seed_section1(cli_env, TRUMP, "BEARISH", "SHORT_WATCH", _fresh())
    seed_section1(cli_env, BTC_SYMBOL, "BULLISH", "LONG_WATCH", _fresh())

    result = runner.invoke(app, ["intel", "alert", "--symbol", TRUMP])

    assert result.exit_code == 0, result.output
    assert "SHORT WATCH" in result.output
    assert "TRUMP/USDT" in result.output
    assert "4H structure: BEARISH" in result.output
    assert "Relative strength vs BTC: COIN WEAKER THAN BTC" in result.output
    assert "BTC alignment: AGAINST BTC" in result.output
    assert "Liquidation imbalance: LONG LIQUIDATIONS > SHORT" in result.output
    assert "POSITION FLOW" in result.output
    assert "KEY DERIVATIVES" in result.output
    assert "WHAT TO WATCH" in result.output
    assert "Rulebooks: section-01-v1.1, position-flow-v0.1" in result.output
    assert_no_banned_language(result.output)


def test_intel_alert_says_so_and_sends_nothing_when_no_watch_is_active(cli_env) -> None:
    seed_section1(cli_env, TRUMP, "NEUTRAL", "WAIT", _fresh())

    result = runner.invoke(app, ["intel", "alert", "--symbol", TRUMP])

    assert result.exit_code == 0, result.output
    assert (
        "No Section 1 WATCH is active for TRUMP/USDT:USDT; no alert would be sent." in result.output
    )
    assert "WHAT TO WATCH" not in result.output


def test_intel_market_prints_the_market_context_after_layer_one(cli_env) -> None:
    result = runner.invoke(app, ["intel", "market", "--symbol", TRUMP])

    assert result.exit_code == 0, result.output
    assert "Open interest: " in result.output  # Layer 1 unchanged
    assert "MARKET CONTEXT" in result.output
    assert "Relative strength vs BTC: COIN WEAKER THAN BTC" in result.output
    assert "ETH (secondary reference)" in result.output
    assert "WHAT TO WATCH" in result.output
    assert_no_banned_language(result.output)


def test_intel_market_json_is_unchanged_and_carries_no_market_context(cli_env) -> None:
    result = runner.invoke(app, ["intel", "market", "--symbol", TRUMP, "--json"])

    assert result.exit_code == 0, result.output
    assert "MARKET CONTEXT" not in result.output
    assert '"market_status": "OK"' in result.output
