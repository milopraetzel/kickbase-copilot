"""Verfassung der Engine: geschützte Spieler + gestaffelte Overpay-Kappe.

Beide Werte sind KEINE gewöhnlichen Stellschrauben — `protected_player_ids` ist
Emils Anweisung in Code gegossen, die Kappen-Staffelung eine belegte Entscheidung
mit Decision-Record. Diese Tests halten die Zusagen fest.
"""

import kickbase_agent.config as cfg
from kickbase_agent.decision.guardrails import (
    DEFAULT_GUARDRAILS,
    DEFAULT_PROTECTED_PLAYER_IDS,
    OLISE_PLAYER_ID,
    PROTECTED_ENV_VAR,
    Guardrails,
    protected_player_ids,
)


def _reset_settings():
    cfg._settings = None


# ------------------------------------------------------------ geschützte Spieler


def test_olise_is_protected_by_default(monkeypatch):
    """Ohne jede Konfiguration steht Olise (8329) unter Schutz."""
    monkeypatch.delenv(PROTECTED_ENV_VAR, raising=False)
    _reset_settings()
    assert OLISE_PLAYER_ID == "8329"
    assert DEFAULT_PROTECTED_PLAYER_IDS == frozenset({"8329"})
    # Default der Settings (ohne .env-Override) enthält Olise
    assert "8329" in cfg.Settings(database_url="sqlite://").protected_player_ids


def test_env_overrides_the_protected_list(monkeypatch):
    monkeypatch.setenv(PROTECTED_ENV_VAR, "8329, 4711 ,,")
    _reset_settings()
    assert protected_player_ids() == frozenset({"8329", "4711"})


def test_empty_env_means_no_protection_old_behaviour(monkeypatch):
    """Leere Liste = Altverhalten (kein Spieler geschützt)."""
    monkeypatch.setenv(PROTECTED_ENV_VAR, "")
    _reset_settings()
    assert protected_player_ids() == frozenset()


def test_broken_settings_fall_back_to_protection(monkeypatch):
    """Fail-safe: Ein kaputter Settings-Ladevorgang darf Olise nicht freigeben."""
    monkeypatch.delenv(PROTECTED_ENV_VAR, raising=False)

    def _boom():
        raise RuntimeError("keine .env")

    monkeypatch.setattr(cfg, "get_settings", _boom)
    assert protected_player_ids() == DEFAULT_PROTECTED_PLAYER_IDS


def test_all_protected_ids_unions_env_and_explicit_guardrails(monkeypatch):
    monkeypatch.setenv(PROTECTED_ENV_VAR, "8329")
    _reset_settings()
    g = Guardrails(protected_player_ids=frozenset({"9"}))
    assert g.all_protected_ids == frozenset({"8329", "9"})
    # Feld selbst bleibt unverändert (nur die Verfassung kommt dazu)
    assert g.protected_player_ids == frozenset({"9"})


# ------------------------------------------------------------ gestaffelte Kappe


def test_bid_premium_is_staggered_and_reaches_the_expectation_tiers():
    """Standard bleibt bei 110 %; Kader-Bedarf erreicht die 120-/135-%-Stufen
    der Erwartungsformel (trading/expectation.py)."""
    g = DEFAULT_GUARDRAILS
    assert g.max_bid_premium == 1.10
    assert g.max_bid_premium_need == 1.35
    # Ziel-Aufschlag: Standard ~8 %, Kader-Bedarf 20 % (= 120 % MW, p 0.60)
    assert g.bid_overpay_fraction == 0.08
    assert g.bid_overpay_need == 0.20
    assert 1 + g.bid_overpay_need >= 1.20
    assert g.max_bid_premium_need >= 1.35  # obere Stufe (p 0.80) erreichbar
    assert g.max_bid_premium_need > g.max_bid_premium


def test_expectation_tiers_are_reachable_within_the_need_cap():
    """Die Erwartungsformel darf keine Stufe kennen, die der Wächter sperrt."""
    from kickbase_agent.trading.expectation import KICKBASE, estimate_bid_success

    mv = 1_000_000
    g = DEFAULT_GUARDRAILS
    at_cap = int(mv * g.max_bid_premium_need)
    assert estimate_bid_success(kind=KICKBASE, amount=at_cap, market_value=mv).p == 0.80
    at_target = int(mv * (1 + g.bid_overpay_need))
    assert estimate_bid_success(kind=KICKBASE, amount=at_target, market_value=mv).p == 0.60
    # Standard-Kappe bleibt bewusst in der untersten Stufe
    at_std = int(mv * g.max_bid_premium)
    assert estimate_bid_success(kind=KICKBASE, amount=at_std, market_value=mv).p == 0.45
