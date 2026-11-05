import json
from pathlib import Path

from typer.testing import CliRunner

from kickbase_agent.cli import app

FIX = Path(__file__).parent.parent / "fixtures"


def test_login_prints_leagues(monkeypatch, httpx_mock):
    monkeypatch.setenv("KICKBASE_EMAIL", "me@x.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "pw")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    payload = json.loads((FIX / "kickbase_login_response.json").read_text())
    payload["leagues"] = payload["srvl"]
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/login",
        json=payload,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["login"])

    assert result.exit_code == 0, result.output
    assert "user-123" in result.output
    assert "Kickbase Mit Kumpels" in result.output
