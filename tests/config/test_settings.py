"""Settings load from the environment without requiring secrets."""

from tidemark.config.settings import Settings


def test_settings_load_with_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TIDEMARK_TELEGRAM_BOT_TOKEN", raising=False)
    settings = Settings(_env_file=None)
    assert settings.telegram_bot_token is None
    assert settings.database_url == "sqlite:///tidemark.db"
    assert settings.rulebook_dir == "docs/rulebook"


def test_secret_fields_never_render_in_repr() -> None:
    settings = Settings(_env_file=None, telegram_bot_token="super-secret-token")
    assert "super-secret-token" not in repr(settings)
    assert "super-secret-token" not in str(settings)
