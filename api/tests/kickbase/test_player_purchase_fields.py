"""Der frische Kauf-Preflight muss den echten Detail-Payload lesen können."""

import json
from pathlib import Path

import pytest

from kickbase_agent.kickbase.client import KickbaseClient
from kickbase_agent.kickbase.models import Player

PAYLOAD = json.loads((Path(__file__).parents[1] / "fixtures"
                      / "kickbase_player_detail_response.json").read_text())


async def test_get_player_accepts_real_detail_name_and_points_fields(httpx_mock):
    httpx_mock.add_response(
        url="https://api.kickbase.com/v4/leagues/42/players/8329", json=PAYLOAD
    )
    client = KickbaseClient(token="test-token")
    player = await client.get_player("42", "8329")
    assert player.last_name == "Olise"
    assert player.points == 7185
    assert player.mv_change_24h == 26_065
    assert player.fitness_status == 0


@pytest.mark.parametrize("invalid_delta", [True, False, 1.8, "1.8", None])
def test_invalid_measured_gain_never_becomes_a_positive_purchase_signal(invalid_delta):
    # Fehlerhafte Eingangswerte simulieren; keine Behauptung eines API-Belegs.
    raw = {**PAYLOAD, "n": "Olise", "tfhmvt": invalid_delta}
    assert Player.from_api(raw).mv_change_24h == 0
