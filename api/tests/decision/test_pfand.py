"""Pfand-Hebel (01.09.): Risiko-Mathematik der Plan-Basis — reine Funktionen.

Eigene Datei neben der versiegelten tests/decision/test_policies.py (L-087).
"""

from kickbase_agent.agent.executor import PlannedAction
from kickbase_agent.decision.models import Proposal
from kickbase_agent.decision.policies import enforce_pledge_budget, pledge_risk

LEAGUE = "1234567"
HAIRCUT = 0.90


def _prop(pid: str, amount: int) -> Proposal:
    return Proposal(
        action=PlannedAction(
            action_type="bid", league_id=LEAGUE, player_id=pid, amount=amount,
            rule_id="buy_explosion",
        ),
        rule_id="buy_explosion",
        rationale="r",
        score=0.6,
        requires_approval=False,
        player_name=pid,
    )


def test_pledge_risk_ist_verlust_bei_rueckverkauf():
    # Gebot 5,47M auf MW 5,35M: Verlust = 5,47 − 0,9 × 5,35 = 0,655M (int-Kappung).
    assert pledge_risk(5_466_079, 5_345_826, HAIRCUT) == 5_466_079 - int(5_345_826 * 0.9)


def test_pledge_risk_nie_negativ():
    # Underpay-Gebot unter dem Rückverkaufserlös: Gewinn, Risiko 0.
    assert pledge_risk(4_000_000, 5_000_000, HAIRCUT) == 0


def test_enforce_haelt_die_risikosumme_unter_dem_budget():
    mv = {"a": 10_000_000, "b": 10_000_000, "c": 10_000_000}
    # je Gebot 110 % MW ⇒ Risiko je 11M − 9M = 2M; Budget 5M ⇒ zwei passen, das dritte nicht.
    props = [_prop(p, 11_000_000) for p in ("a", "b", "c")]
    kept, dropped = enforce_pledge_budget(props, mv, 5_000_000, HAIRCUT)
    assert [p.action.player_id for p in kept] == ["a", "b"] and dropped == 1
    used = sum(pledge_risk(p.action.amount, mv[p.action.player_id], HAIRCUT) for p in kept)
    assert used <= 5_000_000


def test_enforce_ueberspringt_zu_grosse_und_nimmt_spaetere_kleine():
    # propose_buys sortiert nach Score — der Filter respektiert die Ordnung,
    # überspringt aber nur den Übergroßen (kein break): das kleine Risiko
    # dahinter passt noch.
    mv = {"gross": 10_000_000, "klein": 1_000_000}
    props = [_prop("gross", 13_000_000), _prop("klein", 1_100_000)]
    kept, dropped = enforce_pledge_budget(props, mv, 3_900_000, HAIRCUT)
    assert [p.action.player_id for p in kept] == ["klein"]  # gross: Risiko 4,0M > 3,9M
    assert dropped == 1


def test_enforce_grenzfall_exakt_am_budget():
    mv = {"a": 10_000_000}
    kept, dropped = enforce_pledge_budget([_prop("a", 13_000_000)], mv, 4_000_000, HAIRCUT)
    assert len(kept) == 1 and dropped == 0  # Risiko 4,0M ≤ Budget 4,0M


def test_enforce_verwirft_ohne_marktwert_konservativ():
    kept, dropped = enforce_pledge_budget([_prop("x", 1_000_000)], {}, 10_000_000, HAIRCUT)
    assert kept == [] and dropped == 1


def test_nullrisiko_gebote_verbrauchen_kein_budget():
    mv = {"a": 5_000_000, "b": 5_000_000}
    props = [_prop("a", 4_000_000), _prop("b", 4_000_000)]  # beide unter 0,9×MW
    kept, dropped = enforce_pledge_budget(props, mv, 0, HAIRCUT)
    assert len(kept) == 2 and dropped == 0
