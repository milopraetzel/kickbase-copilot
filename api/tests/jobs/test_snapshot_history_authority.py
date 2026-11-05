"""Kickbases Marktwert-Verlauf ist maßgeblich (Befund 01.10.2026).

Der Snapshot 22:05 schrieb am 30.09. für 9 von 16 geprüften Spielern den Vortageswert unter das
neue Datum: Kickbases 22-Uhr-Update kam für sie erst nach 22:05, der Live-Wert war noch der alte.
Das Nachladen überschrieb bestehende Zeilen nie — der Fehler blieb stehen. Fixture: die echte
Antwort für Scheller (7320) vom 01.10.2026 (`tests/fixtures/snapshot_20261001/SOURCE.md`).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, PlayerHistory
from kickbase_agent.db.models import Player as DbPlayer
from kickbase_agent.jobs.snapshot import run_full_history_reload, run_snapshot
from kickbase_agent.kickbase.models import (
    MarketValueSnapshot,
    Player,
    Position,
    SquadPlayer,
)

BERLIN = ZoneInfo("Europe/Berlin")
FIXTURE = Path(__file__).parents[1] / "fixtures" / "snapshot_20261001"
SCHELLER = "7320"
STALE = 5_479_033  # Wert nach dem Update am 29.09. — stand in der DB auch am 30.09.
FRESH = 5_580_536  # Kickbase für den 30.09. (Wert nach dem Update am 30.09.)


def _history() -> list[MarketValueSnapshot]:
    raw = json.loads((FIXTURE / "scheller-7320-mv-history-92.json").read_text("utf-8"))
    return [MarketValueSnapshot.from_api(item) for item in raw["it"]]


def _until(day: date) -> list[MarketValueSnapshot]:
    """Kickbases Verlauf, wie er VOR dem Update am Folgetag aussah (letzter Punkt `day`)."""
    return [snap for snap in _history() if snap.date.date() <= day]


def _day(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _scheller(mv: int) -> Player:
    return Player(
        id=SCHELLER, first_name="Jan-Luca", last_name="Scheller",
        position=Position.DEFENDER, market_value=mv, market_value_trend=2, team_id="29",
    )


class SquadOnly:
    """Kader = Scheller mit dem Live-Wert zur Snapshot-Zeit (plus `extra` Spieler); Markt leer.

    `broken` = Verlauf für ALLE nicht lesbar, `broken_ids` = nur für diese Spieler."""

    def __init__(self, live_mv: int, history: list[MarketValueSnapshot], *, broken=False,
                 extra: tuple[Player, ...] = (), broken_ids: tuple[str, ...] = ()):
        self.live_mv, self.history, self.broken = live_mv, history, broken
        self.extra, self.broken_ids = extra, set(broken_ids)
        self.history_calls: list[str] = []

    async def get_squad(self, league_id):
        return [SquadPlayer(player=p) for p in (_scheller(self.live_mv), *self.extra)]

    async def get_market(self, league_id):
        return []

    async def get_player_mv_history(self, league_id, player_id, timeframe=92):
        self.history_calls.append(player_id)
        if self.broken or player_id in self.broken_ids:
            raise RuntimeError("429 Too Many Requests")
        return self.history

    async def get_player_performance(self, league_id, player_id):
        return []


def _value(session: Session, day: date) -> list[int]:
    return [
        row.market_value
        for row in session.query(PlayerHistory).filter_by(player_id=SCHELLER).all()
        if row.recorded_at.replace(tzinfo=UTC) == _day(day)
    ]


async def test_late_update_leaves_today_missing_instead_of_writing_yesterdays_value(session):
    """30.09. 22:05: Live noch der Wert vom 29.09., Kickbases Verlauf endet am 29.09."""
    client = SquadOnly(STALE, _until(date(2026, 9, 29)))

    result = await run_snapshot(client, session, "L", snapshot_date=date(2026, 9, 30))

    assert _value(session, date(2026, 9, 30)) == []  # fehlt — nicht falsch
    assert _value(session, date(2026, 9, 29)) == [STALE]
    assert result.history_rows_inserted == 0 and result.history_missing_today == 1
    assert session.get(DbPlayer, SCHELLER).current_market_value == STALE  # Live bleibt Live


async def test_the_next_snapshot_closes_the_gap_with_kickbases_value(session):
    await run_snapshot(SquadOnly(STALE, _until(date(2026, 9, 29))), session, "L",
                       snapshot_date=date(2026, 9, 30))

    result = await run_snapshot(SquadOnly(FRESH, _history()), session, "L",
                                snapshot_date=date(2026, 10, 1))

    assert _value(session, date(2026, 9, 30)) == [FRESH]
    assert result.history_missing_today == 1  # der 01.10. steht erst nach dessen Update fest


async def test_a_stale_row_is_corrected_from_kickbases_history(session):
    """So stand es am 01.10. auf der Box: 30.09. mit dem Vortageswert."""
    session.add(DbPlayer(id=SCHELLER, kickbase_player_id=SCHELLER, name="Jan-Luca Scheller",
                         position=int(Position.DEFENDER), team="29",
                         current_market_value=STALE))
    session.add(PlayerHistory(player_id=SCHELLER, recorded_at=_day(date(2026, 9, 30)),
                              market_value=STALE, points=0, is_starter=False))
    session.commit()

    result = await run_snapshot(SquadOnly(FRESH, _history()), session, "L",
                                snapshot_date=date(2026, 10, 1))

    assert _value(session, date(2026, 9, 30)) == [FRESH]
    assert result.history_rows_corrected == 1
    assert result.backfill_rows_inserted == 91  # die übrigen Tage des Verlaufs


def _other(pid: str = "9999") -> Player:
    return Player(id=pid, first_name="Max", last_name="Andere", position=Position.MIDFIELDER,
                  market_value=2_000_000, market_value_trend=1, team_id="2")


async def test_the_snapshot_survives_a_failing_history_call(session):
    """Ein 429 beim Verlauf eines Spielers kostet nur dessen Zeilen — Spieler und Live-Wert
    werden trotzdem gespeichert, und es wird nichts aus dem Live-Wert geraten."""
    client = SquadOnly(FRESH, _history(), extra=(_other(),), broken_ids=("9999",))
    result = await run_snapshot(client, session, "L", snapshot_date=date(2026, 10, 1))

    assert result.history_failed == 1 and result.players_upserted == 2
    assert session.query(PlayerHistory).filter_by(player_id="9999").count() == 0
    assert session.get(DbPlayer, "9999").current_market_value == 2_000_000


async def test_a_failing_history_for_most_players_turns_the_job_red_but_keeps_the_rest(session):
    """Review 01.10.: Bricht der Verlaufs-Endpoint für alle, schrieb der Snapshot keine
    Historie mehr und meldete trotzdem „ok“. Jetzt: gespeichert wird, was da ist, dann rot."""
    with pytest.raises(RuntimeError, match="Verlauf"):
        await run_snapshot(SquadOnly(FRESH, [], broken=True), session, "L",
                           snapshot_date=date(2026, 10, 1))
    assert session.get(DbPlayer, SCHELLER).current_market_value == FRESH


async def test_a_lagging_history_takes_the_live_value_once_it_moved(session):
    """Hinkt Kickbases Verlauf um 22:05 hinterher (kein Punkt für heute, aber der von gestern),
    hat sich der Live-Wert aber schon bewegt, ist das Update durch — der MW ändert sich nur
    dabei. Dann ist der Live-Wert der heutige. Unbewegt (wie Scheller am 30.09.) bleibt der Tag
    offen."""
    result = await run_snapshot(SquadOnly(FRESH, _until(date(2026, 9, 29))), session, "L",
                                snapshot_date=date(2026, 9, 30))

    assert _value(session, date(2026, 9, 30)) == [FRESH]
    assert (result.history_today_from_live, result.history_missing_today) == (1, 0)


async def test_no_write_lock_is_held_while_kickbase_is_asked(tmp_path):
    """Review 01.10.: Der Snapshot hielt die SQLite-Schreibsperre über alle Netzabrufe (rund
    100 s) — andere Jobs und Vetos aus der Web-App liefen in die 30-s-Sperre. Jetzt wird erst
    gelesen, dann in einer kurzen Transaktion geschrieben."""
    path = tmp_path / "lock.db"
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    free: list[bool] = []

    def probe() -> None:
        con = sqlite3.connect(path, timeout=0)
        try:
            con.execute("BEGIN IMMEDIATE")
            con.rollback()
            free.append(True)
        except sqlite3.OperationalError:
            free.append(False)
        finally:
            con.close()

    class Probing(SquadOnly):
        async def get_player_mv_history(self, league_id, player_id, timeframe=92):
            probe()
            return await super().get_player_mv_history(league_id, player_id, timeframe)

        async def get_player_performance(self, league_id, player_id):
            probe()
            return []

    with Session(engine, future=True) as s:
        await run_snapshot(Probing(FRESH, _history(), extra=(_other(),)), s, "L",
                           snapshot_date=date(2026, 10, 1))
    assert len(free) == 4 and all(free)


async def test_a_failing_performance_call_keeps_everything_else_and_turns_the_job_red(session):
    class NoPerformance(SquadOnly):
        async def get_player_performance(self, league_id, player_id):
            raise RuntimeError("503")

    with pytest.raises(RuntimeError, match="Leistungsdaten"):
        await run_snapshot(NoPerformance(FRESH, _history()), session, "L",
                           snapshot_date=date(2026, 10, 1))
    assert _value(session, date(2026, 9, 30)) == [FRESH]


async def test_a_missing_value_never_overwrites_a_stored_one(session):
    """Ein fehlendes `mv` parst als 0 — das ist kein Marktwert (Untergrenze 500.000 €)."""
    session.add(DbPlayer(id=SCHELLER, kickbase_player_id=SCHELLER, name="Jan-Luca Scheller",
                         position=int(Position.DEFENDER), team="29",
                         current_market_value=FRESH))
    session.add(PlayerHistory(player_id=SCHELLER, recorded_at=_day(date(2026, 9, 30)),
                              market_value=FRESH, points=0, is_starter=False))
    session.commit()
    # Wie ein Eintrag ohne `mv` nach dem Parser aussähe (Wert 0) — als Domainobjekt gebaut.
    broken = [MarketValueSnapshot(date=datetime(2026, 9, 30, 2, 0), value=0),
              MarketValueSnapshot(date=datetime(2026, 10, 1, 2, 0), value=0)]

    result = await run_snapshot(SquadOnly(FRESH, broken), session, "L",
                                snapshot_date=date(2026, 10, 1))

    assert _value(session, date(2026, 9, 30)) == [FRESH]
    assert _value(session, date(2026, 10, 1)) == []
    assert result.history_rows_corrected == 0 and result.history_rows_inserted == 0


async def test_every_duplicate_of_a_day_is_corrected(session):
    session.add(DbPlayer(id=SCHELLER, kickbase_player_id=SCHELLER, name="Jan-Luca Scheller",
                         position=int(Position.DEFENDER), team="29",
                         current_market_value=STALE))
    for _ in range(2):
        session.add(PlayerHistory(player_id=SCHELLER, recorded_at=_day(date(2026, 9, 30)),
                                  market_value=STALE, points=0, is_starter=False))
    session.commit()

    result = await run_snapshot(SquadOnly(FRESH, _history()), session, "L",
                                snapshot_date=date(2026, 10, 1))

    assert _value(session, date(2026, 9, 30)) == [FRESH, FRESH]
    assert result.history_rows_corrected == 2


# --- Voll-Nachladen: korrigiert, und `check_only` zählt nur ---------------------------------


class ReloadClient:
    def __init__(self):
        self.calls: list[str] = []

    async def get_player_mv_history(self, league_id, player_id, timeframe=92):
        self.calls.append(player_id)
        return _history()

    async def get_player_performance(self, league_id, player_id):
        return []


def _stale_box(session: Session) -> None:
    session.add(DbPlayer(id=SCHELLER, kickbase_player_id=SCHELLER, name="Jan-Luca Scheller",
                         position=int(Position.DEFENDER), team="29",
                         current_market_value=FRESH))
    session.add(PlayerHistory(player_id=SCHELLER, recorded_at=_day(date(2026, 9, 29)),
                              market_value=STALE, points=0, is_starter=False))
    session.add(PlayerHistory(player_id=SCHELLER, recorded_at=_day(date(2026, 9, 30)),
                              market_value=STALE, points=0, is_starter=False))
    session.commit()


async def test_the_full_reload_corrects_stale_rows(session):
    _stale_box(session)
    result = await run_full_history_reload(
        ReloadClient(), session, league_id="L", pause_seconds=0,
        now=datetime(2026, 10, 1, 13, 30, tzinfo=BERLIN),
    )

    assert result["status"] == "ok" and result["history_rows_corrected"] == 1
    assert result["history_rows_added"] == 90  # 92 Punkte minus die beiden vorhandenen Tage
    session.expire_all()
    assert _value(session, date(2026, 9, 30)) == [FRESH]
    assert _value(session, date(2026, 9, 29)) == [STALE]  # war richtig, bleibt


async def test_check_only_counts_but_writes_nothing(session):
    _stale_box(session)
    result = await run_full_history_reload(
        ReloadClient(), session, league_id="L", pause_seconds=0,
        now=datetime(2026, 10, 1, 13, 30, tzinfo=BERLIN), check_only=True,
    )

    assert result["check_only"] is True
    assert (result["history_rows_corrected"], result["history_rows_added"]) == (1, 90)
    session.expire_all()
    assert _value(session, date(2026, 9, 30)) == [STALE]
    assert session.query(PlayerHistory).count() == 2


async def test_two_points_on_one_day_keep_the_later_one(session):
    """Liefert Kickbase einen Tag doppelt (früher Stand, dann der nach dem Update), zählt der
    spätere Zeitpunkt — auch wenn die Liste anders sortiert ist."""
    early = MarketValueSnapshot(date=datetime(2026, 9, 30, 2, 0), value=STALE)
    late = MarketValueSnapshot(date=datetime(2026, 9, 30, 23, 0), value=FRESH)

    await run_snapshot(SquadOnly(FRESH, [late, early]), session, "L",
                       snapshot_date=date(2026, 9, 30))

    assert _value(session, date(2026, 9, 30)) == [FRESH]


async def test_check_only_rolls_back_each_player_even_when_the_run_pauses(session):
    """Review 01.10.: Mit einem Spieler ließ sich Rollback je Spieler nicht von Rollback am Ende
    unterscheiden. Zwei Spieler, Pause vor dem zweiten — der erste darf nicht gespeichert sein."""
    _stale_box(session)
    session.add(DbPlayer(id="9999", kickbase_player_id="9999", name="Max Andere",
                         position=int(Position.MIDFIELDER), team="2",
                         current_market_value=2_000_000))
    session.commit()
    ticks = iter([datetime(2026, 10, 1, 21, 24, tzinfo=BERLIN),  # Start: frei
                  datetime(2026, 10, 1, 21, 25, tzinfo=BERLIN)])  # vor Spieler 2: Sperrfenster

    result = await run_full_history_reload(
        ReloadClient(), session, league_id="L", pause_seconds=0, now=lambda: next(ticks),
        check_only=True, player_ids=[SCHELLER, "9999"],
    )

    assert result["status"] == "paused_window" and result["done"] == 1
    assert result["history_rows_corrected"] == 1
    session.expire_all()
    assert _value(session, date(2026, 9, 30)) == [STALE]
    assert session.query(PlayerHistory).count() == 2


async def test_the_reload_never_touches_todays_row(session):
    """Den heutigen Tag schreibt nur der Snapshot — auch eine abweichende heutige Zeile bleibt
    beim Nachladen stehen."""
    _stale_box(session)
    session.add(PlayerHistory(player_id=SCHELLER, recorded_at=_day(date(2026, 10, 1)),
                              market_value=1_111_111, points=0, is_starter=False))
    session.commit()

    class WithToday(ReloadClient):
        async def get_player_mv_history(self, league_id, player_id, timeframe=92):
            return [*_history(), MarketValueSnapshot(date=datetime(2026, 10, 1, 2, 0),
                                                     value=5_600_000)]

    await run_full_history_reload(WithToday(), session, league_id="L", pause_seconds=0,
                                  now=datetime(2026, 10, 1, 23, 30, tzinfo=BERLIN))
    session.expire_all()
    assert _value(session, date(2026, 10, 1)) == [1_111_111]
