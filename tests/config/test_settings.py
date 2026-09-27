"""Settings load from the environment without requiring secrets."""

from tidemark.config.settings import Settings


def test_settings_load_with_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TIDEMARK_TELEGRAM_BOT_TOKEN", raising=False)
    settings = Settings(_env_file=None)
    assert settings.telegram_bot_token is None
    assert settings.database_url == "sqlite:///tidemark.db"
    assert settings.rulebook_dir == "docs/rulebook"
    assert settings.venue == "binanceusdm"
    assert settings.backfill_days == 180


def test_symbol_list_parses_and_trims_csv() -> None:
    settings = Settings(_env_file=None, symbols=" BTC/USDT:USDT ,ETH/USDT:USDT,, ")
    assert settings.symbol_list() == ["BTC/USDT:USDT", "ETH/USDT:USDT"]


def test_secret_fields_never_render_in_repr() -> None:
    settings = Settings(_env_file=None, telegram_bot_token="super-secret-token")
    assert "super-secret-token" not in repr(settings)
    assert "super-secret-token" not in str(settings)


def test_no_exchange_credential_fields() -> None:
    """Public market data only — no field may hold an *exchange* trading
    credential (`data/exchange.py`'s ccxt venue takes no `apiKey`/
    `secret` at all — see ADR 0002). `coinalyze_api_key` is exempt: it's
    a read-only third-party derivatives-data key for the strictly
    separate `market_intel` layer (ADR 0011), never a venue with trading
    permissions.
    """
    exempt = {"coinalyze_api_key"}
    field_names = Settings.model_fields.keys()
    suspect = {name for name in field_names if "api_key" in name or "api_secret" in name} - exempt
    assert not suspect
