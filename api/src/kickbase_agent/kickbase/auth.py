"""Kickbase-Authentifizierung: Passwort-Login, Token-Refresh, JWT-Auslesung.

Zwei Wege:
- Token-Modus (bevorzugt): ein einmal im Browser geholtes Token-Paar (Access +
  Refresh) wird genutzt; der Refresh-Token erneuert den Access-Token via
  /v4/user/refreshtokens — ganz ohne Passwort (nötig für Apple/Google-Accounts).
- Passwort-Modus (Fallback): klassischer /v4/user/login mit E-Mail + Passwort.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from kickbase_agent.config import get_settings
from kickbase_agent.kickbase import endpoints
from kickbase_agent.kickbase.errors import KickbaseAuthError
from kickbase_agent.kickbase.models import League, LoginResponse

logger = logging.getLogger(__name__)

# JWT-Claims, unter denen Kickbase die User-ID ablegen könnte (tolerant geprüft).
_USER_ID_CLAIMS = ("sub", "kb.uid", "uid", "userId", "u", "id")


class LeagueNotFoundError(KickbaseAuthError):
    """Konfigurierte Liga (KICKBASE_LEAGUE_ID) existiert nicht im Account.

    Bewusst fail-closed: Jobs sollen laut scheitern, statt still in der
    falschen Liga zu handeln (der Account enthält fast gleichnamige Ligen).
    """


@dataclass(frozen=True)
class TokenBundle:
    token: str
    refresh_token: str


@dataclass(frozen=True)
class AuthResult:
    token: str
    user_id: str
    leagues: list[League]


def _extract_user_id(raw_user: Any) -> str:
    if isinstance(raw_user, dict):
        return str(raw_user.get("id") or raw_user.get("i") or "")
    return str(raw_user or "")


def _token_claims(token: str) -> dict[str, Any]:
    """JWT-Payload lesen (ohne Signaturprüfung — nur Claims); unlesbar → {}."""
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        segment = parts[1]
        padded = segment + "=" * (-len(segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, binascii.Error, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def user_id_from_token(token: str) -> str:
    """User-ID aus dem JWT-Payload lesen (ohne Signaturprüfung — nur Claims)."""
    payload = _token_claims(token)
    for claim in _USER_ID_CLAIMS:
        if claim in payload and payload[claim]:
            return str(payload[claim])
    return ""


def token_expiry(token: str) -> datetime | None:
    """Ablaufzeit aus dem `exp`-Claim (Kickbase: 7 Tage nach der Anmeldung); sonst None."""
    exp = _token_claims(token).get("exp")
    if isinstance(exp, bool) or not isinstance(exp, int | float):
        return None
    try:
        return datetime.fromtimestamp(exp, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _leagues_from_payload(payload: Any) -> list[League]:
    """Ligen aus einer (undokumentierten) Selection-Antwort tolerant herauslesen."""
    if isinstance(payload, list):
        raw_list = payload
    elif isinstance(payload, dict):
        raw_list = (
            payload.get("it")
            or payload.get("srvl")
            or payload.get("leagues")
            or payload.get("lgs")
            or []
        )
    else:
        raw_list = []
    leagues: list[League] = []
    for raw in raw_list:
        if isinstance(raw, dict):
            try:
                leagues.append(League.from_api(raw))
            except (KeyError, ValueError):
                continue
    return leagues


async def login(email: str, password: str) -> LoginResponse:
    """Passwort-Login (Fallback-Weg)."""
    settings = get_settings()
    url = settings.kickbase_base_url + endpoints.LOGIN
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            url,
            json={"em": email, "pass": password, "loy": False, "rep": {}},
        )
        if response.status_code == 401:
            raise KickbaseAuthError(
                "Kickbase-Login abgelehnt (401 AccessDenied). "
                "KICKBASE_EMAIL/KICKBASE_PASSWORD in api/.env prüfen — "
                "Apple/Google-Accounts brauchen stattdessen KICKBASE_TOKEN + "
                "KICKBASE_REFRESH_TOKEN (Browser-Login)."
            )
        response.raise_for_status()
        payload = response.json()

    raw_leagues = payload.get("leagues") or payload.get("srvl") or []
    return LoginResponse(
        tkn=payload["tkn"],
        u=_extract_user_id(payload.get("u")),
        leagues=[League.from_api(lg) for lg in raw_leagues],
    )


async def refresh_tokens(refresh_token: str) -> TokenBundle:
    """Access-Token via Refresh-Token erneuern (passwortlos)."""
    settings = get_settings()
    url = settings.kickbase_base_url + endpoints.REFRESH_TOKENS
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json={"rtkn": refresh_token})
        if response.status_code == 401:
            raise KickbaseAuthError(
                "Refresh-Token abgelehnt (401). KICKBASE_REFRESH_TOKEN in api/.env ist "
                "abgelaufen oder ungültig — bitte im Browser neu einloggen und Token erneuern."
            )
        response.raise_for_status()
        payload = response.json()
    new_token = payload.get("tkn")
    if not new_token:
        raise KickbaseAuthError("Refresh-Antwort enthielt kein Token ('tkn').")
    # Rolling Refresh: falls ein neuer Refresh-Token kommt, diesen übernehmen.
    new_refresh = payload.get("rtkn") or refresh_token
    return TokenBundle(token=new_token, refresh_token=new_refresh)


def _prioritize(leagues: list[League], league_id: str) -> list[League]:
    """Konfigurierte Liga nach vorne — sämtlicher Code nutzt leagues[0].

    Fail-closed: Ist eine Liga konfiguriert, aber im Account nicht vorhanden
    (z. B. Tippfehler in KICKBASE_LEAGUE_ID), wird hart abgebrochen. Ein
    stiller Fallback auf leagues[0] würde sämtliche Reads UND Writes in eine
    falsche Liga schicken — der Account enthält fast gleichnamige Ligen.
    Ohne konfigurierte Liga bleibt die erste Liga erlaubt, aber mit Warnung.
    """
    if not league_id:
        if leagues:
            logger.warning(
                "KICKBASE_LEAGUE_ID ist nicht gesetzt — nutze die erste Liga "
                "'%s' (%s). Bei mehreren Ligen bitte explizit konfigurieren.",
                leagues[0].name,
                leagues[0].id,
            )
        return leagues
    chosen = [lg for lg in leagues if lg.id == league_id]
    if not chosen:
        available = ", ".join(f"'{lg.name}' ({lg.id})" for lg in leagues) or "keine"
        raise LeagueNotFoundError(
            f"Konfigurierte Liga {league_id} (KICKBASE_LEAGUE_ID) ist im Account "
            f"nicht vorhanden. Verfügbare Ligen: {available}. Abbruch statt stillem "
            "Fallback, damit keine Aktion in der falschen Liga landet."
        )
    rest = [lg for lg in leagues if lg.id != league_id]
    return chosen + rest


async def authenticate() -> AuthResult:
    """Authentifiziert je nach konfiguriertem Modus (Token bevorzugt, sonst Passwort)."""
    settings = get_settings()
    if settings.kickbase_token:
        leagues = await fetch_leagues(settings.kickbase_token)
        return AuthResult(
            token=settings.kickbase_token,
            user_id=user_id_from_token(settings.kickbase_token),
            leagues=_prioritize(leagues, settings.kickbase_league_id),
        )
    if settings.kickbase_email and settings.kickbase_password:
        result = await login(settings.kickbase_email, settings.kickbase_password)
        leagues = result.leagues
        # Der Passwort-Login liefert nur die zuletzt aktive Liga in `srvl`. Ist die
        # gewünschte nicht dabei, die vollständige Liste über selection nachladen.
        want = settings.kickbase_league_id
        if want and not any(lg.id == want for lg in leagues):
            leagues = await fetch_leagues(result.token)
        return AuthResult(
            token=result.token,
            user_id=result.user_id,
            leagues=_prioritize(leagues, want),
        )
    raise KickbaseAuthError(
        "Keine Kickbase-Zugangsdaten konfiguriert. Entweder KICKBASE_TOKEN "
        "(+ KICKBASE_REFRESH_TOKEN) oder KICKBASE_EMAIL + KICKBASE_PASSWORD in api/.env setzen."
    )


async def fetch_leagues(token: str) -> list[League]:
    """Ligen des Accounts über den Access-Token abrufen (Token-Modus)."""
    settings = get_settings()
    url = settings.kickbase_base_url + endpoints.LEAGUES_SELECTION
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        if response.status_code == 401:
            raise KickbaseAuthError("Access-Token abgelehnt (401) beim Ligen-Abruf.")
        response.raise_for_status()
        payload = response.json()
    return _leagues_from_payload(payload)
