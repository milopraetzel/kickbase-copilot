"""Quellenfairness und gemeinsamer Dashboard-Build mit echten Export-Domainwerten.

Die Zahlen kommen aus der dokumentierten Export-Teilmenge in test_strategy.py.
Fehler, Uhrzeit und Cachealter sind Testbedingungen, keine erfundenen API-Antworten.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from intel.test_strategy import REAL_EXPORT
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kickbase_agent.cli import dashboard
from kickbase_agent.cli.cockpit import data as cockpit_data
from kickbase_agent.cli.cockpit import strategy_data as source
from kickbase_agent.cli.cockpit import workspace
from kickbase_agent.db.models import PlayerHistory, PlayerPerformance
from kickbase_agent.db.session import Base
from kickbase_agent.kickbase.models import MarketValueSnapshot, MatchdayPerformance

NOW = datetime(2026, 9, 12, 8, tzinfo=UTC)


def _dt(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class Clock(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


@pytest.fixture
def local_sources(monkeypatch, tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        for row in REAL_EXPORT["performance_rows"]:
            if row["player_id"] == "6668" and row["season"] == "2026/2027":
                session.add(
                    PlayerPerformance(
                        **{
                            k: row[k]
                            for k in ("player_id", "season", "matchday", "points", "minutes")
                        },
                        match_date=_dt(row["match_date"]),
                    )
                )
        history = next(r for r in REAL_EXPORT["history_rows"] if r["player_id"] == "8329")
        session.add(
            PlayerHistory(
                player_id="8329",
                recorded_at=_dt(history["recorded_at"]),
                market_value=history["market_value"],
                points=0,
                is_starter=False,
            )
        )
        session.commit()
    monkeypatch.setattr(source, "SessionLocal", sessions)
    monkeypatch.setattr(source, "_cache_path", lambda league: tmp_path / f"sources-{league}.json")
    monkeypatch.setattr(Clock, "current", NOW)
    monkeypatch.setattr(source, "datetime", Clock)
    yield sessions
    engine.dispose()


class ExportClient:
    def __init__(self, failing_ids=()):
        self.failing_ids = set(failing_ids)
        self.calls = []

    async def get_player_mv_history(self, league_id, player_id, timeframe=92):
        self.calls.append(("history", player_id))
        if player_id in self.failing_ids:
            raise OSError("Simulierter lesender API-Ausfall")
        return [
            MarketValueSnapshot(date=_dt(r["recorded_at"]), value=r["market_value"])
            for r in REAL_EXPORT["history_rows"]
            if r["player_id"] == player_id
        ]

    async def get_player_performance(self, league_id, player_id):
        self.calls.append(("performance", player_id))
        if player_id in self.failing_ids:
            raise OSError("Simulierter lesender API-Ausfall")
        return [
            MatchdayPerformance(
                **{k: r[k] for k in ("season", "matchday", "points", "minutes")},
                match_date=_dt(r["match_date"]),
            )
            for r in REAL_EXPORT["performance_rows"]
            if r["player_id"] == player_id
        ]


async def test_manager_dashboard_live_total_is_not_a_completed_score():
    raw = json.loads((Path(__file__).parents[1] / "fixtures" /
                      "manager_points_20260911.json").read_text())

    class ManagerClient:
        async def get_manager_squad(self, league_id, user_id):
            return raw["squad"]

        async def get_manager_dashboard(self, league_id, user_id):
            return raw["dashboard"]

    result = await cockpit_data.read_manager_squads(ManagerClient(), "1234567", [
        {"user_id": "2663242", "name": "emil", "me": True, "rank": 12, "points": 827},
    ])
    assert raw["dashboard"]["tp"] == sum(raw["dashboard"]["ph"]) == 827
    assert result[0]["completed_points"] is None
    assert result[0]["dashboard_points"] == 827
    assert result[0]["punkte_saison"] == 827


def _cached_entry():
    fetched = (NOW - timedelta(hours=7)).isoformat()
    history = next(r for r in REAL_EXPORT["history_rows"] if r["player_id"] == "8329")
    perf = next(
        r
        for r in REAL_EXPORT["performance_rows"]
        if r["player_id"] == "8329" and r["season"] == "2026/2027"
    )
    return {
        "history": [
            {
                "player_id": history["player_id"],
                "recorded_at": history["recorded_at"],
                "market_value": history["market_value"],
                "source": "api_history",
                "captured_at": fetched,
            }
        ],
        "history_fetched_at": fetched,
        "performance": [{**perf, "source": "api_performance", "captured_at": fetched}],
        "performance_fetched_at": fetched,
    }


async def test_twelve_permanent_failures_do_not_starve_the_next_player(
    monkeypatch,
    local_sources,
):
    pids = list(
        dict.fromkeys(
            r["player_id"] for r in REAL_EXPORT["history_rows"] if r["player_id"] != "8329"
        )
    )[:12]
    assert len(pids) == 12
    pids.append("8329")
    client = ExportClient(failing_ids=pids[:12])
    monkeypatch.setattr(source, "REFRESH_BATCH", 3)
    for _ in range(5):
        _, _, meta = await source.load_strategy_sources(
            client,
            "1234567",
            [{"pid": pid} for pid in pids],
            [],
            [],
            [],
        )
        assert meta["refresh_batch_players"] <= 3
        Clock.current += timedelta(minutes=1)
    assert ("history", "8329") in client.calls
    assert ("performance", "8329") in client.calls
    cached = source._read_cache(source._cache_path("1234567"), "1234567")
    assert cached["players"]["8329"]["history"]
    assert cached["players"]["8329"]["performance"]


async def test_database_and_api_zero_minute_rows_remain_observations(local_sources):
    _, performance, meta = await source.load_strategy_sources(
        ExportClient(),
        "1234567",
        [{"pid": "6668"}],
        [],
        [],
        [],
    )
    zeros = [
        r
        for r in performance
        if r["player_id"] == "6668" and r["season"] == "2026/2027" and r["matchday"] == 2
    ]
    assert len(zeros) == 2
    assert all(r["points"] == 0 and r["minutes"] == 0 for r in zeros)
    assert {r["source"] for r in zeros} == {"database", "api_performance"}
    direct = next(r for r in zeros if r["source"] == "api_performance")
    assert direct["captured_at"] == NOW.isoformat()
    assert meta["errors"] == []


async def test_stale_own_player_refreshes_ahead_of_historical_backfill(
    monkeypatch, local_sources,
):
    source._write_cache(source._cache_path("1234567"), {
        "version": 1, "league_id": "1234567", "players": {"8329": _cached_entry()},
    })
    historical_ids = list(dict.fromkeys(
        r["player_id"] for r in REAL_EXPORT["history_rows"] if r["player_id"] != "8329"
    ))[:12]
    monkeypatch.setattr(source, "REFRESH_BATCH", 3)
    client = ExportClient()
    await source.load_strategy_sources(
        client, "1234567", [{"pid": "8329"}], [],
        [{"spieler": [{"pid": pid} for pid in historical_ids]}], [],
    )
    refreshed = {pid for _, pid in client.calls}
    assert "8329" in refreshed
    assert len(refreshed.intersection(historical_ids)) == 2


async def test_api_read_error_preserves_old_data_and_original_capture_time(local_sources):
    entry = _cached_entry()
    path = source._cache_path("1234567")
    source._write_cache(path, {"version": 1, "league_id": "1234567", "players": {"8329": entry}})
    history, performance, meta = await source.load_strategy_sources(
        ExportClient(failing_ids=["8329"]),
        "1234567",
        [{"pid": "8329"}],
        [],
        [],
        [],
    )
    assert all(row in history for row in entry["history"])
    assert all(row in performance for row in entry["performance"])
    saved = source._read_cache(path, "1234567")["players"]["8329"]
    for field in ("history", "performance", "history_fetched_at", "performance_fetched_at"):
        assert saved[field] == entry[field]
    assert saved["history_attempted_at"] == NOW.isoformat()
    assert len(meta["errors"]) == 2


async def test_database_read_error_preserves_existing_api_cache(monkeypatch, local_sources):
    entry = _cached_entry()
    path = source._cache_path("1234567")
    source._write_cache(path, {"version": 1, "league_id": "1234567", "players": {"8329": entry}})

    def unavailable_database():
        raise OSError("Simulierter lokaler DB-Lesefehler")

    monkeypatch.setattr(source, "SessionLocal", unavailable_database)
    history, performance, meta = await source.load_strategy_sources(
        ExportClient(failing_ids=["8329"]),
        "1234567",
        [{"pid": "8329"}],
        [],
        [],
        [],
    )
    assert all(row in history for row in entry["history"])
    assert all(row in performance for row in entry["performance"])
    assert any(e["source"] == "database" for e in meta["errors"])


async def test_build_lock_covers_collection_for_the_whole_output_directory(monkeypatch, tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def collect():
        calls.append("collect")
        entered.set()
        await release.wait()
        return {"stand": "real lock test"}

    monkeypatch.setattr(cockpit_data, "collect", collect)
    monkeypatch.setattr(dashboard, "write_dashboard", lambda data, out: 17)
    first = asyncio.create_task(dashboard.build_dashboard(tmp_path / "index.html"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert await dashboard.build_dashboard(tmp_path / "other-name.html") is None
        assert calls == ["collect"]
        # Ein zweiter Prozess sieht denselben OS-Lock; keine reine asyncio-Sperre.
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import fcntl,sys; f=open(sys.argv[1],'a'); "
                "fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)",
                str(tmp_path / ".build.lock"),
            ],
            capture_output=True,
            timeout=3,
            check=False,
        )
        assert probe.returncode != 0
        assert b"BlockingIOError" in probe.stderr
    finally:
        release.set()
        assert await asyncio.wait_for(first, timeout=2) == 17
    assert await dashboard.build_dashboard(tmp_path / "index.html") == 17
    assert calls == ["collect", "collect"]


@pytest.mark.parametrize("stage", ["collect", "write"])
async def test_build_releases_shared_lock_after_failure(monkeypatch, tmp_path, stage):
    failed = False

    async def collect():
        nonlocal failed
        if stage == "collect" and not failed:
            failed = True
            raise RuntimeError("Sammlung fehlgeschlagen")
        return {}

    def write(data, out):
        nonlocal failed
        if stage == "write" and not failed:
            failed = True
            raise RuntimeError("Renderer fehlgeschlagen")
        return 19

    monkeypatch.setattr(cockpit_data, "collect", collect)
    monkeypatch.setattr(dashboard, "write_dashboard", write)
    with pytest.raises(RuntimeError):
        await dashboard.build_dashboard(tmp_path / "index.html")
    assert await dashboard.build_dashboard(tmp_path / "index.html") == 19


def test_render_failure_preserves_all_previous_published_files(monkeypatch, tmp_path):
    old = {
        "index.html": "existing index",
        "strategy.json": '{"existing":true}',
    }
    for name, text in old.items():
        (tmp_path / name).write_text(text)

    def failure(data):
        raise RuntimeError("Renderer nicht verfügbar")

    monkeypatch.setattr(workspace, "render_workspace", failure)
    with pytest.raises(RuntimeError):
        dashboard.write_dashboard({"strategy": {}}, tmp_path / "index.html")
    assert {name: (tmp_path / name).read_text() for name in old} == old
    assert {p.name for p in tmp_path.iterdir()} == set(old)


def test_successful_publication_replaces_index_last(monkeypatch, tmp_path):
    replaced = []
    actual_replace = Path.replace

    def record_replace(self, target):
        replaced.append(Path(target).name)
        return actual_replace(self, target)

    monkeypatch.setattr(Path, "replace", record_replace)
    monkeypatch.setattr(workspace, "render_workspace", lambda d: "main result")
    size = dashboard.write_dashboard({"strategy": {"value": 0}}, tmp_path / "index.html")
    assert replaced == ["strategy.json", "index.html"]
    assert size == len(b"main result")
    assert json.loads((tmp_path / "strategy.json").read_text()) == {"value": 0}
    assert not list(tmp_path.glob(".cockpit-*"))


async def test_missing_squad_retains_ranked_manager_and_history(monkeypatch):
    from kickbase_agent.cli.cockpit import league_history
    from kickbase_agent.news import strategy_news

    observed = []

    async def histories(client, league_id, manager_rows, feed_events):
        observed.extend(manager_rows)
        return {"feed_events": [], "manager_performance": {}, "coverage": {}, "overview": {}}

    async def sources(*args):
        return [], [], {"errors": []}

    async def news(**kwargs):
        return {"articles": [], "errors": []}

    monkeypatch.setattr(league_history, "load_league_history", histories)
    monkeypatch.setattr(source, "load_strategy_sources", sources)
    monkeypatch.setattr(strategy_news, "fetch_strategy_news", news)
    result = await cockpit_data._collect_strategy(
        client=object(), league_id="1234567", expected_managers=2,
        ranking_rows=[{"user_id": "2663242", "name": "emil", "points": 827},
                      {"user_id": "2227102", "name": "Niklas B.", "points": 1825}],
        feed_events=[], squad_rows=[], market_rows=[], manager_rows=[], budget=2920437,
        open_bids=[], saved_lineup={"it": []}, user_id="2663242", user_name="emil",
    )
    assert {m["user_id"] for m in observed} == {"2663242", "2227102"}
    rival = next(m for m in result["manager_profiles"] if m["user_id"] == "2227102")
    assert rival["live_points"] == 1825
    assert rival["summary"]["squad_value_eur"] is None
    assert rival["summary"]["budget_eur"] is None
    assert any(e.get("source") == "manager_squads" for e in result["source_refresh"]["errors"])
