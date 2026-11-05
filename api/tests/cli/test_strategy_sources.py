"""Datenfrische und Fehler bleiben von beobachteten Nullwerten getrennt."""
from datetime import UTC, datetime, timedelta

from kickbase_agent.cli.cockpit import strategy_data as source


def test_freshness_rejects_future_and_expired_timestamps():
    now = datetime(2026, 9, 11, 20, tzinfo=UTC)
    assert source._fresh({"history_fetched_at": now.isoformat()}, "history", now)
    for at in (now + timedelta(seconds=1), now - source.SOURCE_TTL):
        assert not source._fresh({"history_fetched_at": at.isoformat()}, "history", now)
    assert not source._fresh({"history_fetched_at": "kaputt"}, "history", now)


def test_invalid_cache_or_different_league_is_not_reused(tmp_path):
    path = tmp_path / "cache.json"
    for raw in ('{}', '{"version":1,"league_id":"1234567","players":[]}',
                '{"version":1,"league_id":"different","players":{}}'):
        path.write_text(raw)
        assert source._read_cache(path, "1234567")["players"] == {}


def test_atomic_cache_retains_explicit_unknown_and_exact_dates(tmp_path):
    path = tmp_path / "cache.json"
    payload = {"version": 1, "league_id": "1234567", "players": {
        "8329": {"history": [], "performance_fetched_at": None}}}
    source._write_cache(path, payload)
    assert source._read_cache(path, "1234567") == payload
    assert list(tmp_path.iterdir()) == [path]


def test_manager_change_never_uses_unverified_api_period():
    managers = [{"spieler": [{"pid": "1580", "mv": 15812500,
                              "api_value_change_sdmvt": 265192}]}]
    source.enrich_manager_changes(managers, [], [], "2026-09-11T21:00:00+00:00")
    assert managers[0]["spieler"][0]["mv24"] is None
    source.enrich_manager_changes(managers, [{"pid": "1580", "mv24": -29863}], [],
                                  "2026-09-11T21:00:00+00:00")
    assert managers[0]["spieler"][0]["mv24"] == -29863
    assert managers[0]["spieler"][0]["mv24_source"] == "kickbase_detail"


def test_manager_history_requires_adjacent_days_and_current_value():
    managers = [{"spieler": [{"pid": "1580", "mv": 15812500}]}]
    players = [{"player_id": "1580", "history": [
        {"date": "2026-09-10", "value": 15842363, "quality": "observed"},
        {"date": "2026-09-11", "value": 15812500, "quality": "observed"}]}]
    source.enrich_manager_changes(managers, [], players, "2026-09-11T21:00:00+00:00")
    assert managers[0]["spieler"][0]["mv24"] == -29863
    players[0]["history"][0]["date"] = "2026-09-09"
    source.enrich_manager_changes(managers, [], players, "2026-09-11T21:00:00+00:00")
    assert managers[0]["spieler"][0]["mv24"] is None
