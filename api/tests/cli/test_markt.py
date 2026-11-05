import json
from pathlib import Path

from typer.testing import CliRunner

from kickbase_agent.cli import app

FIX = Path(__file__).parent.parent / "fixtures"


def test_markt_lists_offers(monkeypatch, httpx_mock):
    monkeypatch.setenv("KICKBASE_EMAIL", "me@x.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "pw")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    login_payload = json.loads((FIX / "kickbase_login_response.json").read_text())
    login_payload["leagues"] = login_payload["srvl"]
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/login",
        json=login_payload,
    )
    market_payload = json.loads((FIX / "kickbase_market_response.json").read_text())
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/42/market",
        json=market_payload,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["markt", "--league-id", "42"])

    assert result.exit_code == 0, result.output
    assert "Hidden" in result.output
    assert "Cheap" in result.output
    assert "5.5" in result.output  # price
