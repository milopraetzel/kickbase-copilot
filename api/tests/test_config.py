from kickbase_agent.config import Settings


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://kba:kba@localhost:5432/kickbase")

    settings = Settings()

    assert settings.kickbase_email == "test@example.com"
    assert settings.kickbase_password == "secret"
    assert settings.kickbase_base_url == "https://api.kickbase.com"
    assert settings.kickbase_rate_limit_per_second == 1.0


def test_settings_rejects_missing_required_fields(monkeypatch):
    monkeypatch.delenv("KICKBASE_EMAIL", raising=False)
    monkeypatch.delenv("KICKBASE_PASSWORD", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
