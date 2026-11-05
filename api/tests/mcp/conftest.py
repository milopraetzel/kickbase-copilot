"""Test-Welt der MCP-Tools: Fake-Client (echte Payload-Strukturen), In-Memory-DB, Fabrik-Patch.

WICHTIG: dieses Verzeichnis hat bewusst KEIN __init__.py — ein Paket namens
`mcp` unter tests/ würde das gleichnamige SDK-Paket verdecken.

Fixtures folgen ausschließlich live belegten Strukturen:
- Squad/Markt-Items wie tests/fixtures/kickbase_*_response.json (+ `lo` = Lineup-Order,
  `prob`/`st`/`tfhmvt`/`pim` laut kickbase/models.py und LEARNINGS 23.08.),
- Markt-`ofs[]` (u/uop/uoid) und `u.n` (Verkäufer) wie KickbaseClient.get_own_bids,
- Feed-Items t=15/22 wie tests/worker/test_bonus_job.py bzw. trading-Tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kickbase_agent.db.models import Base
from kickbase_agent.kickbase import endpoints
from kickbase_agent.kickbase.models import Fixture, MarketPlayer, SquadPlayer
from kickbase_agent.mcp import tools_ops, tools_read, tools_write

LEAGUE = "L1"
USER = "u1"
MY_NAME = "emil"
NOW = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)

# --- Rohdaten (Strukturen wie live/Fixtures) -------------------------------------

SQUAD_RAW: list[dict[str, Any]] = [
    # Kader-Item: i/fn/n/pos/mv/st/tid (Fixture) + lo (Lineup-Order), prob, tfhmvt, pim, mvt, p
    {"i": "1", "fn": "Manuel", "n": "Neuer", "pos": 1, "mv": 8_500_000, "st": 0, "tid": "2",
     "lo": 0, "prob": 1, "tfhmvt": 120_000, "pim": "content/file/neuer.png", "mvt": 1, "p": 80},
    {"i": "2", "fn": "Joshua", "n": "Kimmich", "pos": 3, "mv": 19_000_000, "st": 2, "tid": "2",
     "lo": 5, "prob": 2, "tfhmvt": -50_000, "p": 140},
    {"i": "3", "fn": "Jamal", "n": "Musiala", "pos": 3, "mv": 25_000_000, "st": 0, "tid": "2",
     "prob": 5, "tfhmvt": 0},
    # Füller bis zum deckungsfähigen 12er-Kader (zentraler Deckungs-Preflight
    # 24.08.: ein endgültiger Verkauf braucht danach ≥ 11 + stellbare Formation).
    # Struktur je Item unverändert (i/fn/n/pos/mv/st/tid).
    {"i": "d1", "fn": "F", "n": "Abw1", "pos": 2, "mv": 1_000_000, "st": 0, "tid": "3"},
    {"i": "d2", "fn": "F", "n": "Abw2", "pos": 2, "mv": 1_000_000, "st": 0, "tid": "3"},
    {"i": "d3", "fn": "F", "n": "Abw3", "pos": 2, "mv": 1_000_000, "st": 0, "tid": "3"},
    {"i": "d4", "fn": "F", "n": "Abw4", "pos": 2, "mv": 1_000_000, "st": 0, "tid": "4"},
    {"i": "m1", "fn": "F", "n": "Mit1", "pos": 3, "mv": 1_000_000, "st": 0, "tid": "4"},
    {"i": "m2", "fn": "F", "n": "Mit2", "pos": 3, "mv": 1_000_000, "st": 0, "tid": "4"},
    {"i": "s1", "fn": "F", "n": "Stu1", "pos": 4, "mv": 1_000_000, "st": 0, "tid": "5"},
    {"i": "s2", "fn": "F", "n": "Stu2", "pos": 4, "mv": 1_000_000, "st": 0, "tid": "5"},
    {"i": "x1", "fn": "F", "n": "Mit3", "pos": 3, "mv": 1_000_000, "st": 0, "tid": "5"},
]

MARKET_RAW: list[dict[str, Any]] = [
    # Kickbase-Listing (kein `u`) mit eigenem Gebot in ofs[] (u = Bieter-User-ID, uop Betrag)
    {"i": "99", "fn": "Hidden", "n": "Gem", "pos": 4, "mv": 5_000_000, "prc": 5_500_000,
     "exs": 3600, "tid": "11", "prob": 1, "tfhmvt": 90_000,
     "ofs": [{"u": USER, "uop": 5_120_000, "uoid": USER}]},
    # Manager-Listing (u.n = Verkäufer, exs 0 = kein Countdown; live 23.08.), kein eigenes Gebot
    {"i": "88", "fn": "Cheap", "n": "Striker", "pos": 4, "mv": 3_000_000, "prc": 3_200_000,
     "exs": 0, "tid": "5", "u": {"n": "Felix K."}, "ofs": []},
    # Eigener gelisteter Kaderspieler (steht in Kader UND Markt — Hermes-Audit)
    {"i": "3", "fn": "Jamal", "n": "Musiala", "pos": 3, "mv": 25_000_000, "prc": 26_000_000,
     "exs": 0, "tid": "2", "u": {"n": MY_NAME}},
]

PLAYER_DETAIL_RAW: dict[str, Any] = {
    # Detail-Endpoint, live geprobt 23.08.26 (Bernardo, st=2): KEIN `n` — Nachname ist `ln`,
    # `tn` = Teamname, `stxt`/`tp`/`ap` nur wenn vorhanden, `ph` = 5 Einträge {hp, p},
    # `stl` = Status-Liste, `plpt`/`plpurl` = LigaInsider-Partnerlink, `sec` = Einsatzsekunden.
    "i": "2", "fn": "", "ln": "Bernardo", "pos": 2, "mv": 7_811_721, "mvt": 2,
    "tfhmvt": -204_124, "tid": "14", "tn": "Hoffenheim", "prob": 3, "st": 2,
    "stxt": "Achilles tendon problems", "stl": [2], "tp": 2381, "ap": 82,
    "ph": [{"hp": True, "p": 47}, {"hp": False}, {"hp": True, "p": 60},
           {"hp": True, "p": 71}, {"hp": True, "p": 12}],
    "plpt": "Ligainsider", "plpurl": "https://www.ligainsider.de/bernardo/12345/",
    "pim": "content/file/bernardo.png", "shn": 13, "g": 0, "a": 0, "y": 9, "r": 0,
    "sec": 152_866, "cv": 8_700_000, "oui": "2663242", "day": 34,
}

# Detail-Payload eines FITTEN Spielers (Hoffmeier, live 23.08.26): ohne stxt/tp/ap.
PLAYER_DETAIL_FIT_RAW: dict[str, Any] = {
    "i": "1", "fn": "Marcel", "ln": "Hoffmeier", "pos": 2, "mv": 8_352_355, "mvt": 1,
    "tfhmvt": -106_971, "tid": "29", "tn": "Paderborn", "prob": 3, "st": 0, "stl": [],
    "ph": [{"hp": False}] * 5, "plpt": "Ligainsider",
    "plpurl": "https://www.ligainsider.de/marcel-hoffmeier/23456/", "pim": "content/file/h.png",
    "shn": 33, "cv": 7_700_000, "oui": "2663242", "day": 34,
}

FEED_RAW: list[dict[str, Any]] = [
    {"i": "1", "t": 15, "dt": "2026-08-23T05:58:22Z",
     "data": {"byr": MY_NAME, "pn": "Hoffmeier", "trp": 8_700_000}},
    {"i": "2", "t": 22, "dt": "2026-08-23T00:28:01Z", "data": {"bn": 50000, "day": 6}},
    {"i": "3", "t": 15, "dt": "2026-08-22T20:00:00Z",
     "data": {"slr": "Stefan", "pn": "Vozar", "trp": 1_280_015}},
    {"i": "4", "t": 3, "dt": "2026-08-22T19:00:00Z", "data": {"ln": "Listing", "mv": 4_000_000}},
]


def _mp(raw: dict[str, Any]) -> MarketPlayer:
    return MarketPlayer.from_api(raw)


class FakeClient:
    """Nur die Methoden, die die Tools/Executor brauchen; Writes werden protokolliert."""

    def __init__(self) -> None:
        self.squad_raw = [dict(it) for it in SQUAD_RAW]
        self.market_raw = [dict(it) for it in MARKET_RAW]
        self.detail_raw = dict(PLAYER_DETAIL_RAW)
        self.feed = [dict(it) for it in FEED_RAW]
        self.budget = 7_410_000
        # Anpfiff relativ zur ECHTEN Uhr (L-075, Nachtrag 26.08.): Der
        # Executor-Preflight holt sein `now` selbst, also wandert die reale Zeit
        # auf einen festen Fixture-Anpfiff zu. Mit `NOW` als Anker (23.08.) lag
        # der Anpfiff auf Fr 28.08. 20:00 UTC — ab Mi 26.08. ~20:00 UTC war das
        # T-48h-Gate scharf und `test_place_bid_live_...` kippte von grün auf rot,
        # ohne dass jemand Code angefasst hatte. Wer gegen `datetime.now()`
        # prueft, muss auch seine Termine daran haengen.
        _jetzt = datetime.now(UTC)
        self.fixtures = [
            Fixture(matchday=1, home_team_id="2", away_team_id="3",
                    kickoff=_jetzt + timedelta(days=5, hours=12)),
            Fixture(matchday=1, home_team_id="5", away_team_id="7",
                    kickoff=_jetzt + timedelta(days=6)),
        ]
        self.writes: list[tuple[Any, ...]] = []
        self.get_calls: list[str] = []
        self.bonus_credit = 0

    # --- Reads --------------------------------------------------------------
    async def _get(self, path: str) -> dict[str, Any]:
        self.get_calls.append(path)
        if path == endpoints.squad(LEAGUE):
            return {"it": self.squad_raw}
        if path == endpoints.market(LEAGUE):
            return {"it": self.market_raw}
        if path == endpoints.player_detail(LEAGUE, "2"):
            return self.detail_raw
        if path == endpoints.player_detail(LEAGUE, "1"):
            return dict(PLAYER_DETAIL_FIT_RAW)
        raise AssertionError(f"unerwarteter Rohdaten-Pfad {path}")

    async def get_squad(self, league_id: str) -> list[SquadPlayer]:
        return [SquadPlayer.from_api(it) for it in self.squad_raw]

    async def get_market(self, league_id: str) -> list[MarketPlayer]:
        return [_mp(it) for it in self.market_raw]

    async def get_budget(self, league_id: str) -> int:
        return self.budget

    async def get_budget_fields(self, league_id: str) -> dict[str, Any]:
        # /me/budget: b = Konto, pbaa = Konto nach Annahme aller offenen Gebote
        # (Live-Fund 23.08.: b − pbaa = Summe eigener Gebote)
        return {"b": self.budget, "pbaa": self.budget - 5_120_000}

    async def get_own_bids(self, league_id: str, user_id: str) -> list[dict[str, Any]]:
        # Gleiche Ableitung wie KickbaseClient.get_own_bids (ofs[] des Markt-Payloads)
        bids = []
        for it in self.market_raw:
            for offer in it.get("ofs", []) or []:
                if str(offer.get("u")) != str(user_id):
                    continue
                bids.append({
                    "player_id": str(it["i"]),
                    "name": f"{it.get('fn', '')} {it.get('n', '')}".strip(),
                    "team_id": str(it.get("tid", "")),
                    "amount": int(offer.get("uop", 0)),
                    "offer_id": str(offer.get("uoid", "")),
                    "expires_in_s": int(it.get("exs", 0) or 0),
                    "seller": str((it.get("u") or {}).get("n") or "").strip(),
                    "listing_price": int(it.get("prc", 0) or 0),
                })
        return bids

    async def get_ranking(self, league_id: str):
        return [SimpleNamespace(user_id=USER, name=MY_NAME),
                SimpleNamespace(user_id="u2", name="Stefan")]

    async def get_activity_feed(self, league_id: str, max_items: int = 30):
        return self.feed[:max_items]

    async def get_league_rules(self, league_id: str) -> dict[str, Any]:
        # Vereinslimit fuer den zentralen Bid-Preflight (Invarianten 24.08.)
        return {"max_players_per_club": 11}

    async def get_matchdays(self):
        return self.fixtures

    async def get_team_names(self):
        return {"2": "Bayern", "3": "Dortmund", "5": "Freiburg", "7": "Leverkusen",
                "11": "Augsburg"}

    # --- Writes (nur via Executor erreichbar) --------------------------------
    async def place_bid(self, league_id, player_id, price):
        self.writes.append(("place_bid", league_id, player_id, price))
        return {"ok": True}

    async def list_player(self, league_id, player_id, price):
        self.writes.append(("list", league_id, player_id, price))
        return {"listed": True}

    async def sell_to_kickbase(self, league_id, player_id):
        self.writes.append(("sell", league_id, player_id))
        return {"sold": True}

    async def delist_player(self, league_id, player_id):
        # Delist-Weg (22.09.2026): DELETE market_delist — Antwort ist leer.
        self.writes.append(("delist", league_id, player_id))
        return {}

    async def collect_bonus(self):
        self.writes.append(("bonus",))
        self.budget += self.bonus_credit
        return {"it": []}


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def world(monkeypatch, engine):
    """FakeClient + In-Memory-DB in alle MCP-Module (und bonus_job) einhängen."""
    client = FakeClient()
    session_local = sessionmaker(bind=engine, future=True)

    async def fake_factory():
        auth = SimpleNamespace(
            token="t", user_id=USER,
            leagues=[SimpleNamespace(id=LEAGUE, name="BASE")], league_id=LEAGUE,
        )
        return client, auth

    import kickbase_agent.worker.bonus_job as bonus_job

    for mod in (tools_read, tools_write, tools_ops, bonus_job):
        if hasattr(mod, "make_kickbase_client"):
            monkeypatch.setattr(mod, "make_kickbase_client", fake_factory)
        if hasattr(mod, "SessionLocal"):
            monkeypatch.setattr(mod, "SessionLocal", session_local)
    return SimpleNamespace(client=client, engine=engine, sessions=session_local)


@pytest.fixture
def env(monkeypatch):
    """Arming-Flags explizit setzen (Default: alles aus, Kill-Switch an)."""

    def _set(dry="1", allow_live="0", test_league="", auto_bonus="0"):
        # Zwei-DB-Fix: lokale .env kann KBA_REMOTE_API_URL setzen — Tests des
        # LOKALEN Pfads müssen den Remote-Modus explizit ausschalten (leere
        # Env-Var überstimmt die .env in pydantic-settings). test_mcp_remote.py
        # setzt die Variablen danach gezielt wieder.
        monkeypatch.setenv("KBA_REMOTE_API_URL", "")
        monkeypatch.setenv("KBA_REMOTE_API_AUTH", "")
        monkeypatch.setenv("AGENT_DRY_RUN", dry)
        monkeypatch.setenv("AGENT_ALLOW_LIVE", allow_live)
        monkeypatch.setenv("AGENT_TEST_LEAGUE_ID", test_league)
        monkeypatch.setenv("AGENT_AUTO_BONUS", auto_bonus)
        monkeypatch.setenv("AGENT_AUTO_PURGE", "0")
        monkeypatch.setenv("AGENT_AUTO_LINEUP", "0")
        monkeypatch.setenv("AGENT_APPROVE_LIVE", "0")
        monkeypatch.setenv("AGENT_AUTO_TRADING", "0")
        import kickbase_agent.config as cfg

        cfg._settings = None

    _set()
    yield _set
    import kickbase_agent.config as cfg

    cfg._settings = None
