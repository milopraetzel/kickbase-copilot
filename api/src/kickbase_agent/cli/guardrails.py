"""CLI der Sicherungen: `kba guardrails-check` · `guardrails-seal` · `freeze`/`unfreeze`.

`guardrails-check` ist das Tor vor jedem Selbst-Deploy: Exit-Code 0 nur, wenn die
versiegelten Sicherungs-Tests unverändert sind UND die volle Suite grün ist. Alles
andere endet mit Exit 1 — agent/self_change.py wertet genau das aus.

`guardrails-seal` ist der bewusste Gegenpol: neu versiegeln, mit Begründung. Der
Selbstumbau ruft ihn NIE auf; das ist Emils bzw. des Orchestrators Handgriff.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import typer

from kickbase_agent.agent import deploy_env as de
from kickbase_agent.agent import freeze as fz
from kickbase_agent.agent import guardrail_lock as gl
from kickbase_agent.db.session import SessionLocal


def guardrails_check_command(
    no_suite: bool = typer.Option(
        False, "--no-suite", help="Nur die Pruefsummen pruefen, die Suite nicht starten."
    ),
    as_json: bool = typer.Option(False, "--json", help="Ergebnis als JSON ausgeben."),
) -> None:
    """Umgebung tauglich + Sicherungen unveraendert + Suite gruen? Sonst Exit 1.

    Die Umgebungspruefung gehoert INS Tor, nicht daneben: Ein gruenes „unversehrt"
    an einem Ort, an dem gar kein Deploy stattfinden kann (kein Git-Repo, keine
    startbare Suite — z. B. im Worker-Container), waere die falsche Auskunft.
    """
    env = de.check_environment()
    result = gl.check(run_suite=not no_suite)
    ok = result.ok and env.ok
    if as_json:
        payload = result.as_dict()
        payload["umgebung"] = env.as_dict()
        payload["ok"] = ok
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(result.summary)
        for problem in result.problems:
            typer.echo(f"  - {problem}")
        if not env.ok:
            typer.echo(env.summary)
            for problem in env.problems:
                typer.echo(f"  - {problem}")
            typer.echo(de.PLACE)
        if result.suite_ok is False and result.suite_output:
            typer.echo("--- Suite (Auszug) ---")
            typer.echo(result.suite_output)
    if not ok:
        raise typer.Exit(1)


def guardrails_seal_command(
    reason: str = typer.Option(
        ..., "--reason", help="Warum wird neu versiegelt? (steht im Manifest)"
    ),
) -> None:
    """Manifest neu versiegeln — bewusster menschlicher Akt, nie aus dem Selbstumbau."""
    if not reason.strip():
        raise typer.BadParameter("Ohne Begruendung wird nicht neu versiegelt.")
    root = gl.project_root()
    path = gl.write_lock(
        root, reason=reason.strip(), sealed_at=datetime.now(UTC).isoformat(timespec="seconds")
    )
    lock = gl.load_lock(path)
    typer.echo(f"Versiegelt: {len(lock['files'])} Sicherungs-Tests in {path.name}")
    for rel, digest in sorted(lock["files"].items()):
        typer.echo(f"  {digest[:12]}  {rel}")


# ----------------------------------------------------------------- Einfrieren


def freeze_status_command() -> None:
    """Ist der Agent gerade selbst eingefroren? (Geld-Writes gesperrt)"""
    with SessionLocal() as session:
        typer.echo(fz.format_status(fz.active_freeze(session)))


def freeze_command(
    reason: str = typer.Option(..., "--reason", help="Warum wird eingefroren?"),
) -> None:
    """Von Hand einfrieren (Geld-Writes sperren)."""
    with SessionLocal() as session:
        row = fz.freeze(
            session, reason=reason.strip(), source=fz.SOURCE_MANUAL, now=datetime.now(UTC)
        )
        typer.echo(fz.format_status(row))


def unfreeze_command(
    by: str = typer.Option(..., "--by", help="Wer entsperrt? (steht im Protokoll)"),
    note: str = typer.Option("", "--note", help="Was wurde geprueft/behoben?"),
) -> None:
    """Entsperren — nur von Hand, nie durch den Agenten selbst."""
    with SessionLocal() as session:
        row = fz.release(session, by=by.strip(), now=datetime.now(UTC), note=note.strip())
        # Werte NOCH IN der Session lesen: nach dem Verlassen ist die Zeile
        # detached und jeder Attributzugriff loest einen Refresh aus, der ohne
        # Session scheitert (DetachedInstanceError — beim ersten CLI-Lauf passiert).
        message = (
            "Es war nichts eingefroren." if row is None
            else f"Entsperrt durch {row.released_by}. Grund war: {row.reason}"
        )
    typer.echo(message)
