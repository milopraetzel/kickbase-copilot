"""`kba report-add` / `kba reports` — Berichte-Ablage (Steuerzentrale, Zone „Berichte").

Zwei Datenbanken (wie bei `kba tip`): die Web-App liest die BOX-DB, lokale Werkzeuge
(MCP `discord_post`) schreiben zunächst lokal. `report-add` ist deshalb der Spiegel-
Befehl auf der Box (`docker exec kickbase-agent uv run kba report-add …`), damit eine
per Chat gepostete Morgen-Recherche auch in der Web-App unter „Berichte" steht.
`reports` listet die letzten Berichte (Sichtprüfung/Hermes).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import typer
from sqlalchemy import select

from kickbase_agent.db.models import Report
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.notify.report_store import REPORT_KINDS, save_report

_KINDS_HELP = " | ".join(REPORT_KINDS)
_BERLIN = ZoneInfo("Europe/Berlin")


def _berlin(dt: datetime) -> datetime:
    """SQLite liefert naive UTC-Zeitstempel — für die Anzeige nach Berlin."""
    aware = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
    return aware.astimezone(_BERLIN)


def report_add_command(
    body: str = typer.Argument(..., help="Volltext des Berichts (Markdown/Text)"),
    kind: str = typer.Option("info", "--kind", "-k", help=f"Berichtsart: {_KINDS_HELP}"),
    source: str = typer.Option(
        "cli:report-add", "--source", "-s", help="Herkunft, z. B. mcp:discord_post"
    ),
    title: str = typer.Option("", "--title", "-t", help="Titel (leer = erste Zeile)"),
    meta: str = typer.Option("", "--meta", "-m", help='JSON-Objekt, z. B. {"delivered": true}'),
) -> None:
    """Bericht in `reports` ablegen (Volltext nachlesbar in der Web-App, Zone „Berichte")."""
    if kind not in REPORT_KINDS:
        raise typer.BadParameter(f"Unbekannte Berichtsart {kind!r} — erlaubt: {_KINDS_HELP}")
    text = body.strip()
    if not text:
        raise typer.BadParameter("Bericht ist leer.")
    meta_obj = None
    if meta.strip():
        try:
            meta_obj = json.loads(meta)
        except ValueError as exc:
            raise typer.BadParameter(f"--meta ist kein gültiges JSON: {exc}") from None
        if not isinstance(meta_obj, dict):
            raise typer.BadParameter("--meta muss ein JSON-Objekt sein.")
    report_id = save_report(kind, title.strip() or None, text, source, meta=meta_obj)
    if report_id is None:
        typer.echo("Bericht NICHT gespeichert — DB/Migration prüfen (Log lesen).")
        raise typer.Exit(1)
    typer.echo(f"Bericht #{report_id} ({kind}, {source}) gespeichert.")


def reports_command(
    kind: str = typer.Option("", "--kind", "-k", help=f"Filter: {_KINDS_HELP}"),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=200),
) -> None:
    """Letzte Berichte anzeigen (neueste zuerst)."""
    stmt = select(Report).order_by(Report.created_at.desc(), Report.id.desc()).limit(limit)
    if kind:
        stmt = stmt.where(Report.kind == kind)
    with SessionLocal() as session:
        rows = list(session.execute(stmt).scalars())
    if not rows:
        typer.echo("Keine Berichte.")
        return
    for r in rows:
        delivered = (r.meta or {}).get("delivered") if isinstance(r.meta, dict) else None
        flag = "" if delivered is not False else " (nicht zugestellt)"
        typer.echo(
            f"  #{r.id:<5} {_berlin(r.created_at):%d.%m %H:%M}  {r.kind:<12} {r.title[:60]}"
            f"  [{r.source}]{flag}"
        )
