"""Profiteur-Scout: wer profitiert davon, dass ein Teamkollege ausfällt?

Emils Frage „wer könnte einen guten Tag haben, weil ein anderer nicht spielt" —
proaktiv beantwortet: Für jeden LigaInsider-Ausfall werden Markt-Spieler desselben
Teams gesucht, die in der voraussichtlichen Startelf stehen. Wenn sich die Position
des Ausfalls auflösen lässt (Name ↔ Kickbase-Pool), werden Positions-Kollegen
bevorzugt — der direkte Job-Erbe ist der wertvollste Kauf, BEVOR sein MW explodiert.
"""

from __future__ import annotations

from dataclasses import dataclass

from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.scouts.ligainsider.availability import CONFIDENT
from kickbase_agent.scouts.ligainsider.matcher import similarity
from kickbase_agent.scouts.ligainsider.models import Availability, InjuryEntry, Status


@dataclass(frozen=True)
class Profiteur:
    player_id: str
    name: str
    team_tid: str
    market_value: int
    total_score: float
    injured: str          # z. B. "Emre Can (Kreuzbandriss)"
    same_position: bool   # Positions-Kollege des Ausfalls → direkter Job-Erbe


def _injured_position(injury: InjuryEntry, pool: list[ExplosionCandidate]) -> int | None:
    """Position des Ausfalls über den Kickbase-Pool auflösen (team-verankert, fuzzy)."""
    best: tuple[float, int] | None = None
    for c in pool:
        if c.team_id != injury.team_tid:
            continue
        s = similarity(c.name, injury.player_name)
        if s >= CONFIDENT and (best is None or s > best[0]):
            best = (s, c.position)
    return best[1] if best else None


def find_profiteurs(
    market_candidates: list[ExplosionCandidate],
    pool_candidates: list[ExplosionCandidate],
    avails: list[Availability],
    injuries: list[InjuryEntry],
    limit: int = 5,
) -> list[Profiteur]:
    """Markt-Spieler, die in der Startelf stehen, während ein Teamkollege ausfällt."""
    status = {a.player_id: a.status for a in avails}
    injured_teams: dict[str, list[InjuryEntry]] = {}
    for inj in injuries:
        injured_teams.setdefault(inj.team_tid, []).append(inj)

    out: list[Profiteur] = []
    for c in market_candidates:
        if status.get(c.player_id) is not Status.STARTER:
            continue  # nur bestätigte Starter profitieren
        team_injuries = injured_teams.get(c.team_id, [])
        if not team_injuries:
            continue
        # Bester Bezug: Ausfall auf derselben Position (direkter Job-Erbe).
        best_inj, same_pos = team_injuries[0], False
        for inj in team_injuries:
            pos = _injured_position(inj, pool_candidates)
            if pos is not None and pos == c.position:
                best_inj, same_pos = inj, True
                break
        out.append(
            Profiteur(
                player_id=c.player_id,
                name=c.name,
                team_tid=c.team_id,
                market_value=c.current_market_value,
                total_score=c.total_score,
                injured=f"{best_inj.player_name} ({best_inj.reason})",
                same_position=same_pos,
            )
        )

    # Direkter Job-Erbe zuerst, dann nach Score.
    out.sort(key=lambda p: (not p.same_position, -p.total_score))
    return out[:limit]


def profiteur_lines(profiteurs: list[Profiteur]) -> list[str]:
    """Kompakte Textzeilen (fürs KI-Recherche-Prompt)."""
    return [
        f"{p.name} (MW {p.market_value / 1_000_000:.1f}M, Score {p.total_score:.2f}) — "
        f"{'Positions-Erbe von' if p.same_position else 'Teamkollege fällt aus:'} {p.injured}"
        for p in profiteurs
    ]
