"""Tausch auf Planungs-Dispo, der ganze Weg (Emil 22./23.09.2026).

Plan (cli/plan.build_plan) → Kaufbeleg (purchase_review_veto) → Planner
(plan_intents/upsert_intents: Gebot UND wartender Sofortverkauf) → Intent-Executor
(execute_intent → ActionExecutor-Preflight mit team_funding_veto) → Zuschlag →
bedingter Verkauf. Dazu die Gegenproben: ohne Kopplung, Kopplung an einen
geschützten Spieler, netto ungedeckt, 33-%-Grenze, nach dem 05.10. — jeweils kein
Gebot und kein verwaister Verkauf.

Keine neuen API-Fixtures: dieselbe Domain-Welt wie tests/cli/test_plan_dispo.py,
ergänzt um die Schreib-/Lesemethoden, die der Default-Preflight braucht.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import kickbase_agent.decision.purchase_review as review_mod
from kickbase_agent.agent.executor import ActionExecutor
from kickbase_agent.cli import plan as plan_mod
from kickbase_agent.cli.plan import load_swap_couplings as real_swap_couplings
from kickbase_agent.db.models import Base, Intent
from kickbase_agent.decision.purchase_review import purchase_review_veto
from kickbase_agent.worker.auto_trading import TodayCounts
from kickbase_agent.worker.intents import (
    MarketRow,
    execute_intent,
    plan_intents,
    upsert_intents,
)

from .test_plan_dispo import DISPO_AUS, WEAK, _swap_world

L1 = "L1"


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s
    engine.dispose()


@pytest.fixture
def armed(monkeypatch):
    for key, value in {
        "AGENT_DRY_RUN": "0", "AGENT_TEST_LEAGUE_ID": "", "AGENT_ALLOW_LIVE": "0",
        "AGENT_AUTO_INTENTS": "1", "AGENT_AUTO_TRADING": "0", "AGENT_AUTO_PURGE": "0",
        "AGENT_AUTO_BONUS": "0", "AGENT_AUTO_LINEUP": "0", "AGENT_APPROVE_LIVE": "0",
        "AGENT_WRITE_SPACING_SECONDS": "0",
    }.items():
        monkeypatch.setenv(key, value)
    import kickbase_agent.config as cfg

    cfg._settings = None
    yield
    cfg._settings = None


async def _world(monkeypatch):
    """Plan-Welt plus die Lese-/Schreibmethoden des Executors (Aufzeichnung)."""
    client = await _swap_world(monkeypatch)
    client.calls = []
    squad = await client.get_squad(L1)

    async def get_matchdays():
        kickoff = datetime.now(UTC) + timedelta(days=3)
        return [SimpleNamespace(kickoff=kickoff, matchday=5, is_finished=False)]

    async def get_budget_fields(league_id):
        return {"b": client.budget}

    async def raw_get(path):
        return {"it": []}  # keine aktuell aufgestellten Starter im rohen Kader

    async def place_bid(league_id, player_id, price):
        client.calls.append(("place_bid", league_id, player_id, price))
        return {"ok": True}

    async def sell_to_kickbase(league_id, player_id):
        client.calls.append(("sell", league_id, player_id))
        return {"sold": True}

    client.get_matchdays = get_matchdays
    client.get_budget_fields = get_budget_fields
    client._get = raw_get
    client.place_bid = place_bid
    client.sell_to_kickbase = sell_to_kickbase
    return client, squad


def _rows(client) -> list[MarketRow]:
    return [
        MarketRow(
            player_id=mp.player.id, price=mp.price,
            expires_in_s=int((mp.expires_at - datetime.now(UTC)).total_seconds()),
            seller_id="", seller_name="", player_name=mp.player.last_name,
            team_id=mp.player.team_id, position=int(mp.player.position),
            kb_prob=mp.player.lineup_prob, our_bid=None, is_ours=False,
        )
        for mp in client.market
    ]


def _stage(session, plan, client, now, *, drafts=None, counts=None, active=None):
    drafts = plan_intents(plan, _rows(client), now) if drafts is None else drafts
    stats = upsert_intents(
        session, drafts, now, purchase_reviews=plan.purchase_reviews,
        market_pids={mp.player.id for mp in client.market},
        squad_pids={sp.player.id for sp in plan.squad}, counts=counts,
        active_bid_targets=active,
    )
    return drafts, stats, {(r.kind, r.player_id): r for r in session.query(Intent).all()}


# ------------------------------------------------------------ der ganze Weg


async def test_tausch_auf_dispo_vom_plan_bis_zum_verkauf(monkeypatch, session, armed):
    client, squad = await _world(monkeypatch)

    # 1) Plan: Tausch-Paar statt Kauf, Kaufbeleg trägt den gekoppelten Verkauf.
    plan = await plan_mod.build_plan()
    now = datetime.now(UTC)  # nach dem Plan: Belege gelten ab ihrer Beobachtung
    [pair] = plan.swaps
    review = pair.bid.action.purchase_review
    amount = pair.bid.action.amount
    assert review["swap_sale"]["player_id"] == WEAK
    assert amount > client.budget  # brutto über dem Konto: nur als Tausch finanziert
    assert purchase_review_veto(review, player_id="st", amount=amount, now=now) is None

    # 2) Planner: Gebot UND wartender Sofortverkauf, eindeutig verknüpft.
    _, stats, rows = _stage(session, plan, client, now)
    bid, sell = rows[("bid", "st")], rows[("sell", WEAK)]
    assert {r.kind for r in stats.created} == {"bid", "sell"}
    assert bid.sources["swap_out"] == WEAK
    assert bid.sources["purchase_review"]["swap_sale"] == review["swap_sale"]
    assert sell.sources["rule_id"] == "swap_sell" and sell.sources["swap_for"] == "st"
    assert sell.sources["after_squad_size"] == 12 and sell.sources["swap_dispo"] is True
    assert sell.amount == 13_000_000

    # 3) Executor-Preflight (frisches Konto 2 M, keine Gebote, Kader 11): Gebot geht.
    executor = ActionExecutor(client, session, user_id="u1")
    assert await execute_intent(session, bid, executor, L1, now=now) == "executed", bid.result
    assert client.calls == [("place_bid", L1, "st", amount)]

    # 4) Vor dem Zuschlag wartet der Verkauf — nichts wird verkauft.
    assert await execute_intent(session, sell, executor, L1, now=now) == "planned"
    assert len(client.calls) == 1

    # 5) Zuschlag: Ziel im Kader, Konto im Minus. Jetzt geht genau der gekoppelte Verkauf.
    [listing] = client.market
    squad.append(SimpleNamespace(player=listing.player, is_starter=False))
    client.budget -= amount
    client.market = []
    assert await execute_intent(session, sell, executor, L1, now=now) == "executed", sell.result
    assert client.calls[-1] == ("sell", L1, WEAK)


# ------------------------------------------------------------ Gegenproben Planner


def _break_coupling(review):
    review.pop("swap_sale")
    review["team_plan"]["swap_sales"] = []


def _olise(review, monkeypatch):
    monkeypatch.setenv("PROTECTED_PLAYER_IDS", f"8329,{WEAK}")


def _net(review):
    review["team_plan"]["budget_eur"] = 0  # 12 M − 11,7 M > 0


def _floor(review):
    review["team_plan"]["team_value_eur"] = 3_000_000  # Grenze −1 M, Konto −10 M


def _deadline(review, monkeypatch):
    monkeypatch.setattr(review_mod, "PLANNING_OVERDRAFT_UNTIL", DISPO_AUS)


@pytest.mark.parametrize("breaker", [_break_coupling, _olise, _net, _floor, _deadline])
async def test_ohne_gueltige_tauschdeckung_kein_gebot_und_kein_verkauf(
    monkeypatch, session, breaker,
):
    client, _ = await _world(monkeypatch)
    plan = await plan_mod.build_plan()
    now = datetime.now(UTC)
    review = plan.purchase_reviews["st"]
    if breaker in (_olise, _deadline):
        breaker(review, monkeypatch)
    else:
        breaker(review)
    assert purchase_review_veto(review, player_id="st", amount=plan.swaps[0].bid.action.amount,
                                now=now) is not None

    _, stats, rows = _stage(session, plan, client, now)

    assert stats.created == [] and rows == {}


async def test_ohne_gekoppelten_verkaufsentwurf_kein_dispo_gebot(monkeypatch, session):
    client, _ = await _world(monkeypatch)
    plan = await plan_mod.build_plan()
    now = datetime.now(UTC)
    drafts = [d for d in plan_intents(plan, _rows(client), now) if d.kind == "bid"]

    _, stats, rows = _stage(session, plan, client, now, drafts=drafts)

    assert stats.created == [] and rows == {}
    # Befund 03.10.2026: So verschwand am 02.10. das Obermair-Gebot — jetzt mit Spieler und Grund.
    assert [pid for pid, _reason in stats.dropped] == ["st"]


async def test_ein_schon_gelaufener_verkauf_koppelt_kein_neues_dispo_gebot(monkeypatch, session):
    """Der Tausch-Verkauf lief bereits (er deckt den Kaufbeleg), wartet aber nicht mehr auf dieses
    Gebot: Das Gebot entsteht nicht allein — und der Planer nennt den Grund."""
    client, _ = await _world(monkeypatch)
    plan = await plan_mod.build_plan()
    now = datetime.now(UTC)
    session.add(Intent(
        created_at=now, updated_at=now, kind="sell", player_id=WEAK, player_name="Schwach",
        amount=9_000_000, sources={"swap_dispo": True, "swap_for": "st", "league_id": L1},
        execute_at=now, status="executed",
    ))
    session.commit()
    drafts = [d for d in plan_intents(plan, _rows(client), now) if d.kind == "bid"]

    _, stats, rows = _stage(session, plan, client, now, drafts=drafts)

    assert ("bid", "st") not in rows
    assert stats.dropped == [("st", "Dispo-Gebot ohne gekoppelten Tausch-Verkauf")]


async def test_gesperrter_oder_gedeckelter_verkauf_verhindert_das_dispo_gebot(
    monkeypatch, session,
):
    client, _ = await _world(monkeypatch)
    plan = await plan_mod.build_plan()
    now = datetime.now(UTC)
    # Verkaufs-Tageskontingent ausgeschöpft: der Verkauf fiele weg — dann auch das Gebot.
    full = TodayCounts(sells=10_000)

    _, stats, rows = _stage(session, plan, client, now, counts=full)

    assert rows == {} and stats.created == []
    assert {d.kind for d in stats.skipped_cap} == {"sell"}
    assert {d.kind for d in stats.skipped_blocked} == {"bid"}
    # Codex-Review 03.10.2026: auch dieser Abwurf nennt Spieler und Grund.
    assert [pid for pid, _reason in stats.dropped] == ["st"]


async def test_vetoierter_verkauf_raeumt_das_offene_dispo_gebot_beim_naechsten_plan(
    monkeypatch, session,
):
    client, _ = await _world(monkeypatch)
    plan = await plan_mod.build_plan()
    now = datetime.now(UTC)
    _, _, rows = _stage(session, plan, client, now)
    bid, sell = rows[("bid", "st")], rows[("sell", WEAK)]
    sell.status, sell.updated_at = "vetoed", now  # Emil überspringt den Verkauf
    session.commit()

    _, stats, _ = _stage(session, plan, client, now)

    assert bid in stats.superseded and bid.status == "superseded"
    assert not any(r.kind == "sell" and r.status == "planned"
                   for r in session.query(Intent).all())


async def test_gesperrtes_gebot_legt_keinen_verkauf_an(monkeypatch, session):
    client, _ = await _world(monkeypatch)
    plan = await plan_mod.build_plan()
    now = datetime.now(UTC)
    drafts = plan_intents(plan, _rows(client), now)
    bid_draft = next(d for d in drafts if d.kind == "bid")
    # Heute schon an diesem Ziel gescheitert: das Gebot ist für heute gesperrt.
    session.add(Intent(
        created_at=now, updated_at=now, kind="bid", player_id="st", player_name="st",
        team_id="club-st", position=4, amount=bid_draft.amount, rationale="r",
        sources={"league_id": L1}, listing_type="kickbase", execute_at=now,
        status="failed",
    ))
    session.commit()

    _, stats, rows = _stage(session, plan, client, now, drafts=drafts)

    assert set(rows) == {("bid", "st")} and rows[("bid", "st")].status == "failed"
    assert stats.created == []


# ------------------------------------------------------------ Gegenproben Executor


async def test_vetoierter_verkauf_stoppt_das_dispo_gebot_vor_dem_write(
    monkeypatch, session, armed,
):
    client, _ = await _world(monkeypatch)
    plan = await plan_mod.build_plan()
    now = datetime.now(UTC)
    _, _, rows = _stage(session, plan, client, now)
    bid, sell = rows[("bid", "st")], rows[("sell", WEAK)]
    sell.status = "vetoed"  # Emil überspringt den Verkauf
    session.commit()

    executor = ActionExecutor(client, session, user_id="u1")
    assert await execute_intent(session, bid, executor, L1, now=now) == "failed"
    assert "Tausch-Kopplung" in bid.result
    assert client.calls == []


async def test_frisch_ungedecktes_konto_verweigert_das_dispo_gebot_im_preflight(
    monkeypatch, session, armed,
):
    client, _ = await _world(monkeypatch)
    plan = await plan_mod.build_plan()
    now = datetime.now(UTC)
    _, _, rows = _stage(session, plan, client, now)
    bid = rows[("bid", "st")]
    client.budget = 0  # netto 12 M − 11,7 M > 0: zwischen Plan und Write Geld verloren

    executor = ActionExecutor(client, session, user_id="u1")
    assert await execute_intent(session, bid, executor, L1, now=now) == "failed"
    assert "netto nicht gedeckt" in bid.result
    assert client.calls == []


# ------------------------------------------------------------ Review 23.09.: Befund 1
#
# Ein Dispo-Verkauf, dessen gekoppeltes Gebot ausgeführt ist und dessen Auktion noch
# läuft (oder dessen Ziel schon im Kader steht), bleibt IMMER stehen — unabhängig davon,
# ob der nächste Plan das Ziel wieder gültig auswählt. Sonst: Zuschlag ohne Verkauf.


async def _live_dispo_bid(monkeypatch, session):
    """Tausch-Paar angelegt, Gebot ausgeführt und live; der Plan kennt die Kopplung."""
    client, squad = await _world(monkeypatch)
    plan = await plan_mod.build_plan()
    now = datetime.now(UTC)
    _, _, rows = _stage(session, plan, client, now)
    bid, sell = rows[("bid", "st")], rows[("sell", WEAK)]
    executor = ActionExecutor(client, session, user_id="u1")
    assert await execute_intent(session, bid, executor, L1, now=now) == "executed", bid.result
    client.own_bids = [{
        "player_id": "st", "name": "st", "team_id": "club-st", "amount": bid.amount,
        "offer_id": "o-st", "expires_in_s": 3600,
    }]
    monkeypatch.setattr(plan_mod, "load_swap_couplings",
                        lambda *args: real_swap_couplings(session, L1))
    return client, squad, bid, sell


def _trigger_503(client, monkeypatch):
    original = client.get_player

    async def get_player(league_id, player_id):
        if player_id == "st":
            raise RuntimeError("HTTP 503 Service Unavailable")
        return await original(league_id, player_id)

    client.get_player = get_player


def _trigger_kb4(client, monkeypatch):
    [listing] = client.market
    client.market = [listing.model_copy(
        update={"player": listing.player.model_copy(update={"lineup_prob": 4})})]


def _trigger_unfit(client, monkeypatch):
    [listing] = client.market
    client.market = [listing.model_copy(
        update={"player": listing.player.model_copy(update={"fitness_status": 1})})]


def _trigger_deadline(client, monkeypatch):
    monkeypatch.setattr(plan_mod, "PLANNING_OVERDRAFT_UNTIL", DISPO_AUS)
    monkeypatch.setattr(review_mod, "PLANNING_OVERDRAFT_UNTIL", DISPO_AUS)


@pytest.mark.parametrize(
    "trigger", [_trigger_503, _trigger_kb4, _trigger_unfit, _trigger_deadline],
    ids=["detail-503", "kb-stufe-4", "unfit", "dispo-frist"],
)
async def test_dispo_verkauf_bleibt_bei_live_gebot_egal_was_der_neue_plan_sagt(
    monkeypatch, session, armed, trigger,
):
    client, _, bid, sell = await _live_dispo_bid(monkeypatch, session)
    trigger(client, monkeypatch)

    plan = await plan_mod.build_plan()
    # Der neue Plan wählt das Ziel nicht mehr gültig aus (das war der Auslöser).
    assert purchase_review_veto(plan.purchase_reviews.get("st"), player_id="st",
                                amount=bid.amount, now=datetime.now(UTC)) is not None
    now = datetime.now(UTC)
    _stage(session, plan, client, now, active={"st": "kickbase"})

    session.refresh(sell)
    assert sell.status == "planned", sell.result
    assert bid.status == "executed"


async def test_dispo_verkauf_bleibt_nach_dem_zuschlag_auch_ohne_neue_auswahl(
    monkeypatch, session, armed,
):
    client, squad, _, sell = await _live_dispo_bid(monkeypatch, session)
    [listing] = client.market
    squad.append(SimpleNamespace(player=listing.player, is_starter=False))
    client.market, client.own_bids = [], []
    client.budget -= 12_000_000
    _trigger_deadline(client, monkeypatch)

    plan = await plan_mod.build_plan()
    _stage(session, plan, client, datetime.now(UTC), active={})

    session.refresh(sell)
    assert sell.status == "planned", sell.result


async def test_dispo_verkauf_endet_wenn_die_auktion_ohne_zuschlag_endet(
    monkeypatch, session, armed,
):
    client, _, bid, sell = await _live_dispo_bid(monkeypatch, session)
    client.market, client.own_bids = [], []  # Auktion vorbei, Ziel nicht im Kader
    later = bid.deadline.replace(tzinfo=UTC) + timedelta(hours=1)

    plan = await plan_mod.build_plan()
    _stage(session, plan, client, later, active={})

    session.refresh(sell)
    assert sell.status == "superseded"


async def test_dispo_verkauf_wartet_kurz_nach_auktionsende_auf_den_kader(
    monkeypatch, session, armed,
):
    """Wettlauf: Die Auktion ist gerade vorbei, der Kader im Plan aber älter als der
    Markt — ohne Karenz räumte der Planner den Verkauf eines gewonnenen Tauschs ab."""
    client, _, bid, sell = await _live_dispo_bid(monkeypatch, session)
    client.market, client.own_bids = [], []
    just_after = bid.deadline.replace(tzinfo=UTC) + timedelta(minutes=5)

    plan = await plan_mod.build_plan()
    _stage(session, plan, client, just_after, active={})

    session.refresh(sell)
    assert sell.status == "planned", sell.result



# ------------------------------------------------------------ Review 23.09.: Befund 3


async def _two_striker_world(monkeypatch):
    """4-4-2 mit zwei schwachen Stürmern (roster-9, roster-10), am Markt zwei bessere."""
    from kickbase_agent.cli.plan import load_pending_bid_commitments as real_pending

    from .test_plan_dispo import _forecast, _kader, _striker

    specs = [(1, 13_000_000)] + [(2, 13_000_000)] * 4 + [(3, 13_000_000)] * 4 \
        + [(4, 13_000_000)] * 2
    client, squad = await _world(monkeypatch)
    squad[:] = _kader(specs)
    _forecast(monkeypatch, {"roster-9": 0.3, WEAK: 0.3, "st": 0.9, "st2": 0.9})
    client.market = [_striker(12_000_000, "st"), _striker(12_000_000, "st2")]
    return client, real_pending


async def test_hoechstens_ein_offenes_dispo_paar_ueber_zwei_planlaeufe(monkeypatch, session):
    client, real_pending = await _two_striker_world(monkeypatch)
    first = await plan_mod.build_plan()
    assert len(first.swaps) == 1
    _stage(session, first, client, datetime.now(UTC))
    # Zweiter Lauf: der Plan liest die angelegten Vorhaben wie in Produktion.
    monkeypatch.setattr(plan_mod, "load_swap_couplings",
                        lambda *args: real_swap_couplings(session, L1))
    monkeypatch.setattr(plan_mod, "load_pending_bid_commitments",
                        lambda *args: real_pending(session, L1, datetime.now(UTC)))

    second = await plan_mod.build_plan()
    _stage(session, second, client, datetime.now(UTC))

    assert second.swaps == []
    planned = [r for r in session.query(Intent).all() if r.status == "planned"]
    assert sorted(r.kind for r in planned) == ["bid", "sell"]



# ------------------------------------------------------------ Review 23.09.: Befund 4


async def _pair_plus_upgrade(monkeypatch, session):
    """Plan mit Tausch-Paar (st ↔ WEAK) UND einem normalen Kauf abw aus demselben Plan."""
    from .test_plan_dispo import _forecast, _striker
    from .test_plan_roster_value import _replacement

    client, squad = await _world(monkeypatch)
    _forecast(monkeypatch, {WEAK: 0.3, "st": 0.9, "abw": 0.95})
    client.market = [_striker(12_000_000, "st"), _replacement(mv=1_000_000, pid="abw")]
    plan = await plan_mod.build_plan()
    assert [b.action.player_id for b in plan.buys] == ["abw"]
    now = datetime.now(UTC)
    _, _, rows = _stage(session, plan, client, now)
    return client, plan, now, rows


async def test_anderer_kauf_bekommt_keine_tauschanrechnung_ohne_wartenden_verkauf(
    monkeypatch, session, armed,
):
    client, _, now, rows = await _pair_plus_upgrade(monkeypatch, session)
    bid_st, sell, bid_abw = rows[("bid", "st")], rows[("sell", WEAK)], rows[("bid", "abw")]
    executor = ActionExecutor(client, session, user_id="u1")
    assert await execute_intent(session, bid_st, executor, L1, now=now) == "executed"
    client.own_bids = [{
        "player_id": "st", "name": "st", "team_id": "club-st", "amount": bid_st.amount,
        "offer_id": "o-st", "expires_in_s": 3600,
    }]
    sell.status, sell.updated_at = "vetoed", now  # Emil überspringt den Verkauf
    session.commit()

    status = await execute_intent(session, bid_abw, executor, L1, now=now)

    assert status == "failed"
    assert "Tausch" in bid_abw.result
    assert [c for c in client.calls if c[2] == "abw"] == []


async def test_anderer_kauf_mit_wartendem_verkauf_behaelt_die_anrechnung(
    monkeypatch, session, armed,
):
    client, _, now, rows = await _pair_plus_upgrade(monkeypatch, session)
    bid_st, bid_abw = rows[("bid", "st")], rows[("bid", "abw")]
    executor = ActionExecutor(client, session, user_id="u1")
    assert await execute_intent(session, bid_st, executor, L1, now=now) == "executed"
    client.own_bids = [{
        "player_id": "st", "name": "st", "team_id": "club-st", "amount": bid_st.amount,
        "offer_id": "o-st", "expires_in_s": 3600,
    }]

    status = await execute_intent(session, bid_abw, executor, L1, now=now)

    assert status == "executed", bid_abw.result


async def test_planner_rechnet_nur_wartende_tauschverkaeufe_an(monkeypatch, session):
    """Neuer Planlauf mit altem Plan (Verkauf übersprungen, Gebot st live): das
    Vorhaben abw wird im Planner geräumt, nicht erst beim Write."""
    client, plan, now, rows = await _pair_plus_upgrade(monkeypatch, session)
    rows[("bid", "st")].status = "executed"
    rows[("sell", WEAK)].status, rows[("sell", WEAK)].updated_at = "vetoed", now
    session.commit()

    _, stats, _ = _stage(session, plan, client, now, active={"st": "kickbase"})

    assert rows[("bid", "abw")] in stats.superseded


# ------------------------------------------------------------ Review 23.09.: Befund 2a


def _set_market_value(squad, pid, value):
    for index, sp in enumerate(squad):
        if sp.player.id == pid:
            squad[index] = sp.model_copy(
                update={"player": sp.player.model_copy(update={"market_value": value})})


async def test_tauschverkauf_nimmt_den_frischen_marktwert_statt_des_planbetrags(
    monkeypatch, session, armed,
):
    """MW-Drift > 20 % zwischen Plan und Zuschlag: Der Executor verweigerte den Verkauf
    am veralteten Betrag, und das Konto blieb nach dem Zuschlag im Minus."""
    client, squad = await _world(monkeypatch)
    plan = await plan_mod.build_plan()
    now = datetime.now(UTC)
    _, _, rows = _stage(session, plan, client, now)
    bid, sell = rows[("bid", "st")], rows[("sell", WEAK)]
    executor = ActionExecutor(client, session, user_id="u1")
    assert await execute_intent(session, bid, executor, L1, now=now) == "executed"
    [listing] = client.market
    squad.append(SimpleNamespace(player=listing.player, is_starter=False))
    client.budget -= bid.amount
    client.market = []
    _set_market_value(squad, WEAK, 9_000_000)  # −31 % gegenüber dem Planbetrag 13 M

    assert await execute_intent(session, sell, executor, L1, now=now) == "executed", sell.result
    assert client.calls[-1] == ("sell", L1, WEAK)
    assert sell.amount == 9_000_000


async def test_nur_dispo_verkaeufe_bekommen_den_frischen_marktwert(session):
    """Normale Tauschverkäufe (ohne Dispo) behalten die Drift-Sicherung am Planbetrag."""
    from kickbase_agent.worker.intents import _refresh_dispo_sale_amount

    async def get_squad(league_id):
        return [SimpleNamespace(player=SimpleNamespace(id=WEAK, market_value=9_000_000))]

    executor = SimpleNamespace(_client=SimpleNamespace(get_squad=get_squad))
    now = datetime.now(UTC)
    plain = Intent(kind="sell", player_id=WEAK, amount=13_000_000, sources={
        "rule_id": "swap_sell", "swap_for": "st", "after_squad_size": 12})
    dispo = Intent(kind="sell", player_id=WEAK, amount=13_000_000, sources={
        "rule_id": "swap_sell", "swap_for": "st", "after_squad_size": 12, "swap_dispo": True})
    for row in (plain, dispo):
        row.created_at = row.updated_at = row.execute_at = now
        row.player_name, row.team_id, row.position, row.rationale = WEAK, "10", 4, "r"
        row.status = "planned"
        session.add(row)
    session.commit()

    await _refresh_dispo_sale_amount(session, plain, executor, L1)
    await _refresh_dispo_sale_amount(session, dispo, executor, L1)

    assert plain.amount == 13_000_000 and dispo.amount == 9_000_000


# ------------------------------------------------------------ Review 23.09.: Befund 2b


async def _won_but_sale_failed(monkeypatch, session, *, sale_status="failed", ago=None,
                               weak_value=9_000_000, bid_status="executed"):
    """Zuschlag da (Ziel im Kader, Konto im Minus), gekoppelter Verkauf gescheitert."""
    client, squad = await _world(monkeypatch)
    plan = await plan_mod.build_plan()
    now = datetime.now(UTC)
    _, _, rows = _stage(session, plan, client, now)
    bid, sell = rows[("bid", "st")], rows[("sell", WEAK)]
    bid.status = bid_status
    sell.status, sell.updated_at = sale_status, now - (ago or timedelta(0))
    sell.result = "refused: Preflight: aktueller Marktwert weicht mehr als 20 % ab"
    session.commit()
    [listing] = client.market
    squad.append(SimpleNamespace(player=listing.player, is_starter=False))
    client.budget -= bid.amount
    client.market = []
    _set_market_value(squad, WEAK, weak_value)
    monkeypatch.setattr(plan_mod, "load_swap_couplings",
                        lambda *args: real_swap_couplings(session, L1))
    after = await plan_mod.build_plan()
    return client, after, now


def _stage_with_smoothing(session, plan, client, now, *, counts=None):
    from kickbase_agent.worker.intents import dispo_smoothing_drafts

    drafts = plan_intents(plan, _rows(client), now) + dispo_smoothing_drafts(session, plan, now)
    return _stage(session, plan, client, now, drafts=drafts, counts=counts, active={})


async def test_gescheiterter_dispo_verkauf_wird_als_glaettung_neu_angelegt(
    monkeypatch, session, armed,
):
    client, plan, now = await _won_but_sale_failed(monkeypatch, session)
    assert plan.budget < 0

    _, stats, rows = _stage_with_smoothing(session, plan, client, now)

    [created] = stats.created
    assert created.kind == "sell" and created.player_id == WEAK
    assert created.sources["rule_id"] == "dispo_smoothing"
    assert created.sources["swap_for"] == "st"
    assert created.amount == 9_000_000  # frischer Marktwert, nicht der alte Planbetrag
    # Veto-Fenster für Emil, danach führt der Intent-Executor ihn aus.
    later = created.execute_at.replace(tzinfo=UTC)
    assert later > now
    executor = ActionExecutor(client, session, user_id="u1")
    assert await execute_intent(session, created, executor, L1, now=later) == "executed", \
        created.result
    assert client.calls[-1] == ("sell", L1, WEAK)


async def test_glaettung_bleibt_ueber_den_naechsten_planlauf_stehen(
    monkeypatch, session,
):
    client, plan, now = await _won_but_sale_failed(monkeypatch, session)
    _, first, _ = _stage_with_smoothing(session, plan, client, now)
    [created] = first.created

    _, second, _ = _stage_with_smoothing(session, plan, client, now + timedelta(minutes=10))

    assert created.status == "planned" and second.created == []
    assert created.execute_at.replace(tzinfo=UTC) == now + timedelta(hours=2)  # 9 M ≥ 5 M


async def test_keine_glaettung_fuer_einen_geschuetzten_spieler(monkeypatch, session):
    client, plan, now = await _won_but_sale_failed(monkeypatch, session)
    monkeypatch.setenv("PROTECTED_PLAYER_IDS", f"8329,{WEAK}")

    _, stats, _ = _stage_with_smoothing(session, plan, client, now)

    assert stats.created == []


async def test_glaettung_umgeht_die_tages_verkaufsgrenze_nicht(monkeypatch, session):
    client, plan, now = await _won_but_sale_failed(monkeypatch, session)

    _, stats, _ = _stage_with_smoothing(
        session, plan, client, now, counts=TodayCounts(sells=10_000),
    )

    assert stats.created == []
    assert [d.sources["rule_id"] for d in stats.skipped_cap] == ["dispo_smoothing"]


async def test_emils_uebersprungener_verkauf_wird_nicht_als_glaettung_wiederholt(
    monkeypatch, session,
):
    # Übersprungen vor 25 h: die Veto-Sperre gilt bis zur (noch laufenden) Frist weiter.
    client, plan, now = await _won_but_sale_failed(
        monkeypatch, session, sale_status="vetoed", ago=timedelta(hours=25),
    )

    _, stats, _ = _stage_with_smoothing(session, plan, client, now)

    assert stats.created == []
    assert stats.skipped_blocked == []  # Emils Veto sperrt das Paar: kein Entwurf


async def test_hoechstens_ein_glaettungsversuch_je_tag(monkeypatch, session):
    client, plan, now = await _won_but_sale_failed(monkeypatch, session)
    _, first, _ = _stage_with_smoothing(session, plan, client, now)
    [created] = first.created
    created.status, created.updated_at = "failed", now
    session.commit()

    _, second, _ = _stage_with_smoothing(session, plan, client, now + timedelta(minutes=10))

    assert second.created == []


async def test_keine_glaettung_ohne_kontominus(monkeypatch, session):
    client, plan, now = await _won_but_sale_failed(monkeypatch, session)
    plan.budget = 500_000

    _, stats, _ = _stage_with_smoothing(session, plan, client, now)

    assert stats.created == []


# ------------------------------------------- Zweites Review 23.09.2026: Auflagen 1, 2, 4


async def test_verlorene_auktion_und_spaeterer_manueller_kauf_loest_keine_glaettung_aus(
    monkeypatch, session, armed,
):
    """Auflage 1: Auktion verloren (24.09.), Emil kauft das Ziel später selbst (27.09.),
    Konto −1 M. Das ist kein Zuschlag aus diesem Paar — kein autonomer Verkauf."""
    from kickbase_agent.worker.intents import RESULT_SWAP_LOST

    client, squad, bid, sell = await _live_dispo_bid(monkeypatch, session)
    [listing] = client.market
    end = bid.deadline.replace(tzinfo=UTC)
    client.market, client.own_bids = [], []  # Auktion vorbei, Ziel nicht im Kader

    plan = await plan_mod.build_plan()
    _stage(session, plan, client, end + timedelta(hours=1), active={})
    session.refresh(sell)
    assert sell.status == "superseded" and sell.result == RESULT_SWAP_LOST

    squad.append(SimpleNamespace(player=listing.player, is_starter=False))  # Emils Kauf
    client.budget = -1_000_000
    later = end + timedelta(days=1)
    plan = await plan_mod.build_plan()
    assert plan.budget < 0

    _, stats, _ = _stage_with_smoothing(session, plan, client, later)

    assert stats.created == [] and stats.skipped_blocked == []
    assert client.calls[-1][0] == "place_bid"  # kein Verkauf


@pytest.mark.parametrize("vetoed", ["tausch", "glaettung"])
async def test_emils_veto_sperrt_die_glaettung_des_paars_dauerhaft(monkeypatch, session, vetoed):
    """Auflage 1c: Ein Veto vor 30 h (Sperre abgelaufen, Frist des Vorhabens vorbei) auf
    den Tauschverkauf oder eine frühere Glättung sperrt dieses Paar weiter."""
    from kickbase_agent.worker.intents import dispo_smoothing_drafts

    client, plan, now = await _won_but_sale_failed(
        monkeypatch, session, sale_status="vetoed" if vetoed == "tausch" else "failed",
    )
    sell = session.query(Intent).filter_by(kind="sell", player_id=WEAK).one()
    if vetoed == "glaettung":
        [draft] = dispo_smoothing_drafts(session, plan, now)
        session.add(Intent(
            created_at=now, updated_at=now - timedelta(hours=30), kind="sell",
            player_id=WEAK, player_name=WEAK, team_id="10", position=4,
            amount=draft.amount, rationale="r", sources=draft.sources,
            listing_type="manager", deadline=now - timedelta(hours=1),
            execute_at=now, status="vetoed",
        ))
    else:
        sell.updated_at = now - timedelta(hours=30)
    sell.deadline = now - timedelta(hours=1)  # Paar vom früheren Spieltag
    session.commit()

    assert dispo_smoothing_drafts(session, plan, now) == []
    _, stats, _ = _stage_with_smoothing(session, plan, client, now)
    assert stats.created == []


async def test_echter_zuschlag_mit_gescheitertem_verkauf_wird_auch_spaeter_geglaettet(
    monkeypatch, session, armed,
):
    """Auflage 1 (Gegenprobe, echter Weg): Zuschlag, der Executor sieht das Ziel kurz
    nach dem Listingende im Kader, der Verkauf scheitert. Drei Stunden später — weit
    nach der Karenz — glättet der Planner trotzdem: der Zuschlag ist vermerkt."""
    client, squad, bid, sell = await _live_dispo_bid(monkeypatch, session)
    [listing] = client.market
    end = bid.deadline.replace(tzinfo=UTC)
    squad.append(SimpleNamespace(player=listing.player, is_starter=False))
    client.market, client.own_bids = [], []
    client.budget -= bid.amount

    async def sell_down(league_id, player_id):
        raise RuntimeError("HTTP 503 Service Unavailable")

    client.sell_to_kickbase = sell_down
    executor = ActionExecutor(client, session, user_id="u1")
    assert await execute_intent(session, sell, executor, L1, now=end + timedelta(minutes=5)) \
        == "failed"

    later = end + timedelta(hours=3)
    plan = await plan_mod.build_plan()
    _, stats, _ = _stage_with_smoothing(session, plan, client, later)

    [created] = stats.created
    assert created.sources["rule_id"] == "dispo_smoothing" and created.player_id == WEAK


async def test_ohne_vermerkten_zuschlag_keine_glaettung_nach_der_karenz(
    monkeypatch, session,
):
    """Auflage 1b: Steht das Ziel erst nach der Karenz im Kader und hat niemand den
    Zuschlag in der Karenz gesehen, belegt der Kader keinen Zuschlag aus diesem Paar."""
    client, plan, now = await _won_but_sale_failed(monkeypatch, session)
    bid = session.query(Intent).filter_by(kind="bid", player_id="st").one()
    later = bid.deadline.replace(tzinfo=UTC) + timedelta(hours=3)

    _, stats, _ = _stage_with_smoothing(session, plan, client, later)

    assert stats.created == []


@pytest.mark.parametrize(("value", "window"), [
    (9_000_000, timedelta(hours=2)), (5_000_000, timedelta(hours=2)),
    (4_000_000, timedelta(minutes=30)),
])
async def test_glaettung_veto_fenster_ab_5_m_mindestens_zwei_stunden(
    monkeypatch, session, value, window,
):
    """Auflage 2: dieselbe Regel wie `schedule_for` — ab 5 M mindestens 2 h Veto."""
    client, plan, now = await _won_but_sale_failed(monkeypatch, session, weak_value=value)

    _, stats, _ = _stage_with_smoothing(session, plan, client, now)

    [created] = stats.created
    assert created.amount == value
    assert created.execute_at.replace(tzinfo=UTC) == now + window


async def test_neues_eigenes_gebot_nach_verlorener_auktion_haelt_den_alten_verkauf_nicht(
    monkeypatch, session, armed,
):
    """Auflage 4: Die Auktion des Paars ist verloren; Emil bietet später in der App erneut
    auf dasselbe Ziel. Der alte Dispo-Verkauf darf nicht mitlaufen."""
    client, _, bid, sell = await _live_dispo_bid(monkeypatch, session)
    end = bid.deadline.replace(tzinfo=UTC)
    client.market = []
    client.own_bids = [{
        "player_id": "st", "name": "st", "team_id": "club-st", "amount": 13_000_000,
        "offer_id": "o-st-2", "expires_in_s": 7200,
    }]

    plan = await plan_mod.build_plan()
    _stage(session, plan, client, end + timedelta(hours=1), active={"st": "manager"})

    session.refresh(sell)
    assert sell.status == "superseded"


async def test_aktives_gebot_vor_dem_listingende_haelt_den_verkauf(
    monkeypatch, session, armed,
):
    client, _, bid, sell = await _live_dispo_bid(monkeypatch, session)
    end = bid.deadline.replace(tzinfo=UTC)
    _trigger_kb4(client, monkeypatch)

    plan = await plan_mod.build_plan()
    _stage(session, plan, client, end - timedelta(minutes=1), active={"st": "kickbase"})

    session.refresh(sell)
    assert sell.status == "planned", sell.result


@pytest.mark.parametrize("bid_status", ["failed", "superseded", "planned"])
async def test_keine_glaettung_ohne_ausgefuehrtes_gebot(monkeypatch, session, bid_status):
    """Auflage 1a: Nur ein ausgeführtes Dispo-Gebot kann den Zuschlag erklären — auch
    ein (widersprüchlicher) Zuschlagsvermerk ersetzt die Ausführung nicht."""
    client, plan, now = await _won_but_sale_failed(monkeypatch, session, bid_status=bid_status)
    bid = session.query(Intent).filter_by(kind="bid", player_id="st").one()
    bid.sources = {**bid.sources, "swap_won_at": now.isoformat()}
    session.commit()

    _, stats, _ = _stage_with_smoothing(session, plan, client, now)

    assert stats.created == []


async def test_keine_glaettung_nach_abgeraeumtem_tauschverkauf(monkeypatch, session):
    """Auflage 1d: Ein wegen verlorener Auktion abgeräumter Verkauf trägt keine Glättung —
    auch nicht, wenn das Ziel noch in der Karenz im Kader auftaucht."""
    from kickbase_agent.worker.intents import RESULT_SWAP_LOST

    client, plan, now = await _won_but_sale_failed(monkeypatch, session, sale_status="superseded")
    sell = session.query(Intent).filter_by(kind="sell", player_id=WEAK).one()
    sell.result = RESULT_SWAP_LOST
    session.commit()

    _, stats, _ = _stage_with_smoothing(session, plan, client, now)

    assert stats.created == []
