from pathlib import Path

from typer.testing import CliRunner

from kickbase_agent.cli import app

FIX = Path(__file__).parent.parent / "fixtures"


def test_news_prints_kicker_feed(monkeypatch, httpx_mock):
    monkeypatch.setenv("KICKBASE_EMAIL", "x@y.z")
    monkeypatch.setenv("KICKBASE_PASSWORD", "pw")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    body = (FIX / "kicker_rss.xml").read_bytes()
    httpx_mock.add_response(
        method="GET",
        url="https://newsfeed.kicker.de/news/bundesliga",
        content=body,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["news"])

    assert result.exit_code == 0, result.output
    assert "Musiala" in result.output
    assert "Wirtz" in result.output
