"""`kba learn-migrate` + `kba learn-evidence` — Erkenntnisse verankern und belegen.

Spec: docs/superpowers/specs/2026-08-25-lern-bilanz-design.md §4.2, §8.

`learn-migrate` versieht die Einträge einmalig mit stabilen IDs und Arten.

Das Skript ändert KEINE Inhalte: Es setzt eine ID hinter den Bindestrich, hängt je
Eintrag eine `**Art:**`-Zeile an und schreibt die `next-id`-Fußzeile. Danach gibt es
eine Übersicht aus, die vor dem Commit gelesen wird — die Art-Zuordnung ist eine
Heuristik, keine Wahrheit.

Ohne `--write` passiert nichts außer der Vorschau (Trockenlauf ist der Default; ein
Schreib-Default wäre genau die Falle vom 21.08. in umgekehrter Richtung).

`learn-evidence` trägt EINEN Beleg ein, den ich beurteilt habe (`source=agent` —
nie wählbar). Zielt eine Box-API über `KBA_REMOTE_API_URL`, geht der Beleg DORTHIN:
Zwei-DB-Lehre vom 24.08. — eine Wahrheit, ein Schreiber. Ohne Remote-Konfiguration
(z. B. `docker exec` im Container) schreibt der Befehl in die lokale DB.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import typer

from kickbase_agent.config import get_settings
from kickbase_agent.db.session import SessionLocal

if TYPE_CHECKING:  # pragma: no cover — nur fuer die Typannotation
    from kickbase_agent.knowledge.migrate import MigrationReport

# Die `knowledge`-Importe stehen bewusst IN den Befehlen, nicht oben:
# `cli/__init__` laedt dieses Modul beim Start jedes CLI-Befehls, und
# `knowledge/__init__` zieht ueber `parameters` den Worker nach — der wiederum
# `cli.plan` importiert. Ein Import auf Modulebene macht daraus einen Zyklus
# (gemessen: `import worker.auto_trading` bricht mit „partially initialized").
# Gleiches Muster wie in `server/routes/intents.py` und `cli/cockpit/data.py`.


def _format_report(report: MigrationReport, path: Path, written: bool) -> str:
    counts: dict[str, int] = {}
    for entry in report.entries:
        counts[entry.art] = counts.get(entry.art, 0) + 1
    head = [
        f"{path}",
        f"{report.total} Einträge · {report.assigned} IDs neu vergeben · "
        f"{report.skipped} schon migriert · next-id {report.next_id}",
        "Arten: " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())),
        "",
    ]
    rows = [
        f"  {e.learning_id}  {e.art:<9} {'neu' if e.assigned else '   '}  "
        f"Z{e.line:<4} {e.title[:72]}"
        for e in report.entries
    ]
    tail = [
        "",
        (
            "GESCHRIEBEN — Diff prüfen (`git diff`), Arten korrigieren, dann committen."
            if written
            else "TROCKENLAUF — nichts geschrieben. Mit --write anwenden."
        ),
    ]
    return "\n".join(head + rows + tail)


def learn_migrate_command(
    path: str = typer.Option(
        "", "--path", "-p", help="LEARNINGS.md (leer = automatische Auflösung)"
    ),
    write: bool = typer.Option(False, "--write", help="Datei wirklich ändern"),
) -> None:
    """IDs (`[L-001]`) und `**Art:**` in LEARNINGS.md nachtragen — idempotent."""
    from kickbase_agent.knowledge.learnings import resolve_learnings_path
    from kickbase_agent.knowledge.migrate import migrate

    resolved = resolve_learnings_path(path or None)
    if resolved is None:
        typer.echo("Keine LEARNINGS.md gefunden (Argument, KBA_LEARNINGS_PATH, Repo-Pfad).")
        raise typer.Exit(code=1)
    before = resolved.read_text(encoding="utf-8")
    after, report = migrate(before)
    if write and after != before:
        resolved.write_text(after, encoding="utf-8")
    typer.echo(_format_report(report, resolved, written=write and after != before))


# ------------------------------------------------------------------ learn-evidence


def remote_target() -> tuple[str, tuple[str, str] | None] | None:
    """Box-API als Ziel, wenn konfiguriert (Zwilling von `mcp.tools_write._remote`)."""
    settings = get_settings()
    url = (os.environ.get("KBA_REMOTE_API_URL") or settings.kba_remote_api_url or "")
    url = url.strip().rstrip("/")
    if not url:
        return None
    raw = (os.environ.get("KBA_REMOTE_API_AUTH") or settings.kba_remote_api_auth or "").strip()
    if raw and ":" in raw:
        user, _, pw = raw.partition(":")
        return url, (user, pw)
    return url, None


def post_evidence_remote(
    base: str, auth: tuple[str, str] | None, payload: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/learning/evidence gegen die Box — mit CSRF-Header und Schreib-Token."""
    headers = {"X-Requested-With": "kba"}
    token = (os.environ.get("KBA_API_WRITE_TOKEN")
             or get_settings().kba_api_write_token or "").strip()
    if token:
        headers["X-KBA-Write-Token"] = token
    with httpx.Client(auth=auth, timeout=30, headers=headers) as http:
        resp = http.post(f"{base}/learning/evidence", json=payload)
    resp.raise_for_status()
    return resp.json() or {}


def learn_evidence_command(
    learning_id: str = typer.Argument(..., help="Anker der Erkenntnis, z. B. L-065"),
    verdict: str = typer.Argument(..., help="confirm | refute"),
    note: str = typer.Option(..., "--note", "-n", help="Ein Satz: was beobachtet wurde"),
    ref: str = typer.Option(..., "--ref", "-r",
                            help="Eindeutige Ereignis-Referenz, z. B. bid:34"),
    at: str = typer.Option("", "--at",
                           help="Zeitpunkt des Ereignisses (ISO); leer = jetzt"),
) -> None:
    """Beleg zu einer Erkenntnis eintragen (immer `source=agent`)."""
    from kickbase_agent.knowledge.evidence import record_agent_evidence

    observed_at: datetime | None = None
    if at.strip():
        try:
            observed_at = datetime.fromisoformat(at.strip())
        except ValueError:
            typer.echo(f"--at ist kein ISO-Zeitstempel: {at!r}")
            raise typer.Exit(code=1) from None

    remote = remote_target()
    if remote is not None:
        base, auth = remote
        payload = {
            "learning_id": learning_id, "verdict": verdict, "note": note, "ref": ref,
        }
        if observed_at is not None:
            payload["observed_at"] = observed_at.isoformat()
        try:
            body = post_evidence_remote(base, auth, payload)
        except httpx.HTTPError as exc:
            typer.echo(f"Box-API nicht erreichbar/abgelehnt: {type(exc).__name__}: {exc}")
            raise typer.Exit(code=1) from exc
        typer.echo(f"Box ({base}): {body.get('note') or 'gespeichert'}")
        return

    with SessionLocal() as session:
        try:
            row = record_agent_evidence(
                session, learning_id, verdict, note=note, ref=ref, observed_at=observed_at,
            )
        except ValueError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from exc
        # Felder NOCH IN der Session lesen: Nach dem commit sind die Attribute
        # abgelaufen, und hinter dem `with` ist die Instanz detached (live
        # gemessen: DetachedInstanceError beim ersten echten CLI-Lauf).
        written = None if row is None else (row.id, row.learning_id, row.verdict, row.ref)

    if written is None:
        typer.echo(
            f"{learning_id.upper()} / {ref}: dieses Ereignis war schon belegt — nichts angelegt."
        )
        return
    row_id, lid, v, r = written
    typer.echo(f"{lid} {v} (agent) gespeichert — Beleg #{row_id}, ref {r}.")
