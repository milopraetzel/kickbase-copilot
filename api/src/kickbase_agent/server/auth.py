"""In-memory token cache for single-user server.

Wählt automatisch den Auth-Weg:
- Token-Modus, wenn KICKBASE_TOKEN gesetzt ist (Refresh via KICKBASE_REFRESH_TOKEN).
- Passwort-Modus sonst (KICKBASE_EMAIL/KICKBASE_PASSWORD).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from kickbase_agent.config import get_settings
from kickbase_agent.kickbase.auth import (
    authenticate,
    refresh_tokens,
    token_expiry,
    user_id_from_token,
)
from kickbase_agent.kickbase.errors import KickbaseAuthError
from kickbase_agent.kickbase.models import League

_log = logging.getLogger(__name__)

#: Kickbase-Tokens gelten 7 Tage. So lange vor Ablauf erneuert der Cache von sich aus — der
#: Worker lief am 12.10.2026 nach 7 Tagen ohne Neustart zehn Stunden lang gegen ein
#: abgelaufenes Token (Kickbase antwortet dann 403, nicht 401).
RENEW_BEFORE = timedelta(hours=12)


class TokenCache:
    def __init__(self) -> None:
        self._token: str | None = None
        self._refresh_token: str | None = None
        self._user_id: str | None = None
        self._leagues: list[League] = []
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        async with self._lock:
            if self._token is None:
                await self._bootstrap()
            elif self._expires_soon():
                await self._renew_early()
            assert self._token is not None
            return self._token

    def _expires_soon(self) -> bool:
        expiry = token_expiry(self._token or "")
        return expiry is not None and expiry - datetime.now(UTC) <= RENEW_BEFORE

    async def _renew_early(self) -> None:
        """Vorsorgliche Erneuerung. Scheitert sie, solange das alte Token noch gilt, bleibt
        es in Gebrauch (der nächste Abruf versucht es erneut); ist es schon abgelaufen, gilt
        der Fehler."""
        try:
            await self._login_again()
        except Exception:
            expiry = token_expiry(self._token or "")
            if expiry is None or expiry <= datetime.now(UTC):
                raise
            _log.warning("Token-Erneuerung vor Ablauf gescheitert — das alte gilt noch bis %s.",
                         expiry, exc_info=True)

    async def get_user_id(self) -> str:
        if self._user_id is None:
            await self.get_token()
        return self._user_id or ""

    async def get_leagues(self) -> list[League]:
        if not self._leagues:
            await self.get_token()
        return self._leagues

    def invalidate(self) -> None:
        self._token = None
        self._user_id = None
        self._leagues = []

    def _token_mode(self) -> bool:
        return bool(get_settings().kickbase_token)

    async def _bootstrap(self) -> None:
        settings = get_settings()
        self._refresh_token = settings.kickbase_refresh_token or None
        result = await authenticate()
        self._token = result.token
        self._user_id = result.user_id
        self._leagues = result.leagues

    async def _login_again(self) -> None:
        """Neues Token — via Refresh-Token (Token-Modus) oder Passwort-Login."""
        if self._token_mode():
            rtkn = self._refresh_token or get_settings().kickbase_refresh_token
            if not rtkn:
                raise KickbaseAuthError(
                    "Token abgelaufen und kein KICKBASE_REFRESH_TOKEN gesetzt — "
                    "bitte im Browser neu einloggen."
                )
            bundle = await refresh_tokens(rtkn)
            self._token = bundle.token
            self._refresh_token = bundle.refresh_token
            if not self._user_id:
                self._user_id = user_id_from_token(bundle.token)
        else:
            result = await authenticate()
            self._token = result.token
            self._user_id = result.user_id
            self._leagues = result.leagues

    async def refresh_token(self) -> str:
        """Nach 401/403: Token erneuern — via Refresh-Token oder Passwort-Login."""
        async with self._lock:
            if not self._token_mode():
                # Das abgelehnte Token gilt nicht mehr, auch wenn der neue Login scheitert.
                self.invalidate()
            await self._login_again()
            assert self._token is not None
            return self._token


_cache: TokenCache | None = None


def get_token_cache() -> TokenCache:
    global _cache
    if _cache is None:
        _cache = TokenCache()
    return _cache
