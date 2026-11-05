"""Community-/Recherche-Tipps → mechanischer Einfluss auf Kauf-Scores.

Schließt Emils Rückkopplungs-Lücke: Die Schlussfolgerungen der Morgen-Recherche
(+ Insider-Quellen wie YouTube/Reddit) landen als `community_tips` in der DB und
BOOSTEN die Kauf-Scores der Engine — transparent als eigenes Signal am Kandidaten.
In Kickbase ist Hype = Nachfrage = Marktwert-Treiber, der Boost ist also ein
Nachfrage-Proxy, kein Bauchgefühl. Bewusst gedeckelt (±0.08·Gewicht) und mit
Verfallszeit (4 Tage), damit kein alter Hype die Datenlage übertönt.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import CommunityTip
from kickbase_agent.intel.models import ExplosionCandidate, Signal
from kickbase_agent.scouts.ligainsider.matcher import similarity

MAX_BOOST = 0.08          # maximale Score-Verschiebung je Richtung
TIP_TTL_DAYS = 4          # Tipps verfallen — Hype ist ein Kurzzeit-Signal
_MATCH_CONFIDENT = 0.90   # Namens-Match-Schwelle (wie LI-Veto)


def save_tip(
    session: Session, player_name: str, weight: float, source: str, note: str = ""
) -> CommunityTip:
    tip = CommunityTip(
        player_name=player_name.strip(),
        weight=max(-1.0, min(1.0, weight)),
        source=source[:64],
        note=note,
        created_at=datetime.now(UTC),
    )
    session.add(tip)
    session.commit()
    return tip


def load_recent_tips(session: Session, days: int = TIP_TTL_DAYS) -> list[CommunityTip]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    return list(
        session.execute(
            select(CommunityTip).where(CommunityTip.created_at >= cutoff)
        ).scalars()
    )


def apply_community_boost(
    candidates: list[ExplosionCandidate],
    tips: list[CommunityTip],
    *,
    only_positive: bool = False,
) -> list[ExplosionCandidate]:
    """Boostet Kandidaten-Scores anhand der Tipps (fuzzy Namens-Match, gedeckelt).

    Review-Fund: pro Tipp wird nur der EINE beste Namens-Match geboostet —
    sonst treffen ähnliche Namen (Ulrich/Ullrich, Brüderpaare) mehrere Spieler.

    only_positive (Review 31.08., Kader-Pfad): Negative Tipps (Gewicht < 0)
    werden übersprungen — der Kader-Boost ist als VERKAUFS-SCHUTZ zugesagt
    (Decision-Record daily_list_cap); ein Gerücht darf einen Kaderspieler nie
    erst unter die Verkaufsschwelle drücken. Das community-Signal wird in dem
    Fall auch nicht angehängt (kein halber Einfluss durch die Hintertür).
    """
    if not tips:
        return candidates
    # Pro Tipp den besten Kandidaten bestimmen (Argmax statt Streuung).
    tip_for_candidate: dict[str, CommunityTip] = {}
    best_sim_for_candidate: dict[str, float] = {}
    for tip in tips:
        best_cid: str | None = None
        best_sim = 0.0
        for c in candidates:
            s = similarity(c.name, tip.player_name)
            if s >= _MATCH_CONFIDENT and s > best_sim:
                best_cid, best_sim = c.player_id, s
        if best_cid is not None and best_sim > best_sim_for_candidate.get(best_cid, 0.0):
            tip_for_candidate[best_cid] = tip
            best_sim_for_candidate[best_cid] = best_sim

    out: list[ExplosionCandidate] = []
    for c in candidates:
        best = tip_for_candidate.get(c.player_id)
        if best is None or (only_positive and best.weight < 0):
            out.append(c)
            continue
        boost = MAX_BOOST * best.weight
        new_score = max(0.0, min(1.0, c.total_score + boost))
        signal = Signal(
            name="community",
            score=max(0.0, min(1.0, 0.5 + best.weight / 2)),
            weight=0.1,
            reason=f"{best.source}: {best.note[:60] or 'Insider-Tipp'}",
        )
        out.append(
            c.model_copy(
                update={
                    "total_score": round(new_score, 4),
                    "signals": [*c.signals, signal],
                }
            )
        )
    return out
