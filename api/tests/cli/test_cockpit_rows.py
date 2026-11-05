"""Row-Vertrag des Cockpits v4 (Spec 2026-08-23 §3): Detail-Keys in Kader-/Markt-Zeilen.

Getestet werden die reinen Helfer aus cli/cockpit/data.py — kein Netz, keine DB:
`_detail_fields` (PlayerDetail → Row-Keys), `_spiele` (letzte 3 + nächstes),
`_merge_details` (Zeilen anreichern, Coverage zählen) und `_load_details`
(asyncio.gather mit Fake-Client; ein Fehler killt den Build nicht).
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kickbase_agent.cli.cockpit import data
from kickbase_agent.kickbase.models import (
    FormEntry,
    MatchdaySummary,
    PlayerDetail,
    Position,
)

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
CDN = "https://kickbase.b-cdn.net"


def test_market_expiry_keeps_original_fetch_time():
    # Echter kba-probe-market-Abruf vom 12.09.2026, zwei unveränderte Angebotszeilen.
    fixture = Path(__file__).parents[1] / "fixtures/league_history_20260912/market-probe.json"
    raw = json.loads(fixture.read_text())
    rows = data._market_rows(raw, {}, {}, "", observed_at=NOW)
    originals = {str(row["i"]): row for row in raw["it"]}
    checked = 0
    for row in rows:
        item = originals[row["pid"]]
        if row["listing_type"] == "kickbase" and item.get("exs") is not None:
            assert row["expires_at"] == (NOW + timedelta(seconds=item["exs"])).isoformat()
            assert row["observed_at"] == NOW.isoformat()
            checked += 1
    assert checked > 0

# Vier Spiele aus `mdsum`, absichtlich in NICHT-chronologischer Lieferreihenfolge:
# Saisonende 2025/26 (Spieltage 32-34, gespielt) + Spieltag 1 der neuen Saison.
MD_32 = MatchdaySummary(
    day=32, home_team_id="14", away_team_id="2", home_goals=3, away_goals=3,
    kickoff=datetime(2026, 5, 2, 13, 30, tzinfo=UTC),
    home_img="content/file/h.svg", away_img="content/file/b.svg",
)
MD_33 = MatchdaySummary(
    day=33, home_team_id="2", away_team_id="43", home_goals=1, away_goals=0,
    kickoff=datetime(2026, 5, 9, 13, 30, tzinfo=UTC),
)
MD_34 = MatchdaySummary(
    day=34, home_team_id="9", away_team_id="2", home_goals=0, away_goals=2,
    kickoff=datetime(2026, 5, 16, 13, 30, tzinfo=UTC), is_current=True,
)
MD_NEXT = MatchdaySummary(
    day=1, home_team_id="18", away_team_id="29", home_goals=0, away_goals=0,
    kickoff=datetime(2026, 8, 29, 13, 30, tzinfo=UTC), is_current=False,
)
MD_LATER = MatchdaySummary(
    day=2, home_team_id="2", away_team_id="3",
    kickoff=datetime(2026, 9, 5, 13, 30, tzinfo=UTC),
)


def _olise() -> PlayerDetail:
    return PlayerDetail(
        id="8329", first_name="Michael", last_name="Olise", shirt_number=17,
        team_id="2", team_name="Bayern", position=Position.MIDFIELDER,
        market_value=64_765_581, market_value_trend=1, mv_change_24h=26_065,
        lineup_prob=1, fitness_status=0, fitness_text="",
        status_since=datetime(2026, 1, 5, 20, 17, 19, tzinfo=UTC),
        total_points=7185, avg_points=225, goals=15, assists=19,
        appearances=32, sub_appearances=1, s11=23, seconds_played=154_488,
        yellow=8, red=0,
        last_points=[
            FormEntry(played=True, points=p) for p in (81, 334, 290, 166, 152)
        ],
        matchdays=[MD_NEXT, MD_32, MD_34, MD_33, MD_LATER],
        image_path="content/file/p.png", team_image="content/file/t.svg",
        partner="Ligainsider", owner_id="0",
    )


def test_detail_fields_cover_full_row_contract():
    row = data._detail_fields(_olise(), NOW)

    assert row["punkte"] == 7185  # Saison-Punkte (int, nicht mehr die DB-Liste)
    assert row["avg"] == 225
    assert row["tore"] == 15
    assert row["vorlagen"] == 19
    assert row["einsaetze"] == 32
    assert row["einwechsel"] == 1
    assert row["s11"] == 23
    assert row["avg_min"] == 80
    assert row["gelb"] == 8
    assert row["rot"] == 0
    assert row["fit"] is True
    assert row["fit_text"] == ""
    assert row["fit_since"] == "05.01.2026"
    assert row["form5"] == [
        {"played": True, "p": 81}, {"played": True, "p": 334}, {"played": True, "p": 290},
        {"played": True, "p": 166}, {"played": True, "p": 152},
    ]
    assert row["nummer"] == 17
    assert row["vorname"] == "Michael"
    assert row["nachname"] == "Olise"
    assert row["club_img"] == f"{CDN}/content/file/t.svg"  # volle URL wie `img`
    assert row["mv24"] == 26_065
    # Vollständigkeit des Vertrags — kein Key vergessen, keiner erfunden
    assert set(row) == {
        "punkte", "avg", "tore", "vorlagen", "einsaetze", "einwechsel", "s11",
        "avg_min", "gelb", "rot", "fit", "fit_text", "fit_since", "form5", "spiele",
        "nummer", "vorname", "nachname", "club_img", "mv24",
    }


def test_spiele_are_last_three_played_plus_next_in_kickoff_order():
    spiele = data._detail_fields(_olise(), NOW)["spiele"]

    # Lieferreihenfolge war durcheinander und `day` 1 < 32 — sortiert wird nach
    # Anpfiff: 32, 33, 34 (gespielt) + Spieltag 1 (nächstes); Spieltag 2 fällt raus.
    assert [s["day"] for s in spiele] == [32, 33, 34, 1]
    assert spiele[0] == {
        "day": 32, "home": "Hoffenheim", "away": "Bayern",
        "home_img": f"{CDN}/content/file/h.svg", "away_img": f"{CDN}/content/file/b.svg",
        "score": "3:3", "date": "2026-05-02", "cur": False,
    }
    assert spiele[1]["score"] == "1:0"
    assert spiele[2]["cur"] is True
    # Das nächste Spiel: kein Ergebnis, Clubnamen aus TEAMS (Mainz/Paderborn)
    assert spiele[3] == {
        "day": 1, "home": "Mainz", "away": "Paderborn", "home_img": "", "away_img": "",
        "score": None, "date": "2026-08-29", "cur": False,
    }


def test_spiele_fall_back_to_team_id_and_handle_missing_kickoff():
    md = [
        MatchdaySummary(day=5, home_team_id="999", away_team_id="2", status=2,
                        home_goals=2, away_goals=2),  # mdst≠0 → gespielt, kein md
        MatchdaySummary(day=6, home_team_id="2", away_team_id="998"),  # offen, kein md
    ]
    spiele = data._spiele(md, NOW)

    assert [s["day"] for s in spiele] == [5, 6]
    assert spiele[0]["home"] == "999"  # unbekannte tid → ID statt Name
    assert spiele[0]["score"] == "2:2"
    assert spiele[0]["date"] == ""
    assert spiele[1]["score"] is None
    assert spiele[1]["away"] == "998"


def test_spiele_without_played_games_only_next():
    assert data._spiele([MD_LATER, MD_NEXT], NOW) == [{
        "day": 1, "home": "Mainz", "away": "Paderborn", "home_img": "", "away_img": "",
        "score": None, "date": "2026-08-29", "cur": False,
    }]
    assert data._spiele([], NOW) == []


def test_detail_fields_injured_player_and_empty_lists():
    d = PlayerDetail(
        id="1954", first_name="", last_name="Bernardo", position=Position.DEFENDER,
        fitness_status=2, fitness_text="Achilles tendon problems",
        status_since=None, appearances=0, seconds_played=900,
    )
    row = data._detail_fields(d, NOW)

    assert row["fit"] is False
    assert row["fit_text"] == "Achilles tendon problems"
    assert row["fit_since"] == ""
    assert row["avg_min"] == 0
    assert row["form5"] == []
    assert row["spiele"] == []
    assert row["club_img"] == ""


def test_merge_details_enriches_only_rows_with_detail_and_keeps_old_keys():
    kader = [
        {"pid": "8329", "name": "Olise", "pos": "MIT", "mv": 1, "score": 0.9,
         "kb_prob": 1, "mv24": 0, "team": "Bayern", "spark": [1, 2]},
        {"pid": "1954", "name": "Bernardo", "pos": "ABW", "mv": 2, "score": 0.5,
         "kb_prob": 3, "mv24": -5, "team": "Hoffenheim", "spark": []},
    ]
    details = {"8329": _olise()}

    hit = data._merge_details(kader, details, NOW)

    assert hit == 1
    olise, bernardo = kader
    # Bestehende Keys bleiben exakt erhalten …
    assert (olise["name"], olise["pos"], olise["mv"], olise["score"]) == ("Olise", "MIT", 1, 0.9)
    assert olise["spark"] == [1, 2] and olise["kb_prob"] == 1 and olise["team"] == "Bayern"
    # … die Detail-Keys kommen dazu (mv24 frisch aus dem Detail)
    assert olise["punkte"] == 7185 and olise["tore"] == 15 and olise["mv24"] == 26_065
    # Ohne Detail: keine Detail-Keys (Renderer zeigt „—"), alte Keys unverändert
    assert "punkte" not in bernardo and "spiele" not in bernardo
    assert bernardo["mv24"] == -5 and bernardo["kb_prob"] == 3


class _FakeClient:
    """Minimaler Client-Ersatz: liefert je pid ein Detail oder wirft."""

    def __init__(self, payload: dict[str, dict], fail: set[str]):
        self.payload = payload
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def get_player_detail(self, league_id: str, player_id: str) -> PlayerDetail:
        self.calls.append((league_id, player_id))
        if player_id in self.fail:
            raise RuntimeError(f"boom {player_id}")
        return PlayerDetail.from_api(self.payload[player_id])


@pytest.mark.asyncio
async def test_load_details_collects_successes_and_survives_single_failures(caplog):
    payload = {
        "8329": {"i": "8329", "ln": "Olise", "pos": 3, "tp": 7185},
        "1954": {"i": "1954", "ln": "Bernardo", "pos": 2, "tp": 1200},
    }
    client = _FakeClient(payload, fail={"4711"})

    with caplog.at_level("WARNING"):
        # 8329 doppelt (eigener Spieler im Kader UND gelistet) → nur ein Call
        details = await data._load_details(client, "42", ["8329", "4711", "1954", "8329"])

    assert set(details) == {"8329", "1954"}
    assert details["8329"].total_points == 7185
    assert sorted(c[1] for c in client.calls) == ["1954", "4711", "8329"]
    assert all(c[0] == "42" for c in client.calls)
    assert "4711" in caplog.text and "2/3" in caplog.text


@pytest.mark.asyncio
async def test_load_details_empty_input_makes_no_calls():
    client = _FakeClient({}, fail=set())
    assert await data._load_details(client, "42", []) == {}
    assert await data._load_details(client, "42", ["", None]) == {}  # type: ignore[list-item]
    assert client.calls == []
