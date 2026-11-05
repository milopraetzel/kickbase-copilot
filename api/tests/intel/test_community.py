"""Community-Tipps: Speichern, Verfall, Score-Boost mit Namens-Match + Deckel."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, CommunityTip
from kickbase_agent.intel.community import (
    MAX_BOOST,
    apply_community_boost,
    load_recent_tips,
    save_tip,
)
from kickbase_agent.intel.models import ExplosionCandidate


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _c(name: str, score: float = 0.50) -> ExplosionCandidate:
    return ExplosionCandidate(player_id=name.lower(), name=name, team_id="1", position=3,
                              current_market_value=5_000_000, total_score=score, signals=[])


def test_save_and_load_with_ttl(session):
    save_tip(session, "Füllkrug", 1.0, "youtube", "Geheimtipp-Video")
    old = CommunityTip(player_name="Alt", weight=1.0, source="x", note="",
                       created_at=datetime.now(UTC) - timedelta(days=9))
    session.add(old)
    session.commit()

    tips = load_recent_tips(session)
    assert [t.player_name for t in tips] == ["Füllkrug"]   # alter Tipp verfallen


def test_boost_applies_with_fuzzy_name_and_cap(session):
    save_tip(session, "Niclas Füllkrug", 1.0, "youtube", "JETZT kaufen")
    tips = load_recent_tips(session)
    out = apply_community_boost([_c("Füllkrug", 0.45), _c("Grüll", 0.28)], tips)

    fk = next(c for c in out if c.name == "Füllkrug")
    gr = next(c for c in out if c.name == "Grüll")
    assert fk.total_score == pytest.approx(0.45 + MAX_BOOST)   # geboostet
    assert any(s.name == "community" for s in fk.signals)      # transparent
    assert gr.total_score == 0.28                              # kein Match → unverändert


def test_negative_tip_lowers_score(session):
    save_tip(session, "Prömel", -1.0, "reddit", "abgeben")
    out = apply_community_boost([_c("Prömel", 0.28)], load_recent_tips(session))
    assert out[0].total_score == pytest.approx(0.28 - MAX_BOOST)


def test_weight_is_clamped(session):
    tip = save_tip(session, "X", 5.0, "test")
    assert tip.weight == 1.0


def test_one_tip_boosts_only_best_match(session):
    """Review-Fix: 'Ulrich'-Tipp darf nicht Ulrich UND Ullrich boosten."""
    save_tip(session, "Laurin Ulrich", 1.0, "youtube", "Tipp")
    out = apply_community_boost(
        [_c("Laurin Ulrich", 0.50), _c("Lukas Ullrich", 0.50)],
        load_recent_tips(session),
    )
    boosted = [c for c in out if c.total_score > 0.50]
    assert len(boosted) == 1 and boosted[0].name == "Laurin Ulrich"


def test_only_positive_ueberspringt_negative_tipps(session):
    """Review 31.08. (Kader-Pfad): Ein negativer Tipp darf einen Kaderspieler
    nie unter die Verkaufsschwelle druecken — Score UND Signal bleiben unberuehrt;
    positive Tipps wirken unveraendert."""
    save_tip(session, "Prömel", -1.0, "reddit", "abgeben")
    out = apply_community_boost(
        [_c("Prömel", 0.42)], load_recent_tips(session), only_positive=True
    )
    assert out[0].total_score == 0.42
    assert all(s.name != "community" for s in out[0].signals)

    save_tip(session, "Grüll", 1.0, "youtube", "kaufen")
    out2 = apply_community_boost(
        [_c("Grüll", 0.42)], load_recent_tips(session), only_positive=True
    )
    assert next(c for c in out2 if c.name == "Grüll").total_score == pytest.approx(
        0.42 + MAX_BOOST
    )
