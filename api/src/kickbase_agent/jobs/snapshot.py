"""Täglicher Snapshot: Kader + Markt → players/player_history.

Marktwert-Historie: Kickbases Verlauf (`get_player_mv_history`, Punkt D = Wert nach dem
22-Uhr-Update von D) ist maßgeblich — je Spieler und Tag genau ein Wert, fehlende Tage werden
eingefügt, abweichende Zeilen berichtigt (`_sync_history_days`). Der Live-Wert aus Kader und
Markt geht nur nach `players.current_market_value`: Um 22:05 kann er noch vor dem Update
liegen (30.09.2026: 9 von 16 geprüften Spielern). Steht der heutige Punkt noch nicht im
Verlauf, fehlt der Tag bis zum nächsten Lauf — fehlend statt falsch. History-Zeilen tragen
points=0/is_starter=False — Punkte-Signale kommen aus `player_performance`.

Spieltags-Punkte (player_performance) werden nur für GESPIELTE Spieltage
importiert und per Upsert nachgezogen; früher importierte Zukunfts-Zeilen
räumt jeder Lauf selbstheilend wieder ab (siehe `_is_played`).

Zusätzlich: `run_full_history_reload` lädt wöchentlich die Marktwert-Historie und
die Leistungsdaten ALLER bekannten Spieler nach (Turnaround-Studie P0-10) und berichtigt dabei
veraltete Zeilen über denselben Pfad; `check_only` zählt nur.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from kickbase_agent.db.models import Player as DbPlayer
from kickbase_agent.db.models import PlayerHistory, PlayerPerformance
from kickbase_agent.kickbase.models import (
    MarketPlayer,
    MarketValueSnapshot,
    MatchdayPerformance,
    Player,
    SquadPlayer,
)

log = logging.getLogger(__name__)

BERLIN = ZoneInfo("Europe/Berlin")

#: Sperrfenster des Voll-Nachladens (Berlin) — deckungsgleich mit dem Deploy-Sperrfenster
#: (eiserne Regel 8): um 22:05 läuft der tägliche Snapshot, um 22:15 das Briefing; ein
#: Massenabruf soll weder deren Kickbase-Kontingent noch die SQLite-Schreibsperre belegen.
RELOAD_BLOCK_START = dtime(21, 25)
RELOAD_BLOCK_END = dtime(22, 25)


class SnapshotClient(Protocol):
    async def get_squad(self, league_id: str) -> list[SquadPlayer]: ...

    async def get_market(self, league_id: str) -> list[MarketPlayer]: ...

    async def get_player_mv_history(
        self, league_id: str, player_id: str, timeframe: int = 92
    ) -> list[MarketValueSnapshot]: ...

    async def get_player_performance(
        self, league_id: str, player_id: str
    ) -> list[MatchdayPerformance]: ...


@dataclass(frozen=True)
class SnapshotResult:
    """`history_rows_inserted` = Zeilen des Snapshot-Tags, `backfill_rows_inserted` = Zeilen
    anderer Tage; `history_missing_today` = Spieler ohne heutigen Wert (Update noch nicht
    durch); `history_today_from_live` = heutiger Wert aus dem Live-Wert, weil Kickbases Verlauf
    hinkte, der Wert sich aber schon bewegt hatte; `history_failed` = Verlauf nicht lesbar."""

    players_upserted: int
    history_rows_inserted: int
    backfill_rows_inserted: int
    performance_rows_inserted: int = 0
    performance_rows_updated: int = 0
    performance_rows_deleted: int = 0
    history_rows_corrected: int = 0
    history_missing_today: int = 0
    history_failed: int = 0
    history_today_from_live: int = 0


def _midnight_utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _is_played(perf: MatchdayPerformance, reference: datetime) -> bool:
    """Gespielt = Minuten/Punkte erfasst ODER der Anstoß liegt klar in der Vergangenheit.

    Der Performance-Endpoint liefert die GANZE Saison inkl. zukünftiger Spieltage
    (points=0, minutes=0, Datum in der Zukunft). Solche Zeilen dürfen nie in die DB —
    sie zogen jedes Form-Signal systematisch auf ~0 (Review-Fund). Konservativ:
    ohne Einsatzdaten UND ohne vergangenes Datum wird nicht importiert; ein heute
    gespielter Spieltag kommt spätestens mit dem morgigen Lauf nach.
    """
    if perf.minutes > 0 or perf.points != 0:
        return True
    md = perf.match_date
    if md is None:
        return False
    if md.tzinfo is None:
        md = md.replace(tzinfo=UTC)  # API liefert UTC ("Z") — naive Werte gleich behandeln
    return md < reference


def _day_values(snapshots: Iterable[MarketValueSnapshot]) -> dict[datetime, int]:
    """Ein Wert je Tag (Mitternacht UTC): der des SPÄTESTEN Zeitpunkts dieses Tags, egal wie
    die Liste sortiert ist. Ein Wert ≤ 0 ist kein Marktwert (Untergrenze 500.000 €, ein
    fehlendes `mv` parst als 0) und zählt nicht."""
    values: dict[datetime, int] = {}
    for snap in sorted(snapshots, key=lambda s: _as_utc(s.date)):
        if snap.value > 0:
            values[_midnight_utc(snap.date.date())] = int(snap.value)
    return values


def _sync_history_days(
    session: Session,
    pid: str,
    snapshots: Iterable[MarketValueSnapshot],
    *,
    skip_days: frozenset[datetime] = frozenset(),
) -> tuple[set[datetime], list[datetime]]:
    """Kickbases Verlauf ist maßgeblich: fehlende Tage einfügen, abweichende berichtigen.

    Gemeinsamer Pfad von täglichem Snapshot und Voll-Nachladen und EINZIGER Duplikatschutz der
    Marktwert-Historie. Tageswerte aus `_day_values`; jede vorhandene Zeile eines Tags mit
    anderem Wert wird berichtigt (auch Dubletten). `skip_days` bleiben unberührt.
    Rückgabe: (eingefügte Tage, berichtigte Tage — je berichtigter Zeile einmal).
    """
    values = _day_values(snapshots)
    existing: dict[datetime, list[PlayerHistory]] = {}
    for row in session.query(PlayerHistory).filter(PlayerHistory.player_id == pid).all():
        existing.setdefault(_midnight_utc(_as_utc(row.recorded_at).date()), []).append(row)
    inserted: set[datetime] = set()
    corrected: list[datetime] = []
    for day, value in values.items():
        if day in skip_days:
            continue
        rows = existing.get(day)
        if not rows:
            session.add(PlayerHistory(player_id=pid, recorded_at=day, market_value=value,
                                      points=0, is_starter=False))
            inserted.add(day)
            continue
        for row in rows:
            if row.market_value != value:
                row.market_value = value
                corrected.append(day)
    return inserted, corrected


def _upsert_performance(
    session: Session,
    pid: str,
    performances: list[MatchdayPerformance],
    existing_perf: dict[tuple[str, str, int], PlayerPerformance],
    reference: datetime,
) -> tuple[int, int]:
    """Upsert pro (Spieler, Saison, Spieltag); liefert (eingefügt, aktualisiert).

    Gemeinsamer Pfad von täglichem Snapshot und Voll-Nachladen. `existing_perf` muss
    mindestens alle vorhandenen Zeilen dieses Spielers enthalten und wird fortgeschrieben.
    """
    inserted = 0
    updated = 0
    for perf in performances:
        if not _is_played(perf, reference):
            continue  # zukünftige/ungespielte Spieltage nie importieren
        key = (pid, perf.season, perf.matchday)
        row = existing_perf.get(key)
        if row is not None and (
            perf.points_observed is False or perf.minutes_observed is False
        ):
            # Eine partielle Antwort darf die letzte gemeinsam beobachtete
            # Punkte-/Minuten-Zeile nicht durch Parser-Nullen überschreiben.
            continue
        if row is None:
            row = PlayerPerformance(
                player_id=pid,
                season=perf.season,
                matchday=perf.matchday,
                points=perf.points,
                minutes=perf.minutes,
                match_date=perf.match_date,
            )
            session.add(row)
            existing_perf[key] = row
            inserted += 1
        elif (row.points != perf.points or row.minutes != perf.minutes
              or (row.match_date.replace(tzinfo=UTC) if row.match_date is not None
                  and row.match_date.tzinfo is None else row.match_date) != perf.match_date):
            row.points = perf.points
            row.minutes = perf.minutes
            row.match_date = perf.match_date
            updated += 1
        # Feldpräsenz gehört zum Label: fehlende API-Felder sind kein Nullergebnis.
        # Finalität wird nur übernommen, wenn die Quelle sie tatsächlich liefert.
        row.points_observed = perf.points_observed
        row.minutes_observed = perf.minutes_observed
        row.is_finished = perf.is_finished
        row.captured_at = datetime.now(UTC)
    return inserted, updated


async def run_snapshot(
    client: SnapshotClient,
    session: Session,
    league_id: str,
    snapshot_date: date | None = None,
    backfill_timeframe: int = 92,
) -> SnapshotResult:
    day = snapshot_date or datetime.now(UTC).date()
    recorded_at = _midnight_utc(day)

    # 1) Erst alles bei Kickbase lesen, DANN schreiben (Review 01.10.): Eine Schreibsperre über
    #    die rund 100 Netzabrufe ließe andere Jobs und Vetos aus der Web-App in die
    #    30-s-Wartezeit laufen (db/session.py). Ein nicht lesbarer Verlauf oder Leistungsabruf
    #    kostet nur die Zeilen dieses Spielers; rot wird der Lauf erst nach dem Speichern.
    squad = await client.get_squad(league_id)
    market = await client.get_market(league_id)

    players: dict[str, Player] = {sp.player.id: sp.player for sp in squad}
    for mp in market:
        players.setdefault(mp.player.id, mp.player)

    histories: dict[str, list[MarketValueSnapshot]] = {}
    history_failed = 0
    for pid in players:
        try:
            histories[pid] = list(await client.get_player_mv_history(
                league_id, pid, timeframe=backfill_timeframe
            ))
        except Exception as exc:  # noqa: BLE001 — ein Spieler kippt den Snapshot nie
            history_failed += 1
            log.warning("Snapshot: Verlauf %s nicht lesbar (%s)", pid, type(exc).__name__)
    performances: dict[str, list[MatchdayPerformance]] = {}
    performance_failed: list[str] = []
    for pid in players:
        try:
            performances[pid] = await client.get_player_performance(league_id, pid)
        except Exception as exc:  # noqa: BLE001 — erst speichern, dann ehrlich rot
            performance_failed.append(pid)
            log.warning("Snapshot: Leistungsdaten %s nicht lesbar (%s)", pid,
                        type(exc).__name__)

    # 2) Schreiben — eine kurze Transaktion.
    players_upserted = 0
    for pid, p in players.items():
        full_name = f"{p.first_name} {p.last_name}".strip()
        db_player = session.get(DbPlayer, pid)
        if db_player is None:
            session.add(
                DbPlayer(
                    id=pid,
                    kickbase_player_id=pid,
                    name=full_name,
                    position=int(p.position),
                    team=p.team_id,
                    current_market_value=p.market_value,
                )
            )
        else:
            db_player.name = full_name
            db_player.position = int(p.position)
            db_player.team = p.team_id
            db_player.current_market_value = p.market_value
        players_upserted += 1
    session.flush()

    # Marktwert-Historie aus Kickbases Verlauf (maßgeblich, siehe Modul-Docstring). Fehlt der
    # heutige Punkt, hat sich der Live-Wert gegenüber gestern aber schon bewegt, ist das Update
    # durch (der MW ändert sich nur dabei) und der Live-Wert der heutige; unbewegt bleibt der
    # Tag offen — fehlend statt falsch (Scheller 30.09.: live = gestern, Update kam später).
    history_rows_inserted = backfill_rows_inserted = 0
    history_missing_today = history_today_from_live = 0
    corrected_days: list[datetime] = []
    yesterday = recorded_at - timedelta(days=1)
    for pid, snapshots in histories.items():
        values = _day_values(snapshots)
        live = int(players[pid].market_value)
        if recorded_at not in values:
            if values.get(yesterday) and live > 0 and live != values[yesterday]:
                snapshots = [*snapshots, MarketValueSnapshot(date=recorded_at, value=live)]
                history_today_from_live += 1
            else:
                history_missing_today += 1
        inserted, corrected = _sync_history_days(session, pid, snapshots)
        history_rows_inserted += recorded_at in inserted
        backfill_rows_inserted += len(inserted - {recorded_at})
        corrected_days += corrected
    history_rows_corrected = len(corrected_days)
    if history_missing_today or corrected_days or history_failed or history_today_from_live:
        per_day = ", ".join(f"{d:%d.%m.}: {n}" for d, n in sorted(Counter(corrected_days).items()))
        log.info(
            "Snapshot %s: %d ohne heutigen Wert, %d heute aus dem Live-Wert, %d berichtigt%s, "
            "%d Verläufe nicht lesbar", day, history_missing_today, history_today_from_live,
            history_rows_corrected, f" ({per_day})" if per_day else "", history_failed,
        )

    # Selbstheilung (Review-Fund): früher importierte ZUKUNFTS-Spieltage (0 Punkte,
    # 0 Minuten, Anstoß heute oder später) wieder löschen — idempotent bei jedem
    # Lauf, bewusst ohne Alembic-Migration. Bankspiele vergangener Spieltage
    # (0/0 mit Datum in der Vergangenheit) bleiben erhalten.
    performance_rows_deleted = (
        session.query(PlayerPerformance)
        .filter(
            PlayerPerformance.minutes == 0,
            PlayerPerformance.points == 0,
            PlayerPerformance.match_date.isnot(None),
            PlayerPerformance.match_date >= recorded_at,
        )
        .delete(synchronize_session=False)
    )

    # Spieltags-Performance JEDEN Lauf für ALLE Spieler nachziehen — Upsert pro
    # (Spieler, Saison, Spieltag): neue GESPIELTE Spieltage einfügen, bestehende
    # Zeilen bei nachträglichen Kickbase-Korrekturen (Punkte/Minuten) aktualisieren.
    # Der frühere Insert-only-Ansatz fror korrigierte Werte dauerhaft ein.
    existing_perf: dict[tuple[str, str, int], PlayerPerformance] = {
        (row.player_id, row.season, row.matchday): row
        for row in session.query(PlayerPerformance).all()
    }
    performance_rows_inserted = 0
    performance_rows_updated = 0
    for pid, performance in performances.items():
        inserted, updated = _upsert_performance(
            session,
            pid,
            performance,
            existing_perf,
            recorded_at,
        )
        performance_rows_inserted += inserted
        performance_rows_updated += updated

    session.commit()

    # 3) Ehrlich rot — nachdem gespeichert ist, was da war (Review 01.10.: ein für alle
    #    gebrochener Verlaufs-Endpoint meldete sonst „ok“ und schrieb keine Historie mehr).
    if players and history_failed * 2 > len(players):
        raise RuntimeError(
            f"Snapshot: Kickbases Verlauf für {history_failed} von {len(players)} Spielern nicht "
            "lesbar — Marktwert-Historie fehlt für sie (Spieler und Leistungsdaten gespeichert)."
        )
    if performance_failed:
        raise RuntimeError(
            f"Snapshot: Leistungsdaten für {len(performance_failed)} von {len(players)} "
            "Spielern nicht lesbar (alles andere gespeichert)."
        )
    return SnapshotResult(
        players_upserted,
        history_rows_inserted,
        backfill_rows_inserted,
        performance_rows_inserted,
        performance_rows_updated,
        performance_rows_deleted,
        history_rows_corrected,
        history_missing_today,
        history_failed,
        history_today_from_live,
    )


class HistoryReloadClient(Protocol):
    """Rein lesender Ausschnitt des Kickbase-Clients für das Voll-Nachladen."""

    async def get_player_mv_history(
        self, league_id: str, player_id: str, timeframe: int = 92
    ) -> list[MarketValueSnapshot]: ...

    async def get_player_performance(
        self, league_id: str, player_id: str
    ) -> list[MatchdayPerformance]: ...


def in_reload_block_window(now: datetime) -> bool:
    """True, wenn `now` im Sperrfenster 21:25:00–22:25:00 Berlin liegt.

    Randsemantik: BEIDE Ränder sind gesperrt, sekundengenau — 21:24:59 erlaubt,
    21:25:00 gesperrt, 22:25:00 gesperrt, 22:25:01 erlaubt. Das entspricht
    `mcp/_util.deploy_window_blocked`; die Funktion steht trotzdem hier, damit der
    Job-Layer nicht vom MCP-Layer abhängt (ein Test hält beide deckungsgleich).
    Zeitzonenbehaftete Werte werden nach Berlin umgerechnet; ein naiver Wert gilt
    als Berliner Ortszeit.
    """
    local = now.replace(tzinfo=BERLIN) if now.tzinfo is None else now.astimezone(BERLIN)
    clock = local.time().replace(tzinfo=None)
    return RELOAD_BLOCK_START <= clock <= RELOAD_BLOCK_END


def _default_league_id() -> str:
    """Konfigurierte Liga als Rückfall, wenn der Aufrufer keine übergibt."""
    from kickbase_agent.config import get_settings

    return str(get_settings().kickbase_league_id or "")


def _reload_clock(now: datetime | Callable[[], datetime] | None) -> Callable[[], datetime]:
    """Uhr des Laufs: Callable wird direkt benutzt, ein fester Startwert läuft monoton weiter."""
    if callable(now):
        return now
    if now is None:
        return lambda: datetime.now(BERLIN)
    started = _time.monotonic()
    return lambda: now + timedelta(seconds=_time.monotonic() - started)


def _as_utc(value: datetime) -> datetime:
    """SQLite liefert `DateTime(timezone=True)` naiv zurück — gespeichert ist UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def run_full_history_reload(
    client: HistoryReloadClient,
    session: Session,
    *,
    league_id: str | None = None,
    now: datetime | Callable[[], datetime] | None = None,
    pause_seconds: float = 0.35,
    player_ids: Iterable[str] | None = None,
    timeframe: int = 92,
    max_consecutive_failures: int = 10,
    check_only: bool = False,
) -> dict[str, Any]:
    """Wöchentliches Voll-Nachladen: Marktwert-Historie + Leistungsdaten ALLER bekannten Spieler.

    Warum (Turnaround-Studie P0-10, mit korrigierter Diagnose): Die Historie ist nie
    „von 427 auf 46 Spieler gefallen". Der tägliche Snapshot erfasst nur Kader und
    Markt, und der 92-Tage-Backfill lief bis 01.10.2026 nur für NIE gesehene Spieler (seitdem
    liest der Snapshot den Verlauf seiner Kader-/Marktspieler jeden Abend). Wer einmal
    gesehen wurde und danach weder im Kader noch auf dem Markt stand, bekommt keine
    weiteren Tageszeilen — dadurch tragen nur rund 6 % der Marktkandidaten-Zeilen einen
    beobachteten Tagesschritt. Die Lücke ist rückwirkend schließbar, weil die
    Kickbase-Historie rund 92 Tage zurückreicht.

    Ablauf je Spieler: `get_player_mv_history` und `get_player_performance` lesen (die
    einzigen beiden Client-Aufrufe — rein lesend gegen Kickbase), dann über
    `_sync_history_days` fehlende Tage einfügen und abweichende Zeilen berichtigen (Kickbases
    Verlauf ist maßgeblich; 30.09.2026 stand der Snapshot-Wert vor dem Update in der DB) und
    Spieltage über `_upsert_performance` schreiben — dieselben Pfade wie der tägliche
    Snapshot. Commit je Spieler: ein Abbruch oder eine Pause verliert nichts, ein zweiter
    Lauf ändert nichts mehr.
    - `check_only`: derselbe Lauf, aber jeder Spieler wird zurückgerollt statt gespeichert —
      die Zähler sagen, was ein echter Lauf einfügen und berichtigen würde.

    - Universum: `player_ids`, sonst alle Zeilen der Tabelle `players` (dort landet jeder
      je im Kader oder Markt gesehene Spieler). Übergebene IDs ohne `players`-Zeile werden
      NICHT abgerufen (History hängt per Fremdschlüssel an `players`) und unter
      `unknown_ids` ausgewiesen.
    - Der heutige Tag (UTC) bleibt dem täglichen Snapshot (dieselbe Quelle; um 04:10 steht
      er ohnehin noch nicht in Kickbases Verlauf). Selbstheilung und Spieler-Upsert bleiben
      ebenfalls beim täglichen Snapshot.
    - Sperrfenster (`in_reload_block_window`): Start im Fenster -> `blocked_window` ohne
      einen einzigen Abruf. Läuft die Uhr während des Laufs hinein, wird der laufende
      Spieler beendet und `paused_window` mit `done`/`remaining` gemeldet; der nächste
      Lauf setzt dank Idempotenz einfach fort.
    - `now`: fester Startzeitpunkt (läuft monoton weiter), Callable als Uhr oder None
      (Systemuhr, Berlin). Drosselung: `pause_seconds` zwischen zwei Spielern.
    - Fehler bei EINEM Spieler (Client oder DB) beenden den Lauf nicht: Rollback dieses
      Spielers, ID in `failed_ids`, weiter.
    - Eine Serie von `max_consecutive_failures` Fehlern in Folge bricht dagegen ab
      (`aborted_failures` mit `done`/`remaining`): Eine gesperrte API (Spieltags-
      Auswertung, 429, abgelaufenes Token) soll nicht mit hunderten aussichtslosen
      Abrufen belegt werden. Der nächste Lauf setzt dank Idempotenz fort.

    Rückgabe (immer alle Zählerfelder): `{"status": "ok" | "paused_window" |
    "blocked_window" | "no_league" | "aborted_failures", "players", "history_rows_added",
    "history_rows_corrected", "performance_rows_added", "performance_rows_updated",
    "failed_ids", "unknown_ids", "check_only"}`,
    bei `paused_window`/`aborted_failures` zusätzlich `done` und `remaining`. `players` ist
    die Größe des Universums, `done` die Zahl der bearbeiteten Spieler (inklusive fehlgeschlagener).
    """
    clock = _reload_clock(now)
    result: dict[str, Any] = {
        "status": "ok",
        "players": 0,
        "history_rows_added": 0,
        "history_rows_corrected": 0,
        "performance_rows_added": 0,
        "performance_rows_updated": 0,
        "failed_ids": [],
        "unknown_ids": [],
        "check_only": check_only,
    }

    started_at = clock()
    if in_reload_block_window(started_at):
        result["status"] = "blocked_window"
        return result

    league = league_id or _default_league_id()
    if not league:
        result["status"] = "no_league"
        return result

    known = sorted(row[0] for row in session.query(DbPlayer.id).all())
    if player_ids is None:
        universe = known
    else:
        known_set = set(known)
        wanted = list(dict.fromkeys(str(pid) for pid in player_ids))
        universe = [pid for pid in wanted if pid in known_set]
        result["unknown_ids"] = [pid for pid in wanted if pid not in known_set]
    result["players"] = len(universe)
    session.rollback()  # keine offene Lesetransaktion über die Netzwerkabrufe halten

    today = _midnight_utc(_as_utc(started_at).date())
    failures_in_a_row = 0

    for index, pid in enumerate(universe):
        if index > 0:
            await asyncio.sleep(pause_seconds)
            if in_reload_block_window(clock()):
                result["status"] = "paused_window"
                result["done"] = index
                result["remaining"] = len(universe) - index
                return result
        try:
            snapshots = await client.get_player_mv_history(league, pid, timeframe=timeframe)
            performances = await client.get_player_performance(league, pid)

            added_days, corrected_days = _sync_history_days(
                session, pid, snapshots, skip_days=frozenset({today})
            )
            existing_perf = {
                (row.player_id, row.season, row.matchday): row
                for row in session.query(PlayerPerformance)
                .filter(PlayerPerformance.player_id == pid)
                .all()
            }
            perf_added, perf_updated = _upsert_performance(
                session, pid, performances, existing_perf, today
            )
            if check_only:
                session.rollback()
            else:
                session.commit()
        except Exception as exc:  # noqa: BLE001 — ein Spieler darf den Lauf nie beenden
            session.rollback()
            result["failed_ids"].append(pid)
            log.warning("Voll-Nachladen: Spieler %s fehlgeschlagen (%s)", pid, type(exc).__name__)
            failures_in_a_row += 1
            if failures_in_a_row >= max_consecutive_failures:
                result["status"] = "aborted_failures"
                result["done"] = index + 1
                result["remaining"] = len(universe) - index - 1
                return result
            continue
        failures_in_a_row = 0
        result["history_rows_added"] += len(added_days)
        result["history_rows_corrected"] += len(corrected_days)
        result["performance_rows_added"] += perf_added
        result["performance_rows_updated"] += perf_updated

    return result
