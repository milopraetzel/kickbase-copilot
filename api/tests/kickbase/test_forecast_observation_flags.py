"""Rohfeldpräsenz zusätzlich zu kompatiblen numerischen Anzeigewerten erhalten."""

import json
from pathlib import Path

from kickbase_agent.kickbase.models import MatchdayPerformance, Player


def test_existing_detail_fixture_distinguishes_measured_zero_from_missing_delta():
    raw = json.loads((Path(__file__).parents[1] / "fixtures"
                      / "kickbase_player_detail_response.json").read_text())
    assert Player.from_api(raw).mv_change_24h_observed is True
    raw.pop("tfhmvt")
    missing = Player.from_api(raw)
    assert missing.mv_change_24h == 0
    assert missing.mv_change_24h_observed is False


def test_missing_performance_fields_never_become_observed_zero_labels():
    # Parser-Grenzfälle, keine behaupteten vollständigen API-Antworten.
    missing = MatchdayPerformance.from_api("2025/2026", {"day": 3})
    assert (missing.points, missing.minutes) == (0, 0)
    assert missing.points_observed is False
    assert missing.minutes_observed is False
    zero = MatchdayPerformance.from_api("2025/2026", {"day": 3, "p": 0, "mp": "0'"})
    assert zero.points_observed is True
    assert zero.minutes_observed is True
    assert zero.is_finished is None
