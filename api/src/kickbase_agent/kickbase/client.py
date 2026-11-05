"""Async HTTP client for the Kickbase API with rate limiting."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from kickbase_agent.config import get_settings
from kickbase_agent.kickbase import endpoints
from kickbase_agent.kickbase.errors import KickbaseAuthError, KickbaseDataError
from kickbase_agent.kickbase.models import (
    Fixture,
    MarketPlayer,
    MarketValueSnapshot,
    MatchdayPerformance,
    Player,
    PlayerDetail,
    RankingEntry,
    SquadPlayer,
    TeamProfile,
)


class RateLimiter:
    """Async minimum-interval rate limiter."""

    def __init__(self, per_second: float) -> None:
        self._min_interval = 1.0 / per_second if per_second > 0 else 0.0
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


class KickbaseClient:
    def __init__(
        self,
        token: str,
        base_url: str | None = None,
        rate_limit_per_second: float | None = None,
        on_auth_failure: Callable[[], Awaitable[str]] | None = None,
        retry_delays_429: tuple[float, ...] = (2.0, 5.0, 13.0),
    ) -> None:
        settings = get_settings()
        self._base_url = base_url or settings.kickbase_base_url
        self._token = token
        self._on_auth_failure = on_auth_failure
        self._retry_delays_429 = retry_delays_429
        self._limiter = RateLimiter(
            rate_limit_per_second
            if rate_limit_per_second is not None
            else settings.kickbase_rate_limit_per_second
        )
        self._http: httpx.AsyncClient | None = None

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=10.0,
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            )
        return self._http

    async def aclose(self) -> None:
        """Schließt den persistenten HTTP-Connection-Pool."""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
        self._http = None

    async def __aenter__(self) -> KickbaseClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}"}
        ua = get_settings().kickbase_user_agent
        if ua:
            headers["User-Agent"] = ua
        return headers

    async def _request(self, method: str, path: str, json: Any | None = None) -> dict[str, Any]:
        auth_retry_left = self._on_auth_failure is not None
        delays = iter(self._retry_delays_429)
        while True:
            await self._limiter.acquire()
            client = self._get_http_client()
            response = await client.request(
                method, self._base_url + path, headers=self._headers(), json=json
            )
            if response.status_code in (401, 403) and auth_retry_left:
                # Ein abgelaufenes Token beantwortet Kickbase mit 403, nicht 401 (Box-Ausfall
                # 12.10.2026 nach genau 7 Tagen Laufzeit). Einmal neu anmelden und wiederholen;
                # bleibt es 403, gilt der Fehler wie bisher (raise_for_status unten).
                auth_retry_left = False
                assert self._on_auth_failure is not None
                try:
                    self._token = await self._on_auth_failure()
                except Exception as relogin_error:
                    # Die Ablehnung steht fest; ein gescheiterter Relogin (etwa ein Timeout)
                    # darf sie nicht verdecken — sonst hielte der Executor ein abgelehntes
                    # Gebot für „vielleicht ausgeführt“ (Codex-Review 12.10.2026).
                    if response.status_code == 401:
                        raise KickbaseAuthError(
                            f"Kickbase-Token abgelehnt (401) für {method} {path} — "
                            "Relogin scheiterte."
                        ) from relogin_error
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as rejected:
                        raise rejected from relogin_error
                    raise
                continue
            if response.status_code == 401:
                raise KickbaseAuthError(
                    f"Kickbase-Token abgelehnt (401) für {method} {path} — Relogin half nicht."
                )
            if response.status_code == 429:
                delay = next(delays, None)
                if delay is None:
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After")
                await asyncio.sleep(float(retry_after) if retry_after else delay)
                continue
            response.raise_for_status()
            return response.json()

    async def _get(self, path: str) -> dict[str, Any]:
        return await self._request("GET", path)

    async def _post(self, path: str, json: Any | None = None) -> dict[str, Any]:
        return await self._request("POST", path, json=json)

    async def _delete(self, path: str) -> dict[str, Any]:
        return await self._request("DELETE", path)

    async def get_squad(self, league_id: str) -> list[SquadPlayer]:
        data = await self._get(endpoints.squad(league_id))
        return [SquadPlayer.from_api(raw) for raw in data.get("it", [])]

    async def get_manager_squad(self, league_id: str, user_id: str) -> dict[str, Any]:
        """Kader eines Mitspielers — ROH, weil die Antwort andere Felder traegt
        als der eigene Kader (kein `prob`, dafuer `mvgl`, `sdmvt`, `tfhmvt`).

        Ein eigenes Modell waere hier Scheingenauigkeit: Die Auswertung braucht
        genau die Felder, die drin stehen, und der Aufrufer sieht sie so, wie
        Kickbase sie liefert. Rohantwort geprobt am 28.08.2026.
        """
        return await self._get(endpoints.manager_squad(league_id, user_id))

    async def get_manager_dashboard(self, league_id: str, user_id: str) -> dict[str, Any]:
        """Kopfzahlen eines Mitspielers (Teamwert, Punkte, Platz, Gewinn)."""
        return await self._get(endpoints.manager_dashboard(league_id, user_id))

    async def get_league_overview(self, league_id: str) -> dict[str, Any]:
        """Ligakonfiguration, insbesondere Startbudget und datierte Resetgrenze."""
        return await self._get(endpoints.league_overview(league_id))

    async def get_manager_transfers(
        self, league_id: str, user_id: str, start: int = 0,
    ) -> dict[str, Any]:
        """Eine echte Historienseite (25 Zeilen); tty=1 Kauf, tty=2 Verkauf.

        Live mit beiden Managerseiten und Activity-Feed abgeglichen am 12.09.2026.
        Leere Seiten beenden die Pagination; Preise sind keine Kontostände.
        """
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise ValueError("start muss ein nichtnegativer ganzzahliger Offset sein")
        return await self._get(
            f"/v4/leagues/{league_id}/managers/{user_id}/transfer?start={start}"
        )

    async def get_manager_performance(self, league_id: str, user_id: str) -> dict[str, Any]:
        """Datierte Spieltage je sid/sn; cur ist auch in Vorsaisons wahr."""
        return await self._get(f"/v4/leagues/{league_id}/managers/{user_id}/performance")

    async def get_market(self, league_id: str) -> list[MarketPlayer]:
        data = await self._get(endpoints.market(league_id))
        return [MarketPlayer.from_api(raw) for raw in data.get("it", [])]

    async def get_player(self, league_id: str, player_id: str) -> Player:
        data = await self._get(endpoints.player_detail(league_id, player_id))
        return Player.from_api(data)

    async def get_team_profile(self, team_id: str, competition_id: str = "1") -> TeamProfile:
        """Alle Spieler eines Clubs inkl. Startelf-Stufe, Ø-Punkte, 7-Tage-MW-Trend."""
        data = await self._get(endpoints.team_profile(team_id, competition_id))
        return TeamProfile.from_api(data)

    async def get_player_detail(self, league_id: str, player_id: str) -> PlayerDetail:
        """Spieler-Detail mit Member-Stats (Punkte, Ø, Tore, Vorlagen, Einsätze, S11,
        Minuten, Karten, Fitness, Startelf-Stufe, Formleiste, Spieltags-Übersicht) —
        Feld-Semantik s. PlayerDetail. Ein Call je Spieler (Rate-Limiter greift)."""
        data = await self._get(endpoints.player_detail(league_id, player_id))
        return PlayerDetail.from_api(data)

    async def get_player_mv_history(
        self, league_id: str, player_id: str, timeframe: int = 92
    ) -> list[MarketValueSnapshot]:
        path = endpoints.player_market_value_history(league_id, player_id) + f"/{timeframe}"
        data = await self._get(path)
        return [MarketValueSnapshot.from_api(raw) for raw in data.get("it", [])]

    async def get_budget(self, league_id: str) -> int:
        """Aktueller Kontostand (Feld `b` aus /me/budget).

        Fehlt `b` oder ist der Payload leer/malformiert, fliegt KickbaseDataError
        statt eines stillen `0` — „Konto 0 €" wäre für die T-15-Notbremse ein
        Verkaufsbefehl, obwohl der echte Zustand schlicht UNBEKANNT ist.
        """
        data = await self._get(endpoints.league_budget(league_id))
        if not isinstance(data, dict) or "b" not in data:
            found = ", ".join(sorted(data)) if isinstance(data, dict) else type(data).__name__
            raise KickbaseDataError(
                "/me/budget ohne Kontostand-Feld 'b' — Zustand unbekannt "
                f"(Payload-Keys: {found or 'leer'})."
            )
        try:
            return int(data["b"])
        except (TypeError, ValueError) as exc:
            raise KickbaseDataError(
                f"/me/budget: Kontostand 'b' ist nicht numerisch ({data['b']!r})."
            ) from exc

    async def get_budget_fields(self, league_id: str) -> dict[str, Any]:
        """Rohe Budget-Felder aus /me/budget: `b` Konto, `pbaa` Konto nach Annahme
        aller offenen Gebote (Live-Fund 23.08.: b − pbaa = Summe eigener Gebote),
        `pbas`. Für die 33-%-Prüfung im Executor-Preflight."""
        data = await self._get(endpoints.league_budget(league_id))
        return data if isinstance(data, dict) else {}

    async def get_ranking(self, league_id: str) -> list[RankingEntry]:
        """Liga-Tabelle: alle Manager mit Platz, Punkten, Teamwert."""
        data = await self._get(endpoints.league_ranking(league_id))
        return [RankingEntry.from_api(u) for u in data.get("us", [])]

    async def get_player_performance(
        self, league_id: str, player_id: str
    ) -> list[MatchdayPerformance]:
        """Bundesliga-Spieltagspunkte über alle Saisons, chronologisch aufsteigend.

        Nur Bundesliga zählt für Kickbase-Punkte — andere Wettbewerbe (Pokal, Europa)
        geben keine Kickbase-Punkte und kollidieren im (Saison, Spieltag)-Schlüssel.
        """
        data = await self._get(endpoints.player_performance(league_id, player_id))
        out: list[MatchdayPerformance] = []
        for season in data.get("it", []):
            if season.get("n") != "Bundesliga":
                continue
            season_name = str(season.get("ti", ""))
            for entry in season.get("ph", []):
                out.append(MatchdayPerformance.from_api(season_name, entry))
        out.sort(key=lambda p: (p.match_date is None, p.match_date))
        return out

    async def get_lineup(self, league_id: str) -> dict[str, Any]:
        return await self._get(endpoints.lineup(league_id))

    # --- Schreib-Operationen (Phase 1) ---
    # Response-Schemas sind unterdokumentiert → rohes dict; typisierte Modelle folgen,
    # sobald echte Antworten in der Test-Liga aufgezeichnet sind.

    async def place_bid(self, league_id: str, player_id: str, price: int) -> dict[str, Any]:
        return await self._post(
            endpoints.market_offers(league_id, player_id), json={"price": price}
        )

    async def cancel_bid(self, league_id: str, player_id: str, offer_id: str) -> dict[str, Any]:
        return await self._delete(endpoints.market_offer(league_id, player_id, offer_id))

    async def accept_offer(
        self, league_id: str, player_id: str, offer_id: str
    ) -> dict[str, Any]:
        return await self._post(endpoints.market_offer_accept(league_id, player_id, offer_id))

    async def decline_offer(
        self, league_id: str, player_id: str, offer_id: str
    ) -> dict[str, Any]:
        return await self._post(endpoints.market_offer_decline(league_id, player_id, offer_id))

    async def list_player(self, league_id: str, player_id: str, price: int) -> dict[str, Any]:
        return await self._post(
            endpoints.market(league_id), json={"pi": player_id, "prc": price}
        )

    async def delist_player(self, league_id: str, player_id: str) -> dict[str, Any]:
        return await self._delete(endpoints.market_delist(league_id, player_id))

    async def sell_to_kickbase(self, league_id: str, player_id: str) -> dict[str, Any]:
        return await self._post(endpoints.player_sell(league_id, player_id))

    async def set_lineup(
        self, league_id: str, formation: str, player_ids: list[str | None]
    ) -> dict[str, Any]:
        # None = leerer Platz einer Teil-Elf (L-109); Wirkung belegt der Aufrufer
        # über die Rücklese von `lo`, nicht über den HTTP-Status.
        return await self._post(
            endpoints.lineup(league_id), json={"type": formation, "players": player_ids}
        )

    async def collect_bonus(self) -> dict[str, Any]:
        return await self._get(endpoints.BONUS_COLLECT)

    async def get_league_rules(self, league_id: str) -> dict[str, Any]:
        """Liga-Regeln aus dem selection-Objekt (live geprobt, 20.08.2026).

        `lpc` = Spieler-Limit pro Bundesliga-Club (Liga-Einstellung 1-11;
        BASE steht auf 11 = praktisch kein Limit). Fallback konservativ 3,
        falls das Feld fehlt.
        """
        raw = await self._get("/v4/leagues/selection")
        entry = next(
            (e for e in raw.get("it", []) if str(e.get("i")) == str(league_id)), {}
        )
        return {
            "max_players_per_club": int(entry.get("lpc", 3)),
            "is_admin": bool(entry.get("adm", False)),
        }

    async def get_own_bids(self, league_id: str, user_id: str) -> list[dict[str, Any]]:
        """Eigene OFFENE Gebote aus dem Markt (`ofs`-Feld, live geprobt 20.08.).

        Je Eintrag: player_id, name, team_id, amount (uop), offer_id (uoid —
        Kickbase keyed Offers per User) und expires_in_s (exs, Listing-Restzeit).
        """
        raw = await self._get(endpoints.market(league_id))
        bids: list[dict[str, Any]] = []
        for it in raw.get("it", []):
            for offer in it.get("ofs", []) or []:
                if str(offer.get("u")) != str(user_id):
                    continue
                seller = (it.get("u") or {}).get("n") or ""
                bids.append({
                    "player_id": str(it.get("i")),
                    "name": f"{it.get('fn', '')} {it.get('n', '')}".strip(),
                    "team_id": str(it.get("tid", "")),
                    "amount": int(offer.get("uop", 0)),
                    "offer_id": str(offer.get("uoid", "")),
                    "expires_in_s": int(it.get("exs", 0) or 0),
                    # Manager-Listing: Verkäufer entscheidet MANUELL (kein
                    # Auto-Zuschlag); leer = Kickbase-Listing mit exs-Countdown.
                    "seller": str(seller).strip(),
                    "listing_price": int(it.get("prc", 0) or 0),
                    # Porträt-Pfad (pim) relativ zum CDN — leer, wenn Kickbase
                    # keins liefert (Anzeige fällt auf Initialen zurück).
                    "image_path": str(it.get("pim", "") or ""),
                })
        return bids

    async def get_activity_feed(self, league_id: str, max_items: int = 30) -> list[dict[str, Any]]:
        """Liga-Activity-Feed (Transfers t=15 mit byr/slr+trp, Markt-Events t=3)."""
        raw = await self._get(f"/v4/leagues/{league_id}/activitiesFeed?max={max_items}")
        return list(raw.get("af", []))

    async def get_matchdays(
        self, competition_id: str = endpoints.BUNDESLIGA_COMPETITION_ID
    ) -> list[Fixture]:
        """Alle Spiele der Saison (mit Wettquoten), flach über alle Spieltage."""
        data = await self._get(endpoints.competition_matchdays(competition_id))
        fixtures: list[Fixture] = []
        for matchday in data.get("it", []):
            for game in matchday.get("it", []):
                fixtures.append(Fixture.from_api(game))
        return fixtures

    async def get_team_names(
        self, competition_id: str = endpoints.BUNDESLIGA_COMPETITION_ID
    ) -> dict[str, str]:
        """tid → Team-Name aus der Ligatabelle."""
        data = await self._get(endpoints.competition_table(competition_id))
        return {
            str(t["tid"]): str(t.get("tn", ""))
            for t in data.get("it", [])
            if "tid" in t
        }
