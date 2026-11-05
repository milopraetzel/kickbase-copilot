"""Zentrale Client-Fabrik: KickbaseClient MIT Token-Refresh für Worker und CLI.

Der API-Server baut seinen Client seit jeher richtig (server/deps.py):
`KickbaseClient(token=…, on_auth_failure=cache.refresh_token)` — läuft der
Access-Token ab, erneuert der Hook ihn transparent (Refresh-Token bzw.
Passwort-Relogin) und der Request wird einmal wiederholt.

Worker-Jobs und CLI-Kommandos bauten dagegen `KickbaseClient(token=auth.token)`
OHNE Refresh-Hook: nach Token-Ablauf starben Morgenjob, Anpfiff-Wächter und
Briefing mit KickbaseAuthError (P0-Review-Fund). Diese Fabrik zentralisiert das
vorhandene Muster — denselben TokenCache samt on_auth_failure-Hook — für alle
Aufrufer außerhalb des Servers. Hier wird NICHTS neu erfunden: TokenCache
(server/auth.py, protokollneutral, kein FastAPI-Import) und der Client-Hook
(kickbase/client.py) existieren; die Fabrik verdrahtet sie an einer Stelle.

Der Cache ist prozessweit (wie im Server): der erste Aufruf authentifiziert
(inkl. fail-closed Liga-Priorisierung aus kickbase/auth.py), spätere Aufrufe
nutzen den gecachten Token. Ein 401 mitten im Request erneuert ihn über den
Hook — der erneuerte Token (inkl. rollendem Refresh-Token) bleibt für alle
folgenden Jobs im Cache erhalten, statt bei jedem Job wieder mit dem
abgelaufenen .env-Token zu starten.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kickbase_agent.kickbase.client import KickbaseClient
from kickbase_agent.kickbase.models import League
from kickbase_agent.server.auth import get_token_cache


@dataclass(frozen=True)
class AuthState:
    """Auth-Kontext für Jobs: aktueller Token, User-ID, priorisierte Ligen."""

    token: str
    user_id: str
    leagues: list[League] = field(default_factory=list)

    @property
    def league_id(self) -> str | None:
        """ID der aktiven Liga (leagues[0], von authenticate() priorisiert)."""
        return self.leagues[0].id if self.leagues else None


async def make_kickbase_client() -> tuple[KickbaseClient, AuthState]:
    """Client MIT Auth-Failure-Refresh bauen — einzige Konstruktion außerhalb des Servers.

    Rückgabe: (client, AuthState) — die meisten Jobs brauchen neben dem Client
    auch league_id und user_id.
    """
    cache = get_token_cache()
    token = await cache.get_token()
    client = KickbaseClient(token=token, on_auth_failure=cache.refresh_token)
    return client, AuthState(
        token=token,
        user_id=await cache.get_user_id(),
        leagues=await cache.get_leagues(),
    )
