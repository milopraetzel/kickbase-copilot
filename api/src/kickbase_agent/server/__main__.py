"""Run `uv run kba-serve` to start FastAPI.

Lokal (Default): 127.0.0.1:8000 mit Auto-Reload. Im Container (docker-compose,
Service `api`) per Umgebung: KBA_API_HOST=0.0.0.0, KBA_API_RELOAD=0 — Traefik
erreicht den Container nur über eine Nicht-Loopback-Adresse.
"""

from __future__ import annotations

import os

import uvicorn

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def serve_config() -> dict[str, object]:
    """Host/Port/Reload aus KBA_API_HOST / KBA_API_PORT / KBA_API_RELOAD (testbar)."""
    port_raw = os.environ.get("KBA_API_PORT", "").strip()
    return {
        "host": os.environ.get("KBA_API_HOST", "").strip() or DEFAULT_HOST,
        "port": int(port_raw) if port_raw else DEFAULT_PORT,
        "reload": _env_flag("KBA_API_RELOAD", default=True),
    }


def main() -> None:
    cfg = serve_config()
    uvicorn.run(
        "kickbase_agent.server.app:app",
        host=str(cfg["host"]),
        port=int(str(cfg["port"])),
        reload=bool(cfg["reload"]),
    )


if __name__ == "__main__":
    main()
