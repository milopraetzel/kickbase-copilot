from pathlib import Path

from fastapi.testclient import TestClient

from kickbase_agent.server.app import create_app

FIX = Path(__file__).parent.parent / "fixtures"


def test_news_returns_kicker_articles(httpx_mock):
    body = (FIX / "kicker_rss.xml").read_bytes()
    httpx_mock.add_response(
        method="GET",
        url="https://newsfeed.kicker.de/news/bundesliga",
        content=body,
    )

    client = TestClient(create_app())
    response = client.get("/api/news")

    assert response.status_code == 200
    articles = response.json()
    assert len(articles) == 2
    assert articles[0]["source"] == "kicker"
    assert "Musiala" in articles[0]["title"]
