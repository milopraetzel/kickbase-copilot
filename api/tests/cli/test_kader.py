import json
from pathlib import Path

from typer.testing import CliRunner

from kickbase_agent.cli import app

FIX = Path(__file__).parent.parent / "fixtures"


def test_kader_lists_squad(monkeypatch, httpx_mock):
    monkeypatch.setenv("KICKBASE_EMAIL", "me@x.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "pw")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    # login
    login_payload = json.loads((FIX / "kickbase_login_response.json").read_text())
    login_payload["leagues"] = login_payload["srvl"]
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/login",
        json=login_payload,
    )
    # squad
    squad_payload = json.loads((FIX / "kickbase_squad_response.json").read_text())
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/42/squad",
        json=squad_payload,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["kader", "--league-id", "42"])

    assert result.exit_code == 0, result.output
    assert "Neuer" in result.output
    assert "Kimmich" in result.output
    assert "Musiala" in result.output
