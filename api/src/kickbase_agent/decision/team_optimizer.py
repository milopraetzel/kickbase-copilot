"""Gemeinsam finanzierte Mannschaft über alle Formationen; keine I/O oder Trades."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from time import monotonic

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from kickbase_agent.decision.lineup import _FORMATIONS


@dataclass(frozen=True)
class PlayerOption:
    player_id: str
    position: int
    team_id: str
    horizon_points: float
    cost_eur: int = 0
    owned: bool = False
    committed: bool = False
    name: str = ""


@dataclass(frozen=True)
class TeamPlan:
    formation: str
    filled_slots: int
    horizon_points: float
    cost_eur: int
    remaining_cash: int
    purchase_ids: tuple[str, ...] = ()
    lineup_ids: tuple[str, ...] = ()
    owned_filled_slots: int = 0
    solver_status: str = "optimal"
    optimal: bool = True
    notes: tuple[str, ...] = ()
    missing_positions: dict[int, int] = field(default_factory=dict)


def _owned_slots(options: list[PlayerOption]) -> int:
    counts = {pos: sum(o.owned and o.position == pos for o in options) for pos in (1, 2, 3, 4)}
    return max(
        sum(min(counts[p], need) for p, need in enumerate((1, *f), start=1)) for f in _FORMATIONS
    )


def optimize_team(
    options: list[PlayerOption],
    *,
    cash_available: int,
    club_counts: dict[str, int],
    max_per_club: int,
    max_new_purchases: int | None = None,
    required_ids: frozenset[str] = frozenset(),
    excluded_ids: frozenset[str] = frozenset(),
    time_limit: float = 2.0,
) -> TeamPlan:
    """Lexikografisch Plätze, Vier-Spieltags-Punkte, verbleibendes Geld optimieren.

    cash_available ist bereits um sämtliche offenen Verpflichtungen vermindert.
    club_counts zählt tatsächlichen Kader und dieselben Verpflichtungen. committed
    zählt nur im bedingten Zuschlagsszenario; owned_filled_slots nennt separat den
    tatsächlichen Besitz. Verkaufserlöse kennt dieser Vertrag ausdrücklich nicht.
    required_ids erzwingt einen Kauf, aber keinen Startplatz für einen schwachen
    Spieler: so lässt sich dessen echter Nutzen bei verschiedenen Preisen prüfen.
    """
    ids = [o.player_id for o in options]
    if len(ids) != len(set(ids)) or any(
        not o.player_id
        or o.position not in (1, 2, 3, 4)
        or not isfinite(o.horizon_points)
        or type(o.cost_eur) is not int
        or o.cost_eur < 0
        or (o.owned and o.committed)
        for o in options
    ):
        raise ValueError("Spieleroptionen brauchen eindeutige IDs und gültige Kosten/Prognosen.")
    available = [o for o in options if o.player_id not in excluded_ids]
    owned_slots = _owned_slots(available)

    def failure(status: str, reason: str) -> TeamPlan:
        return TeamPlan(
            "",
            owned_slots,
            0.0,
            0,
            cash_available,
            owned_filled_slots=owned_slots,
            solver_status=status,
            optimal=False,
            notes=(reason,),
        )

    if cash_available < 0:
        return failure("infeasible", f"Nach Verpflichtungen fehlen {-cash_available:,} € Deckung.")
    if not required_ids <= {o.player_id for o in available}:
        return failure("infeasible", "Erzwungener Kauf ist nicht verfügbar.")
    if max_per_club < 1 or time_limit <= 0:
        return failure("invalid_input", "Vereinslimit oder Rechenzeit ungültig.")
    n = len(available)
    width = 2 * n + len(_FORMATIONS)
    rows: list[np.ndarray] = []
    lowers: list[float] = []
    uppers: list[float] = []
    lower = np.zeros(width)
    upper = np.ones(width)

    def constraint(coefficients: dict[int, float], low: float, high: float) -> None:
        vector = np.zeros(width)
        for index, value in coefficients.items():
            vector[index] = value
        rows.append(vector)
        lowers.append(low)
        uppers.append(high)

    for i, option in enumerate(available):
        if option.owned or option.committed:
            upper[n + i] = 0  # bereits besessen/reserviert, kein erneuter Kauf
        else:
            constraint({i: 1, n + i: -1}, -np.inf, 0)
            if option.player_id in required_ids:
                lower[n + i] = 1
    constraint({2 * n + f: 1 for f in range(len(_FORMATIONS))}, 1, 1)
    for position in (1, 2, 3, 4):
        coefficients: dict[int, float] = {
            i: 1 for i, o in enumerate(available) if o.position == position
        }
        coefficients.update(
            {2 * n + f: -(1, *formation)[position - 1] for f, formation in enumerate(_FORMATIONS)}
        )
        constraint(coefficients, -np.inf, 0)
    # Millionen-Skalierung stabilisiert den Solver; die Lösung wird anschließend
    # nochmals mit exakten Eurobeträgen und ganzzahligen Besitzentscheidungen geprüft.
    scale = 1_000_000
    constraint(
        {n + i: o.cost_eur / scale for i, o in enumerate(available)},
        -np.inf,
        cash_available / scale,
    )
    for club in {o.team_id for o in available if not o.owned and not o.committed}:
        capacity = max(0, max_per_club - club_counts.get(club, 0))
        constraint(
            {n + i: 1 for i, o in enumerate(available) if o.team_id == club}, -np.inf, capacity
        )
    if max_new_purchases is not None:
        constraint({n + i: 1 for i in range(n)}, -np.inf, max(0, max_new_purchases))
    objectives = [np.zeros(width) for _ in range(3)]
    objectives[0][:n] = -1
    objectives[1][:n] = [-o.horizon_points for o in available]
    objectives[2][n : 2 * n] = [o.cost_eur / scale for o in available]
    started = monotonic()
    solution = None
    optimal = True
    for stage, objective in enumerate(objectives):
        remaining = time_limit - (monotonic() - started)
        if remaining <= 0:
            optimal = False
            break
        try:
            result = milp(
                objective,
                integrality=np.ones(width),
                bounds=Bounds(lower, upper),
                constraints=LinearConstraint(np.array(rows), np.array(lowers), np.array(uppers)),
                options={"time_limit": remaining, "mip_rel_gap": 0.0},
            )
        except (ValueError, RuntimeError) as exc:
            return failure(
                "solver_error", f"Mannschaftsrechnung fehlgeschlagen: {type(exc).__name__}."
            )
        if result.x is None:
            if solution is None:
                return failure(
                    "infeasible" if result.status == 2 else "solver_error",
                    "Kein finanzierbares Paket mit den verlangten Bedingungen gefunden.",
                )
            optimal = False
            break
        solution = np.rint(result.x).astype(int)
        if result.status != 0:
            optimal = False
            break
        if stage == 0:
            count = int(sum(solution[:n]))
            constraint({i: 1 for i in range(n)}, count, count)
        elif stage == 1:
            points = sum(o.horizon_points * solution[i] for i, o in enumerate(available))
            constraint(
                {i: o.horizon_points for i, o in enumerate(available)}, points - 1e-6, np.inf
            )
    if solution is None:
        return failure("solver_error", "Rechenzeit ohne zulässige Mannschaft ausgeschöpft.")
    lineup = [o for i, o in enumerate(available) if solution[i]]
    purchases = [o for i, o in enumerate(available) if solution[n + i]]
    cost = sum(o.cost_eur for o in purchases)
    if cost > cash_available:
        return failure("solver_error", "Numerische Lösung überschreitet die exakte Eurodeckung.")
    chosen = int(np.argmax(solution[2 * n :]))
    formation = _FORMATIONS[chosen]
    missing = {
        pos: need - sum(o.position == pos for o in lineup)
        for pos, need in enumerate((1, *formation), start=1)
        if need > sum(o.position == pos for o in lineup)
    }
    notes = []
    if missing:
        notes.append(
            f"Mit diesen Optionen und diesem Budget bleiben {sum(missing.values())} Plätze frei."
        )
    if any(not o.owned for o in lineup):
        notes.append(
            "Geplante Elf gilt nur bei den genannten Zuschlägen; offene Gebote sind kein Besitz."
        )
    if not optimal:
        notes.append(
            "Zulässige Lösung gefunden, Optimalität innerhalb der Rechenzeit nicht bewiesen."
        )
    return TeamPlan(
        "-".join(map(str, formation)),
        len(lineup),
        sum(o.horizon_points for o in lineup),
        cost,
        cash_available - cost,
        tuple(o.player_id for o in purchases),
        tuple(o.player_id for o in lineup),
        owned_slots,
        "optimal" if optimal else "feasible_time_limit",
        optimal,
        tuple(notes),
        missing,
    )
