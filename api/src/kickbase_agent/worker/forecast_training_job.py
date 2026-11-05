"""Wöchentliches DB-Kandidatentraining; keine Aktivierung und kein Kickbase-Zugriff."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Player, PlayerHistory, PlayerPerformance, Report
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.intel.forecasting.dataset import utc
from kickbase_agent.intel.forecasting.service import DEFAULT_REGISTRY_DIR
from kickbase_agent.intel.forecasting.training import canonical, train_candidate

LIMITS = [
    "DB-Performance enthält den zuletzt gespeicherten, möglicherweise korrigierten Stand.",
    "captured_at bleibt der vorhandene Abrufzeitpunkt; der Export erfindet keinen früheren.",
    "Frühere immutable Wochen-Seeds liefern damalige Eingaben; heutige Werte nur spätere Labels.",
    "engine_decision speichert begrenzte datierte Eingaben, keine vollständige Spielerhistorie. "
    "Diese Snapshots werden hier noch nicht als historischer Veröffentlichungsnachweis verbunden.",
    "PlayerHistory enthält auch Rückfüllungen ohne Abrufzeit; nur MW-Kontext, keine Startlabels.",
    "Keine trainierte Start-/Einsatzwahrscheinlichkeit; MW-Tagesmodelle werden separat geprüft.",
]
_training_lock = asyncio.Lock()


def _db_utc(value: datetime | None) -> datetime | None:
    # SQLite verliert Zeitzonen dieser bereits als UTC gespeicherten DB-Spalten.
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def export_seed(
    session: Session, *, as_of: datetime, registry_dir: str | Path,
) -> tuple[Path, dict[str, int]]:
    """Datierter, inhaltsversionierter Seed; unbekannte Feldpräsenz bleibt unbekannt."""
    stamp = utc(as_of)
    if stamp is None:
        raise ValueError("Trainingsstichtag benötigt eine Zeitzone.")
    players: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    for player in session.scalars(select(Player).order_by(Player.id)):
        players[player.id] = {
            "name": player.name, "position": player.position, "team_id": player.team,
            "metadata_scope": "current_only_not_historical_features",
            "performance": [], "market_history": [],
        }
    for row in session.scalars(select(PlayerPerformance).order_by(
        PlayerPerformance.player_id, PlayerPerformance.season, PlayerPerformance.matchday,
    )).yield_per(1000):
        capture, at = _db_utc(row.captured_at), _db_utc(row.match_date)
        if capture is not None and capture > stamp:
            counts["future_capture_excluded"] += 1
            continue
        if at is not None and at >= stamp:
            counts["future_match_excluded"] += 1
            continue
        entry = players.setdefault(row.player_id, {"performance": [], "market_history": []})
        entry["performance"].append({
            "player_id": row.player_id, "season": row.season, "matchday": row.matchday,
            "points": row.points, "minutes": row.minutes,
            "match_date": at.isoformat() if at else None,
            "captured_at": capture.isoformat() if capture else None,
            "points_observed": row.points_observed, "minutes_observed": row.minutes_observed,
            "is_finished": row.is_finished,
        })
        counts["performance_rows"] += 1
    for history in session.scalars(select(PlayerHistory).where(
        PlayerHistory.recorded_at >= stamp - timedelta(days=370),
        PlayerHistory.recorded_at <= stamp,
    ).order_by(PlayerHistory.player_id, PlayerHistory.recorded_at)).yield_per(1000):
        at = _db_utc(history.recorded_at)
        if at is None or at > stamp:
            continue
        entry = players.setdefault(history.player_id, {"performance": [], "market_history": []})
        # Historische points=0/is_starter=False waren neutrale DB-Defaults.
        entry["market_history"].append({
            "recorded_at": at.isoformat(), "market_value": history.market_value,
            "publication_observed_at": None,
        })
        counts["market_history_rows"] += 1
    payload = {
        "schema_version": 1, "exported_at": stamp.isoformat(), "players": players,
        "provenance": {
            "source": "local_database", "historical_publication_proven": False,
            "market_history_days": 370, "limitations": LIMITS,
        },
        "export_audit": dict(counts),
    }
    content = canonical(payload) + b"\n"
    version = hashlib.sha256(content).hexdigest()[:20]
    path = Path(registry_dir) / "seeds" / f"db-seed-{stamp:%Y%m%dT%H%M%S}-{version}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Identischer Wiederholungsaufruf darf denselben Datenstand wiederverwenden.
    try:
        with path.open("xb") as handle:
            handle.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise ValueError("Versionierter Seed stimmt nicht mit seinem Inhalt überein.") from None
    return path.resolve(), dict(counts)


def train_from_database(
    session: Session, *, as_of: datetime, registry_dir: str | Path = DEFAULT_REGISTRY_DIR,
) -> dict[str, Any]:
    """Nur Kandidat + Prüfbericht; vorhandene aktive Referenzen werden nie berührt."""
    result: dict[str, Any] = {
        "status": "error", "activated": False, "as_of": as_of.isoformat(),
        "limitations": LIMITS, "registry_dir": str(Path(registry_dir).resolve()),
    }
    try:
        seed, audit = export_seed(session, as_of=as_of, registry_dir=registry_dir)
        result.update(seed_path=str(seed), export_audit=audit)
        prior_seeds = sorted((Path(registry_dir) / "seeds").glob("db-seed-*.json"))
        artifact = train_candidate(seed, registry_dir=registry_dir, as_of=as_of,
                                   historical_seeds=prior_seeds)
        version = artifact["model_version"]
        directory = Path(registry_dir).resolve() / "candidates"
        result.update(
            status="candidate", model_version=version, audit=artifact["audit"],
            split=artifact["split"], evaluation=artifact["evaluation"],
            promotion_gate=artifact["evaluation"]["promotion_gate"],
            prospective_coverage=artifact["prospective_coverage"],
            candidate_path=str(directory / f"{version}.json"),
            holdout_path=str(directory / f"{version}-holdout.jsonl"),
        )
    except Exception as exc:
        session.rollback()
        insufficient = isinstance(exc, ValueError) and any(
            phrase in str(exc) for phrase in ("Zeitgruppen", "Finalitätsabstand")
        )
        result.update(status="skipped" if insufficient else "error", error_type=type(exc).__name__)
        result["reason"] = (
            "Zu wenige abgeschlossene Zeitgruppen; kein Kandidat trainiert."
            if insufficient
            else "Kandidatentraining fehlgeschlagen; aktives Modell bleibt bestehen."
        )
    body = (
        f"Forecast-Training: {result['status']}. Keine Modellaktivierung.\n"
        + "\n".join(LIMITS) + "\n\n"
        + json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    )
    session.add(Report(
        created_at=as_of, kind="model_evaluation", source="worker:forecast_training",
        title="Wöchentlicher Modellvergleich: Punkte und Minuten", body=body, meta=result,
    ))
    session.commit()
    return result


def _run(as_of: datetime, registry_dir: str | Path) -> dict[str, Any]:
    with SessionLocal() as session:
        return train_from_database(session, as_of=as_of, registry_dir=registry_dir)


async def run_forecast_training_job(
    *, now: datetime | None = None, registry_dir: str | Path = DEFAULT_REGISTRY_DIR,
) -> dict[str, Any]:
    """Scheduler-Einstieg; numerisches Training blockiert den Executor-Eventloop nicht."""
    if _training_lock.locked():
        return {"status": "already_running", "activated": False}
    async with _training_lock:
        return await asyncio.to_thread(_run, now or datetime.now(UTC), registry_dir)
