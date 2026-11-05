"""Accept-Pfad seit 16.09.2026: Emils Listungspreis ist die Untergrenze.

Bewusst eine eigene, unversiegelte Datei: `tests/decision/test_policies.py` ist
über `guardrails.lock` versiegelt und bleibt unverändert — die dortigen Tests
laufen mit `listing_price=0` und dem Default-Floor weiter genau so wie vorher.
"""

from kickbase_agent.decision.guardrails import Guardrails
from kickbase_agent.decision.policies import (
    IncomingOffer,
    offers_below_asking,
    propose_accepts,
)

LEAGUE = "L1"


def _offer(pid: str, mv: int, amount: int, oid: str = "O1", *, asking: int = 0) -> IncomingOffer:
    return IncomingOffer(pid, pid, "1", 3, mv, amount, oid, listing_price=asking)


def _squad(n_mf: int = 5, n_st: int = 2, n_def: int = 4) -> dict[str, int]:
    squad = {"gk": 1}
    squad.update({f"d{i}": 2 for i in range(n_def)})
    squad.update({f"m{i}": 3 for i in range(n_mf)})
    squad.update({f"s{i}": 4 for i in range(n_st)})
    return squad


def test_offer_below_asking_price_is_never_proposed():
    """Olise-Fall: 103 % MW reichen nicht, wenn Emil 100 M verlangt."""
    squad = _squad()  # 12 Spieler → 11er-Floor hält auch nach einer Annahme
    offers = [_offer("m0", 65_000_000, 70_000_000, "A", asking=100_000_000)]
    assert propose_accepts(offers, squad, LEAGUE, Guardrails()) == []
    below = offers_below_asking(offers, squad)
    assert [(o.player_id, o.amount) for o in below] == [("m0", 70_000_000)]


def test_offer_at_or_above_asking_price_is_proposed_with_asking_in_rationale():
    squad = _squad()
    offers = [_offer("m0", 65_000_000, 100_000_000, "A", asking=100_000_000)]
    props = propose_accepts(offers, squad, LEAGUE, Guardrails())
    assert [p.action.offer_id for p in props] == ["A"]
    assert "≥ Listungspreis 100.00M" in props[0].rationale
    assert props[0].requires_approval is True
    assert offers_below_asking(offers, squad) == []


def test_mv_floor_still_applies_even_when_asking_price_is_low():
    """Ein zu niedrig gesetzter Listungspreis hebelt den 103-%-Floor nicht aus."""
    squad = _squad()
    offers = [_offer("m0", 10_000_000, 10_000_000, "A", asking=9_000_000)]  # 100 % MW
    assert propose_accepts(offers, squad, LEAGUE, Guardrails()) == []
    # … und es ist auch kein „Angebot unter Preis" — der Preis wäre erreicht.
    assert offers_below_asking(offers, squad) == []


def test_unknown_asking_price_keeps_old_behaviour():
    """listing_price=0 (Altverhalten): nur der MW-Floor zählt."""
    squad = _squad()
    offers = [_offer("m0", 10_000_000, 10_400_000, "A")]
    props = propose_accepts(offers, squad, LEAGUE, Guardrails())
    assert [p.action.offer_id for p in props] == ["A"]
    assert "Listungspreis" not in props[0].rationale
    assert offers_below_asking(offers, squad) == []


def test_below_asking_dedupes_to_best_offer_and_ignores_unusable_entries():
    squad = _squad()
    offers = [
        _offer("m0", 10_000_000, 11_000_000, "A", asking=15_000_000),
        _offer("m0", 10_000_000, 12_000_000, "B", asking=15_000_000),  # besser
        _offer("m1", 10_000_000, 12_500_000, "", asking=15_000_000),   # ohne uoid
        _offer("fremd", 10_000_000, 14_000_000, "C", asking=15_000_000),  # nicht im Kader
        _offer("s0", 10_000_000, 13_000_000, "D", asking=15_000_000),
    ]
    below = offers_below_asking(offers, squad)
    assert [(o.player_id, o.offer_id) for o in below] == [("s0", "D"), ("m0", "B")]


def test_floor_outside_t120_uses_position_minimums_only():
    """min_squad_size=0 (außerhalb T-120): Annahme darf unter 11 fallen,
    solange 1 TW/3 ABW/2 MF/1 ST bleiben — wie im Executor (L-095)."""
    squad = _squad(n_mf=3, n_st=1, n_def=3)  # 8 Spieler: TW1 ABW3 MF3 ST1
    mf_offer = _offer("m0", 10_000_000, 10_400_000, "M")
    st_offer = _offer("s0", 10_000_000, 10_400_000, "S")
    # Default (11er-Floor): bei 8 Spielern geht gar nichts.
    assert propose_accepts([mf_offer, st_offer], squad, LEAGUE, Guardrails()) == []
    # Außerhalb des Fensters: das Mittelfeld hat 3 (Minimum 2) → annehmbar,
    # der einzige Stürmer bleibt gesperrt (Vogt-Fall).
    props = propose_accepts(
        [mf_offer, st_offer], squad, LEAGUE, Guardrails(), min_squad_size=0
    )
    assert [p.action.player_id for p in props] == ["m0"]


def test_floor_outside_t120_consumes_minimums_across_proposals():
    """Zwei Angebote auf Mittelfeldspieler bei MF=3: nur eines darf durch."""
    squad = _squad(n_mf=3, n_st=1, n_def=3)
    offers = [
        _offer("m0", 10_000_000, 10_400_000, "A"),
        _offer("m1", 10_000_000, 10_300_000, "B"),
    ]
    props = propose_accepts(offers, squad, LEAGUE, Guardrails(), min_squad_size=0)
    assert [p.action.player_id for p in props] == ["m0"]


def test_floor_inside_t120_requires_eleven_and_a_formation():
    squad = _squad(n_mf=4, n_st=2, n_def=4)  # 11 Spieler
    offers = [_offer("m0", 10_000_000, 10_400_000, "A")]
    assert propose_accepts(offers, squad, LEAGUE, Guardrails(), min_squad_size=11) == []
    squad["extra"] = 3  # 12 Spieler → eine Annahme ist gedeckt
    assert len(propose_accepts(offers, squad, LEAGUE, Guardrails(), min_squad_size=11)) == 1


def test_bench_windfall_offer_above_115pct_is_accepted_even_below_showcase_anchor():
    """Emils Beispiel (20.10.2026): Ein 1-Mio.-Bankspieler steht für 5 M im Schaufenster
    und ein Mitspieler bietet 4,5 M (450 % MW) — wird bei Bankspielern automatisch
    angenommen; ein Starter (nicht in bench_ids) bleibt unter Listungspreis gesperrt."""
    squad = _squad()  # 12 Spieler
    offers = [
        _offer("m0", 1_000_000, 4_500_000, "WINDFALL", asking=5_000_000),
        _offer("m1", 65_000_000, 70_000_000, "STARTER", asking=100_000_000),
    ]
    props = propose_accepts(offers, squad, LEAGUE, Guardrails(), bench_ids={"m0"})
    assert [p.action.offer_id for p in props] == ["WINDFALL"]
    assert "Bank-Übergebot ≥ 115% MW" in props[0].rationale
    below = offers_below_asking(offers, squad, bench_ids={"m0"})
    assert [(o.player_id, o.offer_id) for o in below] == [("m1", "STARTER")]

