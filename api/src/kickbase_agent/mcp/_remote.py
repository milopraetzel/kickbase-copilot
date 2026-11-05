"""Remote-Lesesicht: die Box ist die Datenquelle, Fehler bleiben sichtbar."""

from __future__ import annotations

from typing import Any

import httpx

from kickbase_agent.cli.learn import remote_target
from kickbase_agent.mcp._util import ToolError


async def read_remote(path: str, **params: str | int) -> dict[str, Any] | None:
    target = remote_target()
    if target is None:
        return None
    base, auth = target
    async with httpx.AsyncClient(
        auth=auth, timeout=30, headers={"X-Requested-With": "kba"},
    ) as http:
        response = await http.get(f"{base}/{path}", params=params)
    if response.status_code in {401, 403}:
        raise ToolError(
            "Box-API hat den Lesezugriff abgelehnt.",
            hint=(
                f"HTTP {response.status_code}: KBA_REMOTE_API_AUTH bzw. KBA_API_AUTH "
                "für die Basic-Auth prüfen. Keine lokale Ersatzbilanz geladen."
            ),
        )
    if response.status_code == 404:
        raise ToolError(
            "Die konfigurierte Box-API bietet diese Lesesicht nicht an.",
            hint=(
                "HTTP 404: API-Basis (endet auf /api) und deployed Code prüfen; "
                "die Box benötigt die Operator-Leserouten. Kein lokaler Fallback."
            ),
        )
    response.raise_for_status()
    # Bei HTTP-/Datenfehlern KEIN lokaler Fallback: eine leere lokale Lernbilanz
    # ist kein Beleg dafür, dass auf der Box nichts passiert ist.
    return {**response.json(), "remote": True, "data_source": "box"}
