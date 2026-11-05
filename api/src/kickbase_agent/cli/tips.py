"""`kba tip` / `kba tips` — Recherche-Schlussfolgerungen mechanisch in die Engine geben.

Die Morgen-Recherche (oder Emil) persistiert überzeugende Tipps; `build_plan`
boostet damit die Kauf-Scores (gedeckelt ±0.08, Verfall 4 Tage). So fließt der
Discord-Bericht wirklich in die Kaufentscheidung — nicht nur in Emils Kopf.
"""

from __future__ import annotations

import typer

from kickbase_agent.db.session import SessionLocal
from kickbase_agent.intel.community import load_recent_tips, save_tip


def tip_command(
    player_name: str = typer.Argument(..., help="Spielername (wie in Kickbase)"),
    weight: float = typer.Option(1.0, "--weight", "-w",
                                 help="+1.0 Kauf-Tipp … -1.0 meiden"),
    source: str = typer.Option("ki-recherche", "--source", "-s",
                               help="z. B. youtube, reddit, ki-recherche"),
    note: str = typer.Option("", "--note", "-n", help="Begründung (kurz)"),
) -> None:
    """Community-/Recherche-Tipp speichern → boostet Kauf-Scores der Engine."""
    with SessionLocal() as session:
        tip = save_tip(session, player_name, weight, source, note)
        typer.echo(
            f"Tipp #{tip.id}: {tip.player_name} ({tip.weight:+.1f}, {tip.source}) gespeichert "
            f"— wirkt {4} Tage auf die Kauf-Scores."
        )


def tips_command() -> None:
    """Aktive Tipps anzeigen (letzte 4 Tage)."""
    with SessionLocal() as session:
        tips = load_recent_tips(session)
    if not tips:
        typer.echo("Keine aktiven Tipps.")
        return
    for t in sorted(tips, key=lambda x: x.created_at, reverse=True):
        typer.echo(
            f"  {t.created_at:%d.%m %H:%M}  {t.player_name:<20} {t.weight:+.1f} "
            f"[{t.source}] {t.note[:50]}"
        )
