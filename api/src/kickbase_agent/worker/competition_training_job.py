"""Wöchentliche Gebotskandidaten aus bestätigten DB-Ergebnissen; keine Aktivierung."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Report, TransferAction
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.trading.competition import (
    DEFAULT_REGISTRY_DIR,
    _atomic,
    _canonical,
    observations_from_actions,
    train_competition,
)

_training_lock = asyncio.Lock()


def train_from_database(
    session: Session,
    *,
    as_of: datetime,
    registry_dir: str | Path = DEFAULT_REGISTRY_DIR,
) -> dict:
    """Pro Liga/Listingart nur datierte Eigenbelege; Kandidaten bleiben unverändert."""
    if as_of.tzinfo is None:
        raise ValueError("Trainingsstichtag benötigt Zeitzone.")
    as_of = as_of.astimezone(UTC)
    result: dict = {
        "status": "skipped",
        "activated": False,
        "as_of": as_of.isoformat(),
        "candidates": [],
        "errors": [],
        "reason": "Keine bestätigten Eigenlabels.",
    }
    leagues = session.scalars(
        select(TransferAction.league_id)
        .where(
            TransferAction.action_type == "bid",
        )
        .distinct()
    ).all()
    for league_id in sorted(lg for lg in leagues if isinstance(lg, str) and lg.isdigit()):
        actions = session.scalars(
            select(TransferAction).where(
                TransferAction.league_id == league_id,
                TransferAction.action_type == "bid",
            )
        ).all()
        observations = observations_from_actions(actions, as_of=as_of)
        for listing_type in ("kickbase", "manager"):
            rows = [row for row in observations if row.listing_type == listing_type]
            if not rows:
                continue
            try:
                artifact = train_competition(
                    rows, league_id=league_id, listing_type=listing_type, as_of=as_of
                )
                path = (
                    Path(registry_dir)
                    / "candidates"
                    / f"{league_id}-{listing_type}"
                    / f"{artifact['version']}-{as_of:%Y%m%dT%H%M%S}.json"
                )
                if path.exists():
                    if _canonical(json.loads(path.read_text())) != _canonical(artifact):
                        raise ValueError("Versionierter Kandidat hat bereits anderen Inhalt.")
                else:
                    _atomic(path, artifact)
                result["candidates"].append(
                    {
                        "league_id": league_id,
                        "listing_type": listing_type,
                        "version": artifact["version"],
                        "rows": artifact["rows"],
                        "eligible_for_promotion": artifact["eligible_for_promotion"],
                        "reason": artifact["reason"],
                        "candidate_path": str(path.resolve()),
                    }
                )
            except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
                result["errors"].append(
                    {
                        "league_id": league_id,
                        "listing_type": listing_type,
                        "error_type": type(exc).__name__,
                    }
                )
    if result["errors"]:
        result.update(
            status="error", reason="Einzelne Kandidaten fehlgeschlagen; keine Aktivierung."
        )
    elif result["candidates"]:
        result.update(status="candidate", reason="Lokale Kandidaten; Freigabe bleibt ausdrücklich.")
    session.add(
        Report(
            created_at=as_of,
            kind="model_evaluation",
            source="worker:competition_training",
            title="Wöchentlicher Modellvergleich: Gebote und Zuschläge",
            body=json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
            meta=result,
        )
    )
    session.commit()
    return result


def _run(as_of: datetime, registry_dir: str | Path) -> dict:
    with SessionLocal() as session:
        return train_from_database(session, as_of=as_of, registry_dir=registry_dir)


async def run_competition_training_job(
    *,
    now: datetime | None = None,
    registry_dir: str | Path = DEFAULT_REGISTRY_DIR,
) -> dict:
    """Numerisches Training mit eigener Thread-Session; der Executor läuft weiter."""
    if _training_lock.locked():
        return {"status": "already_running", "activated": False}
    async with _training_lock:
        return await asyncio.to_thread(_run, now or datetime.now(UTC), registry_dir)
