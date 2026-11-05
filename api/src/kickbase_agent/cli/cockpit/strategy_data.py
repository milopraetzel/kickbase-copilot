"""Datierte Quellen für die Strategieansicht; kein Kickbase-Schreibpfad."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from kickbase_agent.config import get_settings
from kickbase_agent.db.models import PlayerHistory, PlayerPerformance
from kickbase_agent.db.session import SessionLocal

log = logging.getLogger(__name__)
CACHE_VERSION = 1
REFRESH_BATCH = 12
SOURCE_TTL = timedelta(hours=6)


def _stamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _cache_path(league_id: str) -> Path:
    target = get_settings().cockpit_html_path
    parent = Path(target).parent.parent if target else Path(tempfile.gettempdir()) / "kba-sources"
    return parent / f"strategy-sources-{league_id}.json"


def _read_cache(path: Path, league_id: str) -> dict:
    try:
        data = json.loads(path.read_text())
        if data.get("league_id") == league_id and data.get("version") == CACHE_VERSION:
            if not isinstance(data.get("players"), dict):
                raise ValueError("players muss ein Objekt sein")
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return {"version": CACHE_VERSION, "league_id": league_id, "players": {}}


def _write_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".strategy-sources-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, ensure_ascii=False)
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _fresh(entry: dict, kind: str, now: datetime) -> bool:
    at = _stamp(entry.get(kind + "_fetched_at"))
    return at is not None and timedelta(0) <= now - at < SOURCE_TTL


async def load_strategy_sources(
    client: Any, league_id: str, squad_rows: list[dict], market_rows: list[dict],
    manager_rows: list[dict], feed_events: list[dict],
) -> tuple[list[dict], list[dict], dict]:
    """DB-Daten plus begrenzte API-Ergänzungen; Quelle/Alter bleiben erhalten.

    Zwölf Spieler je Build, maximal zwei Calls je Spieler. Der vorhandene Client
    drosselt. Die sechs Stunden gültigen Einträge lassen spätere Builds weitere
    Markt-/Ligaspieler nachziehen statt immer nur den eigenen Kader abzufragen.
    """
    now = datetime.now(UTC)
    history, performance, errors = [], [], []
    try:
        with SessionLocal() as session:
            history = [{"id": r.id, "player_id": r.player_id,
                        "recorded_at": r.recorded_at.isoformat(), "market_value": r.market_value,
                        "source": "database_daily"}
                       for r in session.scalars(select(PlayerHistory)).all()]
            performance = [{"id": r.id, "player_id": r.player_id, "season": r.season,
                            "matchday": r.matchday, "points": r.points, "minutes": r.minutes,
                            "match_date": r.match_date.isoformat() if r.match_date else None,
                            "source": "database"}
                           for r in session.scalars(select(PlayerPerformance)).all()]
    except Exception as exc:  # noqa: BLE001 — API-/Cache-Quellen bleiben unabhängig
        log.warning("Strategie-DB nicht lesbar: %s", type(exc).__name__)
        errors.append({"source": "database", "type": type(exc).__name__})
    # Einmaliger Bootstrap war am 20.08. vor dem MW-Update gelaufen.
    # Ohne direkten API-Beleg darf dieses Tageslabel keine Prämie begründen.
    for row in history:
        if row["recorded_at"][:10] == "2026-08-20":
            row.update(quality="unknown", unknown_reason="bootstrap_day_unverified")
    pids = list(dict.fromkeys(
        [str(r.get("pid") or "") for r in squad_rows + market_rows]
        + [str((e.get("data") or {}).get("pi") or "") for e in feed_events
           if e.get("t") == 15]
        + [str(p.get("pid") or "") for m in manager_rows for p in m.get("spieler") or []]
    ))
    pids = [pid for pid in pids if pid]
    cache_path = _cache_path(league_id)
    cache = _read_cache(cache_path, league_id)
    cached = cache.setdefault("players", {})
    # Fehlende historische Kaufwerte zuerst füllen; danach Datenfrische und Kader.
    known_days = {(r["player_id"], r["recorded_at"][:10]) for r in history}
    history_missing = set()
    from zoneinfo import ZoneInfo
    for event in feed_events:
        data = event.get("data") or {}
        when = _stamp(event.get("dt"))
        if event.get("t") != 15 or not data.get("byr") or when is None:
            continue
        local = when.astimezone(ZoneInfo("Europe/Berlin"))
        day = local.date() if local.hour >= 22 else local.date() - timedelta(days=1)
        pid = str(data.get("pi") or "")
        if (pid, day.isoformat()) not in known_days and not cached.get(pid, {}).get("history"):
            history_missing.add(pid)
    # Alte/nie abgefragte Quellen zuerst; ein Teil des Batches bleibt dafür frei.
    # Eigener Kader darf aber nicht hinter der gesamten historischen Liga warten.
    epoch = datetime.min.replace(tzinfo=UTC)
    pids.sort(key=lambda pid: (
        min(_stamp(cached.get(pid, {}).get(kind + "_attempted_at"))
            or _stamp(cached.get(pid, {}).get(kind + "_fetched_at")) or epoch
            for kind in ("history", "performance")),
        pid not in history_missing,
    ))
    own_ids = {str(r.get("pid")) for r in squad_rows}
    core_limit = max(1, REFRESH_BATCH * 2 // 3)
    core = [pid for pid in pids if pid in own_ids and any(
        not _fresh(cached.get(pid, {}), kind, now) for kind in ("history", "performance")
    )][:core_limit]
    pids = core + [pid for pid in pids if pid not in core]
    attempted, calls = 0, 0
    for pid in pids:
        entry = cached.setdefault(pid, {})
        needs_history = not _fresh(entry, "history", now)
        needs_performance = not _fresh(entry, "performance", now)
        if not (needs_history or needs_performance):
            continue
        if attempted >= REFRESH_BATCH:
            break
        attempted += 1
        if needs_history:
            entry["history_attempted_at"] = now.isoformat()
            try:
                values = await client.get_player_mv_history(league_id, pid, timeframe=92)
                calls += 1
                entry["history"] = [
                    {"player_id": pid, "recorded_at": r.date.isoformat(),
                     "market_value": r.value, "source": "api_history",
                     "captured_at": now.isoformat()}
                    for r in values
                ]
                entry["history_fetched_at"] = now.isoformat()
            except Exception as exc:  # noqa: BLE001 — einzelne Quelle bleibt offen
                errors.append({"player_id": pid, "source": "history", "type": type(exc).__name__})
        if needs_performance:
            entry["performance_attempted_at"] = now.isoformat()
            try:
                values = await client.get_player_performance(league_id, pid)
                calls += 1
                entry["performance"] = [
                    {**r.model_dump(mode="json"), "player_id": pid,
                     "source": "api_performance", "captured_at": now.isoformat()}
                    for r in values
                ]
                entry["performance_fetched_at"] = now.isoformat()
            except Exception as exc:  # noqa: BLE001 — niemals Nullen statt Fehler
                errors.append({"player_id": pid, "source": "performance",
                               "type": type(exc).__name__})
    try:
        _write_cache(cache_path, cache)
    except OSError:
        log.warning("Strategiequellen-Cache nicht speicherbar", exc_info=True)
        errors.append({"source": "cache", "type": "write_failed"})
    for pid in pids:
        entry = cached.get(pid, {})
        history.extend(entry.get("history") or [])
        performance.extend(entry.get("performance") or [])
    return history, performance, {
        "generated_at": now.isoformat(), "api_calls_succeeded": calls,
        "refresh_batch_players": attempted, "total_players": len(pids),
        "history_cached_players": sum(bool(cached.get(p, {}).get("history")) for p in pids),
        "performance_cached_players": sum(bool(cached.get(p, {}).get("performance")) for p in pids),
        "errors": errors,
    }


def enrich_manager_changes(
    managers: list[dict], known_rows: list[dict], players: list[dict], generated_at: str,
) -> None:
    """Fremde 24h-Werte nur aus nachgewiesenen, aufeinanderfolgenden Tageswerten."""
    from zoneinfo import ZoneInfo

    now = _stamp(generated_at)
    if now is None:
        return
    local = now.astimezone(ZoneInfo("Europe/Berlin"))
    value_day = local.date() if local.hour >= 22 else local.date() - timedelta(days=1)
    previous_day = value_day - timedelta(days=1)
    histories = {str(p["player_id"]): p.get("history", []) for p in players}
    known = {str(p["pid"]): p for p in known_rows if p.get("pid")}
    for manager in managers:
        for player in manager.get("spieler") or []:
            pid = str(player["pid"])
            player["mv24"], player["mv24_source"] = None, None
            if pid in known and known[pid].get("mv24") is not None:
                player["mv24"] = known[pid]["mv24"]
                player["mv24_source"] = "kickbase_detail"
                continue
            values = {r["date"]: r["value"] for r in histories.get(pid, [])
                      if r.get("quality") == "observed" and r.get("value") is not None}
            current, prior = values.get(value_day.isoformat()), values.get(previous_day.isoformat())
            if current == player.get("mv") and prior is not None:
                player["mv24"] = current - prior
                player["mv24_source"] = "consecutive_dated_history"
