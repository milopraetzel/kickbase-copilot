"""Kauf-Diagnose: warum kam kein Kaufvorschlag zustande?

Lehre vom 28.08.: Der Planner legte null Vorhaben an, obwohl das Kader-Gesetz
verletzt war (0 aufstellbare Stuermer vor Spieltag 1). Der Grund — kein Kandidat
passte in die an das KONTO gekoppelte Gebotskappe (60 % von 9,23 M = 5,54 M),
waehrend die Kaufkraft 56 M auswies — war nur durch Code-Lesen und einen Lauf auf
der Box zu ermitteln. `buy_blocker_reason` beantwortet dieselbe Frage in einer
Zeile, damit „0 Vorhaben" nicht mehr wie ein defekter Planner aussieht.

Eigene Datei, weil `test_policies.py` durch `guardrails.lock` versiegelt ist —
neu versiegeln ist laut Betriebsanleitung ein bewusster menschlicher Akt.
"""

from kickbase_agent.decision.guardrails import Guardrails
from kickbase_agent.decision.policies import buy_blocker_reason
from kickbase_agent.intel.models import ExplosionCandidate


def cand(
    pid: str,
    mv: int,
    score: float,
    name: str = "Spieler",
    position: int = 3,
) -> ExplosionCandidate:
    return ExplosionCandidate(
        player_id=pid,
        name=name,
        team_id="1",
        position=position,
        current_market_value=mv,
        total_score=score,
        signals=[],
    )


def test_keine_meldung_wenn_ein_kauf_moeglich_waere() -> None:
    """Kandidat ueber Schwelle UND unter der Kappe ⇒ es gibt nichts zu erklaeren."""
    assert buy_blocker_reason([cand("a", 1_000_000, 0.80)], 10_000_000, Guardrails()) is None


def test_meldet_leeres_kauf_universum() -> None:
    reason = buy_blocker_reason([], 10_000_000, Guardrails())
    assert reason is not None
    assert "Kauf-Universum" in reason


def test_meldet_zu_schwache_scores_als_marktlage() -> None:
    """Alle unter der Schwelle ⇒ die Marktlage bremst, nicht das Geld."""
    reason = buy_blocker_reason([cand("a", 1_000_000, 0.20)], 10_000_000, Guardrails())
    assert reason is not None
    assert "Schwelle" in reason
    assert "0.20" in reason
    assert "Marktlage" in reason


def test_meldet_gebotskappe_und_nennt_den_billigsten_kandidaten() -> None:
    """Der reale Fall vom 28.08.: Score reicht, aber das Konto deckelt das Gebot."""
    reason = buy_blocker_reason(
        [
            cand("teuer", 20_000_000, 0.70),
            cand("billig", 6_490_000, 0.60, name="Schnellbacher"),
        ],
        9_231_524,  # Konto am 28.08. ⇒ Kappe 5,54 M
        Guardrails(),
    )
    assert reason is not None
    assert "Gebotskappe" in reason
    # Der BILLIGSTE ueber der Schwelle ist die nuetzliche Information: an ihm
    # laesst sich ablesen, wie viel Konto fehlt.
    assert "Schnellbacher" in reason
    assert "fehlen" in reason


def test_positions_not_senkt_die_schwelle_auch_in_der_diagnose() -> None:
    """Sonst meldet die Diagnose „zu schwach", wo propose_buys laengst kaufen wuerde."""
    g = Guardrails()
    schwach = cand("s", 30_000_000, g.buy_score_threshold - 0.03, position=4)
    assert "Schwelle" in (buy_blocker_reason([schwach], 9_231_524, g) or "")
    assert "Gebotskappe" in (
        buy_blocker_reason([schwach], 9_231_524, g, need_positions={4}) or ""
    )
