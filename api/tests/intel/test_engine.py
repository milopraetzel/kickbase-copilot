from kickbase_agent.intel.engine import RuleEngine
from kickbase_agent.intel.models import ExplosionCandidate, Signal


def test_engine_combines_signals_as_weighted_mean():
    signals = [
        Signal(name="form", score=0.8, weight=0.35, reason="good"),
        Signal(name="matchup", score=0.6, weight=0.25, reason="ok"),
        Signal(name="trend", score=1.0, weight=0.40, reason="great"),
    ]
    engine = RuleEngine()
    total = engine.score(signals)
    # Weighted mean: (0.8*0.35 + 0.6*0.25 + 1.0*0.40) / (0.35+0.25+0.40)
    expected = (0.8 * 0.35 + 0.6 * 0.25 + 1.0 * 0.40) / 1.0
    assert abs(total - expected) < 0.001


def test_engine_empty_signals_returns_neutral():
    assert RuleEngine().score([]) == 0.5


def test_engine_sort_candidates_descending_by_score():
    a = ExplosionCandidate(
        player_id="a", name="A", team_id="1", position=3,
        current_market_value=10_000_000, total_score=0.4, signals=[],
    )
    b = ExplosionCandidate(
        player_id="b", name="B", team_id="1", position=3,
        current_market_value=10_000_000, total_score=0.9, signals=[],
    )
    c = ExplosionCandidate(
        player_id="c", name="C", team_id="1", position=3,
        current_market_value=10_000_000, total_score=0.7, signals=[],
    )
    engine = RuleEngine()
    ranked = engine.rank([a, b, c])
    assert [x.player_id for x in ranked] == ["b", "c", "a"]
