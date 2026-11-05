"""FastAPI application factory."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kickbase_agent.config import get_settings
from kickbase_agent.kickbase.errors import KickbaseAuthError
from kickbase_agent.server.routes import (
    freeze,
    health,
    intel,
    intents,
    jobs,
    learning,
    live,
    market,
    me,
    news,
    operator,
    refresh,
    reports,
    rules,
    squad,
    strategy,
)

log = logging.getLogger(__name__)

#: Mutierende Methoden, die den CSRF-Header brauchen (same-origin fetch setzt ihn).
_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_HEADER_VALUE = "kba"

#: Beweis "durch die Vordertuer gekommen" — Traefik faerbt jeden Request auf dem
#: /api-Router damit ein (customRequestHeaders UEBERSCHREIBT einen mitgeschickten
#: Wert), siehe docker-compose.yml. Wer ihn nicht traegt, kam direkt aus dem
#: Docker-Netz und darf nicht schreiben.
WRITE_TOKEN_HEADER = "X-KBA-Write-Token"


def create_app() -> FastAPI:
    app = FastAPI(title="Kickbase Agent API", version="0.1.0")

    @app.exception_handler(KickbaseAuthError)
    async def _kickbase_auth_error(request: Request, exc: KickbaseAuthError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _csrf_guard(request: Request, call_next):
        # Review-Fund 23.08.: Die Intent-Endpunkte haengen nur hinter Basic-Auth — ein
        # fremdes Formular koennte mit den Browser-Credentials POSTen (CSRF). Browser
        # koennen Custom-Header nur per same-origin fetch/XHR setzen (sonst CORS-
        # Preflight) — deshalb verlangen mutierende Requests `X-Requested-With`.
        if request.method in _MUTATING and request.url.path.startswith("/api/"):
            if request.headers.get("x-requested-with", "").lower() != CSRF_HEADER_VALUE:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF-Schutz: Header X-Requested-With: kba fehlt."},
                )
        return await call_next(request)

    @app.middleware("http")
    async def _write_token_guard(request: Request, call_next):
        # Messung 24.08. (Codex-Folge-Review P1, verifiziert): Traefik terminiert die
        # Basic-Auth am Proxy — `kickbase-api` haengt aber im geteilten Netz
        # `n8n_default` mit sieben fremden Containern (n8n, ai-messenger, Previews).
        # Ein POST /api/refresh aus `atoz-chat-preview` kam nachweislich bis in die App
        # (HTTP 429): der CSRF-Header ist fuer einen Nicht-Browser eine Zeile Code.
        # Deshalb sitzt das Gate an ALLEN mutierenden Routen — nicht nur an
        # POST /intents. `adjust` (Betrag ohne Obergrenze) und `execute-now`
        # (Veto-Fenster weg) sind der gefaehrlichere Pfad und muessen fuer die
        # Zentrale-Buttons HTTP bleiben.
        # Leeres Setting = Gate aus (lokale Entwicklung), dafuer Warnung beim Start.
        if request.method in _MUTATING and request.url.path.startswith("/api/"):
            expected = get_settings().kba_api_write_token.strip()
            if expected and request.headers.get(WRITE_TOKEN_HEADER, "") != expected:
                return JSONResponse(
                    status_code=401,
                    content={
                        "detail": (
                            f"Schreib-Gate: Header {WRITE_TOKEN_HEADER} fehlt oder ist "
                            "falsch — Schreibzugriffe nur durch Traefik."
                        )
                    },
                )
        return await call_next(request)

    if not get_settings().kba_api_write_token.strip():
        log.warning(
            "Schreib-Gate AUS: KBA_API_WRITE_TOKEN ist leer — mutierende /api-Routen "
            "stehen jedem Container im Docker-Netz offen. Token in api/.env UND "
            "/opt/kickbase-agent/.env setzen (Traefik-Middleware kba-writetoken)."
        )

    app.include_router(health.router, prefix="/api")
    app.include_router(me.router, prefix="/api")
    app.include_router(squad.router, prefix="/api")
    app.include_router(strategy.router, prefix="/api")
    app.include_router(market.router, prefix="/api")
    app.include_router(intel.router, prefix="/api")
    app.include_router(news.router, prefix="/api")
    app.include_router(operator.router, prefix="/api")
    app.include_router(intents.router, prefix="/api")
    app.include_router(reports.router, prefix="/api")
    app.include_router(refresh.router, prefix="/api")
    app.include_router(learning.router, prefix="/api")
    app.include_router(live.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(freeze.router, prefix="/api")
    app.include_router(rules.router, prefix="/api")

    return app


app = create_app()
