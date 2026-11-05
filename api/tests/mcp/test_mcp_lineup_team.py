"""MCP-Tools `recommend_lineup` und `team_profile` — gegen Fakes, ohne Netz.

Team-Profil-Fixture = echtes Olise-Item aus der Probe 23.08.26
(`/v4/competitions/1/teams/2/teamprofile`, Keys: tid/tn/tim/it …).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kickbase_agent.kickbase.models import TeamProfile
from kickbase_agent.mcp import tools_read
from kickbase_agent.scouts.ligainsider.models import Status

OLISE_ITEM = {
    "i": "8329", "n": "Olise", "lst": 1, "st": 0, "pos": 3, "mv": 64765581, "mvt": 1,
    "ap": 225, "iotm": False, "ofc": 0, "tid": "2", "sdmvt": 211676, "mvgl": 0,
    "pim": "content/file/2d1bffc491014bb582c5d1c622b0ee37.png", "prob": 1,
}
BANK_ITEM = {
    "i": "9001", "n": "Reserve", "st": 2, "pos": 2, "mv": 1_200_000, "mvt": -1,
    "ap": 12, "iotm": True, "ofc": 2, "tid": "2", "sdmvt": -50_000, "prob": 5,
}
TEAM_RAW = {
    "tid": "2", "tn": "Bayern", "tim": "content/file/ff70df040a9f4179a7b45219225a2273.svg",
    "it": [OLISE_ITEM, BANK_ITEM, {"n": "ohne id"}],
}


def test_team_profile_model_parses_real_olise_item():
    tp = TeamProfile.from_api(TEAM_RAW)
    assert tp.team_id == "2" and tp.name == "Bayern"
    assert len(tp.players) == 2  # Item ohne `i` wird verworfen
    olise = tp.players[0]
    assert olise.id == "8329" and olise.last_name == "Olise"
    assert olise.avg_points == 225 and olise.mv_change_7d == 211676
    assert olise.lineup_prob == 1 and olise.fitness_status == 0
    assert olise.on_market is False and olise.offer_count == 0
    bank = tp.players[1]
    assert bank.lineup_prob == 5 and bank.on_market is True and bank.offer_count == 2


@pytest.fixture
def team_world(world, monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_team_profile(team_id: str, competition_id: str = "1"):
        calls.append((team_id, competition_id))
        return TeamProfile.from_api(TEAM_RAW)

    monkeypatch.setattr(world.client, "get_team_profile", fake_team_profile, raising=False)
    world.calls = calls
    return world


async def test_team_profile_resolves_name_sorts_and_labels(team_world):
    res = await tools_read.team_profile("bayern")
    assert "error" not in res
    assert team_world.calls == [("2", "1")]
    assert res["team"]["name"] == "Bayern" and res["count"] == 2
    first = res["players"][0]
    assert first["name"] == "Olise" and first["avg_points"] == 225
    assert first["lineup_prob"] == 1 and first["lineup_label"] == "Sicher"
    assert first["mv7d_eur"] == 211676 and first["mv7d_fmt"]
    assert res["players"][1]["lineup_label"] == "Ausgeschlossen"


async def test_team_profile_accepts_team_id_and_filters_min_prob(team_world):
    res = await tools_read.team_profile("2", min_prob=2)
    assert res["count"] == 1 and res["total_in_squad"] == 2
    assert res["players"][0]["name"] == "Olise"


async def test_team_profile_sort_variants(team_world):
    by_mv7d = await tools_read.team_profile("Bayern", sort="mv7d")
    assert [p["name"] for p in by_mv7d["players"]] == ["Olise", "Reserve"]
    by_prob = await tools_read.team_profile("Bayern", sort="prob")
    assert by_prob["players"][0]["lineup_prob"] == 1


async def test_team_profile_unknown_club_and_bad_sort_are_error_dicts(team_world):
    bad = await tools_read.team_profile("Hogwarts")
    assert "error" in bad and "Unbekannter Club" in bad["error"]
    bad2 = await tools_read.team_profile("Bayern", sort="hype")
    assert "error" in bad2 and "sort" in bad2["error"]
    assert team_world.calls == []  # nie beim Client gelandet


def test_resolve_team_is_case_insensitive_and_detects_ambiguity():
    assert tools_read._resolve_team("union berlin") == ("40", "Union Berlin")
    assert tools_read._resolve_team("2")[0] == "2"
    assert tools_read._resolve_team("") is None
    with pytest.raises(ValueError):
        tools_read._resolve_team("b")  # trifft mehrere Clubs


async def test_team_profile_cross_checks_league_market_for_on_market_and_own_bid(
    team_world, monkeypatch
):
    orig_get = team_world.client._get

    async def fake_get(path: str):
        if path.endswith("/market"):
            return {"it": [{"i": "8329", "ofs": [{"u": "u1", "uop": 65_000_000}]}]}
        return await orig_get(path)

    monkeypatch.setattr(team_world.client, "_get", fake_get)
    res = await tools_read.team_profile("Bayern")
    olise = next(p for p in res["players"] if p["id"] == "8329")
    assert olise["on_market"] is True
    assert olise["own_bid"] is True


# ------------------------------------------------------------------ recommend_lineup


def _cand(pid: str, name: str, pos: int, elf: float, kauf: float, mv: int = 5_000_000):
    return SimpleNamespace(
        player_id=pid, name=name, team_id="2", position=pos,
        current_market_value=mv, total_score=kauf, lineup_score=elf,
    )


@pytest.fixture
def lineup_world(world, monkeypatch):
    starters = [_cand("1", "Schwäbe", 1, 0.38, 0.30), _cand("2", "Minjae", 2, 0.77, 0.65)]
    bench = [_cand("3", "Vogt", 4, 0.21, 0.20)]
    avail = [
        SimpleNamespace(player_id="1", status=Status.STARTER, multiplier=1.0,
                        reason="LI: Stammkeeper", confidence=1.0),
        SimpleNamespace(player_id="3", status=Status.BENCH, multiplier=0.4,
                        reason="LI: kein Stammplatz", confidence=0.9),
    ]
    quar = [SimpleNamespace(player_id="2", kb_name="Minjae", li_name="Kim Min-jae",
                            li_reason="Aufbautraining", confidence=0.8)]
    rec = SimpleNamespace(
        lineup=SimpleNamespace(formation="4-5-1", starters=starters, bench=bench,
                               expected_score=4.17),
        scout=SimpleNamespace(availabilities=avail, quarantines=quar),
    )

    async def fake_recommend(client, session, squad):
        return rec

    monkeypatch.setattr(tools_read, "_recommend_lineup", fake_recommend)
    return world


async def test_recommend_lineup_returns_structured_elf_with_li_and_kb(lineup_world):
    res = await tools_read.recommend_lineup()
    assert "error" not in res, res
    assert res["formation"] == "4-5-1" and res["expected_score"] == 4.17
    assert res["count"] == 2 and len(res["bench"]) == 1
    s1 = res["starters"][0]
    assert s1["name"] == "Schwäbe" and s1["pos"] == "TW"
    assert s1["li_status"] == "starter" and s1["li_reason"] == "LI: Stammkeeper"
    assert s1["elf_score"] == 0.38 and s1["kauf_score"] == 0.30
    assert "lineup_prob" in s1 and "lineup_label" in s1
    s2 = res["starters"][1]
    assert s2["li_status"] == "unknown"  # keine LI-Zeile → ehrlich unknown
    assert res["quarantines"][0]["kb_name"] == "Minjae"
    assert res["li_status_counts"] == {"starter": 1, "bench": 1}
    assert res["duration_s"] >= 0
