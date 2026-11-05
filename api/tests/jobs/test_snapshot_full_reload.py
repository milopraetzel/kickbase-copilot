"""Wöchentliches Voll-Nachladen der Marktwert-Historie und Leistungsdaten (P0-10).

Die Fakes liefern typisierte Domainobjekte nach dem Muster aus `test_snapshot.py`;
es werden keine API-Antworten erfunden und kein Netzwerk berührt.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, PlayerHistory, PlayerPerformance
from kickbase_agent.db.models import Player as DbPlayer
from kickbase_agent.jobs import snapshot as snapshot_module
from kickbase_agent.jobs.snapshot import (
    in_reload_block_window,
    run_full_history_reload,
    run_snapshot,
)
from kickbase_agent.kickbase.models import (
    MarketPlayer,
    MarketValueSnapshot,
    MatchdayPerformance,
    Player,
    Position,
    SquadPlayer,
)

BERLIN = ZoneInfo("Europe/Berlin")
#: Mittwochmorgen, weit außerhalb des Sperrfensters.
FREE = datetime(2026, 9, 23, 5, 50, tzinfo=BERLIN)
TODAY_UTC = datetime(2026, 9, 23, tzinfo=UTC)
LEAGUE = "42"


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _known(session: Session, *pids: str) -> None:
    for pid in pids:
        session.add(
            DbPlayer(
                id=pid, kickbase_player_id=pid, name=f"Spieler {pid}",
                position=int(Position.MIDFIELDER), team="2", current_market_value=5_000_000,
            )
        )
    session.commit()


class ReadOnlyFake:
    """Kennt NUR die zwei lesenden Methoden; jeder andere Attributzugriff scheitert."""

    def __init__(self, broken: tuple[str, ...] = ()):
        self.broken = set(broken)
        self.history_calls: list[str] = []
        self.performance_calls: list[str] = []
        self.forbidden: list[str] = []

    def __getattr__(self, name: str):
        # Greift nur bei UNBEKANNTEN Attributen (z. B. place_bid, set_lineup, get_squad).
        self.forbidden.append(name)
        raise AssertionError(f"unerlaubter Client-Zugriff: {name}")

    async def get_player_mv_history(
        self, league_id: str, player_id: str, timeframe: int = 92
    ) -> list[MarketValueSnapshot]:
        assert league_id == LEAGUE
        self.history_calls.append(player_id)
        if player_id in self.broken:
            raise RuntimeError("Kickbase antwortet nicht")
        return [
            MarketValueSnapshot(date=datetime(2026, 9, 20, tzinfo=UTC), value=4_800_000),
            MarketValueSnapshot(date=datetime(2026, 9, 21, tzinfo=UTC), value=4_900_000),
            MarketValueSnapshot(date=datetime(2026, 9, 22, tzinfo=UTC), value=5_000_000),
            # Heutiger Tag: gehört dem täglichen Snapshot, das Nachladen lässt ihn aus.
            MarketValueSnapshot(date=datetime(2026, 9, 23, tzinfo=UTC), value=5_100_000),
        ]

    async def get_player_performance(
        self, league_id: str, player_id: str
    ) -> list[MatchdayPerformance]:
        assert league_id == LEAGUE
        self.performance_calls.append(player_id)
        return [
            MatchdayPerformance(
                season="2026/2027", matchday=1, points=120, minutes=90,
                match_date=datetime(2026, 8, 28, tzinfo=UTC),
            ),
            MatchdayPerformance(
                season="2026/2027", matchday=2, points=0, minutes=0,
                match_date=datetime(2026, 9, 12, tzinfo=UTC),  # Bank, vergangen — zählt
            ),
            MatchdayPerformance(
                season="2026/2027", matchday=5, points=0, minutes=0,
                match_date=datetime(2026, 9, 26, tzinfo=UTC),  # Zukunft — nie importieren
            ),
        ]


# --- Sperrfenster (reine Hilfsfunktion) -------------------------------------------


@pytest.mark.parametrize(
    ("clock", "blocked"),
    [
        ((21, 24, 59), False),
        ((21, 25, 0), True),
        ((22, 0, 0), True),
        ((22, 25, 0), True),
        ((22, 25, 1), False),
        ((5, 50, 0), False),
    ],
)
def test_block_window_edges(clock, blocked):
    now = datetime(2026, 9, 23, *clock, tzinfo=BERLIN)
    assert in_reload_block_window(now) is blocked


def test_block_window_converts_other_timezones_to_berlin():
    # 19:30 UTC ist im Sommer 21:30 Berlin — gesperrt, obwohl die UTC-Uhr "frei" zeigt.
    assert in_reload_block_window(datetime(2026, 9, 23, 19, 30, tzinfo=UTC)) is True
    assert in_reload_block_window(datetime(2026, 9, 23, 21, 30, tzinfo=UTC)) is False


def test_block_window_matches_deploy_window_semantics():
    """Dieselbe Randsemantik wie das Deploy-Sperrfenster der MCP-Tools (kein Drift)."""
    from kickbase_agent.mcp._util import deploy_window_blocked

    for clock in [(21, 24, 59), (21, 25, 0), (22, 25, 0), (22, 25, 1)]:
        now = datetime(2026, 9, 23, *clock, tzinfo=BERLIN)
        assert in_reload_block_window(now) is deploy_window_blocked(now)


# --- Voll-Nachladen ---------------------------------------------------------------


async def test_reload_fills_every_known_player(session):
    _known(session, "p1", "p2", "p3")
    client = ReadOnlyFake()

    result = await run_full_history_reload(
        client, session, league_id=LEAGUE, now=FREE, pause_seconds=0
    )

    assert result["status"] == "ok"
    assert result["players"] == 3
    assert result["history_rows_added"] == 9  # 3 vergangene Tage x 3 Spieler
    assert result["performance_rows_added"] == 6  # 2 gespielte Spieltage x 3 Spieler
    assert result["failed_ids"] == []
    assert sorted(client.history_calls) == ["p1", "p2", "p3"]
    assert sorted(client.performance_calls) == ["p1", "p2", "p3"]
    for pid in ("p1", "p2", "p3"):
        assert session.query(PlayerHistory).filter_by(player_id=pid).count() == 3
        assert session.query(PlayerPerformance).filter_by(player_id=pid).count() == 2
    # Backfill-Zeilen bleiben punkte-neutral wie beim bestehenden Backfill.
    assert all(r.points == 0 and r.is_starter is False for r in session.query(PlayerHistory))
    # Der heutige Tag gehört dem täglichen Snapshot.
    assert session.query(PlayerHistory).filter(PlayerHistory.recorded_at == TODAY_UTC).count() == 0
    # Zukunfts-Spieltag nie importiert.
    assert session.query(PlayerPerformance).filter_by(matchday=5).count() == 0


async def test_second_reload_adds_nothing(session):
    _known(session, "p1", "p2")
    client = ReadOnlyFake()
    await run_full_history_reload(client, session, league_id=LEAGUE, now=FREE, pause_seconds=0)

    again = await run_full_history_reload(
        client, session, league_id=LEAGUE, now=FREE, pause_seconds=0
    )

    assert again["status"] == "ok"
    assert again["history_rows_added"] == 0
    assert again["performance_rows_added"] == 0
    assert session.query(PlayerHistory).count() == 6
    assert session.query(PlayerPerformance).count() == 4


async def test_reload_fills_gaps_and_corrects_rows_that_differ_from_kickbase(session):
    """Ein vorhandener Tag wird nie dupliziert; weicht sein Wert von Kickbases Verlauf ab,
    wird er berichtigt (30.09.2026: Snapshot-Wert vor dem Update), sonst bleibt er."""
    _known(session, "p1")
    for day, value in ((20, 4_800_000), (21, 1_234_567)):
        session.add(
            PlayerHistory(
                player_id="p1", recorded_at=datetime(2026, 9, day, tzinfo=UTC),
                market_value=value, points=0, is_starter=False,
            )
        )
    session.commit()

    result = await run_full_history_reload(
        ReadOnlyFake(), session, league_id=LEAGUE, now=FREE, pause_seconds=0
    )

    assert result["history_rows_added"] == 1  # nur der 22.09. fehlte
    assert result["history_rows_corrected"] == 1  # 21.09.; der richtige 20.09. bleibt
    rows = session.query(PlayerHistory).filter_by(player_id="p1").all()
    assert len(rows) == 3
    by_day = {r.recorded_at.replace(tzinfo=UTC).day: r.market_value for r in rows}
    assert by_day == {20: 4_800_000, 21: 4_900_000, 22: 5_000_000}


async def test_broken_player_does_not_stop_the_run(session):
    _known(session, "p1", "p2", "p3")
    client = ReadOnlyFake(broken=("p2",))

    result = await run_full_history_reload(
        client, session, league_id=LEAGUE, now=FREE, pause_seconds=0
    )

    assert result["status"] == "ok"
    assert result["players"] == 3
    assert result["failed_ids"] == ["p2"]
    assert result["history_rows_added"] == 6
    assert session.query(PlayerHistory).filter_by(player_id="p2").count() == 0
    assert session.query(PlayerPerformance).filter_by(player_id="p2").count() == 0
    assert session.query(PlayerHistory).filter_by(player_id="p3").count() == 3


async def test_series_of_failures_stops_instead_of_hammering_a_locked_api(session):
    """Gesperrte API (Spieltags-Auswertung, 429, abgelaufenes Token): Nach einer
    Fehlerserie bricht der Lauf ab, statt hunderte aussichtslose Abrufe zu senden."""
    pids = [f"p{i:02d}" for i in range(12)]
    _known(session, *pids)
    client = ReadOnlyFake(broken=tuple(pids))

    result = await run_full_history_reload(
        client, session, league_id=LEAGUE, now=FREE, pause_seconds=0,
        max_consecutive_failures=5,
    )

    assert result["status"] == "aborted_failures"
    assert client.history_calls == pids[:5]
    assert result["failed_ids"] == pids[:5]
    assert result["done"] == 5 and result["remaining"] == 7


async def test_single_failures_between_successes_never_abort(session):
    pids = [f"p{i:02d}" for i in range(8)]
    _known(session, *pids)
    client = ReadOnlyFake(broken=tuple(pids[::2]))

    result = await run_full_history_reload(
        client, session, league_id=LEAGUE, now=FREE, pause_seconds=0,
        max_consecutive_failures=2,
    )

    assert result["status"] == "ok"
    assert result["failed_ids"] == pids[::2]


async def test_blocked_window_makes_no_client_call(session):
    _known(session, "p1", "p2")
    client = ReadOnlyFake()

    result = await run_full_history_reload(
        client, session, league_id=LEAGUE,
        now=datetime(2026, 9, 23, 21, 25, 0, tzinfo=BERLIN), pause_seconds=0,
    )

    assert result["status"] == "blocked_window"
    assert client.history_calls == [] and client.performance_calls == []
    assert client.forbidden == []
    assert session.query(PlayerHistory).count() == 0


async def test_run_pauses_when_clock_enters_window(session):
    _known(session, "p1", "p2", "p3")
    client = ReadOnlyFake()
    ticks = iter(
        [
            datetime(2026, 9, 23, 21, 24, 0, tzinfo=BERLIN),  # Startprüfung: frei
            datetime(2026, 9, 23, 21, 24, 30, tzinfo=BERLIN),  # vor p2: frei
            datetime(2026, 9, 23, 21, 25, 0, tzinfo=BERLIN),  # vor p3: gesperrt
        ]
    )

    result = await run_full_history_reload(
        client, session, league_id=LEAGUE, now=lambda: next(ticks), pause_seconds=0
    )

    assert result["status"] == "paused_window"
    assert result["done"] == 2
    assert result["remaining"] == 1
    assert client.history_calls == ["p1", "p2"]
    # Bereits Geladenes ist dauerhaft gespeichert.
    session.rollback()
    assert session.query(PlayerHistory).count() == 6


async def test_explicit_player_ids_limit_the_universe(session):
    _known(session, "p1", "p2", "p3")
    client = ReadOnlyFake()

    result = await run_full_history_reload(
        client, session, league_id=LEAGUE, now=FREE, pause_seconds=0,
        player_ids=["p3", "p3", "ghost"],
    )

    assert result["players"] == 1
    assert client.history_calls == ["p3"]
    # Ohne players-Zeile keine History (Fremdschlüssel) — ausgewiesen, nicht abgerufen.
    assert result["unknown_ids"] == ["ghost"]


async def test_throttle_sleeps_between_players(session, monkeypatch):
    _known(session, "p1", "p2", "p3")
    pauses: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(snapshot_module.asyncio, "sleep", fake_sleep)

    await run_full_history_reload(ReadOnlyFake(), session, league_id=LEAGUE, now=FREE)

    assert pauses == [0.35, 0.35]  # zwischen den Spielern, nicht nach dem letzten


async def test_reload_never_touches_write_methods(session):
    _known(session, "p1")
    client = ReadOnlyFake()

    result = await run_full_history_reload(
        client, session, league_id=LEAGUE, now=FREE, pause_seconds=0
    )

    assert client.forbidden == []
    assert result["failed_ids"] == []


async def test_missing_league_makes_no_client_call(session, monkeypatch):
    _known(session, "p1")
    client = ReadOnlyFake()
    monkeypatch.setattr(snapshot_module, "_default_league_id", lambda: "")

    result = await run_full_history_reload(client, session, now=FREE, pause_seconds=0)

    assert result["status"] == "no_league"
    assert client.history_calls == []


# --- Zusammenspiel mit dem täglichen Snapshot -------------------------------------


class DailyFake:
    """Minimaler Tages-Client wie in test_snapshot.py (Kader p1, Markt p2)."""

    def __init__(self):
        self.history_calls: list[str] = []

    async def get_squad(self, league_id):
        return [SquadPlayer(player=_player("p1"), is_starter=True)]

    async def get_market(self, league_id):
        return [
            MarketPlayer(
                player=_player("p2"), price=3_100_000,
                expires_at=datetime(2026, 9, 25, tzinfo=UTC),
            )
        ]

    async def get_player_mv_history(self, league_id, player_id, timeframe=92):
        self.history_calls.append(player_id)
        # Derselbe Verlauf wie beim Nachladen — inklusive des heutigen Punkts (Update durch).
        return await ReadOnlyFake().get_player_mv_history(league_id, player_id, timeframe)

    async def get_player_performance(self, league_id, player_id):
        return []


def _player(pid: str) -> Player:
    return Player(
        id=pid, first_name="Max", last_name=f"Spieler{pid}", position=Position.MIDFIELDER,
        market_value=5_000_000, market_value_trend=1, points=120, team_id="2",
    )


async def test_daily_snapshot_still_writes_today_after_reload(session):
    """Das Nachladen lässt den heutigen Tag frei — der 22:05-Snapshot schreibt ihn aus
    Kickbases Verlauf, sobald das Update dort steht."""
    _known(session, "p1", "p2")
    await run_full_history_reload(
        ReadOnlyFake(), session, league_id=LEAGUE, now=FREE, pause_seconds=0
    )

    daily = DailyFake()
    result = await run_snapshot(daily, session, LEAGUE, snapshot_date=date(2026, 9, 23))

    assert result.history_rows_inserted == 2
    assert result.backfill_rows_inserted == 0  # beide bereits bekannt
    assert result.history_rows_corrected == 0
    assert daily.history_calls == ["p1", "p2"]  # der Verlauf ist maßgeblich, jeden Lauf
    today = session.query(PlayerHistory).filter(PlayerHistory.recorded_at == TODAY_UTC).all()
    assert sorted(r.player_id for r in today) == ["p1", "p2"]
    # Kickbases Wert für heute, nicht der Live-Wert (5,0 M) zur Snapshot-Zeit.
    assert all(r.market_value == 5_100_000 for r in today)
    # Genau eine Zeile je Spieler und Tag.
    days = [(r.player_id, r.recorded_at) for r in session.query(PlayerHistory)]
    assert len(days) == len(set(days))
