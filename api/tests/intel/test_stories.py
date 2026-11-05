from unittest.mock import AsyncMock, patch

import pytest

from kickbase_agent.intel.models import ExplosionCandidate, Signal
from kickbase_agent.intel.stories import StoryWriter


@pytest.mark.asyncio
async def test_story_writer_returns_narrative():
    candidate = ExplosionCandidate(
        player_id="1",
        name="Florian Wirtz",
        team_id="4",
        position=3,
        current_market_value=20_000_000,
        total_score=0.85,
        signals=[
            Signal(name="form", score=0.9, weight=0.35, reason="Starker Aufwärtstrend."),
            Signal(name="matchup", score=0.8, weight=0.25, reason="Leichte Aufgabe gegen Bochum."),
            Signal(name="trend", score=0.8, weight=0.40, reason="Marktwert steigt stark."),
        ],
    )

    # Mock the Anthropic client
    fake_text = (
        '{"headline":"Wirtz vor Ausbruch",'
        '"narrative":"Starke Form bei leichter Aufgabe.",'
        '"confidence":"hoch"}'
    )
    fake_response = type(
        "R",
        (),
        {"content": [type("B", (), {"text": fake_text})()]},
    )()
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=fake_response)

    with patch("kickbase_agent.intel.stories.AsyncAnthropic", return_value=mock_client):
        writer = StoryWriter(api_key="fake-key")
        story = await writer.for_candidate(candidate)

    assert story.player_id == "1"
    assert "Wirtz" in story.headline
    assert story.confidence == "hoch"
