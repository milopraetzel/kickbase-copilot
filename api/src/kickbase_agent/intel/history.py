"""Marktwert-/Punkte-Historie aus der DB laden (für Signal-Berechnung)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from kickbase_agent.db.models import PlayerHistory, PlayerPerformance
from kickbase_agent.intel.signals import current_and_last_season
from kickbase_agent.kickbase.models import MatchdayPerformance


def _stored_utc(value: datetime | None) -> datetime | None:
    # SQLite entfernt die Zone; diese DB-Spalten werden vom Sammler in UTC geschrieben.
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def load_history(session: Session, player_ids: list[str]) -> dict[str, list[PlayerHistory]]:
    history: dict[str, list[PlayerHistory]] = {pid: [] for pid in player_ids}
    if not player_ids:
        return history
    rows = (
        session.query(PlayerHistory)
        .filter(PlayerHistory.player_id.in_(player_ids))
        .order_by(PlayerHistory.recorded_at)
        .all()
    )
    for row in rows:
        history.setdefault(row.player_id, []).append(row)
    return history


def load_performance(
    session: Session, player_ids: list[str]
) -> dict[str, list[MatchdayPerformance]]:
    """Gespielte Spieltags-Performance aus der DB, chronologisch (für Form/Rohpunkte).

    Nur GESPIELTE Einsätze (Minuten > 0) aus aktueller + letzter Saison: zukünftige
    Spieltage (0 Punkte/0 Minuten) zogen die Form-Signale systematisch auf ~0, und
    Uralt-Saisons sagen nichts über die heutige Rolle (Review-Fund). Die Signale
    filtern zusätzlich selbst — hier ist die Verteidigungslinie für ALLE Aufrufer.
    """
    result: dict[str, list[MatchdayPerformance]] = {pid: [] for pid in player_ids}
    if not player_ids:
        return result
    rows = (
        session.query(PlayerPerformance)
        .filter(
            PlayerPerformance.player_id.in_(player_ids),
            PlayerPerformance.minutes > 0,
            PlayerPerformance.season.in_(current_and_last_season()),
        )
        .order_by(PlayerPerformance.season, PlayerPerformance.matchday)
        .all()
    )
    for row in rows:
        result.setdefault(row.player_id, []).append(
            MatchdayPerformance(
                season=row.season,
                matchday=row.matchday,
                points=row.points,
                minutes=row.minutes,
                match_date=_stored_utc(row.match_date),
                points_observed=row.points_observed,
                minutes_observed=row.minutes_observed,
                is_finished=row.is_finished,
                captured_at=_stored_utc(row.captured_at),
            )
        )
    return result
