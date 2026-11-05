"""LigaInsider-Scout: Matching, Parsing (echte Fixtures), Verfügbarkeits-Auflösung."""

from pathlib import Path

from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.scouts.ligainsider.availability import (
    apply_to_candidates,
    resolve_availability,
)
from kickbase_agent.scouts.ligainsider.matcher import best_match, normalize, similarity
from kickbase_agent.scouts.ligainsider.models import (
    InjuryEntry,
    LineupEntry,
    Status,
)
from kickbase_agent.scouts.ligainsider.parser import parse_injuries, parse_lineup

_FIX = Path(__file__).parent.parent / "fixtures" / "ligainsider"


# ---- Matching (Regel 3) ----

def test_normalize_strips_accents_and_punctuation():
    assert normalize("Min-jae Kim") == "min jae kim"
    assert normalize("Ouédraogo") == "ouedraogo"
    assert normalize("Schwäbe") == "schwabe"


def test_similarity_subset_is_confident():
    # Kickbase-Nachname ⊂ LI-Vollname → über der Scharf-Schwelle (0.90)
    assert similarity("Minjae", "Minjae Kim") >= 0.90
    assert similarity("Ouédraogo", "Assan Ouédraogo") >= 0.90


def test_similarity_avoids_false_positive():
    # „Kim" darf NICHT auf „Kimmich" matchen (Sicherheitsnetz)
    assert similarity("Kim", "Kimmich") < 0.90


def test_best_match_is_team_anchored():
    idx, conf = best_match("Minjae", ["Neuer", "Kimmich", "Minjae", "Kane"])
    assert idx == 2 and conf == 1.0


# ---- Parsing echter Fixtures ----

def test_parse_injuries_real_fixture():
    entries = parse_injuries((_FIX / "injuries.html").read_text())
    names = {e.player_name for e in entries}
    assert "Emre Can" in names           # Dortmund
    assert "Assan Ouédraogo" in names     # Leipzig
    # Team-Zuordnung korrekt (Dortmund tid=3, Leipzig tid=43)
    can = next(e for e in entries if e.player_name == "Emre Can")
    assert can.team_tid == "3" and "Kreuzband" in can.reason


def test_parse_lineup_real_fixture_contains_minjae():
    xi = parse_lineup((_FIX / "lineup_bayern.html").read_text(), "2")
    names = {e.player_name for e in xi}
    assert "Minjae" in names and "Kane" in names
    assert all(e.team_tid == "2" for e in xi)


# ---- Verfügbarkeits-Auflösung (Regeln 1+2) ----

_XI = [LineupEntry("2", "Minjae"), LineupEntry("2", "Kane")]
_INJ = [InjuryEntry("3", "Emre Can", "Kreuzbandriss")]


def test_confident_injury_triggers_hard_veto():
    av, q = resolve_availability([("p1", "Emre Can", "3")], _XI, _INJ, {"2", "3"})
    assert av[0].status is Status.OUT and av[0].multiplier == 0.0
    assert not q


def test_starter_in_probable_xi():
    av, _ = resolve_availability([("p2", "Minjae", "2")], _XI, _INJ, {"2"})
    assert av[0].status is Status.STARTER and av[0].multiplier == 1.0


def test_fit_but_not_in_xi_is_bench():
    av, _ = resolve_availability([("p3", "Boey", "2")], _XI, _INJ, {"2"})
    assert av[0].status is Status.BENCH and av[0].multiplier == 0.4


def test_no_li_data_is_unknown_no_veto():
    # Team 99 nicht gescraped → kein Veto
    av, _ = resolve_availability([("p4", "Wer Auch Immer", "99")], _XI, _INJ, {"2"})
    assert av[0].status is Status.UNKNOWN and av[0].multiplier == 1.0


def test_grey_zone_injury_quarantines_without_veto():
    # Tippfehler-nahe Schreibweise → in [0.72, 0.90) → Quarantäne, kein Veto
    inj = [InjuryEntry("3", "Emre Can", "Kreuzbandriss")]
    av, q = resolve_availability([("p5", "Emro Cen", "3")], _XI, inj, {"3"})
    assert av[0].status is not Status.OUT       # NICHT genullt
    assert len(q) == 1 and q[0].kb_name == "Emro Cen"


def test_apply_to_candidates_multiplies_score():
    cands = [ExplosionCandidate(player_id="p1", name="Emre Can", team_id="3",
                                position=2, current_market_value=1, total_score=0.8, signals=[])]
    av, _ = resolve_availability([("p1", "Emre Can", "3")], _XI, _INJ, {"3"})
    out = apply_to_candidates(cands, av)
    assert out[0].total_score == 0.0   # verletzt → genullt


def test_apply_to_candidates_sets_injury_severity_on_out():
    """OUT-Spieler tragen Grund + Schwere — der Verkaufspfad braucht beides."""
    cands = [ExplosionCandidate(player_id="p1", name="Emre Can", team_id="3", position=2,
                                current_market_value=1, total_score=0.8, lineup_score=0.7,
                                signals=[])]
    av, _ = resolve_availability([("p1", "Emre Can", "3")], _XI, _INJ, {"3"})
    out = apply_to_candidates(cands, av)
    assert out[0].total_score == 0.0
    assert out[0].lineup_score == 0.0          # beide Scores genullt
    assert out[0].injury_severity == "long"    # Kreuzbandriss → Langzeit
    assert "Kreuzbandriss" in out[0].injury_reason


def test_apply_to_candidates_multiplies_both_scores_for_bench():
    """Bank-Faktor ×0.4 trifft total_score UND lineup_score; kein Ausfall-Flag."""
    cands = [ExplosionCandidate(player_id="p3", name="Boey", team_id="2", position=2,
                                current_market_value=1, total_score=0.8, lineup_score=0.6,
                                signals=[])]
    av, _ = resolve_availability([("p3", "Boey", "2")], _XI, _INJ, {"2"})
    out = apply_to_candidates(cands, av)
    assert out[0].total_score == 0.32
    assert out[0].lineup_score == 0.24
    assert out[0].injury_severity is None
    assert out[0].injury_reason == ""


def test_apply_to_candidates_leaves_fit_starters_untouched():
    cands = [ExplosionCandidate(player_id="p2", name="Minjae", team_id="2", position=2,
                                current_market_value=1, total_score=0.8, lineup_score=0.6,
                                signals=[])]
    av, _ = resolve_availability([("p2", "Minjae", "2")], _XI, _INJ, {"2"})
    out = apply_to_candidates(cands, av)
    assert out[0].total_score == 0.8
    assert out[0].lineup_score == 0.6
    assert out[0].injury_severity is None


# ---- Verletzungs-Schwere → Profi-Regel ----

def test_severity_long_injuries_say_sell_now():
    from kickbase_agent.scouts.ligainsider.severity import injury_severity, sell_advice
    for reason in ("Kreuzbandriss", "Mittelfußbruch", "Schien- und Wadenbeinbruch",
                   "fehlt 3 Monate", "Aufbautraining"):
        assert injury_severity(reason).value == "long", reason
        assert "SOFORT verkaufen" in sell_advice(reason)


def test_severity_short_injuries_say_hold():
    from kickbase_agent.scouts.ligainsider.severity import injury_severity, sell_advice
    for reason in ("angeschlagen", "fraglich (Erkältung)", "muskuläre Probleme",
                   "Trainingsrückstand"):
        assert injury_severity(reason).value == "short", reason
        assert "HALTEN" in sell_advice(reason)


def test_severity_unknown_says_watch():
    from kickbase_agent.scouts.ligainsider.severity import sell_advice
    assert "beobachten" in sell_advice("Ausfall")


def test_borderline_xi_match_goes_to_quarantine_not_bench():
    """Review-Fix: Startelf-Match in [0.72,0.90) → UNKNOWN + Quarantäne, NICHT Bank."""
    # "Hanche-Olsen" ↔ "Hanke-Olsen": geteilter Nachname, abweichender Rest → ~0.85
    xi = [LineupEntry("18", "Hanke-Olsen")]
    av, q = resolve_availability([("p9", "Hanche-Olsen", "18")], xi, [], {"18"})
    assert av[0].status is Status.UNKNOWN and av[0].multiplier == 1.0
    assert len(q) == 1 and "unsicher" in q[0].li_reason
