"""LigaInsider-Scraper: 1 Ausfall-Seite + 18 Startelf-Seiten = ganze Liga.

Scraping-Hygiene (Emils Regel 4): kompakte Übersichtsseiten statt 500 Profile,
echter Browser-User-Agent, ~1,4 s Abstand (RateLimiter), In-Process-TTL, damit ein
Briefing-Lauf sich EINEN Snapshot teilt. Degraded-Mode: fällt eine Seite aus (Fehler
oder Cloudflare-Wall „Just a moment"), wird sie übersprungen und gemeldet — nie ein
Crash, nie ein Falsch-Veto (fehlende Ausfall-Daten = kein Veto).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from kickbase_agent.config import get_settings
from kickbase_agent.kickbase.client import RateLimiter
from kickbase_agent.scouts.ligainsider.models import InjuryEntry, LineupEntry
from kickbase_agent.scouts.ligainsider.parser import parse_injuries, parse_lineup
from kickbase_agent.scouts.ligainsider.teams import TEAMS

_INJURY_PATH = "/bundesliga/verletzte-und-gesperrte-spieler/"
_BLOCK_MARKERS = ("just a moment", "cf-browser-verification", "attention required")


@dataclass
class ScoutSnapshot:
    lineups: list[LineupEntry] = field(default_factory=list)
    injuries: list[InjuryEntry] = field(default_factory=list)
    scraped_tids: set[str] = field(default_factory=set)  # Teams mit gültiger Startelf
    degraded: list[str] = field(default_factory=list)     # ausgefallene Seiten
    fetched_at: float = 0.0


_cache: ScoutSnapshot | None = None


def _looks_blocked(html: str) -> bool:
    head = html[:2000].lower()
    return any(m in head for m in _BLOCK_MARKERS)


async def _get(client: httpx.AsyncClient, limiter: RateLimiter, path: str) -> str | None:
    await limiter.acquire()
    try:
        resp = await client.get(path)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200 or _looks_blocked(resp.text):
        return None
    return resp.text


async def fetch_snapshot(*, force: bool = False) -> ScoutSnapshot:
    """Liga-weiter Snapshot (Startelf + Ausfälle). Nutzt TTL-Cache, außer force=True."""
    global _cache
    settings = get_settings()
    if (
        not force
        and _cache is not None
        and (time.monotonic() - _cache.fetched_at) < settings.ligainsider_ttl_seconds
    ):
        return _cache

    limiter = RateLimiter(settings.ligainsider_rate_limit_per_second)
    snap = ScoutSnapshot(fetched_at=time.monotonic())
    async with httpx.AsyncClient(
        base_url=settings.ligainsider_base_url,
        headers={"User-Agent": settings.ligainsider_user_agent},
        timeout=httpx.Timeout(15.0),
        follow_redirects=True,
    ) as client:
        # 1) Zentrale Ausfall-Seite (1 Request → ganze Liga).
        injury_html = await _get(client, limiter, _INJURY_PATH)
        if injury_html is None:
            snap.degraded.append("ausfaelle")
        else:
            snap.injuries = parse_injuries(injury_html)
            if not snap.injuries and len(injury_html) > 1000:
                snap.degraded.append("ausfaelle_empty_parse")
                if _cache is not None and _cache.injuries:
                    snap.injuries = list(_cache.injuries)

        # 2) Startelf je Team.
        for team in TEAMS.values():
            html = await _get(client, limiter, team.lineup_path)
            if html is None:
                snap.degraded.append(team.slug)
                continue
            entries = parse_lineup(html, team.tid)
            if entries:
                snap.lineups.extend(entries)
                snap.scraped_tids.add(team.tid)

    _cache = snap
    return snap


def clear_cache() -> None:
    global _cache
    _cache = None
