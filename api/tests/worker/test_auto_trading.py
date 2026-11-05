"""Auto-Trading (Stufe 2, dormant): jedes Gate einzeln, Schatten ohne Writes,
scharfer Pfad mit Abbruch beim ersten nicht-executed."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import kickbase_agent.worker.auto_trading as at
from kickbase_agent.agent.executor import PlannedAction
from kickbase_agent.cli.plan import Plan
from kickbase_agent.db.models import Base, TransferAction
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS, Guardrails
from kickbase_agent.decision.models import Proposal
from kickbase_agent.kickbase.models import Player
from kickbase_agent.worker.auto_trading import (
    TodayCounts,
    gate_buys,
    gate_sells,
    load_today_state,
    run_auto_trading,
    select_auto_buys,
    select_auto_sells,
)

#: Settings-Stub für die reinen Gate-Funktionen (nur dieses Feld wird gelesen).
S = SimpleNamespace(agent_trading_max_per_player=1_500_000)


def _prop(
    pid="B1", amount=1_200_000, action_type="bid", approval=False, name="Kaufmann",
    reviewed=False,
):
    rule = "buy_explosion" if action_type == "bid" else "sell_weak"
    observed = datetime.now(UTC)
    return Proposal(
        action=PlannedAction(
            action_type=action_type, league_id="L1", player_id=pid, amount=amount, rule_id=rule,
            purchase_review={
                "schema_version": 1, "player_id": pid, "selected": True,
                "max_bid_eur": amount, "market_value_eur": amount,
                "observed_at": observed.isoformat(),
                "valid_until": (observed + timedelta(minutes=20)).isoformat(),
                "purpose": "starter", "reasons": ["Isolierter Auto-Trading-Runner-Test"],
            } if reviewed else None,
        ),
        rule_id=rule,
        rationale="r",
        score=0.7,
        requires_approval=approval,
        player_name=name,
    )


def _squad_member(pid: str, mv: int = 1_000_000):
    return SimpleNamespace(player=SimpleNamespace(id=pid, market_value=mv, position=3,
                                                  team_id="T9"))


def _plan(buys=(), sells=(), squad_pids=(), saturated=False) -> Plan:
    return Plan(
        league_id="L1",
        budget=20_000_000,
        buys=list(buys),
        sells=list(sells),
        squad=[_squad_member(pid) for pid in squad_pids],
        trade_count_saturated=saturated,
    )


# ------------------------------------------------------------- Kauf-Gates


def test_buy_over_limit_filtered():
    """Gebot über AGENT_TRADING_MAX_PER_PLAYER (1,5 M) läuft NIE autonom."""
    plan = _plan(buys=[_prop(amount=1_600_000), _prop(pid="B2", amount=1_500_000)])
    sel = select_auto_buys(plan, S, TodayCounts(), False)
    assert [p.action.player_id for p in sel] == ["B2"]  # exakt am Limit ist ok
    verdict = gate_buys(plan, S, TodayCounts(), False)[0]
    assert not verdict.passed and "Autonomie-Limit" in verdict.reason


def test_buy_big_intent_locked_for_stage_two():
    """requires_approval (> 5 M) fasst Stufe 2 nicht an — auch unterm Betrags-Limit."""
    plan = _plan(buys=[_prop(approval=True)])
    assert select_auto_buys(plan, S, TodayCounts(), False) == []
    verdict = gate_buys(plan, S, TodayCounts(), False)[0]
    assert "Groß-Vorhaben" in verdict.reason


def test_buy_daily_cap_respects_todays_executions():
    """Cap 3, heute schon 2 executed → nur noch EIN Kauf; Rest mit Cap-Grund."""
    plan = _plan(buys=[_prop(pid=f"B{i}") for i in range(3)])
    sel = select_auto_buys(plan, S, TodayCounts(buys=2), False, Guardrails(daily_buy_cap=3))
    assert [p.action.player_id for p in sel] == ["B0"]
    reasons = [v.reason for v in gate_buys(plan, S, TodayCounts(buys=2), False,
                                           Guardrails(daily_buy_cap=3))]
    assert reasons[0] == "ok"
    assert all("Tages-Cap" in r for r in reasons[1:])


def test_buy_in_own_squad_filtered():
    """Kaderspieler sind nie Kauf-Kandidaten (Core-Schutz, konservative Näherung)."""
    plan = _plan(buys=[_prop(pid="C1")], squad_pids=("C1",))
    assert select_auto_buys(plan, S, TodayCounts(), False) == []
    verdict = gate_buys(plan, S, TodayCounts(), False)[0]
    assert "Core-Schutz" in verdict.reason


def test_incident_blocks_all_candidates():
    """Incident (heute unknown/failed) stoppt Käufe UND Verkäufe komplett."""
    plan = _plan(buys=[_prop()], sells=[_prop(pid="T1", action_type="list")])
    assert select_auto_buys(plan, S, TodayCounts(), True) == []
    assert select_auto_sells(plan, S, TodayCounts(), True, {"T1"}) == []
    assert all(
        "Incident" in v.reason
        for v in gate_buys(plan, S, TodayCounts(), True)
        + gate_sells(plan, S, TodayCounts(), True, {"T1"})
    )


# ---------------------------------------------------------- Verkaufs-Gates


def test_sell_immediate_sell_not_for_stage_two():
    """way='sell' (Sofortverkauf) fasst Stufe 2 nicht an — nur 'list' ist erlaubt."""
    plan = _plan(sells=[_prop(pid="T1", action_type="sell"), _prop(pid="T2", action_type="list")])
    sel = select_auto_sells(plan, S, TodayCounts(), False, {"T1", "T2"})
    assert [p.action.player_id for p in sel] == ["T2"]
    verdict = gate_sells(plan, S, TodayCounts(), False, {"T1", "T2"})[0]
    assert "Stufe 2 listet nur" in verdict.reason


def test_sell_core_zone_allowed_for_fable51_listings():
    """Core ist kein Gate mehr: fallender MW darf autonom gelistet werden."""
    plan = _plan(sells=[_prop(pid="CORE", action_type="list"), _prop(pid="T1", action_type="list")])
    sel = select_auto_sells(plan, S, TodayCounts(), False, trading_ids={"T1"})
    assert [p.action.player_id for p in sel] == ["CORE", "T1"]
    assert all(v.reason == "ok" for v in gate_sells(plan, S, TodayCounts(), False, {"T1"}))


def test_sell_daily_cap_for_listings():
    """Listings laufen autonom auch über dem früheren Betrags-Limit; Cap bleibt."""
    plan = _plan(
        sells=[
            _prop(pid="T1", action_type="list", amount=2_000_000),
            _prop(pid="T2", action_type="list"),
            _prop(pid="T3", action_type="list"),
        ]
    )
    trading = {"T1", "T2", "T3"}
    sel = select_auto_sells(plan, S, TodayCounts(sells=2), False, trading,
                            Guardrails(daily_sell_cap=3))
    assert [p.action.player_id for p in sel] == ["T1"]  # T1 trotz >Limit, T2/T3 Cap
    reasons = [v.reason for v in gate_sells(plan, S, TodayCounts(sells=2), False, trading,
                                            Guardrails(daily_sell_cap=3))]
    assert reasons[0] == "ok"
    assert "Tages-Cap" in reasons[1] and "Tages-Cap" in reasons[2]


def test_shadow_report_contains_gates_and_is_quiet_when_empty():
    """Report nennt Würde-Ausführen + Gate-Filter; ohne Kandidaten → None (leise)."""
    plan = _plan(buys=[_prop(name="Wirtz"), _prop(pid="B9", approval=True, name="Star")])
    verdicts = gate_buys(plan, S, TodayCounts(), False)
    msg = at.format_shadow_report(verdicts, TodayCounts())
    assert "Auto-Trading (Schatten)" in msg and "hätte ausgeführt" in msg
    assert "Wirtz" in msg and "auto-trading-buy" in msg
    assert "Star" in msg and "Groß-Vorhaben" in msg  # Gate-Ergebnis des Gefilterten
    # Cap kommt aus den Guardrails (23.08. 5, seit 02.09. 8) — nie als Literal.
    assert f"Käufe 0/{DEFAULT_GUARDRAILS.daily_buy_cap}" in msg and "KEINE Writes" in msg
    assert at.format_shadow_report([], TodayCounts()) is None


# ------------------------------------------------- run_auto_trading (gemockt)


NOW = datetime.now(UTC)


class _FakeChannel:
    def __init__(self):
        self.sent: list[str] = []
        self.kinds: list[str | None] = []

    async def send_info(self, text: str, *, kind: str | None = None, title=None) -> None:
        self.sent.append(text)
        self.kinds.append(kind)


class _FakeClient:
    """Write-Client nach Muster tests/agent: Reads füttern den Default-Preflight."""

    def __init__(self, fail_bid: bool = False):
        self.calls: list[tuple] = []
        self.fail_bid = fail_bid
        self.budget = 20_000_000
        # MW = Gebot (1,2 M): der Nachanker (02.09.) greift so nicht — er hat
        # eigene Tests (tests/agent/test_bid_reanchor.py).
        # Domain-Testbedingungen für den frischen Zielspieler-Preflight;
        # diese kontrollierten Werte sind kein nachgebauter API-Payload.
        self.detail = Player(
            id="B1", first_name="", last_name="Kaufmann", position=3, team_id="T1",
            market_value=1_200_000, fitness_status=0, lineup_prob=1, mv_change_24h=26_065,
        )
        self.detail_reads = 0
        self.market = [SimpleNamespace(
            player=self.detail,
            price=1_200_000,
        )]
        self.squad = [_squad_member("S1", 900_000)]

    async def get_budget(self, league_id):
        return self.budget

    async def get_budget_fields(self, league_id):
        # pbaa = Konto nach offenen Geboten (Fake ohne offene Gebote) —
        # seit 23.08. verlangt der Bid-Preflight dieses Feld (fail-closed).
        return {"b": self.budget, "pbaa": self.budget}

    async def get_league_rules(self, league_id):
        return {"max_players_per_club": 11}

    async def get_own_bids(self, league_id, user_id):
        return []

    async def _get(self, path):
        return {"it": []}

    async def get_matchdays(self):
        # Spielplan fürs T-120-/T-48h-Fenster des Bid-Preflights (24.08.):
        # EIN fernes Fixture = Deadline bekannt, Fenster inaktiv (eine leere
        # Liste ist seit dem Codex-Re-Check fail-closed).
        from datetime import UTC as _UTC
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        return [SimpleNamespace(kickoff=_dt.now(_UTC) + _td(days=10), matchday=99,
                                is_finished=False)]

    async def get_market(self, league_id):
        return self.market

    async def get_player(self, league_id, player_id):
        assert player_id == self.detail.id
        self.detail_reads += 1
        return self.detail

    async def get_squad(self, league_id):
        return self.squad

    async def place_bid(self, league_id, player_id, price):
        self.calls.append(("place_bid", league_id, player_id, price))
        if self.fail_bid:
            raise RuntimeError("API kaputt")
        return {"ok": True}

    async def list_player(self, league_id, player_id, price):
        self.calls.append(("list", league_id, player_id, price))
        return {"listed": True}


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def env(monkeypatch):
    def _set(dry="1", auto_trading="0", allow_live="0", test_league=""):
        monkeypatch.setenv("AGENT_DRY_RUN", dry)
        monkeypatch.setenv("AGENT_TEST_LEAGUE_ID", test_league)
        monkeypatch.setenv("AGENT_ALLOW_LIVE", allow_live)
        monkeypatch.setenv("AGENT_AUTO_TRADING", auto_trading)
        monkeypatch.setenv("AGENT_AUTO_PURGE", "0")
        monkeypatch.setenv("AGENT_AUTO_BONUS", "0")
        monkeypatch.setenv("AGENT_AUTO_LINEUP", "0")
        monkeypatch.setenv("AGENT_APPROVE_LIVE", "0")
        # Tests warten nicht 2 s zwischen Writes.
        monkeypatch.setenv("AGENT_WRITE_SPACING_SECONDS", "0")
        import kickbase_agent.config as cfg

        cfg._settings = None

    yield _set
    import kickbase_agent.config as cfg

    cfg._settings = None


def _patch_world(monkeypatch, plan: Plan, engine, client: _FakeClient | None = None) -> dict:
    """build_plan, SessionLocal und Client-Fabrik auf die Test-Welt umbiegen."""

    async def fake_build_plan():
        return plan

    monkeypatch.setattr(at, "build_plan", fake_build_plan)
    monkeypatch.setattr(at, "SessionLocal", sessionmaker(bind=engine, future=True))
    world = {"factory_calls": 0, "client": client or _FakeClient()}

    async def fake_factory():
        world["factory_calls"] += 1
        return world["client"], SimpleNamespace(user_id="U1", league_id="L1")

    monkeypatch.setattr(at, "make_kickbase_client", fake_factory)
    return world


def _insert_action(engine, *, rule_id, action_type, status, when, pid="X1"):
    with Session(engine, future=True) as s:
        s.add(
            TransferAction(
                league_id="L1",
                action_type=action_type,
                player_id=pid,
                amount=1_000_000,
                status=status,
                idempotency_key=f"k-{rule_id}-{action_type}-{pid}-{status}-{when.isoformat()}",
                rule_id=rule_id,
                dry_run=False,
                created_at=when,
            )
        )
        s.commit()


def test_load_today_state_counts_prefix_and_incident(engine):
    """Zählt NUR heutige executed mit auto-trading-Präfix; Incident nur heute."""
    _insert_action(engine, rule_id="auto-trading-buy", action_type="bid",
                   status="executed", when=NOW)
    _insert_action(engine, rule_id="auto-trading-list", action_type="list",
                   status="executed", when=NOW, pid="X2")
    # zählt NICHT: fremde rule_id, anderer Tag, nicht-executed
    _insert_action(engine, rule_id="buy_explosion", action_type="bid",
                   status="executed", when=NOW, pid="X3")
    _insert_action(engine, rule_id="auto-trading-buy", action_type="bid",
                   status="executed", when=NOW - timedelta(days=1), pid="X4")
    _insert_action(engine, rule_id="auto-trading-buy", action_type="bid",
                   status="refused", when=NOW, pid="X5")
    with Session(engine, future=True) as s:
        counts, incident = load_today_state(s, NOW)
    assert (counts.buys, counts.sells, incident) == (1, 1, False)

    _insert_action(engine, rule_id="irgendwas", action_type="sell",
                   status="failed", when=NOW - timedelta(days=1), pid="X6")
    with Session(engine, future=True) as s:
        assert load_today_state(s, NOW)[1] is False  # gestriger Fehler blockt nicht
    _insert_action(engine, rule_id="irgendwas", action_type="sell",
                   status="unknown", when=NOW, pid="X7")
    with Session(engine, future=True) as s:
        assert load_today_state(s, NOW)[1] is True


@pytest.mark.asyncio
async def test_run_blocked_saturated_posts_alarm(monkeypatch, engine, env):
    """200er-Zählung gesättigt → sofortiger Stopp mit Alarmzeile, kein Client."""
    env()
    world = _patch_world(monkeypatch, _plan(buys=[_prop()], saturated=True), engine)
    channel = _FakeChannel()
    assert await run_auto_trading(channel) == "blocked_saturated"
    assert len(channel.sent) == 1 and "blockiert" in channel.sent[0]
    assert world["factory_calls"] == 0 and world["client"].calls == []


@pytest.mark.asyncio
async def test_run_blocked_incident_pauses_itself(monkeypatch, engine, env):
    """Heutige unknown/failed-Aktion (egal welche rule_id) → Komplett-Stopp."""
    env()
    _insert_action(engine, rule_id="sell_weak", action_type="sell", status="failed", when=NOW)
    world = _patch_world(monkeypatch, _plan(buys=[_prop()]), engine)
    channel = _FakeChannel()
    assert await run_auto_trading(channel) == "blocked_incident"
    assert "pausiert sich selbst" in channel.sent[0]
    assert world["factory_calls"] == 0 and world["client"].calls == []


@pytest.mark.asyncio
async def test_run_incident_yesterday_does_not_block(monkeypatch, engine, env):
    """Nur HEUTIGE Incidents stoppen — ein gestriger failed läuft normal weiter."""
    env()
    _insert_action(
        engine, rule_id="sell_weak", action_type="sell",
        status="failed", when=NOW - timedelta(days=1),
    )
    _patch_world(monkeypatch, _plan(buys=[_prop()]), engine)
    channel = _FakeChannel()
    reports = []
    monkeypatch.setattr(at, "save_report", lambda **kw: reports.append(kw) or 1)
    assert await run_auto_trading(channel) == "shadow"  # Default-Flag aus → Schatten
    # Meldungs-Umbau 31.08.: Schatten-Report nur noch ins Archiv, kein Discord-Post.
    assert channel.sent == [] and [r["kind"] for r in reports] == ["auto_trading"]


@pytest.mark.asyncio
async def test_run_quiet_without_passing_candidates(monkeypatch, engine, env):
    """Leerer Plan ODER alles weggefiltert → 'quiet', kein Post."""
    env()
    _patch_world(monkeypatch, _plan(), engine)
    channel = _FakeChannel()
    assert await run_auto_trading(channel) == "quiet"
    assert channel.sent == []
    # alles gefiltert (nur Approval-Kandidat) → ebenfalls leise
    _patch_world(monkeypatch, _plan(buys=[_prop(approval=True)]), engine)
    assert await run_auto_trading(channel) == "quiet"
    assert channel.sent == []


@pytest.mark.asyncio
async def test_run_shadow_posts_report_without_writes(monkeypatch, engine, env):
    """Flag AUS (Default): Report mit Gate-Ergebnissen, aber NULL Writes/Clients."""
    env(dry="0")  # selbst ohne Not-Aus darf der Schatten-Modus nichts schreiben
    _insert_action(  # T-Zone: S1 wurde als Trading gekauft (gestern, executed)
        engine, rule_id="trading_buy", action_type="bid",
        status="executed", when=NOW - timedelta(days=1), pid="S1",
    )
    plan = _plan(
        buys=[_prop(name="Wirtz"), _prop(pid="B9", amount=9_000_000, name="Teuer")],
        sells=[_prop(pid="S1", action_type="list", amount=900_000, name="Trader")],
        squad_pids=("S1",),
    )
    world = _patch_world(monkeypatch, plan, engine)
    channel = _FakeChannel()

    reports = []
    monkeypatch.setattr(at, "save_report", lambda **kw: reports.append(kw) or 1)
    assert await run_auto_trading(channel) == "shadow"

    assert world["factory_calls"] == 0 and world["client"].calls == []  # keine Writes
    with Session(engine, future=True) as s:  # kein neuer Audit-Eintrag entstanden
        assert s.query(TransferAction).count() == 1
    # Meldungs-Umbau 31.08.: Report landet im Archiv (Cockpit), nicht in Discord.
    assert channel.sent == []
    msg = reports[0]["body"]
    assert "Auto-Trading (Schatten)" in msg and "hätte ausgeführt" in msg
    assert "Wirtz" in msg and "auto-trading-buy" in msg
    assert "Trader" in msg and "auto-trading-list" in msg
    assert "Teuer" in msg and "Autonomie-Limit" in msg  # Gate-Ergebnis sichtbar


@pytest.mark.asyncio
async def test_run_live_executes_with_auto_trading_rule_ids(monkeypatch, engine, env):
    """Flag AN: echte Executor-Writes (Interlock via AGENT_AUTO_TRADING allein),
    rule_ids 'auto-trading-buy'/'auto-trading-list' landen im Audit-Log."""
    env(dry="0", auto_trading="1")  # allow_live=0, keine Test-Liga!
    _insert_action(
        engine, rule_id="trading_buy", action_type="bid",
        status="executed", when=NOW - timedelta(days=1), pid="S1",
    )
    plan = _plan(
        buys=[_prop(name="Wirtz", reviewed=True)],
        sells=[_prop(pid="S1", action_type="list", amount=900_000, name="Trader")],
        squad_pids=("S1",),
    )
    world = _patch_world(monkeypatch, plan, engine)
    channel = _FakeChannel()

    assert await run_auto_trading(channel) == "executed"
    assert channel.kinds == ["auto_trading"]
    assert world["client"].detail_reads == 1

    assert world["client"].calls == [
        ("place_bid", "L1", "B1", 1_200_000),
        ("list", "L1", "S1", 900_000),
    ]
    with Session(engine, future=True) as s:
        rules = {
            r.rule_id: r.status
            for r in s.query(TransferAction).filter(
                TransferAction.rule_id.like("auto-trading%")
            )
        }
    assert rules == {"auto-trading-buy": "executed", "auto-trading-list": "executed"}
    assert "Auto-Trading (LIVE)" in channel.sent[0] and "→ executed" in channel.sent[0]


@pytest.mark.asyncio
async def test_run_live_aborts_on_first_non_executed(monkeypatch, engine, env):
    """Erster Write endet nicht als executed → Restliste wird NICHT angefasst."""
    env(dry="0", auto_trading="1")
    _insert_action(
        engine, rule_id="trading_buy", action_type="bid",
        status="executed", when=NOW - timedelta(days=1), pid="S1",
    )
    plan = _plan(
        buys=[_prop(name="Wirtz", reviewed=True)],
        sells=[_prop(pid="S1", action_type="list", amount=900_000, name="Trader")],
        squad_pids=("S1",),
    )
    world = _patch_world(monkeypatch, plan, engine, client=_FakeClient(fail_bid=True))
    channel = _FakeChannel()

    assert await run_auto_trading(channel) == "aborted"
    assert world["client"].detail_reads == 1

    assert world["client"].calls == [("place_bid", "L1", "B1", 1_200_000)]  # kein list!
    msg = channel.sent[0]
    assert "ABGEBROCHEN" in msg and "failed" in msg
    assert "Trader" in msg and "übersprungen" in msg
