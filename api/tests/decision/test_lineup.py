"""Auto-Aufstellung: beste 11 unter Formations-Constraints (nach erwarteten Punkten)."""

from kickbase_agent.decision.lineup import best_lineup
from kickbase_agent.intel.models import ExplosionCandidate


def _c(
    pid: str, position: int, score: float, lineup: float | None = None
) -> ExplosionCandidate:
    # Ohne explizites `lineup` fällt lineup_score auf total_score zurück (Modell-Default).
    return ExplosionCandidate(
        player_id=pid, name=f"P{pid}", team_id="1", position=position,
        current_market_value=1_000_000, total_score=score, lineup_score=lineup, signals=[],
    )


def _full_squad() -> list[ExplosionCandidate]:
    # 2 TW, 6 DEF, 6 MID, 4 STR — genug für jede Formation
    squad = []
    squad += [_c(f"gk{i}", 1, 0.9 - i * 0.1) for i in range(2)]
    squad += [_c(f"df{i}", 2, 0.8 - i * 0.05) for i in range(6)]
    squad += [_c(f"mf{i}", 3, 0.85 - i * 0.05) for i in range(6)]
    squad += [_c(f"st{i}", 4, 0.7 - i * 0.05) for i in range(4)]
    return squad


def test_lineup_has_11_players_and_one_keeper():
    result = best_lineup(_full_squad())
    assert len(result.starters) == 11
    keepers = [p for p in result.starters if p.position == 1]
    assert len(keepers) == 1


def test_lineup_respects_a_valid_formation():
    result = best_lineup(_full_squad())
    d = sum(1 for p in result.starters if p.position == 2)
    m = sum(1 for p in result.starters if p.position == 3)
    s = sum(1 for p in result.starters if p.position == 4)
    assert result.formation == f"{d}-{m}-{s}"
    assert 3 <= d <= 5 and 2 <= m <= 6 and 1 <= s <= 3


def test_lineup_picks_highest_scorers():
    result = best_lineup(_full_squad())
    # bester TW (gk0=0.9) muss spielen, schlechtester (gk1=0.8) auf die Bank
    ids = {p.player_id for p in result.starters}
    assert "gk0" in ids
    assert "gk1" not in ids
    assert result.expected_score > 0


def test_lineup_falls_back_when_squad_incomplete():
    # Nur 1 TW + 3 DEF + 3 MID → keine volle 11, aber kein Absturz
    thin = [_c("gk", 1, 0.9)] + [_c(f"d{i}", 2, 0.5) for i in range(3)] \
        + [_c(f"m{i}", 3, 0.5) for i in range(3)]
    result = best_lineup(thin)
    assert result.formation == ""       # keine gültige Formation möglich
    assert result.starters == []


def test_lineup_optimizes_lineup_score_not_total_score():
    """Review-Fix: Marktwert-Trend (Teil von total_score) erzeugt keine Spieltags-
    punkte — die Aufstellung muss über lineup_score gehen, nicht über total_score."""
    squad = [c for c in _full_squad() if c.position != 1]
    # gk_hype: Trend-getrieben hoher Gesamtscore, aber punktschwach (lineup 0.3);
    # gk_solide: unspektakulärer Gesamtscore, aber punktstark (lineup 0.8).
    squad += [_c("gk_hype", 1, 0.9, lineup=0.3), _c("gk_solide", 1, 0.6, lineup=0.8)]
    result = best_lineup(squad)
    starter_ids = {p.player_id for p in result.starters}
    assert "gk_solide" in starter_ids
    assert "gk_hype" not in starter_ids
    # expected_score summiert lineup_scores (nicht total_scores)
    assert result.expected_score == sum(p.lineup_score for p in result.starters)
