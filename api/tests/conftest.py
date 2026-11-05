import pytest


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "pw")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    # Test-Isolation von der lokalen .env (Token/Liga/Agent-Flags dürfen nicht leaken)
    monkeypatch.setenv("KICKBASE_TOKEN", "")
    monkeypatch.setenv("KICKBASE_REFRESH_TOKEN", "")
    monkeypatch.setenv("KICKBASE_LEAGUE_ID", "")
    monkeypatch.setenv("AGENT_DRY_RUN", "1")
    monkeypatch.setenv("AGENT_TEST_LEAGUE_ID", "")
    monkeypatch.setenv("AGENT_ALLOW_LIVE", "0")
    monkeypatch.setenv("AGENT_AUTO_LINEUP", "0")
    monkeypatch.setenv("AGENT_AUTO_INTENTS", "0")
    # Verfassung (26.08.): geschützte Spieler sind in Tests IMMER die Default-
    # Liste (Olise, 8329). Explizit gesetzt, damit das Ergebnis nicht daran
    # hängt, was gerade in Emils .env steht — Tests, die eine andere Liste
    # brauchen, überschreiben gezielt (monkeypatch.setenv).
    monkeypatch.setenv("PROTECTED_PLAYER_IDS", "8329")
    # Reset the singleton in config so fresh env is picked up per test
    import kickbase_agent.config as cfg

    cfg._settings = None
    yield
    cfg._settings = None

# --- Zwei-DB-Fix-Sicherung (24.08., Vorfall Intents #5/#6): Die lokale .env kann
# KBA_REMOTE_API_URL setzen — damit würden Write-Tool-Tests ECHTE Vorhaben auf der
# Box-API anlegen (genau das passierte beim ersten Suite-Lauf: Test-Payloads
# "Spieler 99"/"Kimmich sell" landeten als Intents #5/#6 in der Prod-DB, beide vom
# Preflight refused). Autouse: Remote-Modus ist in Tests IMMER aus; Tests, die ihn
# brauchen, setzen die Variablen gezielt selbst (tests/mcp/test_mcp_remote.py).
#: Env-Variablen, die PRODUKTIONSVERHALTEN steuern und aus Emils lokaler .env in
#: die Suite leaken würden. Regel (zweimal reingefallen, 24.+25.08.): Wer eine
#: solche Variable neu einführt, trägt sie HIER ein — sonst hängt das Testergebnis
#: davon ab, was gerade in der .env steht (Remote-URL ⇒ echte Vorhaben; Write-Token
#: ⇒ 401 in allen Intent-Tests).
_PRODUCTION_ENV_VARS = (
    "KBA_REMOTE_API_URL",
    "KBA_REMOTE_API_AUTH",
    "KBA_API_URL",
    "KBA_API_AUTH",
    "KBA_API_WRITE_TOKEN",
    # Schlüssel leer ⇒ Verletzungs-Schwere fragt in Tests nie die echte TypeSafe-API.
    "TYPESAFE_API_KEY",
)
#: Dieselbe Regel für SCHALTER: Ein leerer String wäre für ein bool kein gültiger
#: Wert (pydantic wirft), deshalb stehen sie mit ihrem Aus-Wert hier.
#: AGENT_SELF_CHANGE ärmt den Selbstumbau (agent/self_change.py) — läge er aus
#: Emils .env auf 1, würden Tests echte Commits und Deploys anstoßen.
_PRODUCTION_ENV_FLAGS = {
    "AGENT_SELF_CHANGE": "0",
    # Handels-Autopilot: im Code Default an (Emils Auftrag 01.10.2026); Tests sehen immer
    # diesen Wert, egal was in Emils .env steht. Den Halt prüfen Tests gezielt.
    "AGENT_TRADE_AUTOPILOT": "1",
}


@pytest.fixture(autouse=True)
def _no_remote_api_in_tests(monkeypatch):
    for name in _PRODUCTION_ENV_VARS:
        monkeypatch.setenv(name, "")
    for name, off in _PRODUCTION_ENV_FLAGS.items():
        monkeypatch.setenv(name, off)


@pytest.fixture(autouse=True)
def jev_rows(monkeypatch):
    """Jev-Protokoll in Tests (Spec 2026-10-01): nie in eine Datenbank schreiben, sondern
    mitschreiben. Tests, die prüfen, was ein Judge protokolliert, nehmen diese Fixture als
    Argument und lesen die Liste."""
    from kickbase_agent.intel import jev_journal

    rows: list = []
    monkeypatch.setattr(jev_journal, "_writer", rows.extend)
    return rows
