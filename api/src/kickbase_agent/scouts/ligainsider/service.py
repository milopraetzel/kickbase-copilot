"""Scout-Service: Kandidaten mit LigaInsider-Verfügbarkeit anreichern.

Ein Aufruf pro Kandidatenliste; der Snapshot wird via TTL-Cache liga-weit einmal
geholt und von allen Aufrufern eines Laufs geteilt. Bei ausgeschaltetem Scout oder
Totalausfall bleiben die Kandidaten unverändert (kein Veto — fail-safe).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kickbase_agent.config import get_settings
from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.scouts.ligainsider.availability import (
    apply_to_candidates,
    resolve_availability,
)
from kickbase_agent.scouts.ligainsider.models import Availability, Quarantine, Status
from kickbase_agent.scouts.ligainsider.scraper import ScoutSnapshot, fetch_snapshot
from kickbase_agent.scouts.ligainsider.severity_judge import judge_severities


@dataclass
class ScoutResult:
    candidates: list[ExplosionCandidate]              # xPoints-angepasst
    availabilities: list[Availability] = field(default_factory=list)
    quarantines: list[Quarantine] = field(default_factory=list)
    snapshot: ScoutSnapshot | None = None


async def enrich_candidates(
    candidates: list[ExplosionCandidate], *, purpose: str = "ausfall"
) -> ScoutResult:
    """Reichert Kandidaten mit LI-Verfügbarkeit an (Veto/Startelf-Faktor auf xPoints).

    `purpose` (`kader` · `kaufpruefung`) benennt im Jev-Protokoll, wessen Ausfallgrund
    beurteilt wurde — nur eigene Spieler kann ein Monate-Urteil zum Verkauf führen."""
    if not get_settings().ligainsider_enabled or not candidates:
        return ScoutResult(candidates)
    snap = await fetch_snapshot()
    kb = [(c.player_id, c.name, c.team_id) for c in candidates]
    avails, quarantines = resolve_availability(
        kb, snap.lineups, snap.injuries, snap.scraped_tids
    )
    out = [a for a in avails if a.status is Status.OUT]
    names = {c.player_id: c.name for c in candidates}
    subjects: dict[str, list[tuple[str, str]]] = {}
    for a in out:  # nur fürs Jev-Protokoll: wen ein Urteil betrifft
        subjects.setdefault(a.reason, []).append((a.player_id, names.get(a.player_id, "")))
    severities = await judge_severities((a.reason for a in out), subjects=subjects,
                                        purpose=purpose)
    adjusted = apply_to_candidates(candidates, avails, severities)
    return ScoutResult(adjusted, avails, quarantines, snap)
