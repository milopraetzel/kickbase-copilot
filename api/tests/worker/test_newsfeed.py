"""News-Update: Feed-Split + Update-Nachricht + Schlussfolgerungen (echte Feed-Formen)."""

from datetime import UTC, datetime, timedelta

from kickbase_agent.news.models import NewsArticle
from kickbase_agent.scouts.ligainsider.models import InjuryEntry
from kickbase_agent.worker.newsfeed import (
    NewsBundle,
    build_conclusions,
    format_update,
    split_feed,
)

_NOW = datetime.now(UTC)


def _feed_transfer(byr: str, pn: str, trp: int, hours_ago: int = 1) -> dict:
    return {"i": "1", "t": 15, "dt": (_NOW - timedelta(hours=hours_ago)).isoformat(),
            "data": {"byr": byr, "pn": pn, "trp": trp, "t": 1}}


def _feed_event(ln: str, mv: int, hours_ago: int = 1) -> dict:
    return {"i": "2", "t": 3, "dt": (_NOW - timedelta(hours=hours_ago)).isoformat(),
            "data": {"ln": ln, "mv": mv}}


def test_split_feed_separates_and_filters_old():
    feed = [
        _feed_transfer("Niklas B.", "Maza", 31_100_000),
        _feed_event("Rieder", 13_547_610),
        _feed_transfer("Alt", "Uralt", 1, hours_ago=90),   # zu alt → raus
    ]
    transfers, events = split_feed(feed, since_hours=26)
    assert len(transfers) == 1 and transfers[0]["data"]["pn"] == "Maza"
    assert len(events) == 1 and events[0]["data"]["ln"] == "Rieder"


def test_format_update_renders_all_blocks():
    b = NewsBundle(
        transfers=[_feed_transfer("Niklas B.", "Maza", 31_100_000)],
        market_events=[_feed_event("Rieder", 13_547_610)],
        injuries=[InjuryEntry("3", "Emre Can", "Kreuzbandriss")],
        news=[NewsArticle(source="kicker", url="", title="Musiala vor Comeback",
                          body="", published_at=_NOW)],
    )
    text = format_update(b)
    assert "Niklas B." in text and "31.1M" in text
    assert "Rieder" in text
    assert "Emre Can" in text
    assert "Musiala vor Comeback" in text


def test_conclusions_flag_stolen_bid_target():
    b = NewsBundle(
        transfers=[_feed_transfer("Rivale99", "Hoffmeier", 9_000_000)],
        bid_names=["Hoffmeier", "Otávio"],
    )
    text = build_conclusions(b)
    assert "Rivale99" in text and "hinfällig" in text


def test_conclusions_note_big_rival_spend():
    b = NewsBundle(transfers=[_feed_transfer("Maximilian", "Pavlović", 35_000_000)])
    text = build_conclusions(b)
    assert "Maximilian" in text and "weniger Konkurrenz" in text


def test_conclusions_squad_injury_alarm():
    b = NewsBundle(
        injuries=[InjuryEntry("2", "Minjae Kim", "Muskelverletzung")],
        squad_names=["Minjae", "Schwäbe"],
    )
    text = build_conclusions(b)
    assert "Kader-Alarm" in text and "Minjae" in text


def test_conclusions_quiet_when_nothing_relevant():
    text = build_conclusions(NewsBundle())
    assert "Nichts davon ändert den Plan" in text
    assert "Reaktion" in text
