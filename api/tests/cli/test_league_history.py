"""Historienintegration ausschließlich mit archivierten echten Kickbase-Antworten."""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kickbase_agent.cli.cockpit import league_history as history

FIX = Path(__file__).parents[1] / "fixtures" / "league_history_20260912"
LEAGUE = "1234567"
OWN = "2663242"
RIVAL = "2465162"
NOW = datetime(2026, 9, 11, 22, 10, tzinfo=UTC)


def read(name):
    return json.loads((FIX / name).read_text())


def cache_with_rows():
    cache = history._empty(LEAGUE)
    cache.update(overview=read("league-overview.json"), overview_fetched_at=NOW.isoformat())
    for row in read("seed-entries.json"):
        entry = cache["managers"].setdefault(row["manager_id"], {
            "name": row["manager_name"].strip(), "transfers": [],
            "history_complete": True, "history_fetched_at": NOW.isoformat(),
            "performance_fetched_at": NOW.isoformat(),
        })
        entry["transfers"].append(row)
    return cache


@pytest.fixture
def cache_path(tmp_path, monkeypatch):
    path = tmp_path / "league-history.json"
    monkeypatch.setattr(history, "_cache_path", lambda _: path)
    monkeypatch.setattr(history, "_now", lambda: NOW)
    return path


def managers():
    return [{"user_id": OWN, "name": "emil"}, {"user_id": RIVAL, "name": "Osei"}]


def test_same_trade_from_two_managers_counts_once_and_keeps_ids():
    cache = cache_with_rows()
    result = history._normalize(cache, NOW, [OWN, RIVAL], [])
    current = result["feed_events"]
    assert len(current) == 31
    ebimbe = next(r for r in current if r["data"]["pi"] == "4406")
    assert ebimbe["manager_view_count"] == 2
    assert ebimbe["data"]["buyer_id"] == OWN
    assert ebimbe["data"]["seller_id"] == RIVAL
    assert ebimbe["synthetic_id"] is True
    assert ebimbe["i"].startswith("archive:")
    assert len(result["historical_feed_events"]) == 1
    assert all(r["dt"] >= cache["overview"]["dt"] for r in current)


def test_real_feed_id_wins_and_join_is_stable_on_repeat():
    cache = cache_with_rows()
    cache["feed"] = read("feed-matching.json")
    first = history._normalize(cache, NOW, [OWN, RIVAL], [])
    second = history._normalize(cache, NOW, [OWN, RIVAL], [])
    assert first == second
    assert {r["i"] for r in first["feed_events"]} == {r["i"] for r in cache["feed"]}
    assert all(not r["synthetic_id"] for r in first["feed_events"])
    assert all(r["source"] == "activity_feed+manager_transfer_history"
               for r in first["feed_events"])
    assert first["coverage"]["added_current_transfers"] == 0


def test_renamed_manager_keeps_all_paired_transfers_by_stable_ids():
    cache = cache_with_rows()
    before = history._normalize(cache, NOW, [OWN, RIVAL], [])
    cache["managers"][OWN]["name"] = "Neuer Anzeigename"
    after = history._normalize(cache, NOW, [OWN, RIVAL], [])
    assert len(after["feed_events"]) == len(before["feed_events"]) == 31
    assert after["coverage"]["ambiguous_transfers"] == 0
    paired = [e for e in after["feed_events"] if e["manager_view_count"] == 2]
    assert len(paired) == 3
    assert all(e["data"]["buyer_id"] == OWN and e["data"]["seller_id"] == RIVAL for e in paired)
    assert all(e["data"]["byr"] == "Neuer Anzeigename" for e in paired)
    assert {e["i"] for e in before["feed_events"]} == {e["i"] for e in after["feed_events"]}


def test_unknown_reset_never_includes_synthetic_old_cost_basis():
    cache = cache_with_rows()
    del cache["overview"]
    cache["feed"] = read("feed-matching.json")[:1]
    result = history._normalize(cache, NOW, [OWN, RIVAL], [])
    assert len(result["feed_events"]) == 1
    assert result["coverage"]["reset_at"] is None
    assert not result["feed_events"][0]["synthetic_id"]


def test_normalization_keeps_raw_season_metadata_and_does_not_use_cur():
    cache = cache_with_rows()
    rival = read("rival-performance.json")
    uid = rival["u"]
    cache["managers"][uid] = {"name": rival["unm"], "transfers": [], "performance": rival}
    result = history._normalize(cache, NOW, [uid], [])
    assert result["manager_performance"][uid] == rival
    assert any(s["sn"] == "2025/2026" and any(d.get("cur") for d in s["it"])
               for s in result["manager_performance"][uid]["it"])


def test_conflicting_manager_views_are_not_fabricated_into_a_transfer():
    cache = cache_with_rows()
    original = cache["managers"][OWN]["transfers"][0]
    # Kein API-Payload: widersprüchlicher interner Cachezustand mit derselben echten Zeile.
    cache["managers"][RIVAL]["transfers"].append(copy.deepcopy(original))
    result = history._normalize(cache, NOW, [OWN, RIVAL], [])
    assert result["coverage"]["ambiguous_transfers"] == 1
    assert result["coverage"]["errors"][0]["type"] == "ambiguous_transfers"
    assert len(result["feed_events"]) == 30


class NoCalls:
    def __getattr__(self, name):
        raise AssertionError("Frischer Cache braucht keinen zusätzlichen API-Aufruf: " + name)


async def test_fresh_cache_uses_no_extra_gets_and_persists_recent_feed(cache_path):
    history._write_cache(cache_path, cache_with_rows())
    events = read("feed-matching.json")[:1]
    result = await history.load_league_history(NoCalls(), LEAGUE, managers(), events)
    assert result["coverage"]["api_requests_attempted"] == 0
    assert events[0]["i"] in {r["i"] for r in result["feed_events"]}
    assert json.loads(cache_path.read_text())["feed"] == events
    second = await history.load_league_history(NoCalls(), LEAGUE, managers(), [])
    assert events[0]["i"] in {r["i"] for r in second["feed_events"]}


async def test_complete_history_refresh_stops_at_overlap_not_at_oldest_page(cache_path):
    cache = cache_with_rows()
    entry = cache["managers"][OWN]
    # Neue drei bestätigte Verkäufe fehlen nur im lokalen Archiv.
    new_page = read("own-transfer-first.json")
    new_keys = {history._row_key(r) for r in new_page["it"][:3]}
    entry["transfers"] = [r for r in entry["transfers"] if history._row_key(r) not in new_keys]
    entry["history_fetched_at"] = (NOW - timedelta(hours=7)).isoformat()
    history._write_cache(cache_path, cache)

    class Client:
        async def get_manager_transfers(self, league, uid, start):
            assert (league, uid, start) == (LEAGUE, OWN, 0)
            return new_page

    result = await history.load_league_history(Client(), LEAGUE, managers(), [])
    assert result["coverage"]["api_requests_attempted"] == 1
    assert len(result["feed_events"]) == 31
    saved = json.loads(cache_path.read_text())["managers"][OWN]
    assert len(saved["transfers"]) == 31
    assert saved["history_cursor"] == 0
    assert saved["history_complete"]


async def test_bootstrap_pagination_uses_actual_short_then_empty_pages(cache_path):
    cache = cache_with_rows()
    cache["managers"][OWN].update(transfers=[], history_complete=False)
    cache["managers"].pop(RIVAL)
    history._write_cache(cache_path, cache)
    seen = []

    class Client:
        async def get_manager_transfers(self, league, uid, start):
            seen.append(start)
            paths = {0: "own-transfer-first.json", 25: "manager-2663242-transfer-00025.json",
                     50: "manager-2663242-transfer-00050.json"}
            return read(paths[start])

    for _ in range(3):
        result = await history.load_league_history(Client(), LEAGUE, managers()[:1], [])
    assert seen == [0, 25, 50]
    assert result["coverage"]["histories_complete"] == 1
    assert result["coverage"]["history_entries"] == 31
    assert result["coverage"]["managers"][0]["history_exhausted_at"] == NOW.isoformat()


async def test_source_failure_retains_archive_and_is_visible(cache_path):
    cache = cache_with_rows()
    cache["managers"][OWN]["history_fetched_at"] = "2026-09-01T12:00:00Z"
    history._write_cache(cache_path, cache)

    class Client:
        async def get_manager_transfers(self, *args, **kwargs):
            raise TimeoutError("upstream")

    result = await history.load_league_history(Client(), LEAGUE, managers(), [])
    assert len(result["feed_events"]) == 31
    assert any(e.get("type") == "TimeoutError" for e in result["coverage"]["errors"])
    assert json.loads(cache_path.read_text())["managers"][OWN]["transfers"]


async def test_failed_manager_does_not_starve_the_other_source(cache_path, monkeypatch):
    cache = cache_with_rows()
    for entry in cache["managers"].values():
        entry["history_fetched_at"] = "2026-09-01T12:00:00Z"
    history._write_cache(cache_path, cache)
    monkeypatch.setattr(history, "MAX_REQUESTS", 1)
    seen = []

    class Client:
        async def get_manager_transfers(self, league, uid, start):
            seen.append(uid)
            raise TimeoutError("upstream")

    for _ in range(2):
        await history.load_league_history(Client(), LEAGUE, managers(), [])
    assert set(seen) == {OWN, RIVAL}


async def test_changed_reset_excludes_previous_purchases(cache_path):
    cache = cache_with_rows()
    # Ein alter gespeicherter Stand ohne die neue echte Resetkonfiguration.
    cache["overview"]["dt"] = "2025-08-10T08:31:42Z"
    cache["overview_fetched_at"] = "2026-09-01T12:00:00Z"
    history._write_cache(cache_path, cache)

    class Client:
        async def get_league_overview(self, league):
            return read("league-overview.json")

    result = await history.load_league_history(Client(), LEAGUE, managers(), [])
    assert len(result["historical_feed_events"]) == 1
    assert len(result["feed_events"]) == 31
    assert result["coverage"]["reset_at"] == "2026-08-10T08:31:42+00:00"


async def test_get_budget_is_bounded_including_overview(cache_path, monkeypatch):
    monkeypatch.setattr(history, "MAX_REQUESTS", 2)
    seen = []

    class Client:
        async def get_league_overview(self, league):
            seen.append("overview")
            return read("league-overview.json")

        async def get_manager_transfers(self, *args, **kwargs):
            seen.append("history")
            raise TimeoutError()

        async def get_manager_performance(self, *args, **kwargs):
            seen.append("performance")
            raise TimeoutError()

    result = await history.load_league_history(Client(), LEAGUE, managers(), [])
    assert len(seen) == result["coverage"]["api_requests_attempted"] == 2


def seed_input(tmp_path):
    path = tmp_path / "seed"
    path.mkdir()
    original = read("manager-history-manifest.json")
    manifest = {**original, "managers": {OWN: original["managers"][OWN]}}
    payloads = {
        "manager-history-manifest.json": manifest,
        "league-overview.json": read("league-overview.json"),
        "manager-transfer-entries-all.json": [r for r in read("seed-entries.json")
                                              if r["manager_id"] == OWN],
        "feed-all.json": read("feed-matching.json"),
        "player-transfer-first.json": read("player-transfer-first.json"),
        "own-performance.json": read("own-performance.json"),
    }
    for name, payload in payloads.items():
        (path / name).write_text(json.dumps(payload))
    return path


def test_seed_is_idempotent_and_preserves_genuine_original_payloads(tmp_path, cache_path):
    seed = seed_input(tmp_path)
    first = history.seed_league_history_from_archive(seed, cache_path)
    second = history.seed_league_history_from_archive(seed, cache_path)
    assert first == second
    assert len(first["feed_events"]) == 31
    assert first["coverage"]["histories_complete"] == 1
    assert first["manager_performance"][OWN] == read("own-performance.json")
    saved = json.loads(cache_path.read_text())
    assert saved["seed"]["reset_player_history_verified"] is True


def test_seed_does_not_replace_newer_cache_or_feed(tmp_path, cache_path):
    seed = seed_input(tmp_path)
    cache = cache_with_rows()
    cache["overview_fetched_at"] = "2026-09-12T10:00:00Z"
    cache["feed"] = [{**read("feed-matching.json")[0], "source": "already_verified"}]
    # Lokale Verifikationsmetadaten zur echten Buchung, kein erfundener API-Payload.
    observed = cache["managers"][OWN]["transfers"][0]
    observed["last_verified_at"] = "2026-09-12T10:00:00Z"
    history._write_cache(cache_path, cache)
    history.seed_league_history_from_archive(seed, cache_path)
    saved = json.loads(cache_path.read_text())
    assert saved["overview_fetched_at"] == "2026-09-12T10:00:00Z"
    event = next(r for r in saved["feed"] if r["i"] == cache["feed"][0]["i"])
    assert event["source"] == "already_verified"
    assert len(saved["managers"][OWN]["transfers"]) == 31
    saved_row = next(r for r in saved["managers"][OWN]["transfers"]
                     if history._row_key(r) == history._row_key(observed))
    assert saved_row["last_verified_at"] == observed["last_verified_at"]


def test_seed_refuses_to_overwrite_corrupt_existing_archive(tmp_path, cache_path):
    seed = seed_input(tmp_path)
    cache_path.write_text("damaged")
    with pytest.raises(ValueError, match="ungültigen Cache"):
        history.seed_league_history_from_archive(seed, cache_path)
    assert cache_path.read_text() == "damaged"


async def test_atomic_write_failure_keeps_previous_archive(cache_path, monkeypatch):
    cache = cache_with_rows()
    history._write_cache(cache_path, cache)
    before = cache_path.read_bytes()
    original = Path.replace

    def fail_replace(self, target):
        if target == cache_path:
            raise OSError("disk")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", fail_replace)
    result = await history.load_league_history(NoCalls(), LEAGUE, managers(), [])
    assert cache_path.read_bytes() == before
    assert any(e["type"] == "write_failed" for e in result["coverage"]["errors"])
    assert not list(cache_path.parent.glob(".league-history-*"))


def test_seed_manifest_cannot_falsely_certify_incomplete_records(tmp_path, cache_path):
    seed = seed_input(tmp_path)
    path = seed / "manager-transfer-entries-all.json"
    rows = json.loads(path.read_text())
    path.write_text(json.dumps(rows[:-1]))
    with pytest.raises(ValueError, match="Manifest"):
        history.seed_league_history_from_archive(seed, cache_path)
    assert not cache_path.exists()


async def test_corrupt_archive_is_reported_and_not_replaced_by_partial_fetch(cache_path):
    cache_path.write_text("damaged existing archive")

    class Client:
        async def get_league_overview(self, league):
            return read("league-overview.json")

    result = await history.load_league_history(Client(), LEAGUE, [], [])
    assert any(e["type"] == "read_failed" for e in result["coverage"]["errors"])
    assert cache_path.read_text() == "damaged existing archive"
