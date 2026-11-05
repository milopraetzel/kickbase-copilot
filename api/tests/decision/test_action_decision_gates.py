"""Entscheidungs-Gates vor allen Kickbase-Aktionen (21.10.2026, Emils GO).

Prüft die vier neuen Entscheidungs-Schichten über alle 9 Aktionsarten:
1. Pre-Execution-Gate für `sell` (Konto-Deckung bereits erfüllt → superseded;
   Kaderspieler inzwischen fitter KB-1-Stammspieler mit +24h-Trend → refused)
   und `accept` (höheres Konkurrenz-Angebot am eigenen Listing → refused).
2. Aktive `delist`-Entscheidung im Planner/Executor (beförderter Stammspieler,
   geschützter Spieler oder Positions-Minimum-Gefahr wird vom Markt genommen;
   Emils Mondpreis-Anker bleibt unangetastet).
3. Aktive `decline`-Entscheidung im Planner/Executor (Lowball-Angebote unter
   103 % MW bzw. unter 115 % MW auf 125-%-Schaufenster-Bankspielern werden
   abgelehnt; Starter-Listings unter Wunschpreis bleiben für Emil offen).
4. Live-Entscheidungs-Gate vor `lineup` (Executor verweigert unfitten Starter
   bei fittem Bank-Ersatz derselben Position; Lücken-Wächter erkennt unfitte
   Starter und stellt neu auf).
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.agent.executor import ActionExecutor, PlannedAction
from kickbase_agent.db.models import Base, Intent
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS, Guardrails
from kickbase_agent.decision.policies import (
    IncomingOffer,
    propose_declines,
    propose_delists,
)
from kickbase_agent.worker.intents import (
    STATUS_EXECUTED,
    STATUS_PLANNED,
    STATUS_SUPERSEDED,
    decline_drafts,
    deficit_cover_unneeded,
    delist_drafts,
    execute_intent,
    own_listings_from_raw,
)

NOW = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)


def _squad_member(
    pid: str,
    mv: int = 5_000_000,
    *,
    pos: int = 3,
    fit: int | None = None,
    prob: int | None = None,
    chg: int | None = None,
    lo: int | None = None,
):
    player = SimpleNamespace(
        id=pid,
        market_value=mv,
        position=pos,
        team_id="T1",
        fitness_status=fit,
        lineup_prob=prob,
        mv_change_24h=chg,
    )
    return SimpleNamespace(player=player, lineup_order=lo)


class _GateClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.budget = 5_000_000
        self.pbaa = 5_000_000
        self.squad = [
            _squad_member("S1", 5_000_000, pos=3),
            _squad_member("gk1", 1_000_000, pos=1),
            _squad_member("d1", 1_000_000, pos=2),
            _squad_member("d2", 1_000_000, pos=2),
            _squad_member("d3", 1_000_000, pos=2),
            _squad_member("d4", 1_000_000, pos=2),
            _squad_member("m1", 1_000_000, pos=3),
            _squad_member("m2", 1_000_000, pos=3),
            _squad_member("m3", 1_000_000, pos=3),
            _squad_member("s1", 1_000_000, pos=4),
            _squad_member("s2", 1_000_000, pos=4),
            _squad_member("x1", 1_000_000, pos=2),
        ]
        self.raw_market: dict = {
            "it": [
                {
                    "i": "S1",
                    "u": {"i": "ME", "n": "emil"},
                    "ofs": [
                        {"u": "R1", "uop": 5_800_000, "uoid": "O_LOW"},
                    ],
                }
            ]
        }

    async def _get(self, path: str):
        if "squad" in str(path):
            return {"it": []}
        return self.raw_market

    async def get_squad(self, league_id: str):
        return self.squad

    async def get_budget(self, league_id: str) -> int:
        return self.budget

    async def get_budget_fields(self, league_id: str) -> dict:
        return {"b": self.budget, "pbaa": self.pbaa}

    async def get_matchdays(self):
        return [
            SimpleNamespace(
                kickoff=datetime(2026, 10, 25, 18, 30, tzinfo=UTC),
                matchday=8,
                is_finished=False,
            )
        ]

    async def sell_to_kickbase(self, league_id: str, player_id: str):
        self.calls.append(("sell", league_id, player_id))
        return {"sold": True}

    async def accept_offer(self, league_id: str, player_id: str, offer_id: str):
        self.calls.append(("accept", league_id, player_id, offer_id))
        return {"accepted": True}

    async def decline_offer(self, league_id: str, player_id: str, offer_id: str):
        self.calls.append(("decline", league_id, player_id, offer_id))
        return {"declined": True}

    async def delist_player(self, league_id: str, player_id: str):
        self.calls.append(("delist", league_id, player_id))
        return {"delisted": True}

    async def set_lineup(self, league_id: str, formation: str, player_ids: list):
        self.calls.append(("lineup", league_id, formation, tuple(player_ids)))
        return {"ok": True}


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    with Session(eng, future=True) as s:
        yield s


@pytest.fixture
def armed_env(monkeypatch):
    monkeypatch.setenv("AGENT_DRY_RUN", "0")
    monkeypatch.setenv("AGENT_AUTO_INTENTS", "1")
    monkeypatch.setenv("AGENT_AUTO_LINEUP", "1")
    monkeypatch.setenv("AGENT_ALLOW_LIVE", "0")
    monkeypatch.setenv("AGENT_TEST_LEAGUE_ID", "")
    import kickbase_agent.config as cfg

    cfg._settings = None
    yield
    cfg._settings = None


# --------------------------------------------------------- 1. Sell & Accept Gates


@pytest.mark.asyncio
async def test_accept_gate_refuses_lower_offer_when_higher_offer_exists(session, armed_env):
    """Liegt am eigenen Listing bereits ein höheres Angebot vor, wird das niedrigere verweigert."""
    client = _GateClient()
    client.raw_market = {
        "it": [
            {
                "i": "S1",
                "u": {"i": "ME", "n": "emil"},
                "ofs": [
                    {"u": "R1", "uop": 5_800_000, "uoid": "O_LOW"},
                    {"u": "R2", "uop": 6_200_000, "uoid": "O_HIGH"},
                ],
            }
        ]
    }
    ex = ActionExecutor(client, session, user_id="ME")

    low = await ex.execute(
        PlannedAction(
            action_type="accept",
            league_id="REAL",
            player_id="S1",
            amount=5_800_000,
            offer_id="O_LOW",
            rule_id="intent-accept",
        ),
        dry_run=False,
        now=NOW,
    )
    assert low.status == "refused"
    assert "höheres Angebot" in low.message
    assert client.calls == []

    high = await ex.execute(
        PlannedAction(
            action_type="accept",
            league_id="REAL",
            player_id="S1",
            amount=6_200_000,
            offer_id="O_HIGH",
            rule_id="intent-accept",
        ),
        dry_run=False,
        now=NOW,
    )
    assert high.status == "executed"
    assert client.calls == [("accept", "REAL", "S1", "O_HIGH")]


@pytest.mark.asyncio
async def test_sell_gate_blocks_autonomous_non_swap_sale_of_fresh_kb1_starter(session, armed_env):
    """Wird ein zum Verkauf geplanter Bankspieler zwischenzeitlich fitter KB-1-Starter
    mit positivem 24h-Trend, stoppt das Entscheidungs-Gate den Verkauf."""
    client = _GateClient()
    client.squad[0] = _squad_member("S1", 5_000_000, pos=3, fit=0, prob=1, chg=120_000)
    ex = ActionExecutor(client, session, user_id="ME")

    res = await ex.execute(
        PlannedAction(
            action_type="sell",
            league_id="REAL",
            player_id="S1",
            amount=5_000_000,
            rule_id="intent-sell",
            swap=False,
        ),
        dry_run=False,
        now=NOW,
    )
    assert res.status == "refused"
    assert "KB-1-Stammspieler" in res.message
    assert client.calls == []

    # Ein gekoppelter Tausch-Verkauf (swap=True) darf weiterhin ausgeführt werden.
    swap_res = await ex.execute(
        PlannedAction(
            action_type="sell",
            league_id="REAL",
            player_id="S1",
            amount=5_000_000,
            rule_id="intent-sell",
            swap=True,
            rationale="Tausch-Verkauf",
        ),
        dry_run=False,
        now=NOW,
    )
    assert swap_res.status == "executed"
    assert client.calls == [("sell", "REAL", "S1")]


@pytest.mark.asyncio
async def test_deficit_cover_sale_superseded_when_account_already_positive(session, armed_env):
    """Ist das Konto unmittelbar vor `sell_deficit_cover` bereits gedeckt, entfällt der Verkauf."""
    client = _GateClient()
    client.budget = 250_000
    client.pbaa = 250_000
    ex = ActionExecutor(client, session, user_id="ME")

    intent = Intent(
        created_at=NOW,
        updated_at=NOW,
        kind="sell",
        player_id="S1",
        player_name="Spieler Eins",
        team_id="T1",
        position=3,
        amount=5_000_000,
        rationale="Konto-Deckung",
        sources={"rule_id": "sell_deficit_cover", "league_id": "REAL"},
        listing_type="manager",
        deadline=None,
        execute_at=NOW,
        status=STATUS_PLANNED,
    )
    session.add(intent)
    session.commit()

    reason = await deficit_cover_unneeded(intent, ex, "REAL")
    assert reason is not None and "Konto bereits gedeckt" in reason

    status = await execute_intent(session, intent, ex, "REAL", now=NOW)
    assert status == STATUS_SUPERSEDED
    assert intent.status == STATUS_SUPERSEDED
    assert "Konto bereits gedeckt" in (intent.result or "")
    assert client.calls == []


# --------------------------------------------------------- 2. Active Delist Gate


def test_propose_delists_pulls_promoted_starter_and_keeps_emil_moonshot():
    """Beförderter LI-Starter (nicht mehr in bench_ids) wird vom Markt genommen;
    Emils 100-M-Mondpreis-Anker bleibt unangetastet."""
    squad_pos = {
        "PROMOTED": 3,
        "BENCH_SHOW": 3,
        "OLISE_MOON": 3,
        "gk1": 1,
        "d1": 2,
        "d2": 2,
        "d3": 2,
        "m1": 3,
        "m2": 3,
        "s1": 4,
    }
    raw = {
        "it": [
            {
                "i": "PROMOTED",
                "fn": "Ramon",
                "n": "Hendriks",
                "tid": "T1",
                "pos": 3,
                "mv": 8_000_000,
                "prc": 10_000_000,
                "exs": 0,
                "u": {"i": "ME", "n": "emil"},
            },
            {
                "i": "BENCH_SHOW",
                "fn": "Bank",
                "n": "Drücker",
                "tid": "T1",
                "pos": 3,
                "mv": 2_000_000,
                "prc": 2_500_000,
                "exs": 0,
                "u": {"i": "ME", "n": "emil"},
            },
            {
                "i": "OLISE_MOON",
                "fn": "Michael",
                "n": "Olise",
                "tid": "T2",
                "pos": 3,
                "mv": 65_000_000,
                "prc": 100_000_000,
                "exs": 0,
                "u": {"i": "ME", "n": "emil"},
            },
        ]
    }
    own = own_listings_from_raw(raw, "ME")
    assert len(own) == 3

    props = propose_delists(
        own,
        squad_pos,
        "REAL",
        Guardrails(protected_player_ids=frozenset({"OLISE_MOON"})),
        bench_ids=frozenset({"BENCH_SHOW"}),
    )
    assert [p.action.player_id for p in props] == ["PROMOTED"]
    drafts = delist_drafts(props, {item.player_id: item for item in own}, NOW)
    assert len(drafts) == 1
    assert drafts[0].kind == "delist" and drafts[0].player_id == "PROMOTED"


@pytest.mark.asyncio
async def test_delist_intent_executes_and_preflight_checks_market(session, armed_env):
    """AGENT_AUTO_INTENTS=1 ärmt `intent-delist`; steht der Spieler nicht mehr am Markt,
    verweigert der Preflight den überflüssigen Rückzug."""
    client = _GateClient()
    ex = ActionExecutor(client, session, user_id="ME")

    ok = await ex.execute(
        PlannedAction(
            action_type="delist",
            league_id="REAL",
            player_id="S1",
            rule_id="intent-delist",
        ),
        dry_run=False,
        now=NOW,
    )
    assert ok.status == "executed"
    assert client.calls == [("delist", "REAL", "S1")]

    gone = await ex.execute(
        PlannedAction(
            action_type="delist",
            league_id="REAL",
            player_id="NOT_LISTED",
            rule_id="intent-delist",
        ),
        dry_run=False,
        now=NOW,
    )
    assert gone.status == "refused"
    assert "nicht (mehr) am Transfermarkt" in gone.message


# --------------------------------------------------------- 3. Active Decline Gate


def test_propose_declines_rejects_lowballs_on_showcase_but_spares_emil_starter():
    """Auf Schaufenster-Bankspielern (125 % MW) werden Angebote < 115 % MW abgelehnt;
    auf Startern über 103 % MW bleibt die Entscheidung bei Emil."""
    squad_pos = {"BENCH1": 3, "STAR1": 3, "LOW1": 2}
    offers = [
        # 105 % MW auf einen 125-%-Schaufenster-Bankspieler (< 115 % Windfall-Schwelle) -> decline
        IncomingOffer(
            player_id="BENCH1",
            player_name="Bankspieler",
            team_id="T1",
            position=3,
            market_value=2_000_000,
            amount=2_100_000,
            offer_id="OB1",
            listing_price=2_500_000,
        ),
        # 108 % MW auf einen Starter mit 150-%-Wunschpreis -> NICHT auto-declinen (Emils Hoheit)
        IncomingOffer(
            player_id="STAR1",
            player_name="Stammspieler",
            team_id="T1",
            position=3,
            market_value=10_000_000,
            amount=10_800_000,
            offer_id="OS1",
            listing_price=15_000_000,
        ),
        # 100 % MW (< 103 % Floor) -> immer decline
        IncomingOffer(
            player_id="LOW1",
            player_name="UnterFloor",
            team_id="T1",
            position=2,
            market_value=4_000_000,
            amount=4_000_000,
            offer_id="OL1",
            listing_price=4_500_000,
        ),
    ]
    props = propose_declines(
        offers,
        squad_pos,
        "REAL",
        DEFAULT_GUARDRAILS,
        bench_ids=frozenset({"BENCH1"}),
    )
    assert [p.action.player_id for p in props] == ["BENCH1", "LOW1"]
    drafts = decline_drafts(props, {o.player_id: o for o in offers}, NOW)
    assert [d.kind for d in drafts] == ["decline", "decline"]
    assert drafts[0].sources["offer_id"] == "OB1"


@pytest.mark.asyncio
async def test_decline_intent_executes_and_verifies_offer_intact(session, armed_env):
    """AGENT_AUTO_INTENTS=1 ärmt `intent-decline`; verschwundene Angebote werden verweigert."""
    client = _GateClient()
    ex = ActionExecutor(client, session, user_id="ME")

    intent = Intent(
        created_at=NOW,
        updated_at=NOW,
        kind="decline",
        player_id="S1",
        player_name="Spieler Eins",
        team_id="T1",
        position=3,
        amount=5_800_000,
        rationale="Lowball ablehnen",
        sources={"offer_id": "O_LOW", "rule_id": "decline_lowball"},
        listing_type="offer",
        deadline=None,
        execute_at=NOW,
        status=STATUS_PLANNED,
    )
    session.add(intent)
    session.commit()

    status = await execute_intent(session, intent, ex, "REAL", now=NOW)
    assert status == STATUS_EXECUTED
    assert client.calls == [("decline", "REAL", "S1", "O_LOW")]

    gone = await ex.execute(
        PlannedAction(
            action_type="decline",
            league_id="REAL",
            player_id="S1",
            amount=5_800_000,
            offer_id="O_GONE",
            rule_id="intent-decline",
        ),
        dry_run=False,
        now=NOW,
    )
    assert gone.status == "refused"
    assert "steht nicht mehr am eigenen Listing" in gone.message


# --------------------------------------------------------- 4. Lineup Decision Gate


@pytest.mark.asyncio
async def test_lineup_gate_refuses_unfit_starter_when_fit_bench_replacement_exists(
    session, armed_env
):
    """Steht ein verletzter Spieler (fitness_status != 0) in der geplanten Startelf,
    obwohl auf derselben Position ein fitter Bankspieler im Kader ist, verweigert das Gate."""
    client = _GateClient()
    # S1 (pos 3) ist verletzt (fit=1), m3 (pos 3) sitzt fit (fit=0) auf der Bank.
    client.squad[0] = _squad_member("S1", 5_000_000, pos=3, fit=1)
    client.squad[8] = _squad_member("m3", 1_000_000, pos=3, fit=0)
    ex = ActionExecutor(client, session, user_id="ME")

    fielded = ["gk1", "d1", "d2", "d3", "d4", "S1", "m1", "m2", "s1", "s2", "x1"]
    bad = await ex.execute(
        PlannedAction(
            action_type="lineup",
            league_id="REAL",
            formation="4-3-3",
            player_ids=fielded,
            rule_id="auto-lineup",
        ),
        dry_run=False,
        now=NOW,
    )
    assert bad.status == "refused"
    assert "Spieler S1 ist nicht fit" in bad.message
    assert client.calls == []

    # Mit dem fitten m3 statt S1 geht die Aufstellung sauber durch.
    fixed = ["gk1", "d1", "d2", "d3", "d4", "m3", "m1", "m2", "s1", "s2", "x1"]
    good = await ex.execute(
        PlannedAction(
            action_type="lineup",
            league_id="REAL",
            formation="4-3-3",
            player_ids=fixed,
            rule_id="auto-lineup",
        ),
        dry_run=False,
        now=NOW,
    )
    assert good.status == "executed"
    assert len(client.calls) == 1


def test_purge_cascade_sells_hack_yamamoto_and_ulrich_before_sole_rising_starter_tigges():
    """Emil 21.10.2026: In der T-15-Notbremse müssen LI-Bankspieler (Hack, Yamamoto) und
    stark fallende Spieler (Ulrich -190k/Tag) zwingend vor einem formstarken Allein-Stürmer
    wie Tigges (+205k/Tag, 412 Punkte) verkauft werden."""
    from kickbase_agent.kickbase.models import Player, Position, SquadPlayer
    from kickbase_agent.worker.kickoff_guard import build_purge_cascade

    def _sp(pid: str, name: str, pos: int, mv: int, prob: int, pts: int, mv24: int) -> SquadPlayer:
        return SquadPlayer(
            player=Player(
                id=pid,
                first_name="",
                last_name=name,
                position=Position(pos),
                market_value=mv,
                team_id=f"T-{pid}",
                lineup_prob=prob,
                fitness_status=0,
                points=pts,
                mv_change_24h=mv24,
            ),
            is_starter=True,
        )

    squad = [
        _sp("GK", "Köhn", 1, 7_680_000, 1, 500, 80_000),
        _sp("D1", "Nusa", 2, 16_300_000, 1, 620, 150_000),
        _sp("D2", "Hendriks", 2, 7_570_000, 3, 290, 359_000),
        _sp("D3", "Ordonez", 2, 9_560_000, 1, 120, 140_000),
        _sp("M1", "Olise", 3, 66_000_000, 1, 1100, 90_000),
        _sp("M2", "Grifo", 3, 19_000_000, 1, 550, 110_000),
        _sp("M3", "Ouedraogo", 3, 14_700_000, 2, 480, 180_000),
        _sp("HACK", "Hack", 3, 5_360_000, 3, -31, 85_000),
        _sp("YAMA", "Yamamoto", 3, 2_370_000, 5, 0, -24_000),
        _sp("ULRICH", "Ulrich", 3, 5_710_000, 3, 120, -190_000),
        _sp("TIGGES", "Tigges", 4, 4_600_000, 3, 412, 205_000),
    ]
    fielded = {sp.player.id for sp in squad}
    cascade = build_purge_cascade(
        squad,
        fielded,
        set(),
        protected_ids=frozenset({"M1"}),
        li_bench_ids=frozenset({"HACK", "YAMA"}),
    )
    order = [pid for pid, _name, _mv, _tier in cascade]

    assert order[:3] == ["HACK", "YAMA", "ULRICH"]
    assert order.index("TIGGES") > order.index("ULRICH")
