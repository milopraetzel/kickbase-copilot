"""Verfügbarkeit auflösen: Kickbase-Spieler → Status (+ Multiplikator) + Quarantäne.

Sicherheitsnetz (Emils Regel 3): Der Hard-Veto (Nullen) greift NUR bei sehr sicherem
Namens-Match (≥ CONFIDENT). Ein Grenzfall-Ausfall (QUARANTINE_FLOOR..CONFIDENT) nullt
NICHT, sondern erzeugt einen Quarantäne-Ping. Fehlen LI-Daten fürs Team → UNKNOWN
(×1.0, kein Veto). Team-verankert: gematcht wird immer nur gegen den Kader DESSELBEN
Teams (~25 Namen statt ~500).
"""

from __future__ import annotations

from collections.abc import Mapping

from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.scouts.ligainsider.matcher import best_match
from kickbase_agent.scouts.ligainsider.models import (
    MULTIPLIER,
    Availability,
    InjuryEntry,
    LineupEntry,
    Quarantine,
    Status,
)
from kickbase_agent.scouts.ligainsider.severity import Severity, injury_severity

CONFIDENT = 0.90        # ab hier Status scharf (nullen/faktorisieren)
QUARANTINE_FLOOR = 0.72  # Ausfall-Match in [FLOOR, CONFIDENT) → Ping statt Veto


def _by_team(entries: list) -> dict[str, list]:
    out: dict[str, list] = {}
    for e in entries:
        out.setdefault(e.team_tid, []).append(e)
    return out


def _avail(pid: str, status: Status, reason: str, conf: float) -> Availability:
    return Availability(pid, status, MULTIPLIER[status], reason, conf)


def resolve_availability(
    kb_players: list[tuple[str, str, str]],   # (player_id, name, team_tid)
    lineups: list[LineupEntry],
    injuries: list[InjuryEntry],
    scraped_tids: set[str],
) -> tuple[list[Availability], list[Quarantine]]:
    """Pro Kickbase-Spieler eine Verfügbarkeit; Grenzfälle in die Quarantäne."""
    inj_by_team = _by_team(injuries)
    xi_by_team = _by_team(lineups)

    avails: list[Availability] = []
    quarantines: list[Quarantine] = []

    for pid, name, tid in kb_players:
        # 1) Ausfall? (team-verankert)
        inj = inj_by_team.get(tid, [])
        idx, conf = best_match(name, [e.player_name for e in inj]) if inj else (None, 0.0)
        if idx is not None and conf >= CONFIDENT:
            avails.append(_avail(pid, Status.OUT, f"LI: {inj[idx].reason}", conf))
            continue
        if idx is not None and conf >= QUARANTINE_FLOOR:
            quarantines.append(
                Quarantine(pid, name, inj[idx].player_name, inj[idx].reason, conf)
            )
            # KEIN Veto — Spieler läuft als fit weiter.

        # 2) In der voraussichtlichen Startelf?
        xi = xi_by_team.get(tid, [])
        if tid not in scraped_tids or not xi:
            avails.append(_avail(pid, Status.UNKNOWN, "keine LI-Startelf", 0.0))
            continue
        jdx, xconf = best_match(name, [e.player_name for e in xi])
        if jdx is not None and xconf >= CONFIDENT:
            avails.append(_avail(pid, Status.STARTER, "LI: Startelf", xconf))
        elif jdx is not None and xconf >= QUARANTINE_FLOOR:
            # Review-Fund: Grenzfall-Match [0.72, 0.90) fiel vorher STILL auf die
            # Bank (×0.4) — ein Stammspieler mit Sonderzeichen-Namen flog damit
            # unbemerkt aus der Elf. Jetzt: kein Malus (×1.0) + Quarantäne-Ping.
            quarantines.append(
                Quarantine(pid, name, xi[jdx].player_name, "Startelf-Match unsicher", xconf)
            )
            avails.append(_avail(pid, Status.UNKNOWN, "LI: Startelf-Match unsicher", xconf))
        else:
            avails.append(_avail(pid, Status.BENCH, "LI: kein Stammplatz", xconf))

    return avails, quarantines


def apply_to_candidates(
    candidates: list[ExplosionCandidate],
    avails: list[Availability],
    severities: Mapping[str, Severity] | None = None,
) -> list[ExplosionCandidate]:
    """Multipliziert BEIDE Scores (total + lineup) mit dem LI-Verfügbarkeits-Faktor.

    Trifft xPoints (Aufstellung + Kauf/Verkauf-Punktwert), nicht das Trading-Momentum
    (separate Pipeline). OUT → 0 → die Aufstellung ignoriert den Spieler; zusätzlich
    werden Ausfallgrund + Schwere (long/short/unknown) am Kandidaten hinterlegt, damit
    der Verkaufspfad Kurzzeit-Ausfälle HALTEN kann statt panisch zu verkaufen.
    `severities` (Grund → Schwere) kommt aus dem TypeSafe-Urteil
    (`severity_judge.judge_severities`); fehlt ein Grund darin, gilt die Wortsuche.
    """
    by_id = {a.player_id: a for a in avails}
    out: list[ExplosionCandidate] = []
    for c in candidates:
        a = by_id.get(c.player_id)
        if a is None or (a.multiplier == 1.0 and a.status is not Status.OUT):
            out.append(c)
            continue
        update: dict[str, object] = {
            "total_score": round(c.total_score * a.multiplier, 4),
            "lineup_score": round(c.lineup_score * a.multiplier, 4),
        }
        if a.status is Status.OUT:
            update["injury_reason"] = a.reason
            judged = (severities or {}).get(a.reason)
            update["injury_severity"] = (judged or injury_severity(a.reason)).value
        out.append(c.model_copy(update=update))
    return out

