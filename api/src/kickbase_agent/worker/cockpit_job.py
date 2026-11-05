"""Cockpit-Build-Job — schreibt die Web-App periodisch ins geteilte Volume.

Der nginx-Container `cockpit-web` serviert die Datei unter
https://localhost:8080 (Traefik-Route, Basic-Auth, Cache-Control:
no-store) — jeder Browser-Refresh zeigt den letzten fertigen Build.
Aktiv nur, wenn `cockpit_html_path` gesetzt ist (auf der Box via Compose-Env;
lokal bleibt der Job aus, dort veröffentlicht die Morgen-Routine das Artifact).
"""

from __future__ import annotations

import logging
from pathlib import Path

from kickbase_agent.config import get_settings

logger = logging.getLogger(__name__)


async def run_cockpit_build() -> str:
    settings = get_settings()
    target = settings.cockpit_html_path
    if not target:
        return "cockpit_build: deaktiviert (cockpit_html_path leer)"

    from kickbase_agent.cli.dashboard import build_dashboard

    out = Path(target)
    size = await build_dashboard(out)
    if size is None:
        return "cockpit_build: bereits in einem anderen Prozess aktiv"
    logger.info("Cockpit gebaut: %s (%d Bytes)", out, size)
    return f"cockpit_build: {size:,} Bytes → {out}"
