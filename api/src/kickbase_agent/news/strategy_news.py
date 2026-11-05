"""Begrenzter RSS-Abruf mit Herkunft, Alter und konservativer Spielerzuordnung."""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import feedparser
import httpx
from bs4 import BeautifulSoup

FEEDS = (
    ("kicker", "kicker", "https://newsfeed.kicker.de/news/bundesliga"),
    ("sportschau", "Sportschau", "https://www.sportschau.de/fussball/bundesliga/index~rss2.xml"),
    ("geissblog", "GEISSBLOG", "https://geissblog.koeln/feed/"),
)
CACHE_TTL_SECONDS = 1200
MAX_FEED_BYTES = 1_000_000
_MEMORY_CACHE: dict | None = None
_AMBIGUOUS = {"muller", "schmidt", "schmid", "klein", "lang", "braun", "brown", "kim",
              "lee", "jung", "young", "can", "koch", "stark", "gross", "neuer"}
# Identitäten stammen aus dem Liga-Audit und den geprüften Spielerprofilen.
_PLAYER_NAMES = {
    "4333": "Lukas Ullrich", "2855": "Ragnar Ache", "1580": "Marvin Schwäbe",
    "6668": "Minjae Kim", "3081": "Gabriel Vidović", "8329": "Michael Olise",
    "7320": "Tjark Scheller", "4406": "Junior Dina Ebimbe",
}


def _plain(value: object, limit: int) -> str:
    soup = BeautifulSoup(str(value or "")[:20_000], "html.parser")
    for tag in soup(["script", "style", "iframe", "object", "svg"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())[:limit]


def _normalized(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(re.findall(r"[^\W_]+", "".join(
        c for c in decomposed if not unicodedata.combining(c)
    )))


def _https(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return bool(parsed.scheme == "https" and parsed.hostname and not parsed.username
                    and not parsed.password and not any(ord(c) < 33 for c in value))
    except ValueError:
        return False


def _date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    if parsed.tzinfo is None:
        return None  # Ohne Zeitzone keine erfundene Veröffentlichung in UTC.
    return parsed.astimezone(UTC)


def _aliases(player: dict, surnames: dict[str, set[str]]) -> list[str]:
    pid = str(player.get("pid", player.get("player_id", player.get("id", ""))))
    names = [str(player.get("name", "")), _PLAYER_NAMES.get(pid, "")]
    aliases = set()
    for name in names:
        normalized = _normalized(name)
        parts = normalized.split()
        if not parts:
            continue
        last = parts[-1]
        if len(parts) > 1:
            aliases.add(normalized)
        if len(last) >= 5 and last not in _AMBIGUOUS and len(surnames.get(last, set())) <= 1:
            aliases.add(last)
    return sorted(aliases)


def _parse_feed(raw: bytes, source_id: str, fetched_at: str) -> list[dict]:
    # Keine DTD/Entities: RSS braucht sie nicht, und Feedtexte sind nicht vertrauenswürdig.
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("XML-Deklaration nicht erlaubt")
    parsed = feedparser.parse(raw)
    if not parsed.version or (parsed.bozo and not parsed.entries):
        raise ValueError("Ungültiger RSS-Feed")
    items = []
    seen = set()
    for entry in parsed.entries[:250]:
        url = str(entry.get("link", "")).strip()
        title = _plain(entry.get("title"), 200)
        if not title or not _https(url) or url in seen:
            continue
        seen.add(url)
        published = _date(entry.get("published"))
        items.append({
            "source_id": source_id, "title": title, "url": url,
            "description": _plain(entry.get("summary"), 1000),
            "published_at": published.isoformat() if published else None,
            "fetched_at": fetched_at,
        })
    return items


async def _fetch_feed(client: httpx.AsyncClient, feed: tuple, now: str) -> tuple:
    ident, name, url = feed
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        if int(response.headers.get("content-length", "0")) > MAX_FEED_BYTES:
            raise ValueError("RSS-Antwort zu groß")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_FEED_BYTES:
                raise ValueError("RSS-Antwort zu groß")
        items = _parse_feed(bytes(body), ident, now)
        source = {
            "id": ident, "name": name, "url": url, "status": "ok", "fetched_at": now,
            "item_count": len(items), "etag": response.headers.get("etag"),
            "last_modified": response.headers.get("last-modified"),
        }
        return items, source


def _read_cache(path: Path | None) -> dict | None:
    if path is None:
        return _MEMORY_CACHE
    try:
        if path.stat().st_size > 4 * MAX_FEED_BYTES:
            return None
        value = json.loads(path.read_text())
        if (isinstance(value, dict) and value.get("version") == 1
                and isinstance(value.get("items"), list)
                and isinstance(value.get("sources"), list)):
            return value
    except (OSError, ValueError):
        pass
    return None


def _write_cache(path: Path | None, value: dict) -> None:
    global _MEMORY_CACHE
    if path is None:
        _MEMORY_CACHE = value
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False))
    temporary.replace(path)


def _for_players(items: list[dict], players: list[dict], sources: list[dict]) -> list[dict]:
    names = {s["id"]: s["name"] for s in sources}
    surname_ids: dict[str, set[str]] = {}
    for player in players:
        pid = str(player.get("pid", player.get("player_id", player.get("id", ""))))
        name = _normalized(_PLAYER_NAMES.get(pid, str(player.get("name", ""))))
        if name:
            surname_ids.setdefault(name.split()[-1], set()).add(pid)
    articles = []
    for player in players:
        pid = str(player.get("pid", player.get("player_id", player.get("id", ""))))
        aliases = _aliases(player, surname_ids)
        if not pid or not aliases:
            continue
        matches = []
        seen = set()
        for item in items:
            haystack = " " + _normalized(item["title"] + " " + item["description"]) + " "
            if item["url"] in seen or not any(f" {alias} " in haystack for alias in aliases):
                continue
            seen.add(item["url"])
            published = _date(item["published_at"])
            # Aus RSS entsteht kein Fitnessstatus und keine behauptete Gebotswirkung.
            matches.append({
                "player_id": pid, "player_name": _PLAYER_NAMES.get(pid, player.get("name", "")),
                "title": item["title"], "url": item["url"],
                "source": names.get(item["source_id"], item["source_id"]),
                "published_at": item["published_at"], "fetched_at": item["fetched_at"],
                "event_type": "news_mention",
                "claim": "Spielername im RSS-Titel oder Anreißer gefunden; Artikelinhalt prüfen.",
                "implication": "Vor Entscheidungen die Originalmeldung und deren Anlass prüfen.",
                "uncertainty": "Automatischer Namenstreffer, keine bestätigte Einsatzprognose."
                    + (" Veröffentlichungszeit fehlt." if not published else ""),
                "expires_at": (published + timedelta(hours=48)).isoformat() if published else None,
            })
        matches.sort(key=lambda a: a["published_at"] or "", reverse=True)
        articles.extend(matches[:12])
    return articles


async def fetch_strategy_news(*, players: list[dict], cache_path: Path | None = None) -> dict:
    """Drei öffentliche Feeds lesen; Ausfälle und veraltete Inhalte sichtbar lassen."""
    now = datetime.now(UTC)
    cached = _read_cache(cache_path)
    cache_date = _date(cached.get("generated_at")) if cached else None
    hit = bool(cache_date and 0 <= (now - cache_date).total_seconds() < CACHE_TTL_SECONDS)
    if hit:
        bundle = cached
    else:
        items, sources, errors = [], [], []
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, headers={
            "User-Agent": "KickbaseAgent/1.0 (RSS research)",
            "Accept": "application/rss+xml, application/xml, text/xml",
        }) as client:
            results = await asyncio.gather(*(
                asyncio.wait_for(_fetch_feed(client, feed, now.isoformat()), timeout=12)
                for feed in FEEDS
            ), return_exceptions=True)
        for feed, result in zip(FEEDS, results, strict=True):
            ident, name, url = feed
            if isinstance(result, Exception):
                errors.append({"source": ident, "error": type(result).__name__})
                old = [i for i in (cached or {}).get("items", []) if i["source_id"] == ident]
                items.extend(old)
                old_source = next((s for s in (cached or {}).get("sources", [])
                                   if s["id"] == ident), {})
                sources.append({"id": ident, "name": name, "url": url,
                                "status": "stale" if old else "error", "item_count": len(old),
                                "fetched_at": old_source.get("fetched_at")})
            else:
                new_items, source = result
                items.extend(new_items)
                sources.append(source)
        bundle = {"version": 1, "generated_at": now.isoformat(), "items": items,
                  "sources": sources, "errors": errors}
        try:
            _write_cache(cache_path, bundle)
        except OSError:
            bundle["errors"].append({"source": "cache", "error": "write_failed"})
    return {
        "articles": _for_players(bundle["items"], players, bundle["sources"]),
        "sources": bundle["sources"], "errors": bundle.get("errors", []),
        "generated_at": bundle["generated_at"], "cache_hit": hit,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
    }
