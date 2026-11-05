"""Öffentliche Artikel direkt abrufen und lokal auslesen, ohne API-Anbieter."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib.util import find_spec
from time import monotonic

import httpx
from agent.web_search_provider import WebSearchProvider
from tools.url_safety import (
    create_ssrf_safe_client,
    is_safe_url,
    redirect_target_from_response,
    sensitive_query_param_name,
)
from tools.website_policy import check_website_access

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
MAX_SECONDS = 40
CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})


def _validate_target(url: str) -> None:
    if sensitive_query_param_name(url):
        raise ValueError("Blocked: credential-like query parameter")
    if not is_safe_url(url):
        raise ValueError("Blocked: private or internal URL")
    blocked = check_website_access(url)
    if blocked:
        raise ValueError(blocked["message"])


def _read_article(url: str) -> tuple[str, str, str]:
    """Größe und Zeit begrenzen; jedes Umleitungsziel und die echte Verbindung prüfen."""
    deadline = monotonic() + MAX_SECONDS
    target = url
    with create_ssrf_safe_client(
        timeout=httpx.Timeout(15, connect=10),
        follow_redirects=False,
        trust_env=False,
        headers={
            "User-Agent": "Hermes-DirectArticleReader/1.0",
            "Accept": "text/html,text/plain",
            # Hermes' brotlicffi 1.2.0.1 scheitert an manchen echten HTTP-Streams.
            # Nur Standarddecoder aushandeln; kein globaler Dependency-Umbau.
            "Accept-Encoding": "gzip, deflate",
        },
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _validate_target(target)
            if monotonic() >= deadline:
                raise ValueError("Article request exceeded its time budget")
            with client.stream("GET", target) as response:
                redirect = redirect_target_from_response(response)
                if redirect:
                    target = redirect
                    continue
                response.raise_for_status()
                coding = response.headers.get("content-encoding", "identity").strip().lower()
                if coding not in {"", "identity", "gzip", "deflate"}:
                    # Missachtet der Server die Aushandlung, bleibt dies ein Quellenfehler.
                    raise ValueError(f"Unsupported article content encoding: {coding}")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type not in CONTENT_TYPES:
                    raise ValueError(
                        f"Unsupported article content type: {content_type or 'missing'}"
                    )
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("Article exceeds the 2 MB extraction limit")
                    if monotonic() >= deadline:
                        raise ValueError("Article request exceeded its time budget")
                    chunks.append(chunk)
                raw = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                return raw, target, content_type
    raise ValueError("Too many article redirects")


class DirectHTTPProvider(WebSearchProvider):
    @property
    def name(self):
        return "direct-http"

    @property
    def display_name(self):
        return "Direct HTTP articles"

    def is_available(self):
        return find_spec("trafilatura") is not None

    def supports_search(self):
        return False

    def supports_extract(self):
        return True

    def extract(self, urls, **kwargs):
        results = []
        for url in urls:
            result = {"url": url, "title": "", "content": "", "raw_content": ""}
            try:
                from trafilatura import extract, extract_metadata

                raw, final_url, content_type = _read_article(url)
                if content_type == "text/plain":
                    content, title, published = raw.strip(), "", None
                else:
                    content = (
                        extract(
                            raw,
                            url=final_url,
                            include_comments=False,
                            include_tables=False,
                            favor_precision=True,
                        )
                        or ""
                    )
                    metadata = extract_metadata(raw, default_url=final_url)
                    title = (metadata.title or "") if metadata else ""
                    published = metadata.date if metadata else None
                blocked_titles = (
                    "just a moment",
                    "access denied",
                    "verify you are human",
                    "captcha",
                )
                if len(content.strip()) < 120 or any(x in title.lower() for x in blocked_titles):
                    raise ValueError(
                        "No readable article; possibly blocked, paywalled or JavaScript-only"
                    )
                result.update(
                    title=title,
                    content=content,
                    raw_content=content,
                    metadata={
                        "sourceURL": final_url,
                        "title": title,
                        "provider": self.name,
                        "fetched_at": datetime.now(UTC).isoformat(),
                        "published_at": published,
                        "extraction": "trafilatura-local",
                    },
                )
            except (httpx.HTTPError, ValueError, ImportError) as exc:
                result["error"] = str(exc)
            results.append(result)
        return results
