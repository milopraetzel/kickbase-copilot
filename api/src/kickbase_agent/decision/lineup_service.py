"""Aufstellungs-Empfehlung: eigenen Kader scouten und die punktbeste Elf bestimmen.

Mit Leistungsdaten gelten dieselben bedingten Punkteprognosen wie im Kaufplan;
LigaInsider-Ausfall/Bank erhalten eine konservative Nullbasis. Ohne Leistungsdaten
bleibt der dokumentierte Index-Fallback. Briefing und Aufstellungs-Job nutzen
dieselbe Empfehlung.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from kickbase_agent.decision.lineup import (
    _FORMATIONS,
    Lineup,
    best_lineup,
    best_partial_lineup,
)
from kickbase_agent.intel.collector import collect_candidates
from kickbase_agent.intel.forecasting.service import forecast_player, load_active
from kickbase_agent.intel.history import load_history, load_performance
from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.kickbase.client import KickbaseClient
from kickbase_agent.kickbase.models import SquadPlayer
from kickbase_agent.scouts.ligainsider.models import Status
from kickbase_agent.scouts.ligainsider.service import ScoutResult, enrich_candidates


@dataclass
class LineupRecommendation:
    lineup: Lineup
    scout: ScoutResult   # Verfügbarkeit + Quarantäne für Briefing/Ping
    forecasts: dict[str, dict] = field(default_factory=dict)
    points_basis: str = "legacy_index"


def _forecast_lineup(candidates: list[ExplosionCandidate], forecasts: dict[str, dict]) -> Lineup:
    """Ohne Käufe/Kosten genügt ein exakter Vergleich aller neun Formationen."""
    own = [c for c in candidates if c.player_id in forecasts]
    by_position = {
        pos: sorted(
            (c for c in own if c.position == pos),
            key=lambda c: (
                -forecasts[c.player_id]["expected_points"],
                -getattr(c, "lineup_score", 0.0),
                -getattr(c, "market_value", 0),
                c.player_id,
            ),
        )
        for pos in (1, 2, 3, 4)
    }
    best = None
    for formation in _FORMATIONS:
        needs = dict(enumerate((1, *formation), start=1))
        if any(len(by_position[pos]) < need for pos, need in needs.items()):
            continue
        starters = [c for pos, need in needs.items() for c in by_position[pos][:need]]
        score = sum(forecasts[c.player_id]["expected_points"] for c in starters)
        if best is None or score > best.expected_score:
            ids = {c.player_id for c in starters}
            best = Lineup("-".join(map(str, formation)), starters,
                          [c for c in own if c.player_id not in ids], score)
    # Unvollständige Kader ergeben hier keine Elf; `recommend_lineup` setzt dann
    # die Teil-Elf. Vorhandene Prognosen werden nie durch den alten Index ersetzt.
    return best or Lineup("", [], own, 0.0)


async def recommend_lineup(
    client: KickbaseClient, session: Session, squad: list[SquadPlayer]
) -> LineupRecommendation:
    """Scoutet den Kader, wendet das LI-Veto an und liefert die beste erlaubte Startelf."""
    player_ids = [sp.player.id for sp in squad]
    fixtures = await client.get_matchdays()
    team_names = await client.get_team_names()
    history = load_history(session, player_ids)
    perf = load_performance(session, player_ids)
    candidates = await collect_candidates(squad, fixtures, team_names, history, perf)
    scout = await enrich_candidates(candidates)
    now = datetime.now(UTC)
    artifact = load_active()
    forecasts = {sp.player.id: forecast_player(
        sp.player, perf.get(sp.player.id, []), as_of=now, artifact=artifact,
    ) for sp in squad}
    # Aktuelle Rollenmeldung hat Vorrang vor historischen Einsatzpunkten.
    statuses = {a.player_id: a.status for a in scout.availabilities}
    for pid, forecast in forecasts.items():
        if statuses.get(pid) in {Status.OUT, Status.BENCH}:
            forecast["expected_points"] = 0.0
            forecast["horizon_points"] = 0.0
            forecast["expected_minutes"] = 0.0
            forecast["availability_adjustment"] = {
                "factor": 0.0, "basis": "conservative_ligainsider_role_heuristic",
            }
            forecast["reasons"].append("Aktueller LigaInsider-Status: konservativ null eingeplant.")
    if not any(f["forecast_available"] for f in forecasts.values()):
        lineup = best_lineup(scout.candidates)
        if not lineup.starters:
            lineup = best_partial_lineup(scout.candidates)
        return LineupRecommendation(lineup, scout, forecasts)
    lineup = _forecast_lineup(scout.candidates, forecasts)
    if not lineup.starters:
        # Teil-Elf (L-109): ALLE unterbringbaren Spieler zählen, auch ohne Prognose —
        # ein aufgestellter Spieler ohne Einsatz kostet null, ein leerer Platz 100.
        lineup = best_partial_lineup(
            scout.candidates,
            lambda c: float((forecasts.get(c.player_id) or {}).get("expected_points") or 0.0),
        )
    return LineupRecommendation(lineup, scout, forecasts, "conditional_points_with_role_heuristic")
