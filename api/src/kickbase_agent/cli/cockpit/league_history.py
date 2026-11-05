"""Persistente Ligaquellen: echte Transfers, Resetgrenze und Manager-Spieltagshistorien.

Der Live-Feed ergänzt das Archiv sofort. Kleine GET-Batches schließen historische
Lücken und erneuern Managerdaten; ein vollständiger Seed verhindert jahreslange
Neudownloads je Cockpit-Build. Alte Saisons kommen nie in die aktuelle Kostenbasis.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from kickbase_agent.cli.cockpit.strategy_data import _cache_path as strategy_cache_path

CACHE_VERSION = 1
MAX_REQUESTS = 8
TRANSFER_PAGE_SIZE = 25
HISTORY_TTL = timedelta(hours=6)
PERFORMANCE_TTL = timedelta(hours=2)
OVERVIEW_TTL = timedelta(hours=1)


def _stamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def _now() -> datetime:
    return datetime.now(UTC)


def _cache_path(league_id: str) -> Path:
    return strategy_cache_path(league_id).with_name(f"league-history-{league_id}.json")


def _empty(league_id: str) -> dict:
    return {"version": CACHE_VERSION, "league_id": league_id, "managers": {}, "feed": []}


def _read_cache(path: Path, league_id: str) -> tuple[dict, list[dict]]:
    if not path.exists():
        return _empty(league_id), []
    try:
        data = json.loads(path.read_text())
        if (
            not isinstance(data, dict)
            or data.get("version") != CACHE_VERSION
            or data.get("league_id") != league_id
            or not isinstance(data.get("managers"), dict)
            or not isinstance(data.get("feed"), list)
        ):
            raise ValueError("Ungültiger Liga-Cache")
        for uid, entry in data["managers"].items():
            if not isinstance(uid, str) or not isinstance(entry, dict):
                raise ValueError("Ungültiger Manager im Liga-Cache")
            if not isinstance(entry.get("transfers", []), list):
                raise ValueError("Ungültige Transferliste")
        return data, []
    except (OSError, ValueError, TypeError):
        return _empty(league_id), [{"source": "cache", "type": "read_failed"}]


def _write_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".league-history-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, allow_nan=False)
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _event_key(row: dict) -> tuple[str, str, int]:
    date = _stamp(row.get("dt"))
    price = row.get("trp")
    if (
        not row.get("pi")
        or date is None
        or isinstance(price, bool)
        or not isinstance(price, (int, float))
        or price < 0
        or int(price) != price
    ):
        raise ValueError("Ungültiges Transferereignis")
    return str(row["pi"]), date.isoformat(), int(price)


def _row_key(row: dict) -> tuple:
    if row.get("tty") not in (1, 2) or isinstance(row.get("tty"), bool):
        raise ValueError("Unbekannte Transferrichtung")
    return (*_event_key(row), row["tty"])


def _merge_rows(old: list[dict], new: list[dict]) -> list[dict]:
    rows = {_row_key(r): r for r in old}
    rows.update({_row_key(r): r for r in new})
    return sorted(rows.values(), key=lambda r: _event_key(r)[1], reverse=True)


def _manager_page(payload: Any, uid: str) -> list[dict]:
    if (
        not isinstance(payload, dict)
        or str(payload.get("u")) != uid
        or not isinstance(payload.get("it"), list)
    ):
        raise ValueError("Historienseite gehört nicht zum erwarteten Manager")
    rows = payload["it"]
    for row in rows:
        _row_key(row)
    return rows


def _performance(payload: Any, uid: str) -> dict:
    if (
        not isinstance(payload, dict)
        or str(payload.get("u")) != uid
        or not isinstance(payload.get("it"), list)
    ):
        raise ValueError("Ungültige Manager-Spieltagshistorie")
    for season in payload["it"]:
        if not season.get("sid") or not season.get("sn") or not isinstance(season.get("it"), list):
            raise ValueError("Explizite Saisonkennung fehlt")
    return payload  # Saison- und Spieltagsfelder unverändert, cur nie als Saisonfilter.


def _merge_feed(old: list[dict], new: list[dict]) -> list[dict]:
    by_id = {str(row["i"]): row for row in old + new if isinstance(row, dict) and row.get("i")}
    return sorted(by_id.values(), key=lambda r: str(r.get("dt", "")), reverse=True)


def _due(entry: dict, kind: str, ttl: timedelta, now: datetime) -> bool:
    fetched = _stamp(entry.get(f"{kind}_fetched_at"))
    return fetched is None or not timedelta(0) <= now - fetched < ttl


def _error(entry: dict, kind: str, exc: Exception, now: datetime) -> None:
    entry[f"{kind}_error"] = {"type": type(exc).__name__, "at": now.isoformat()}


async def _history_page(client: Any, league_id: str, uid: str, entry: dict, now: datetime):
    start = int(entry.get("history_cursor", 0))
    old = entry.get("transfers", [])
    if start == 0:
        entry["history_anchor_at"] = max((_event_key(r)[1] for r in old), default=None)
    raw = await client.get_manager_transfers(league_id, uid, start=start)
    rows = _manager_page(raw, uid)
    known = {_row_key(r) for r in old}
    anchor = entry.get("history_anchor_at")
    reached_anchor = (
        entry.get("history_complete", False)
        and anchor
        and any(_row_key(r) in known and _event_key(r)[1] <= anchor for r in rows)
    )
    entry["transfers"] = _merge_rows(old, rows)
    entry["name"] = str(raw.get("unm") or entry.get("name") or uid).strip()
    if not rows or reached_anchor:
        entry.update(history_cursor=0, history_complete=True, history_fetched_at=now.isoformat())
        if not rows:
            entry["history_exhausted_at"] = now.isoformat()
    elif rows and not any(_row_key(r) not in known for r in rows) and start > 0:
        raise ValueError("Historienseite wiederholt sich ohne Fortschritt")
    else:
        entry["history_cursor"] = start + TRANSFER_PAGE_SIZE
    entry.pop("history_error", None)


def _normalize(cache: dict, now: datetime, active_ids: list[str], errors: list[dict]) -> dict:
    managers = cache["managers"]
    names: dict[str, set[str]] = defaultdict(set)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    invalid = 0
    for uid, entry in managers.items():
        names[str(entry.get("name", "")).strip()].add(uid)
        for row in entry.get("transfers", []):
            try:
                _row_key(row)
                groups[_event_key(row)].append({**row, "manager_id": uid})
            except (ValueError, TypeError, AttributeError, OverflowError):
                invalid += 1
    overview = cache.get("overview") or {}
    reset = _stamp(overview.get("dt"))
    real_feed = cache.get("feed", [])
    real_by_key = {}
    for event in real_feed:
        if event.get("t") == 15:
            try:
                real_by_key[_event_key({**event.get("data", {}), "dt": event.get("dt")})] = event
            except (ValueError, TypeError, OverflowError):
                invalid += 1
    events, ambiguous = [], 0
    for key, rows in groups.items():
        buyers = [r for r in rows if r["tty"] == 1]
        sellers = [r for r in rows if r["tty"] == 2]
        if len(buyers) > 1 or len(sellers) > 1:
            ambiguous += 1
            continue
        # Genau eine Kauf- und Verkaufsseite desselben Ereignisses identifizieren
        # die Gegenparteien per UID. Historische Anzeigenamen dürfen nach einer
        # Umbenennung keine belegten Transfers aus der Auswertung entfernen.
        data = {k: rows[0][k] for k in ("pi", "pn", "tid", "pim") if k in rows[0]}
        data["trp"] = key[2]
        for role, side, other in (("byr", buyers, sellers), ("slr", sellers, buyers)):
            id_key = "buyer_id" if role == "byr" else "seller_id"
            if side:
                uid = side[0]["manager_id"]
                data[role] = str(managers[uid].get("name") or uid).strip()
                data[id_key] = uid
            elif other and other[0].get("othnm"):
                name = str(other[0]["othnm"]).strip()
                data[role] = name
                if len(names.get(name, set())) == 1:
                    data[id_key] = next(iter(names[name]))
        real = real_by_key.pop(key, None)
        synthetic = "archive:" + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:24]
        event = {
            "i": synthetic,
            "t": 15,
            "dt": rows[0]["dt"],
            "data": data,
            "source": "manager_transfer_history",
            "synthetic_id": True,
            "manager_view_count": len(rows),
        }
        if real:
            event.update(real)
            event.update(source="activity_feed+manager_transfer_history", synthetic_id=False)
            # Echte Feednamen/Preise bleiben führend, stabile IDs kommen aus den Managerseiten.
            event["data"] = {**data, **real["data"]}
        events.append(event)
    for event in real_by_key.values():
        data = dict(event["data"])
        for role, id_key in (("byr", "buyer_id"), ("slr", "seller_id")):
            match = names.get(str(data.get(role, "")).strip(), set())
            if len(match) == 1:
                data[id_key] = next(iter(match))
        events.append({**event, "data": data, "source": "activity_feed", "synthetic_id": False})
    # Ohne belegte Grenze bleiben Archivtransfers draußen. Der aktuelle Feed ist
    # separat als Quelle sichtbar; seine Vollständigkeit wird nicht unterstellt.
    current, historical = [], []
    for event in events:
        at = _stamp(event.get("dt"))
        if at is None:
            invalid += 1
        elif reset and at < reset:
            event["season_segment"] = "prior"
            historical.append(event)
        elif reset or not event["synthetic_id"]:
            event["season_segment"] = "current" if reset else "unverified"
            current.append(event)
    other_feed = []
    for row in real_feed:
        if row.get("t") == 15:
            continue
        when = _stamp(row.get("dt"))
        if reset is None or (when is not None and when >= reset):
            other_feed.append(row)
    manager_status = []
    for uid in active_ids:
        entry = managers.get(uid, {})
        manager_status.append(
            {
                "user_id": uid,
                "name": entry.get("name", uid),
                "history_complete": bool(entry.get("history_complete")),
                "history_cursor": entry.get("history_cursor", 0),
                "history_entries": len(entry.get("transfers", [])),
                "history_fetched_at": entry.get("history_fetched_at"),
                "history_exhausted_at": entry.get("history_exhausted_at"),
                "performance_fetched_at": entry.get("performance_fetched_at"),
                "history_stale": _due(entry, "history", HISTORY_TTL, now),
                "performance_stale": _due(entry, "performance", PERFORMANCE_TTL, now),
            }
        )
        for kind in ("history", "performance"):
            if entry.get(kind + "_error"):
                errors.append({"source": kind, "manager_id": uid, **entry[kind + "_error"]})
    for kind in ("overview",):
        if cache.get(kind + "_error"):
            errors.append({"source": kind, **cache[kind + "_error"]})
    if invalid:
        errors.append({"source": "archive", "type": "invalid_rows", "count": invalid})
    if ambiguous:
        errors.append({"source": "archive", "type": "ambiguous_transfers", "count": ambiguous})
    perf = {
        uid: entry["performance"]
        for uid, entry in managers.items()
        if uid in active_ids and isinstance(entry.get("performance"), dict)
    }
    return {
        "feed_events": sorted(current + other_feed, key=lambda r: r["dt"], reverse=True),
        "historical_feed_events": sorted(historical, key=lambda r: r["dt"], reverse=True),
        "manager_performance": perf,
        "overview": overview,
        "coverage": {
            "generated_at": now.isoformat(),
            "source": "manager_transfer_history",
            "reset_at": reset.isoformat() if reset else None,
            "reset_basis": "league_overview.dt",
            "reset_player_history_verified": bool(
                cache.get("seed", {}).get("reset_player_history_verified")
                and cache.get("seed", {}).get("reset_at") == overview.get("dt")
            ),
            "configured_start_budget_eur": overview.get("b"),
            "manager_count": len(active_ids),
            "histories_complete": sum(r["history_complete"] for r in manager_status),
            "history_entries": sum(len(v.get("transfers", [])) for v in managers.values()),
            "archived_transfers": len(events),
            "historical_transfers": len(historical),
            "current_transfers": len(current),
            "added_current_transfers": sum(r["synthetic_id"] for r in current),
            "manager_performance_count": len(perf),
            "ambiguous_transfers": ambiguous,
            "overview_fetched_at": cache.get("overview_fetched_at"),
            "managers": manager_status,
            "errors": errors,
            "limitations": [
                "Erreichbare Historie heutiger Manager; gelöschte Vorgänge bleiben unbekannt.",
                "Fremde Kontostände fehlen; Startbudget plus Transfers ist kein Ist-Konto.",
                "Alte Saisons sind separat; cur ist kein verlässlicher Saisonfilter.",
                "Resetgrenze aus Ligakonfiguration; Seed-Gegenprobe separat ausgewiesen.",
            ],
        },
    }


async def load_league_history(
    client: Any,
    league_id: str,
    manager_rows: list[dict],
    feed_events: list[dict],
) -> dict:
    """Aktuelle Ereignisse und getrenntes Altarchiv; höchstens acht zusätzliche GETs."""
    league_id = str(league_id)
    now = _now()
    path = _cache_path(league_id)
    cache, errors = _read_cache(path, league_id)
    cache["feed"] = _merge_feed(cache["feed"], feed_events)
    active_ids = []
    for row in manager_rows:
        uid = str(row.get("user_id") or row.get("manager_id") or "")
        if not uid or uid in active_ids:
            continue
        active_ids.append(uid)
        entry = cache["managers"].setdefault(uid, {"transfers": []})
        entry["name"] = str(row.get("name") or entry.get("name") or uid).strip()
    attempted = 0
    if _due(cache, "overview", OVERVIEW_TTL, now):
        cache["overview_attempted_at"] = now.isoformat()
        attempted += 1
        try:
            overview = await client.get_league_overview(league_id)
            if (
                not isinstance(overview, dict)
                or str(overview.get("i")) != league_id
                or _stamp(overview.get("dt")) is None
            ):
                raise ValueError("Ligakonfiguration ohne datierte Grenze")
            cache.update(overview=overview, overview_fetched_at=now.isoformat())
            cache.pop("overview_error", None)
        except Exception as exc:  # noqa: BLE001 — das vorhandene Archiv bleibt nutzbar
            _error(cache, "overview", exc, now)
    tasks = []
    epoch = datetime.min.replace(tzinfo=UTC)
    for uid in active_ids:
        entry = cache["managers"][uid]
        for kind, ttl in (("history", HISTORY_TTL), ("performance", PERFORMANCE_TTL)):
            incomplete = kind == "history" and (
                entry.get("history_cursor") or not entry.get("history_complete")
            )
            if _due(entry, kind, ttl, now) or incomplete:
                tasks.append(
                    (
                        _stamp(entry.get(kind + "_attempted_at"))
                        or _stamp(entry.get(kind + "_fetched_at"))
                        or epoch,
                        uid,
                        kind,
                    )
                )
    for _, uid, kind in sorted(tasks):
        if attempted >= MAX_REQUESTS:
            break
        entry = cache["managers"][uid]
        entry[kind + "_attempted_at"] = now.isoformat()
        attempted += 1
        try:
            if kind == "history":
                await _history_page(client, league_id, uid, entry, now)
            else:
                raw = await client.get_manager_performance(league_id, uid)
                entry["performance"] = _performance(raw, uid)
                entry["performance_fetched_at"] = now.isoformat()
                entry.pop("performance_error", None)
        except Exception as exc:  # noqa: BLE001 — einzelne Fehler dürfen andere nicht aushungern
            _error(entry, kind, exc, now)
    if not any(e.get("type") == "read_failed" for e in errors):
        try:
            _write_cache(path, cache)
        except (OSError, ValueError):
            errors.append({"source": "cache", "type": "write_failed"})
    result = _normalize(cache, now, active_ids, errors)
    result["coverage"].update(api_requests_attempted=attempted, max_requests_per_build=MAX_REQUESTS)
    return result


def seed_league_history_from_archive(
    archive_dir: Path | str, cache_path: Path | None = None
) -> dict:
    """Geprüfte Originalantworten importieren; bestehende neuere Einträge bleiben erhalten.

    archive_dir ist der Datenordner mit manager-history-manifest.json,
    manager-transfer-entries-all.json und league-overview.json. Kein API-Aufruf.
    """
    root = Path(archive_dir)
    manifest = json.loads((root / "manager-history-manifest.json").read_text())
    overview = json.loads((root / "league-overview.json").read_text())
    league_id = str(overview["i"])
    if _stamp(overview.get("dt")) is None:
        raise ValueError("Seed ohne Resetgrenze")
    captured = manifest["captured_at"]
    captured_at = _stamp(captured)
    if captured_at is None:
        raise ValueError("Seed ohne Abrufdatum")
    path = Path(cache_path) if cache_path else _cache_path(league_id)
    cache, errors = _read_cache(path, league_id)
    if errors:
        raise ValueError("Vorhandenen ungültigen Cache nicht still überschreiben")
    overview_at = _stamp(cache.get("overview_fetched_at"))
    if overview_at is None or overview_at <= captured_at:
        cache.update(overview=overview, overview_fetched_at=captured)
    raw = json.loads((root / "manager-transfer-entries-all.json").read_text())
    by_manager: dict[str, list[dict]] = defaultdict(list)
    for row in raw:
        _row_key(row)
        by_manager[str(row["manager_id"])].append(row)
    for uid, info in manifest["managers"].items():
        if len({_row_key(r) for r in by_manager[uid]}) != info["count"]:
            raise ValueError("Seed-Manifest und tatsächliche Managerzeilen stimmen nicht überein")
        entry = cache["managers"].setdefault(uid, {"transfers": []})
        # Ein später erneut importierter Seed darf verifizierte Live-Metadaten
        # derselben Buchung nicht durch den älteren Rohstand ersetzen.
        entry["transfers"] = _merge_rows(by_manager[uid], entry["transfers"])
        entry.setdefault("name", info["name"].strip())
        history_at = _stamp(entry.get("history_fetched_at"))
        if history_at is None or history_at <= captured_at:
            entry.update(
                history_complete=info.get("stop") == "empty_page",
                history_cursor=0,
                history_fetched_at=captured,
                history_exhausted_at=captured if info.get("stop") == "empty_page" else None,
            )
    feed_path = root / "feed-all.json"
    if feed_path.exists():
        cache["feed"] = _merge_feed(json.loads(feed_path.read_text()), cache["feed"])
    for source in sorted(root.glob("*-performance.json")):
        payload = json.loads(source.read_text())
        uid = str(payload.get("u", ""))
        if uid not in manifest["managers"]:
            continue
        entry = cache["managers"][uid]
        if not entry.get("performance_fetched_at"):
            entry.update(performance=_performance(payload, uid), performance_fetched_at=captured)
    cache["seed"] = {
        "captured_at": captured,
        "source": "authenticated_archive",
        "reset_at": overview["dt"],
        "reset_player_history_verified": any(
            r.get("t") == 4 and r.get("trp") == 0 and r.get("dt") == overview["dt"]
            for r in json.loads((root / "player-transfer-first.json").read_text())["it"]
        )
        if (root / "player-transfer-first.json").exists()
        else False,
    }
    _write_cache(path, cache)
    return _normalize(cache, _now(), list(manifest["managers"]), [])
