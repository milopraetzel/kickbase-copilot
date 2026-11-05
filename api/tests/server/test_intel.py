import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from kickbase_agent.db.models import Base, PlayerHistory
from kickbase_agent.db.models import Player as DbPlayer
from kickbase_agent.server import deps
from kickbase_agent.server.app import create_app
from kickbase_agent.server.auth import get_token_cache

FIX = Path(__file__).parent.parent / "fixtures"


def _app_with_db(seed=None):
    """App mit In-Memory-DB; `seed(session)` kann Testdaten einfügen."""
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine, future=True)
    if seed is not None:
        seed(session)
        session.commit()
    app = create_app()
    app.dependency_overrides[deps.db_session] = lambda: session
    return app


def _mock_login_and_squad(httpx_mock):
    login_payload = json.loads((FIX / "kickbase_login_response.json").read_text())
    login_payload["leagues"] = login_payload["srvl"]
    httpx_mock.add_response(
        method="POST",
        url="https://api.kickbase.com/v4/user/login",
        json=login_payload,
    )
    squad_payload = json.loads((FIX / "kickbase_squad_response.json").read_text())
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/42/squad",
        json=squad_payload,
    )


def _mock_fixtures(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/competitions/1/matchdays",
        json={"it": [{"day": 1, "it": [
            {"day": 1, "t1": "2", "t2": "5", "st": 0, "dt": "2026-08-28T18:30:00Z",
             "bo": {"o1": 1.3, "ox": 6.0, "o2": 8.0}},
        ]}]},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/competitions/1/table",
        json={"it": [{"tid": "2", "tn": "Bayern"}, {"tid": "5", "tn": "Freiburg"}]},
    )


def test_candidates_endpoint_returns_ranked_list(httpx_mock):
    _mock_login_and_squad(httpx_mock)
    _mock_fixtures(httpx_mock)

    get_token_cache().invalidate()
    client = TestClient(_app_with_db())
    response = client.get("/api/leagues/42/intel/candidates")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 3  # squad has 3 players
    for item in body:
        assert "total_score" in item
        assert "signals" in item
        assert "position_label" in item


def test_candidates_reads_db_history(httpx_mock):
    _mock_login_and_squad(httpx_mock)
    _mock_fixtures(httpx_mock)

    def seed(session):
        session.add(
            DbPlayer(
                id="1",
                kickbase_player_id="1",
                name="Manuel Neuer",
                position=1,
                team="2",
                current_market_value=8_500_000,
            )
        )
        # 7 Tage steigender Marktwert (+10 %) → Trend-Signal muss anschlagen
        for day, mv in enumerate(
            [8_000_000, 8_150_000, 8_300_000, 8_450_000, 8_600_000, 8_750_000, 8_800_000],
            start=1,
        ):
            session.add(
                PlayerHistory(
                    player_id="1",
                    recorded_at=datetime(2026, 8, day, tzinfo=UTC),
                    market_value=mv,
                    points=0,
                    is_starter=True,
                )
            )

    get_token_cache().invalidate()
    client = TestClient(_app_with_db(seed))
    response = client.get("/api/leagues/42/intel/candidates")  # kein matchday-Param

    assert response.status_code == 200
    body = response.json()
    neuer = next(item for item in body if item["player_id"] == "1")
    trend = next(s for s in neuer["signals"] if s["name"] == "trend")
    assert trend["score"] > 0.7  # echter Aufwärtstrend statt 0.5-Stub
    kimmich = next(item for item in body if item["player_id"] == "2")
    trend_ohne_daten = next(s for s in kimmich["signals"] if s["name"] == "trend")
    assert trend_ohne_daten["score"] == 0.5  # ohne Historie weiter neutral


def test_story_endpoint_calls_claude_and_returns_narrative(monkeypatch, httpx_mock):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")

    _mock_login_and_squad(httpx_mock)
    _mock_fixtures(httpx_mock)

    get_token_cache().invalidate()
    fake_text = (
        '{"headline":"Top-Tipp",'
        '"narrative":"Starke Form.",'
        '"confidence":"hoch"}'
    )
    fake_resp = type(
        "R",
        (),
        {"content": [type("B", (), {"text": fake_text})()]},
    )()
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=fake_resp)

    with patch("kickbase_agent.intel.stories.AsyncAnthropic", return_value=mock_client):
        client = TestClient(_app_with_db())
        response = client.get("/api/leagues/42/players/1/story")

    assert response.status_code == 200
    body = response.json()
    assert body["player_id"] == "1"
    assert body["confidence"] == "hoch"
