"""Gemeinsame Bausteine der Jev-Urteile über kicker-Anreißer (intel/news_text.py)."""

from datetime import UTC, datetime, timedelta

from kickbase_agent.intel.news_text import (
    last_kickoffs,
    mentions,
    news_pairs,
    normalize,
    valid_choice,
)
from kickbase_agent.kickbase.models import Fixture, Player, Position
from kickbase_agent.news.models import NewsArticle

_NOW = datetime(2026, 9, 30, 8, 0, tzinfo=UTC)


def test_kickbase_and_kicker_spellings_match():
    assert normalize("Grønbæk") == normalize("Grönbaek")
    assert normalize("Kramarić") == normalize("Kramaric")
    assert normalize("Dinkçi") == normalize("Dinkci")
    assert mentions("Grønbæk", "Bitter für den HSV: Top-Scorer Grönbaek fällt vorerst aus")
    assert mentions("Džeko", "Muslic verteidigt Startelfeinsatz von Dzeko")
    # Typografischer Apostroph und geschützter Bindestrich wie im Satz von kicker.
    assert mentions("N'Diaye", "N’Diaye trifft doppelt")
    assert mentions("Claude-Maurice", "Claude‑Maurice startet")


def test_only_whole_names_match_including_the_genitive():
    assert mentions("Bah", "Claude-Maurice und Bah sind zu gut für die FCA-Bank")
    assert mentions("Claude-Maurice", "Claude-Maurice und Bah sind zu gut für die FCA-Bank")
    assert mentions("Kane", "Kanes vierjährige Serie reißt")
    assert not mentions("Bah", "Die Bahn kam zu spät")
    assert not mentions("Ba", "Ba und Bo")  # zu kurz für einen sicheren Treffer


def test_pairs_come_newest_first_and_only_after_the_last_kickoff():
    player = Player(id="43", first_name="Mitchell", last_name="Weiser",
                    position=Position.DEFENDER, market_value=8_000_000, team_id="10")
    played = Fixture(matchday=5, home_team_id="10", away_team_id="3",
                     kickoff=_NOW - timedelta(hours=40))
    upcoming = Fixture(matchday=6, home_team_id="3", away_team_id="10",
                       kickoff=_NOW + timedelta(hours=60))
    articles = [
        NewsArticle(source="kicker", url=f"u{h}", title=f"Weiser Meldung {h}", body="",
                    published_at=_NOW - timedelta(hours=h))
        for h in (50, 30, 10)
    ]

    pairs = news_pairs([player], articles, _NOW - timedelta(hours=96),
                       last_kickoffs([played, upcoming], _NOW))

    assert [a.title for _, a, _ in pairs] == ["Weiser Meldung 10", "Weiser Meldung 30"]


def test_valid_choice_accepts_only_known_labels_and_real_probabilities():
    class Answer:
        def __init__(self, choice, p):
            self.choice, self.probabilities = choice, {choice: p, "rest": 1 - p}

    labels = ("a", "b")
    assert valid_choice(Answer("a", 0.97), labels) == ("a", 0.97)
    assert valid_choice(Answer("x", 0.97), labels) is None
    assert valid_choice(Answer("a", float("nan")), labels) is None
    assert valid_choice(Answer("a", 1.5), labels) is None
    assert valid_choice(None, labels) is None


def test_an_article_published_exactly_at_kickoff_counts():
    player = Player(id="43", first_name="Mitchell", last_name="Weiser",
                    position=Position.DEFENDER, market_value=8_000_000, team_id="10")
    kickoff = _NOW - timedelta(hours=5)
    at_kickoff = NewsArticle(source="kicker", url="u", title="Weiser in der Startelf", body="",
                             published_at=kickoff)

    pairs = news_pairs([player], [at_kickoff], _NOW - timedelta(hours=96), {"10": kickoff})

    assert len(pairs) == 1
