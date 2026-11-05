"""Insider-Quellen: Kickbase-YouTube-Kanäle (RSS) + r/Kickbase (JSON) — ohne API-Keys.

Liefert der Morgen-Recherche, was die Profi-Szene gerade empfiehlt („Geheimtipps
Spieltag X", „diese Spieler JETZT kaufen"). Titel reichen als Signal — Hype in
Titeln = kommende Community-Nachfrage = Kickbase-MW-Treiber. Best-effort: jede
Quelle darf ausfallen, nie ein Crash.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import feedparser
import httpx

from kickbase_agent.config import get_settings

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh) kba-research/1.0"}


@dataclass(frozen=True)
class CommunityItem:
    source: str    # z. B. "yt:Kickbase Guide" oder "r/Kickbase"
    title: str
    url: str = ""


async def _resolve_channel_id(client: httpx.AsyncClient, handle_or_id: str) -> str | None:
    """@handle → UC-Kanal-ID (aus dem Seiten-HTML); UC… geht direkt durch."""
    ident = handle_or_id.strip()
    if ident.startswith("UC"):
        return ident
    handle = "@" + ident.lstrip("@")
    try:
        resp = await client.get(f"https://www.youtube.com/{handle}")
        m = re.search(r'"channelId":"(UC[^"]+)"', resp.text)
        return m.group(1) if m else None
    except httpx.HTTPError:
        return None


async def fetch_youtube(limit_per_channel: int = 4) -> list[CommunityItem]:
    channels = [c for c in get_settings().community_youtube_channels.split(",") if c.strip()]
    items: list[CommunityItem] = []
    async with httpx.AsyncClient(timeout=12.0, headers=_UA, follow_redirects=True) as client:
        for raw in channels:
            cid = await _resolve_channel_id(client, raw)
            if not cid:
                continue
            try:
                resp = await client.get(
                    f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
                )
                feed = feedparser.parse(resp.content)
                channel_title = feed.feed.get("title", raw)[:30]
                for e in feed.entries[:limit_per_channel]:
                    items.append(CommunityItem(
                        source=f"yt:{channel_title}",
                        title=e.get("title", ""),
                        url=e.get("link", ""),
                    ))
            except httpx.HTTPError:
                continue
    return items


async def fetch_reddit(limit: int = 8) -> list[CommunityItem]:
    try:
        async with httpx.AsyncClient(timeout=12.0, headers=_UA) as client:
            resp = await client.get(
                f"https://www.reddit.com/r/Kickbase/hot.json?limit={limit}"
            )
            resp.raise_for_status()
            posts = resp.json().get("data", {}).get("children", [])
    except (httpx.HTTPError, ValueError):
        return []
    return [
        CommunityItem(
            source="r/Kickbase",
            title=p.get("data", {}).get("title", ""),
            url="https://reddit.com" + p.get("data", {}).get("permalink", ""),
        )
        for p in posts
        if p.get("data", {}).get("title")
    ]


async def fetch_community() -> list[CommunityItem]:
    """YouTube + Reddit gebündelt (jede Quelle best-effort)."""
    items = await fetch_youtube()
    items += await fetch_reddit()
    return items
