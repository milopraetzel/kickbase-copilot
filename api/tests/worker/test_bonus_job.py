"""Prämien-Feed-Gegenprobe: t=22-Ereignis des heutigen Berliner Tages erkennen."""

from datetime import UTC, datetime

from kickbase_agent.worker.bonus_job import find_todays_bonus_event

FEED = [
    {"i": "1", "t": 15, "dt": "2026-08-23T05:58:22Z", "data": {"slr": "Stefan"}},
    {"i": "2", "t": 22, "dt": "2026-08-23T00:28:01Z", "data": {"bn": 50000, "day": 6}},
    {"i": "3", "t": 22, "dt": "2026-08-21T22:23:01Z", "data": {"bn": 40000, "day": 5}},
    {"i": "4", "t": 22, "dt": "2026-08-21T08:01:11Z", "data": {"bn": 30000, "day": 4}},
]


def test_finds_todays_event_in_berlin_time():
    """00:28Z = 02:28 MESZ am 23.08. — zählt für den 23.08."""
    ev = find_todays_bonus_event(FEED, datetime(2026, 8, 23, 8, 50, tzinfo=UTC))
    assert ev is not None
    assert (ev["amount"], ev["day"]) == (50000, 6)
    assert ev["at"].strftime("%H:%M") == "02:28"


def test_berlin_day_boundary_not_utc():
    """21.08 22:23Z liegt in Berlin schon am 22.08. (Tag 5) — UTC-Datum wäre falsch."""
    ev = find_todays_bonus_event(FEED, datetime(2026, 8, 22, 6, 0, tzinfo=UTC))
    assert ev is not None and ev["day"] == 5
    assert find_todays_bonus_event(FEED, datetime(2026, 8, 24, 6, 0, tzinfo=UTC)) is None


def test_ignores_malformed_items():
    items = [{"t": 22, "dt": "kaputt", "data": {}}, {"t": 22, "data": {"bn": "x"}}]
    assert find_todays_bonus_event(items, datetime(2026, 8, 23, 8, 50, tzinfo=UTC)) is None
