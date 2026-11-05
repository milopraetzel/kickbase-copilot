import asyncio
import gzip
import importlib
import json
import os
import shutil
import sys
import tempfile
import zlib
from pathlib import Path

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = Path(os.environ.get("HERMES_SOURCE_DIR", ROOT.parent / "hermes-heartbeat" / "repo"))
sys.path.insert(0, str(REPO))
PLUGIN_ROOT = ROOT / ("plugin" if (ROOT / "plugin").exists() else "plugins")
sys.path.insert(0, str(PLUGIN_ROOT))
os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="hermes-web-test-")

provider = importlib.import_module("web_direct_http.provider")

ARTICLE = """<html><head><title>Training am Sonntag</title></head><body>
<nav>Navigation und Werbung</nav><article><h1>Training am Sonntag</h1>
<p>Die Mannschaft trainiert am Sonntag auf dem Vereinsgelände. Der Trainer berichtet,
dass mehrere Spieler wieder am Mannschaftstraining teilnehmen konnten. Über ihren
Einsatz am kommenden Spieltag wird erst nach der nächsten Einheit entschieden.</p>
<p>Der Verein veröffentlicht die nächste Pressekonferenz am Donnerstag. Bis dahin
bleibt die Startaufstellung offen und eine Teilnahme am Training bestätigt noch
keinen Einsatz in der Startelf.</p></article></body></html>"""


class ResponseChunks(httpx.SyncByteStream):
    """Echte HTTPX-Dekodierung auf mehreren empfangenen Transportblöcken."""

    def __init__(self, content, size=16_384):
        self.content, self.size = content, size

    def __iter__(self):
        for offset in range(0, len(self.content), self.size):
            yield self.content[offset:offset + self.size]


def mock_article_server(monkeypatch, respond):
    monkeypatch.setattr(
        provider, "create_ssrf_safe_client",
        lambda **kwargs: httpx.Client(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(provider, "is_safe_url", lambda url: True)
    monkeypatch.setattr(provider, "check_website_access", lambda url: None)


def test_real_brotli_stream_negotiates_readable_response(monkeypatch):
    """Mit Hermes' brotlicffi 1.2.0.1 vor dem Fix exakt der Produktionsfehler."""
    brotlicffi = pytest.importorskip("brotlicffi")
    import httpx._decoders as decoders

    monkeypatch.setattr(decoders, "brotli", brotlicffi)
    monkeypatch.setitem(decoders.SUPPORTED_DECODERS, "br", decoders.BrotliDecoder)
    compressed = (ROOT / "tests/fixtures/bundesliga-matchday.html.br").read_bytes()
    expected = brotlicffi.decompress(compressed)
    requests = []

    def respond(request):
        requests.append(request)
        accepts = request.headers.get("accept-encoding", "")
        coding = "br" if "br" in accepts else "gzip"
        body = compressed if coding == "br" else gzip.compress(expected)
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8", "content-encoding": coding},
            stream=ResponseChunks(body),
        )

    mock_article_server(monkeypatch, respond)
    raw, _, _ = provider._read_article("https://www.bundesliga.com/de/bundesliga/spieltag/2026-2027/4")
    assert raw == expected.decode("utf-8")
    assert len(requests) == 1


@pytest.mark.parametrize("coding", ["gzip", "deflate", "identity"])
def test_negotiated_compression_preserves_article(monkeypatch, coding):
    def respond(request):
        assert set(request.headers["accept-encoding"].replace(" ", "").split(",")) == {
            "gzip", "deflate",
        }
        raw = ARTICLE.encode()
        body = gzip.compress(raw) if coding == "gzip" else (
            zlib.compress(raw) if coding == "deflate" else raw
        )
        return httpx.Response(
            200, headers={"content-type": "text/html", "content-encoding": coding},
            stream=ResponseChunks(body, size=31),
        )

    mock_article_server(monkeypatch, respond)
    result = provider.DirectHTTPProvider().extract(["https://public.example/article"])[0]
    assert not result.get("error")
    assert "keinen Einsatz in der Startelf" in result["content"]


@pytest.mark.parametrize("coding", ["br", "zstd", "gzip, br"])
def test_server_ignoring_negotiation_is_an_explicit_error(monkeypatch, coding):
    def respond(request):
        return httpx.Response(
            200, headers={"content-type": "text/html", "content-encoding": coding},
            stream=ResponseChunks(b"not an accepted representation"),
        )

    mock_article_server(monkeypatch, respond)
    result = provider.DirectHTTPProvider().extract(["https://public.example/article"])[0]
    assert result["error"] == f"Unsupported article content encoding: {coding}"
    assert result["content"] == result["raw_content"] == ""


def test_compressed_article_still_obeys_decoded_size_limit(monkeypatch):
    def respond(request):
        return httpx.Response(
            200, headers={"content-type": "text/html", "content-encoding": "gzip"},
            stream=ResponseChunks(gzip.compress(b"x" * (provider.MAX_BYTES + 1))),
        )

    mock_article_server(monkeypatch, respond)
    result = provider.DirectHTTPProvider().extract(["https://public.example/article"])[0]
    assert result["error"] == "Article exceeds the 2 MB extraction limit"
    assert result["content"] == ""


@pytest.mark.parametrize(
    "case",
    ["article", "status", "mime", "too_large", "private_redirect", "policy_redirect", "challenge"],
)
def test_direct_fetch_content_or_explicit_error_without_vendor(monkeypatch, case):
    requests = []

    def respond(request):
        requests.append(str(request.url))
        if case == "private_redirect":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/internal"})
        if case == "policy_redirect":
            return httpx.Response(302, headers={"location": "https://blocked.example/article"})
        if case == "status":
            return httpx.Response(403)
        if case == "mime":
            return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")
        content = (
            ARTICLE
            if case != "challenge"
            else ARTICLE.replace("Training am Sonntag", "Just a moment")
        )
        if case == "too_large":
            content = "x" * (provider.MAX_BYTES + 1)
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text=content
        )

    monkeypatch.setattr(
        provider,
        "create_ssrf_safe_client",
        lambda **kw: httpx.Client(transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(provider, "is_safe_url", lambda url: "127.0.0.1" not in url)
    monkeypatch.setattr(
        provider,
        "check_website_access",
        lambda url: {"message": "Blocked by website policy"} if "blocked.example" in url else None,
    )
    result = provider.DirectHTTPProvider().extract(["https://public.example/article"])[0]
    assert requests == ["https://public.example/article"]
    if case == "article":
        assert "keinen Einsatz in der Startelf" in result["content"]
        assert "Navigation und Werbung" not in result["content"]
        assert result["metadata"]["provider"] == "direct-http"
        assert result["metadata"]["fetched_at"]
        assert "error" not in result
    else:
        assert result["error"]
        assert result["content"] == ""


@pytest.mark.parametrize("with_plugin", [True, False])
def test_real_profile_plugin_discovery_and_dispatch_never_rescue(
    monkeypatch, tmp_path, with_plugin
):
    """Echter Profil-Loader und Tool-Dispatch; Fehler dürfen keinen Anbieterwechsel auslösen."""
    profile = tmp_path / "profile"
    plugin_path = profile / "plugins" / "web" / "direct_http"
    profile.mkdir()
    if with_plugin:
        shutil.copytree(PLUGIN_ROOT / "web_direct_http", plugin_path)
    patch = json.loads((ROOT / "profile-web.patch.json").read_text())
    config = {
        "web": patch["web"],
        "plugins": {
            "enabled": patch["plugins_enabled_add"],
            "disabled": patch["plugins_disabled_add"],
        },
    }
    (profile / "config.yaml").write_text(yaml.safe_dump(config))
    monkeypatch.setenv("HERMES_HOME", str(profile))
    from hermes_cli.plugins import _ensure_plugins_discovered

    _ensure_plugins_discovered()
    from agent.web_search_registry import get_provider
    from plugins.web.ddgs.provider import DDGSWebSearchProvider
    from tools import web_tools, web_tools_rescue

    direct = get_provider("direct-http")
    assert web_tools._get_search_backend() == "ddgs"
    assert web_tools._get_extract_backend() == "direct-http"

    def forbidden(*args, **kwargs):
        raise AssertionError("A vendor fallback must never be invoked")

    monkeypatch.setattr(web_tools_rescue, "_rescue_search", forbidden)
    monkeypatch.setattr(web_tools_rescue, "_rescue_extract", forbidden)
    monkeypatch.setattr(web_tools, "_rescue_search", forbidden)
    from tools import web_tools_extract

    monkeypatch.setattr(web_tools_extract, "_rescue_extract", forbidden)
    monkeypatch.setattr(web_tools, "async_is_safe_url", _safe_url)
    if not with_plugin:
        assert direct is None
        result = json.loads(
            asyncio.run(web_tools.web_extract_tool(["https://www.example.com/article"]))
        )
        assert result.get("success") is False or result.get("error")
        assert "direct-http" in json.dumps(result)
        return
    assert direct is not None and direct.supports_extract() and not direct.supports_search()
    monkeypatch.setattr(
        DDGSWebSearchProvider,
        "search",
        lambda self, query, limit=5: {"success": False, "error": "DDGS unavailable"},
    )
    monkeypatch.setattr(
        type(direct),
        "extract",
        lambda self, urls, **kw: [{"url": u, "content": "", "error": "HTTP 403"} for u in urls],
    )
    search = json.loads(web_tools.web_search_tool("fresh football news"))
    assert search["success"] is False
    extracted = json.loads(
        asyncio.run(web_tools.web_extract_tool(["https://www.example.com/article"]))
    )
    assert extracted["results"][0]["error"] == "HTTP 403"
    assert not web_tools_rescue._rescue_eligible(direct)
    assert not web_tools_rescue._rescue_eligible(get_provider("ddgs"))


async def _safe_url(url):
    return True
