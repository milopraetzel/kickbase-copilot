from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, PlayerHistory, PlayerPerformance
from kickbase_agent.db.models import Player as DbPlayer
from kickbase_agent.jobs.snapshot import run_snapshot
from kickbase_agent.kickbase.models import (
    MarketPlayer,
    MarketValueSnapshot,
    MatchdayPerformance,
    Player,
    Position,
    SquadPlayer,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _player(pid: str, mv: int = 5_000_000) -> Player:
    return Player(
        id=pid,
        first_name="Max",
        last_name=f"Spieler{pid}",
        position=Position.MIDFIELDER,
        market_value=mv,
        market_value_trend=1,
        points=120,
        team_id="2",
    )


#: Kickbases Verlauf je Spieler (Tag im August, Wert nach dem Update dieses Tages). Der
#: Snapshot-Tag 13.08. steht schon darin — sein Update ist durch.
_DAYS = {"p1": [(11, 4_800_000), (12, 4_900_000), (13, 5_000_000)],
         "p2": [(11, 2_800_000), (12, 2_900_000), (13, 3_000_000)]}


class FakeClient:
    def __init__(self, mv: int = 5_000_000, perf_points: int = 120, perf_minutes: int = 90,
                 extra_days: dict[str, list[tuple[int, int]]] | None = None):
        self.mv = mv
        self.perf_points = perf_points
        self.perf_minutes = perf_minutes
        self.history_calls: list[str] = []
        self.days = {pid: [*days, *(extra_days or {}).get(pid, [])]
                     for pid, days in _DAYS.items()}

    async def get_squad(self, league_id: str) -> list[SquadPlayer]:
        return [SquadPlayer(player=_player("p1", mv=self.mv), is_starter=True)]

    async def get_market(self, league_id: str) -> list[MarketPlayer]:
        return [
            MarketPlayer(
                player=_player("p2", mv=3_000_000),
                price=3_100_000,
                expires_at=datetime(2026, 8, 14, tzinfo=UTC),
            )
        ]

    async def get_player_mv_history(
        self, league_id: str, player_id: str, timeframe: int = 92
    ) -> list[MarketValueSnapshot]:
        self.history_calls.append(player_id)
        return [MarketValueSnapshot(date=datetime(2026, 8, day, tzinfo=UTC), value=value)
                for day, value in self.days[player_id]]

    async def get_player_performance(
        self, league_id: str, player_id: str
    ) -> list[MatchdayPerformance]:
        return [
            MatchdayPerformance(
                season="2025/2026", matchday=1,
                points=self.perf_points, minutes=self.perf_minutes,
                match_date=datetime(2025, 8, 23, tzinfo=UTC),
            ),
            # Zukunfts-Spieltag, wie ihn der echte Endpoint liefert (0/0 + Datum in
            # der Zukunft) — darf NIE importiert werden (Review-Fund: Form → ~0).
            MatchdayPerformance(
                season="2026/2027", matchday=1, points=0, minutes=0,
                match_date=datetime(2026, 9, 15, tzinfo=UTC),
            ),
        ]


@pytest.mark.asyncio
async def test_snapshot_upserts_players_and_writes_history(session):
    result = await run_snapshot(FakeClient(), session, "42", snapshot_date=date(2026, 8, 13))

    assert result.players_upserted == 2
    assert result.history_rows_inserted == 2  # p1 + p2 für heute (aus Kickbases Verlauf)
    assert result.backfill_rows_inserted == 4  # 2 Tage × 2 neue Spieler
    assert result.performance_rows_inserted == 2  # 1 Spieltag × 2 neue Spieler
    assert session.query(DbPlayer).count() == 2
    assert session.query(PlayerHistory).count() == 6

    row_today = (
        session.query(PlayerHistory)
        .filter(
            PlayerHistory.player_id == "p1",
            PlayerHistory.recorded_at == datetime(2026, 8, 13, tzinfo=UTC),
        )
        .one()
    )
    # Marktwert aus Kickbases Verlauf (der Live-Wert kann um 22:05 vor dem Update liegen);
    # points + is_starter neutral bis performance-/lineup-Endpunkte
    # (squad `p` ist kumulativ, `st` ist ein Status-Feld — beides würde Signale verfälschen).
    assert row_today.market_value == 5_000_000
    assert row_today.points == 0
    assert row_today.is_starter is False


@pytest.mark.asyncio
async def test_snapshot_is_idempotent_per_day(session):
    client = FakeClient()
    await run_snapshot(client, session, "42", snapshot_date=date(2026, 8, 13))

    result2 = await run_snapshot(client, session, "42", snapshot_date=date(2026, 8, 13))

    assert result2.history_rows_inserted == 0
    assert result2.backfill_rows_inserted == 0
    assert result2.history_rows_corrected == 0
    assert session.query(PlayerHistory).count() == 6
    # Kickbases Verlauf wird jeden Lauf gelesen (er ist maßgeblich) — geändert wird nur,
    # was abweicht.
    assert client.history_calls.count("p1") == 2


@pytest.mark.asyncio
async def test_snapshot_next_day_appends_and_updates_mv(session):
    await run_snapshot(FakeClient(), session, "42", snapshot_date=date(2026, 8, 13))

    result = await run_snapshot(
        FakeClient(mv=5_500_000, extra_days={"p1": [(14, 5_500_000)], "p2": [(14, 3_000_000)]}),
        session, "42", snapshot_date=date(2026, 8, 14),
    )

    assert result.history_rows_inserted == 2
    assert result.backfill_rows_inserted == 0  # Spieler bereits bekannt
    assert session.get(DbPlayer, "p1").current_market_value == 5_500_000


@pytest.mark.asyncio
async def test_snapshot_skips_unplayed_future_matchdays(session):
    """Zukunfts-Spieltage (0/0, Datum nach dem Snapshot-Tag) landen nie in der DB."""
    await run_snapshot(FakeClient(), session, "42", snapshot_date=date(2026, 8, 13))

    seasons = {row.season for row in session.query(PlayerPerformance).all()}
    assert seasons == {"2025/2026"}  # der 2026/2027-Zukunftseintrag wurde übersprungen
    assert session.query(PlayerPerformance).count() == 2  # nur der gespielte Spieltag


@pytest.mark.asyncio
async def test_snapshot_upserts_corrected_performance(session):
    """Nachträgliche Kickbase-Korrekturen (Punkte/Minuten) ziehen per Upsert nach."""
    await run_snapshot(FakeClient(), session, "42", snapshot_date=date(2026, 8, 13))

    result = await run_snapshot(
        FakeClient(perf_points=135, perf_minutes=95), session, "42",
        snapshot_date=date(2026, 8, 14),
    )

    assert result.performance_rows_inserted == 0
    assert result.performance_rows_updated == 2  # p1 + p2 korrigiert
    row = (
        session.query(PlayerPerformance)
        .filter_by(player_id="p1", season="2025/2026", matchday=1)
        .one()
    )
    assert row.points == 135
    assert row.minutes == 95
    # Unveränderte Werte lösen kein Update aus (idempotent)
    result3 = await run_snapshot(
        FakeClient(perf_points=135, perf_minutes=95), session, "42",
        snapshot_date=date(2026, 8, 15),
    )
    assert result3.performance_rows_updated == 0


@pytest.mark.asyncio
async def test_snapshot_preserves_known_result_when_followup_response_is_partial(session):
    client = FakeClient()
    await run_snapshot(client, session, "42", snapshot_date=date(2026, 8, 13))

    async def partial(league_id, player_id):
        return [MatchdayPerformance(
            season="2025/2026", matchday=1, points=0, minutes=90,
            points_observed=False, minutes_observed=True,
            match_date=datetime(2025, 8, 23, tzinfo=UTC),
        )]

    client.get_player_performance = partial
    result = await run_snapshot(client, session, "42", snapshot_date=date(2026, 8, 14))
    assert result.performance_rows_updated == 0
    assert all(row.points == 120 for row in session.query(PlayerPerformance))
    assert all(row.points_observed is not False for row in session.query(PlayerPerformance))


@pytest.mark.asyncio
async def test_snapshot_updates_corrected_date_and_rehydrates_label_times_as_utc(session):
    from kickbase_agent.intel.history import load_performance

    client = FakeClient()
    await run_snapshot(client, session, "42", snapshot_date=date(2026, 8, 13))
    corrected = datetime(2025, 8, 24, tzinfo=UTC)

    async def corrected_performance(league_id, player_id):
        return [MatchdayPerformance(
            season="2025/2026", matchday=1, points=120, minutes=90,
            points_observed=True, minutes_observed=True, is_finished=True, match_date=corrected,
        )]

    client.get_player_performance = corrected_performance
    result = await run_snapshot(client, session, "42", snapshot_date=date(2026, 8, 14))
    assert result.performance_rows_updated == 2
    session.expire_all()
    performance = load_performance(session, ["p1"])["p1"][0]
    assert performance.match_date == corrected
    assert performance.captured_at.utcoffset() == timedelta(0)
    assert performance.points_observed is True and performance.minutes_observed is True
    assert performance.is_finished is True


@pytest.mark.asyncio
async def test_snapshot_self_heals_stale_future_rows(session):
    """Früher importierte Zukunfts-Zeilen werden beim Lauf gelöscht (idempotent);
    echte Bankspiele der Vergangenheit (0/0 mit altem Datum) bleiben stehen."""
    session.add(PlayerPerformance(
        player_id="p1", season="2026/2027", matchday=3, points=0, minutes=0,
        match_date=datetime(2026, 9, 20, tzinfo=UTC),
    ))
    session.add(PlayerPerformance(
        player_id="p1", season="2025/2026", matchday=2, points=0, minutes=0,
        match_date=datetime(2025, 8, 30, tzinfo=UTC),  # Bank ohne Einsatz — bleibt
    ))
    session.commit()

    result = await run_snapshot(FakeClient(), session, "42", snapshot_date=date(2026, 8, 13))

    assert result.performance_rows_deleted == 1
    remaining = {
        (row.season, row.matchday) for row in
        session.query(PlayerPerformance).filter_by(player_id="p1").all()
    }
    assert ("2026/2027", 3) not in remaining
    assert ("2025/2026", 2) in remaining  # Bankspiel überlebt die Selbstheilung

    result2 = await run_snapshot(FakeClient(), session, "42", snapshot_date=date(2026, 8, 14))
    assert result2.performance_rows_deleted == 0  # nichts mehr zu heilen
