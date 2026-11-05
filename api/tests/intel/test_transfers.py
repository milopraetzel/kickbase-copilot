"""league_events (intel/transfers.py): Feed-Items → Protokoll-Einträge.

Feed-Strukturen exakt wie live belegt (tests/worker/test_bonus_job.py,
tests/trading/test_wallet.py): t=15 mit byr ODER slr + pn + trp, t=22 mit bn/day.
"""

from __future__ import annotations

from datetime import datetime

from kickbase_agent.intel.transfers import league_events

FEED = [
    {"i": "1", "t": 15, "dt": "2026-08-23T10:00:00Z",
     "data": {"byr": "Emil", "pn": "Olise", "trp": 68_017_197}},
    {"i": "2", "t": 15, "dt": "2026-08-23T09:00:00Z",
     "data": {"slr": "emil", "pn": "Khannouss", "trp": 21_500_000}},
    {"i": "3", "t": 15, "dt": "2026-08-23T08:00:00Z",
     "data": {"byr": "Osei", "pn": "Vozar", "trp": 1_280_015}},
    {"i": "4", "t": 15, "dt": "2026-08-23T07:00:00Z",
     "data": {"slr": "Kili", "pn": "Sané", "trp": 30_000_000}},
    {"i": "5", "t": 22, "dt": "2026-08-23T06:00:00Z", "data": {"bn": 50_000, "day": 6}},
    {"i": "6", "t": 3, "dt": "2026-08-23T05:00:00Z", "data": {"pn": "Bellingham"}},
]


def test_own_buy_becomes_zuschlag_transfer():
    events = league_events(FEED, me="emil")
    entry = next(e for e in events if e["feed_id"] == "1")
    assert entry["kind"] == "transfer"
    assert entry["title"] == "Zuschlag: Olise für 68,02 M"
    assert entry["status"] == "zuschlag"
    assert entry["sources"] == {"price": 68_017_197, "price_fmt": "68,02 M", "buyer": "Emil"}
    assert entry["player_name"] == "Olise"
    assert entry["amount"] == 68_017_197
    assert "Olise" in entry["why"]


def test_own_sell_becomes_verkauft_transfer():
    entry = next(e for e in league_events(FEED) if e["feed_id"] == "2")
    assert entry["kind"] == "transfer"
    assert entry["title"] == "Verkauft: Khannouss für 21,50 M"
    assert entry["status"] == "verkauft"
    assert entry["sources"]["seller"] == "emil"


def test_foreign_buy_and_sell_become_league_entries():
    events = {e["feed_id"]: e for e in league_events(FEED, me="emil")}
    buy = events["3"]
    assert buy["kind"] == "league"
    assert buy["title"] == "Liga: Osei kauft Vozar für 1,28 M"
    assert buy["status"] == "liga"
    assert buy["sources"] == {"price": 1_280_015, "price_fmt": "1,28 M", "buyer": "Osei"}
    sell = events["4"]
    assert sell["kind"] == "league"
    assert sell["title"] == "Liga: Kili verkauft Sané für 30,00 M"
    assert sell["sources"]["seller"] == "Kili"


def test_bonus_becomes_praemie_transfer():
    entry = next(e for e in league_events(FEED) if e["feed_id"] == "5")
    assert entry["kind"] == "transfer"
    assert entry["title"] == "Prämie kassiert (+50 k)"
    assert entry["status"] == "praemie"
    assert entry["sources"] == {"bonus": 50_000, "bonus_fmt": "50 k", "tag": 6}
    assert "Tag 6" in entry["why"]


def test_other_types_and_broken_items_are_skipped():
    broken = [
        {"i": "6", "t": 3, "dt": "2026-08-23T05:00:00Z", "data": {}},   # Markt-Event
        {"i": "7", "t": 15, "dt": "kaputt", "data": {"byr": "Osei", "trp": 1}},
        {"i": "8", "t": 15, "dt": "2026-08-23T05:00:00", "data": {"byr": "Osei"}},  # naiv
        {"i": "9", "t": 15, "dt": "2026-08-23T05:00:00Z", "data": {}},  # weder byr noch slr
        {"i": "10", "t": 15, "dt": "2026-08-23T05:00:00Z"},             # ohne data
        "kein-dict",
    ]
    assert league_events(broken) == []
    assert league_events([]) == []
    assert league_events(None) == []


def test_events_sorted_newest_first_with_parseable_ts():
    events = league_events(FEED)
    assert len(events) == 5  # t=3 fliegt raus
    ts = [e["ts"] for e in events]
    assert ts == sorted(ts, reverse=True)
    for value in ts:
        assert datetime.fromisoformat(value).tzinfo is not None


def test_me_match_is_case_insensitive_and_bids_param_accepted():
    events = league_events(FEED, me="EMIL", bids=[{"player_id": "1"}])
    assert events[0]["kind"] == "transfer"  # Emil/emil erkannt trotz EMIL
    assert sum(1 for e in events if e["kind"] == "transfer") == 3
    assert sum(1 for e in events if e["kind"] == "league") == 2
