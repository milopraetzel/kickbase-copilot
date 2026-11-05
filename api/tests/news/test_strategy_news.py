"""RSS-Verarbeitung: Identitäten, Alter, Grenzen und Ausfälle statt Trading-Writes."""

import json
from datetime import UTC, datetime, timedelta
from xml.sax.saxutils import escape

import httpx
import pytest

from kickbase_agent.news import strategy_news as news


def _rss(*rows: tuple[str, str, str]) -> bytes:
    # Kleine RSS-Dokumente testen XML-Verhalten; keine Kickbase-API-Fixtures.
    items = "".join(
        f"<item><title>{escape(title)}</title><link>{escape(url)}</link>"
        f"<pubDate>{escape(date)}</pubDate></item>" for title, url, date in rows
    )
    return f'<rss version="2.0"><channel><title>Test</title>{items}</channel></rss>'.encode()


def _respond(httpx_mock, body: bytes):
    for _, _, url in news.FEEDS:
        httpx_mock.add_response(url=url, content=body, headers={"Content-Type": "text/xml"})


async def test_real_article_title_matches_player_and_keeps_publication_time(httpx_mock, tmp_path):
    _respond(httpx_mock, _rss(("Dallinga zurück im FC-Training",
        "https://geissblog.koeln/2026/09/sturmsorgen-kleiner-el-mala-muss-warten-dallinga-zurueck-im-fc-training/",
        "Mon, 07 Sep 2026 14:57:00 +0200")))
    result = await news.fetch_strategy_news(
        players=[{"pid": "16945", "name": "Thijs Dallinga", "team": "Köln"}],
        cache_path=tmp_path / "news.json",
    )
    article = result["articles"][0]
    assert article["published_at"] == "2026-09-07T12:57:00+00:00"
    assert article["expires_at"] == "2026-09-09T12:57:00+00:00"
    assert article["event_type"] == "news_mention"
    assert "keine bestätigte Einsatzprognose" in article["uncertainty"]
    assert len(result["articles"]) == 1  # Dasselbe URL-Ereignis bleibt einmalig.


async def test_cache_is_reused_but_player_filter_is_not_cached(httpx_mock, tmp_path):
    _respond(httpx_mock, _rss(("Michael Olise und Tjark Scheller", "https://example.org/a", "")))
    path = tmp_path / "news.json"
    first = await news.fetch_strategy_news(players=[{"pid": "8329", "name": "Olise"}],
                                           cache_path=path)
    second = await news.fetch_strategy_news(players=[{"pid": "7320", "name": "Scheller"}],
                                            cache_path=path)
    assert not first["cache_hit"] and second["cache_hit"]
    assert second["articles"][0]["player_id"] == "7320"
    assert second["articles"][0]["published_at"] is None
    assert second["articles"][0]["expires_at"] is None
    assert len(httpx_mock.get_requests()) == 3


async def test_expired_cache_retains_stale_articles_when_source_fails(httpx_mock, tmp_path):
    path = tmp_path / "news.json"
    _respond(httpx_mock, _rss(("Michael Olise", "https://example.org/a", "")))
    await news.fetch_strategy_news(players=[], cache_path=path)
    old = json.loads(path.read_text())
    old["generated_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    path.write_text(json.dumps(old))
    for _, _, url in news.FEEDS:
        httpx_mock.add_exception(httpx.ReadTimeout("timeout"), url=url)
    result = await news.fetch_strategy_news(players=[{"pid": "8329", "name": "Olise"}],
                                            cache_path=path)
    assert len(result["articles"]) == 1
    assert all(source["status"] == "stale" for source in result["sources"])
    assert len(result["errors"]) == 3
    assert result["articles"][0]["fetched_at"] == old["items"][0]["fetched_at"]


async def test_unsafe_links_scripts_and_entities_are_not_accepted(httpx_mock, tmp_path):
    body = _rss(("<script>Olise</script> Nachricht", "javascript:alert(1)", ""),
                ("<b>Michael Olise</b><script>Schadtext</script>", "https://example.org/a", ""))
    _respond(httpx_mock, body)
    result = await news.fetch_strategy_news(players=[{"pid": "8329", "name": "Olise"}],
                                            cache_path=tmp_path / "news.json")
    assert [a["title"] for a in result["articles"]] == ["Michael Olise"]
    with pytest.raises(ValueError, match="XML-Deklaration"):
        news._parse_feed(b'<!DOCTYPE rss [<!ENTITY a "bad">]><rss/>', "test", "")


def test_short_ambiguous_names_and_word_fragments_do_not_match():
    def articles(title, players):
        items = news._parse_feed(_rss((title, "https://example.org/a", "")), "test", "")
        return news._for_players(items, players, [])
    assert not articles("Kim spricht mit Ache", [{"pid": "6668", "name": "Minjae"},
                                                {"pid": "2855", "name": "Ache"}])
    assert len(articles("Minjae Kim und Ragnar Ache", [{"pid": "6668", "name": "Minjae"},
                                                    {"pid": "2855", "name": "Ache"}])) == 2
    assert not articles("Schellerspiel und Olisekeinname", [{"pid": "8329", "name": "Olise"}])
    assert not articles("Müller trifft", [{"pid": "1", "name": "Thomas Müller"},
                                         {"pid": "2", "name": "Andreas Müller"}])
    assert len(articles("Gabriel Vidovic", [{"pid": "3081", "name": "Vidović"}])) == 1


async def test_article_count_is_limited_and_oversized_feeds_are_errors(httpx_mock, tmp_path):
    body = _rss(*[(f"Michael Olise Nachricht {i}", f"https://example.org/{i}", "")
                  for i in range(20)])
    _respond(httpx_mock, body)
    result = await news.fetch_strategy_news(players=[{"pid": "8329", "name": "Olise"}],
                                            cache_path=tmp_path / "news.json")
    assert len(result["articles"]) == 12
    for _, _, url in news.FEEDS:
        httpx_mock.add_response(url=url, content=b"x" * (news.MAX_FEED_BYTES + 1))
    result = await news.fetch_strategy_news(players=[], cache_path=tmp_path / "large.json")
    assert len(result["errors"]) == 3
    assert all(source["status"] == "error" for source in result["sources"])


@pytest.mark.parametrize("value", ["invalid", "2026-09-11T12:00:00", "", None])
def test_unknown_or_ambiguous_publication_dates_stay_missing(value):
    assert news._date(value) is None
