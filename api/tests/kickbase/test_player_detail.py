"""PlayerDetail: Member-Stats aus dem Spieler-Detail-Endpoint (Spec cockpit-v4 §1).

Das Olise-Fixture ist der ECHTE Payload der Probe vom 23.08.2026 (gegen den
App-Screenshot verifiziert); `mdsum` war in der Probe gekürzt und wird separat
mit dem echten Hoffmeier-Element getestet.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kickbase_agent.kickbase.client import KickbaseClient
from kickbase_agent.kickbase.models import MatchdaySummary, PlayerDetail, Position

FIX = Path(__file__).parent.parent / "fixtures"
OLISE = json.loads((FIX / "kickbase_player_detail_response.json").read_text())

# Echtes mdsum-Element (Hoffmeier 3492, Probe 23.08.26): Spieltag 1 der neuen
# Saison, noch nicht gespielt (mdst 0, Anpfiff 29.08. in der Zukunft).
HOFFMEIER_MD = {
    "t1": "18", "t2": "29", "t1g": 0, "t2g": 0, "day": 1,
    "md": "2026-08-29T13:30:00Z", "cur": False, "mdst": 0, "mdln": "1 Match Day",
    "t1im": "content/file/d4ac9675b44944039d463837f888cb1c.svg",
    "t2im": "content/file/ce455adfd794457f9ab90f7bab522812.svg",
}
# Nach dem Bernardo-Fragment nachgebaut (day 32, md 02.05.2026, 3:3 — gespielt).
BERNARDO_MD = {
    "t1": "14", "t2": "2", "t1g": 3, "t2g": 3, "day": 32,
    "md": "2026-05-02T13:30:00Z", "cur": False, "mdst": 0,
    "t1im": "content/file/a.svg", "t2im": "content/file/b.svg",
}
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def test_player_detail_parses_real_olise_payload():
    d = PlayerDetail.from_api(OLISE)

    assert d.id == "8329"
    assert d.first_name == "Michael"
    assert d.last_name == "Olise"  # Detail liefert `ln`, nicht `n`
    assert d.shirt_number == 17
    assert d.team_id == "2"
    assert d.team_name == "Bayern"
    assert d.position is Position.MIDFIELDER
    assert d.market_value == 64_765_581
    assert d.market_value_trend == 1
    assert d.mv_change_24h == 26_065
    # Die vier Kern-Stats + Einsätze/S11/Minuten/Karten wie im App-Screenshot
    assert d.total_points == 7185
    assert d.avg_points == 225
    assert d.goals == 15
    assert d.assists == 19
    assert d.appearances == 32
    assert d.sub_appearances == 1
    assert d.s11 == 23
    assert d.seconds_played == 154_488
    assert d.avg_minutes == 80  # 154488 / 60 / 32 = 80.46 → 80′
    assert d.yellow == 8
    assert d.red == 0
    # Startelf + Fitness
    assert d.lineup_prob == 1
    assert d.fitness_status == 0
    assert d.fitness_text == ""
    assert d.status_since == datetime(2026, 1, 5, 20, 17, 19, tzinfo=UTC)
    # Formleiste (5 Segmente, chronologisch wie geliefert)
    assert len(d.last_points) == 5
    assert [e.points for e in d.last_points] == [81, 334, 290, 166, 152]
    assert all(e.played for e in d.last_points)
    # Bilder / Partner / Besitzer
    assert d.image_path == "content/file/2d1bffc491014bb582c5d1c622b0ee37.png"
    assert d.team_image == "content/file/ff70df040a9f4179a7b45219225a2273.svg"
    assert d.partner == "Ligainsider"
    assert d.partner_image == "content/file/6f3b219505f04f13a708b1e538d8ace4.png"
    assert d.owner_id == "0"
    # mdsum war in der Probe gekürzt → leer, kein Fehler
    assert d.matchdays == []


def test_player_detail_is_frozen_like_player():
    d = PlayerDetail.from_api(OLISE)
    with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError/TypeError
        d.total_points = 1  # type: ignore[misc]


def test_player_detail_does_not_model_unclear_fields():
    # cv, stud, pes, cs, sl, opl, iposl sind ungeklärt (Spec §1) — bewusst draußen.
    fields = set(PlayerDetail.model_fields)
    for unwanted in ("cv", "stud", "pes", "cs", "sl", "opl", "iposl"):
        assert unwanted not in fields


def test_player_detail_minimal_payload_uses_safe_defaults():
    d = PlayerDetail.from_api(
        {"i": "1", "fn": "Max", "ln": "Muster", "pos": 4, "mv": 1_000_000, "tid": "9"}
    )

    assert d.id == "1"
    assert d.position is Position.STRIKER
    assert d.market_value == 1_000_000
    assert d.team_id == "9"
    assert d.team_name == ""
    assert d.shirt_number == 0
    assert d.lineup_prob is None  # fehlendes prob ist NIE eine erfundene Stufe
    assert d.fitness_status == 0
    assert d.fitness_text == ""
    assert d.status_since is None
    assert (d.total_points, d.avg_points, d.goals, d.assists) == (0, 0, 0, 0)
    assert (d.appearances, d.sub_appearances, d.s11, d.seconds_played) == (0, 0, 0, 0)
    assert d.avg_minutes == 0  # keine Division durch 0 Einsätze
    assert (d.yellow, d.red) == (0, 0)
    assert d.last_points == []
    assert d.matchdays == []
    assert (d.image_path, d.team_image, d.partner, d.partner_image, d.owner_id) == (
        "", "", "", "", "",
    )


def test_player_detail_tolerates_null_and_garbage_values():
    raw = {
        "i": 8329, "ln": "Olise", "pos": 3, "mv": None, "tp": None, "ap": "abc",
        "sec": "x", "ismc": None, "dt": "kein datum", "ph": "kaputt", "mdsum": None,
        "prob": None, "oui": 0,
    }
    d = PlayerDetail.from_api(raw)
    assert d.id == "8329"
    assert d.market_value == 0
    assert d.total_points == 0
    assert d.avg_points == 0
    assert d.avg_minutes == 0
    assert d.status_since is None
    assert d.last_points == []
    assert d.matchdays == []
    assert d.lineup_prob is None
    assert d.owner_id == "0"  # numerische 0 bleibt „frei", wird nicht zu ""


def test_player_detail_accepts_squad_style_last_name_key():
    # Squad/Markt-Items nennen den Nachnamen `n` — Detail `ln`; beides geht.
    d = PlayerDetail.from_api({"i": "2", "n": "Kane", "pos": 4})
    assert d.last_name == "Kane"


def test_matchday_summary_parses_real_hoffmeier_element():
    m = MatchdaySummary.from_api(HOFFMEIER_MD)

    assert m.day == 1
    assert m.home_team_id == "18"
    assert m.away_team_id == "29"
    assert (m.home_goals, m.away_goals) == (0, 0)
    assert m.kickoff == datetime(2026, 8, 29, 13, 30, tzinfo=UTC)
    assert m.is_current is False
    assert m.status == 0
    assert m.home_img == "content/file/d4ac9675b44944039d463837f888cb1c.svg"
    assert m.away_img == "content/file/ce455adfd794457f9ab90f7bab522812.svg"
    # Anpfiff in der Zukunft + mdst 0 → nicht gespielt
    assert m.is_played(NOW) is False


def test_matchday_summary_played_by_past_kickoff_or_status():
    played = MatchdaySummary.from_api(BERNARDO_MD)
    assert played.is_played(NOW) is True  # md liegt vor NOW
    assert (played.home_goals, played.away_goals) == (3, 3)

    # mdst ≠ 0 zählt auch ohne/mit zukünftigem Anpfiff als gespielt (Annahme)
    by_status = MatchdaySummary.from_api({**HOFFMEIER_MD, "mdst": 2})
    assert by_status.is_played(NOW) is True

    # weder Anpfiff noch Status → nicht gespielt, kein Crash
    blank = MatchdaySummary.from_api({"t1": "1", "t2": "2"})
    assert blank.is_played(NOW) is False
    assert blank.day == 0 and blank.kickoff is None


def test_player_detail_parses_mdsum_list_in_delivered_order():
    raw = {**OLISE, "mdsum": [BERNARDO_MD, HOFFMEIER_MD, "müll", None]}
    d = PlayerDetail.from_api(raw)

    assert [m.day for m in d.matchdays] == [32, 1]
    assert d.matchdays[0].is_played(NOW) is True
    assert d.matchdays[1].is_played(NOW) is False


@pytest.mark.asyncio
async def test_client_get_player_detail_hits_detail_endpoint(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="https://api.kickbase.com/v4/leagues/42/players/8329",
        json=OLISE,
    )
    client = KickbaseClient(token="t")

    d = await client.get_player_detail("42", "8329")

    assert isinstance(d, PlayerDetail)
    assert d.last_name == "Olise"
    assert d.total_points == 7185
    assert httpx_mock.get_request().headers["authorization"] == "Bearer t"
