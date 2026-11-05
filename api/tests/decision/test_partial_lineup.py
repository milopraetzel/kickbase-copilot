"""Teil-Elf (L-109, 18.09.2026): Kader unter elf stellt alle unterbringbaren Spieler auf.

Anlass: Spieltag 4 mit 8 Spielern (1 TW, 3 ABW, 3 MIT, 1 STU) — der Job setzte
gar nichts, zwei vorhandene Spieler blieben draußen (je 100 Punkte, L-080).
Platzreihenfolge belegt durch `kba probe lineup` vom 18.09.2026: `lo` 0 = TW,
danach Abwehr, Mittelfeld, Sturm laut Formation; Lücken mitten in einer Reihe
kommen real vor (ABW auf 1, 2, 4 — Platz 3 leer).
"""

from kickbase_agent.decision.lineup import best_lineup, best_partial_lineup
from kickbase_agent.intel.models import ExplosionCandidate


def _c(pid: str, position: int, score: float) -> ExplosionCandidate:
    return ExplosionCandidate(
        player_id=pid, name=f"P{pid}", team_id="1", position=position,
        current_market_value=1_000_000, total_score=score, signals=[],
    )


def _matchday4_squad() -> list[ExplosionCandidate]:
    return [
        _c("1580", 1, 0.58),
        _c("6668", 2, 0.74), _c("7320", 2, 0.55), _c("4333", 2, 0.35),
        _c("8329", 3, 0.71), _c("12158", 3, 0.21), _c("15196", 3, 0.20),
        _c("2855", 4, 0.37),
    ]


def test_best_lineup_keeps_its_empty_contract_for_short_squads():
    # Kaufprüfung und Kadersicherungen lesen „leer" als „keine Elf" — unverändert.
    assert best_lineup(_matchday4_squad()).starters == []


def test_partial_lineup_fields_every_player_that_fits():
    lineup = best_partial_lineup(_matchday4_squad())
    assert {c.player_id for c in lineup.starters} == {
        "1580", "6668", "7320", "4333", "8329", "12158", "15196", "2855",
    }
    assert lineup.bench == []
    assert lineup.open_slots == 3
    assert len(lineup.slot_ids) == 11


def test_partial_slots_follow_kickbase_order_with_gaps_at_line_end():
    lineup = best_partial_lineup(_matchday4_squad())
    d, m, s = (int(x) for x in lineup.formation.split("-"))
    slots = lineup.slot_ids
    assert slots[0] == "1580"
    defence, midfield, attack = slots[1:1 + d], slots[1 + d:1 + d + m], slots[1 + d + m:]
    assert len(attack) == s
    assert [p for p in defence if p] == ["6668", "7320", "4333"]      # beste zuerst
    assert [p for p in midfield if p] == ["8329", "12158", "15196"]
    assert [p for p in attack if p] == ["2855"]
    for line in (defence, midfield, attack):
        filled = [p is not None for p in line]
        assert filled == sorted(filled, reverse=True)                  # Lücken hinten
    assert lineup.write_ids == slots


def test_partial_prefers_the_formation_that_places_more_players():
    # 5 Stürmer-lose Mittelfeldspieler: 3-6-1 bringt 6 MIT unter, 4-3-3 nur 3.
    squad = [_c("gk", 1, 0.5)] + [_c(f"d{i}", 2, 0.5) for i in range(3)] \
        + [_c(f"m{i}", 3, 0.5) for i in range(6)]
    lineup = best_partial_lineup(squad)
    assert lineup.formation == "3-6-1"
    assert len(lineup.starters) == 10 and lineup.open_slots == 1


def test_partial_without_keeper_still_fields_the_outfield_players():
    squad = [_c(f"d{i}", 2, 0.5) for i in range(3)] + [_c("m0", 3, 0.5)]
    lineup = best_partial_lineup(squad)
    assert lineup.slot_ids[0] is None
    assert len(lineup.starters) == 4


def test_partial_uses_the_given_score_for_selection():
    # Zwei Torhüter: die Prognose (nicht der Index) entscheidet, wer spielt.
    squad = [_c("gkA", 1, 0.9), _c("gkB", 1, 0.1), _c("d0", 2, 0.5)]
    points = {"gkA": 0.0, "gkB": 80.0, "d0": 40.0}
    lineup = best_partial_lineup(squad, lambda c: points[c.player_id])
    assert lineup.slot_ids[0] == "gkB"
    assert [c.player_id for c in lineup.bench] == ["gkA"]
    assert lineup.expected_score == 120.0


def test_full_squad_yields_a_normal_eleven_without_slot_list():
    squad = [_c("gk", 1, 0.9)] + [_c(f"d{i}", 2, 0.5) for i in range(5)] \
        + [_c(f"m{i}", 3, 0.6) for i in range(5)] + [_c(f"s{i}", 4, 0.7) for i in range(3)]
    lineup = best_partial_lineup(squad)
    assert len(lineup.starters) == 11 and lineup.open_slots == 0
    assert lineup.slot_ids == []
    assert lineup.write_ids == [c.player_id for c in lineup.starters]
    assert lineup.formation == best_lineup(squad).formation


def test_empty_squad_stays_empty():
    lineup = best_partial_lineup([])
    assert lineup.starters == [] and lineup.formation == ""
