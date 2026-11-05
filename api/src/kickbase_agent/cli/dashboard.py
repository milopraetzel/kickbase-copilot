"""`kba dashboard-html` — Baut das Kickbase-Cockpit (`index.html` + `strategy.json`).

Einzige Oberfläche ist `cockpit/workspace.py` (`render_workspace`) mit den
Ansichten Heute, Übersicht, Alle Spieler, Liga, Jev-Analyse und Regelwerk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

import typer

log = logging.getLogger(__name__)


def dashboard_html_command(
    out: str = typer.Argument("/tmp/kba-dashboard.html", help="Ausgabedatei"),
) -> None:
    """Cockpit-Seite aus Live-Daten bauen."""
    size = asyncio.run(build_dashboard(Path(out)))
    if size is None:
        typer.echo("Cockpit-Build läuft bereits; vorhandene Ansicht bleibt erhalten.")
        return
    typer.echo(f"Cockpit gebaut: {out} ({Path(out).stat().st_size:,} Bytes)")


def write_dashboard(d: dict, out: Path) -> int:
    """Alle Ansichten erst rendern, dann vollständig ersetzen; index zuletzt."""
    from kickbase_agent.cli.cockpit.workspace import render_workspace

    payloads = [
        (
            out.with_name("strategy.json"),
            json.dumps(d.get("strategy") or {}, ensure_ascii=False),
        ),
        (out, render_workspace(d)),
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary: list[tuple[Path, Path]] = []
    try:
        for target, content in payloads:
            fd, name = tempfile.mkstemp(prefix=".cockpit-", dir=out.parent)
            path = Path(name)
            temporary.append((path, target))
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            path.chmod(0o644)  # nginx liest aus demselben Volume als eigener Benutzer
        for path, target in temporary:
            path.replace(target)
    finally:
        for path, _ in temporary:
            path.unlink(missing_ok=True)
    return len(payloads[-1][1].encode("utf-8"))


async def build_dashboard(out: Path) -> int | None:
    """Ein Build je gemeinsamem Zielverzeichnis über Worker-/API-Prozesse hinweg."""
    import fcntl

    from kickbase_agent.cli.cockpit.data import collect

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.with_name(".build.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("Cockpit-Build übersprungen: ein anderer Prozess baut bereits (%s)", out)
            return None
        d = await collect()
        return write_dashboard(d, out)
