"""Auto-Aufstellung: die punktbeste erlaubte Startelf.

Für jede in Kickbase gültige Formation (immer 1 Torwart) wird gierig die beste
Besetzung pro Position gewählt und die erwarteten Punkte summiert; die Formation
mit der höchsten Summe gewinnt. Reicht der Kader für keine Formation, bleibt
`best_lineup` leer; der Aufstellungspfad setzt dann über `best_partial_lineup`
eine Teil-Elf mit allen unterbringbaren Spielern (L-109).

Optimiert wird über `lineup_score` (Signale OHNE Marktwert-Trend): ein steigender
Marktwert erzeugt keine Spieltagspunkte — für Kauf/Verkauf zählt weiter total_score.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from kickbase_agent.intel.models import ExplosionCandidate

# (Abwehr, Mittelfeld, Sturm) — die gängigen Kickbase-Formationen, je + 1 Torwart.
_FORMATIONS: tuple[tuple[int, int, int], ...] = (
    (3, 4, 3), (3, 5, 2), (3, 6, 1),
    (4, 3, 3), (4, 4, 2), (4, 5, 1),
    (5, 2, 3), (5, 3, 2), (5, 4, 1),
)

#: Jede Formation ist 1 + 10 — ein Kader unter 11 Spielern stellt nie eine Elf.
MIN_SQUAD_SIZE = 11


def can_field_any_formation(counts: dict[int, int]) -> bool:
    """True, wenn die Positions-Zählung (1=TW, 2=ABW, 3=MF, 4=ST) mindestens
    EINE gültige Kickbase-Formation voll besetzt (1 TW + d-m-s).

    Einzige Formations-Wahrheit für Policies UND Executor-Preflight (Codex-
    Re-Check 24.08., P1: die Kader-Deckung muss zentral an der Schreibstelle
    prüfbar sein, nicht nur je Planner-Lauf).
    """
    if counts.get(1, 0) < 1:
        return False
    return any(
        counts.get(2, 0) >= d and counts.get(3, 0) >= m and counts.get(4, 0) >= s
        for d, m, s in _FORMATIONS
    )


@dataclass
class Lineup:
    formation: str
    starters: list[ExplosionCandidate]
    bench: list[ExplosionCandidate]
    expected_score: float
    #: Nur bei einer TEIL-Elf gesetzt: die elf Plätze in Kickbase-Reihenfolge
    #: (Platz 0 = TW, dann Abwehr, Mittelfeld, Sturm laut Formation), leere Plätze
    #: als None. Belegt durch `kba probe lineup` vom 18.09.2026: Kickbase führt je
    #: Spieler den Platzindex `lo` 0–10 und lässt Lücken mitten in einer Reihe zu.
    slot_ids: list[str | None] = field(default_factory=list)

    @property
    def open_slots(self) -> int:
        """Leere Plätze einer Teil-Elf (0 bei einer vollen Elf oder ohne Aufstellung)."""
        return sum(1 for pid in self.slot_ids if pid is None)

    @property
    def write_ids(self) -> list[str | None]:
        """Platzliste für den Aufstellungs-Write: Teil-Elf mit None-Lücken, sonst die Elf."""
        return list(self.slot_ids) if self.slot_ids else [c.player_id for c in self.starters]


def _by_position(squad: list[ExplosionCandidate]) -> dict[int, list[ExplosionCandidate]]:
    buckets: dict[int, list[ExplosionCandidate]] = {1: [], 2: [], 3: [], 4: []}
    for c in squad:
        buckets.setdefault(c.position, []).append(c)
    for players in buckets.values():
        players.sort(key=lambda c: c.lineup_score, reverse=True)
    return buckets


def best_lineup(squad: list[ExplosionCandidate]) -> Lineup:
    pos = _by_position(squad)
    keeper = pos[1][:1]  # bester Torwart
    if not keeper:
        return Lineup(formation="", starters=[], bench=list(squad), expected_score=0.0)

    best: Lineup | None = None
    for d, m, s in _FORMATIONS:
        if len(pos[2]) < d or len(pos[3]) < m or len(pos[4]) < s:
            continue  # Kader reicht für diese Formation nicht
        starters = keeper + pos[2][:d] + pos[3][:m] + pos[4][:s]
        score = sum(c.lineup_score for c in starters)
        if best is None or score > best.expected_score:
            starter_ids = {c.player_id for c in starters}
            best = Lineup(
                formation=f"{d}-{m}-{s}",
                starters=starters,
                bench=[c for c in squad if c.player_id not in starter_ids],
                expected_score=score,
            )

    if best is None:
        return Lineup(formation="", starters=[], bench=list(squad), expected_score=0.0)
    return best


def best_partial_lineup(
    squad: list[ExplosionCandidate],
    score: Callable[[ExplosionCandidate], float] | None = None,
) -> Lineup:
    """Teil-Elf für Kader, die keine Formation voll besetzen (18.09.2026, L-109).

    Jeder leere Platz kostet 100 Punkte, ein aufgestellter Spieler ohne Einsatz
    null (L-080). Deshalb gewinnt die Formation, die die MEISTEN vorhandenen
    Spieler unterbringt; erst bei Gleichstand entscheidet die Punktsumme, danach
    die Reihenfolge in `_FORMATIONS`. Bewusst getrennt von `best_lineup`: dessen
    leeres Ergebnis heißt für Kaufprüfung und Kadersicherungen „keine Elf" und
    darf diese Bedeutung nicht verlieren.
    """
    rate = score or (lambda c: c.lineup_score)
    pos: dict[int, list[ExplosionCandidate]] = {1: [], 2: [], 3: [], 4: []}
    for c in squad:
        if c.position in pos:
            pos[c.position].append(c)
    for players in pos.values():
        players.sort(key=lambda c: (-rate(c), c.player_id))

    best: Lineup | None = None
    best_key: tuple[int, float] | None = None
    for d, m, s in _FORMATIONS:
        lines = [pos[1][:1], pos[2][:d], pos[3][:m], pos[4][:s]]
        starters = [c for line in lines for c in line]
        key = (len(starters), sum(rate(c) for c in starters))
        if best_key is not None and key <= best_key:
            continue
        slots: list[str | None] = []
        for line, size in zip(lines, (1, d, m, s), strict=True):
            slots += [c.player_id for c in line] + [None] * (size - len(line))
        starter_ids = {c.player_id for c in starters}
        best_key = key
        best = Lineup(
            formation=f"{d}-{m}-{s}",
            starters=starters,
            bench=[c for c in squad if c.player_id not in starter_ids],
            expected_score=key[1],
            slot_ids=slots if len(starters) < MIN_SQUAD_SIZE else [],
        )
    if best is None or not best.starters:
        return Lineup(formation="", starters=[], bench=list(squad), expected_score=0.0)
    return best
