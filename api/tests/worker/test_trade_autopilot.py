"""Handels-Autopilot (worker/trade_autopilot.py, Spec 2026-10-01-handels-autopilot.md).

Emil 01.10.2026: „alles soll automatisch geschehen ich will nicht dass ich bieten muss“ — der
Autopilot legt aus dem Handels-Radar Gebots-Vorhaben an und verkauft Handelsspieler nach dem
ersten fallenden Update oder am Verkaufstermin. Er schreibt nie selbst bei Kickbase; jede
Aktion läuft als Vorhaben durch die Schreibstelle.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from kickbase_agent.db.models import Base, Intent, Report, TransferAction
from kickbase_agent.kickbase.models import (
    Fixture,
    MarketPlayer,
    Player,
    Position,
    SquadPlayer,
)
from kickbase_agent.worker import trade_autopilot as ta
from kickbase_agent.worker.trade_autopilot import (
    Candidate,
    Capacity,
    Holding,
    Slot,
    plan_bids,
    plan_sales,
    sync_trade_intents,
)

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 2, 6, 17, tzinfo=UTC)  # Fr 02.10. 08:17 Berlin
H = timedelta(hours=1)
SALE_BY = datetime(2026, 10, 5, 18, 30, tzinfo=UTC)  # Mo 05.10. 20:30 Berlin (Frist − 96 h)
DEADLINE = datetime(2026, 10, 9, 18, 30, tzinfo=UTC)

ROOM = Capacity(exposure=0, open_trades=0, trade_bids_today=0, buys_today=0,
                engine_bid_intents=0, after_bids=2_376_645, floor=-43_000_000)


def _cand(pid="a", mv=3_000_000, max_bid=3_900_000, *, expires=NOW + 10 * H, r3=0.18):
    return Candidate(player_id=pid, name=f"Steiger {pid}", mv=mv, max_bid=max_bid,
                     premium=max_bid / mv - 1, r3=r3, expected=0.6, horizon_days=4,
                     basis="Stufe ≥ 15 %", expires_at=expires)


def _slot(pid="a", mv=3_000_000, *, expires=NOW + 10 * H, seller="", fitness=0, prob=2):
    return Slot(player_id=pid, name=f"Steiger {pid}", team_id="9", position=3, mv=mv, price=mv,
                expires_at=expires, seller_id=seller, fitness=fitness, lineup_prob=prob)


def _plan(cands, slots=None, *, capacity=ROOM, **kwargs):
    slots = slots if slots is not None else {c.player_id: _slot(c.player_id, c.mv,
                                                                expires=c.expires_at)
                                             for c in cands}
    return plan_bids(cands, slots, now=NOW, sale_by=SALE_BY, capacity=capacity, **kwargs)


# ------------------------------------------------------------------ Gebote planen


def test_a_radar_candidate_becomes_a_bid_at_the_radar_limit():
    [bid], skipped = _plan([_cand()])
    assert skipped == {}
    assert bid.amount == 3_900_000 and bid.deadline == NOW + 10 * H
    assert bid.execute_at == NOW + 10 * H - timedelta(minutes=15)  # 15 min vor Auktionsende
    review = bid.review
    assert (review["purpose"], review["selected"], review["target_bid_eur"]) == (
        "handel", True, 3_900_000)
    assert review["max_bid_eur"] == 3_900_000  # 130 % MW: Emils +10 % bleibt darunter möglich
    assert review["observed_at"] == NOW.isoformat()
    assert review["valid_until"] == (NOW + 3 * H).isoformat()
    assert "Handels-Autopilot" in bid.rationale and "Bank" in bid.rationale


def test_the_bid_never_exceeds_the_trade_stage_of_the_write_point():
    """Der Radar rundet auf ganze Tausend auf — die Schreibstelle kappt bei 130 % MW exakt."""
    [bid], _ = _plan([_cand(mv=1_234_567, max_bid=1_605_000)])
    assert bid.amount == int(1_234_567 * 1.30) == 1_604_937


def test_a_big_bid_still_runs_before_the_auction_ends():
    """Ab 5 M gilt das 2-h-Veto-Fenster, aber nie später als 15 min vor dem Auktionsende."""
    [soon], _ = _plan([_cand(mv=8_000_000, max_bid=9_000_000, expires=NOW + 90 * 60 *
                             timedelta(seconds=1))])
    assert soon.execute_at == NOW + timedelta(minutes=75)


@pytest.mark.parametrize(("slot", "reason"), [
    (None, "nicht mehr am Markt"),
    (_slot(seller="rival"), "nicht mehr am Markt"),
    (_slot(mv=3_100_000), "Marktwert-Update seit dem Radar"),
    (_slot(expires=NOW + timedelta(minutes=10)), "Auktion endet gleich"),
    (_slot(expires=SALE_BY + H), "kein Update vor dem Verkaufstermin"),
    (_slot(fitness=2), "nicht fit oder KB-Stufe 4/5"),
    (_slot(prob=4), "nicht fit oder KB-Stufe 4/5"),
])
def test_a_candidate_is_dropped_when_the_market_no_longer_fits(slot, reason):
    bids, skipped = _plan([_cand()], {"a": slot} if slot else {})
    assert bids == [] and skipped == {"a": reason}


def test_taken_players_and_todays_blocks_get_no_new_bid():
    bids, skipped = _plan([_cand("eng"), _cand("vet")], taken={"eng"}, blocked={"vet"})
    assert bids == []
    assert skipped["eng"].startswith("eigenes Gebot")
    assert skipped["vet"].startswith("heute gesperrt")


def test_while_the_engine_plans_a_bid_no_new_trade_starts_but_planned_ones_stay():
    """Elf zuerst: Ein neues Handelsgebot hätte das Geld eines geplanten Elf-Kaufs gebunden.
    Ein schon geplantes Handelsvorhaben bleibt — die Engine plante mit ihm."""
    bids, skipped = _plan([_cand("old"), _cand("new")], existing={"old"}, engine_busy=True,
                          blocked={"old"})
    assert [b.player_id for b in bids] == ["old"]
    assert skipped == {"new": "Engine plant ein Gebot — Elf zuerst"}


def test_without_transfers_left_only_planned_trades_stay():
    bids, skipped = _plan([_cand("old"), _cand("new")], existing={"old"}, transfers_left=False)
    assert [b.player_id for b in bids] == ["old"] and "Transfer-Reserve" in skipped["new"]


@pytest.mark.parametrize(("capacity", "reason"), [
    (Capacity(48_000_000, 1, 0, 0, 0, 30_000_000, -43_000_000), "Handelsrahmen"),
    (Capacity(0, 6, 0, 0, 0, 30_000_000, -43_000_000), "schon 6 Handelsspieler"),
    (Capacity(0, 0, 6, 6, 0, 30_000_000, -43_000_000), "Tageskontingent 6 Handelsgebote"),
    (Capacity(0, 0, 0, 6, 2, 30_000_000, -43_000_000), "Tages-Cap Käufe aller Pfade"),
    # −39 M − 3,9 M = −42,9 M liegt näher als 1 M an der Grenze −43 M.
    (Capacity(0, 0, 0, 0, 0, -39_000_000, -43_000_000), "33-%-Grenze"),
])
def test_the_frame_and_the_daily_quotas_hold(capacity, reason):
    bids, skipped = _plan([_cand()], capacity=capacity)
    assert bids == [] and reason in skipped["a"]


def test_planned_trades_keep_their_place_before_new_candidates():
    """Hysterese: Ist nur noch Platz für ein Gebot, bleibt das geplante — auch wenn der Radar
    einen neuen Kandidaten weiter vorn nennt."""
    capacity = Capacity(0, 5, 0, 0, 0, 30_000_000, -43_000_000)
    bids, skipped = _plan([_cand("new"), _cand("old")], capacity=capacity, existing={"old"})
    assert [b.player_id for b in bids] == ["old"] and "schon 6" in skipped["new"]


def test_several_bids_share_the_frame():
    capacity = Capacity(40_000_000, 1, 0, 0, 0, 30_000_000, -43_000_000)
    bids, skipped = _plan([_cand("a", 4_000_000, 5_200_000), _cand("b", 4_000_000, 5_200_000)],
                          capacity=capacity)
    assert [b.player_id for b in bids] == ["a"] and "Handelsrahmen" in skipped["b"]


def test_unreadable_radar_rows_never_bid():
    assert Candidate.from_meta({"player_id": "x", "mv": 3_000_000}) is None
    assert Candidate.from_meta({"player_id": "x", "mv": 3_000_000, "max_bid": 2_000_000,
                                "premium": 0.1, "expires_at": NOW.isoformat()}) is None
    row = {"player_id": "x", "name": "X", "mv": 3_000_000, "max_bid": 3_300_000, "premium": 0.1,
           "r3": 0.12, "expected": 0.2, "horizon_days": 4, "basis": "Klasse",
           "expires_at": (NOW + H).isoformat(), "jev_return": True}
    cand = Candidate.from_meta(row)
    assert cand is not None and cand.max_bid == 3_300_000 and cand.jev_return is True
    assert Candidate.from_meta({**row, "expires_at": "2026-10-02T09:00:00"}) is None  # ohne Zone


# ------------------------------------------------------------------ Verkäufe planen


def _holding(pid="h", *, step, mv=3_200_000, buy=3_000_000):
    return Holding(player_id=pid, name=f"Handel {pid}", team_id="9", position=2, mv=mv,
                   buy_price=buy, last_step=step)


def test_a_falling_update_sells_after_thirty_minutes_before_the_next_update():
    [sale] = plan_sales([_holding(step=-80_000)], now=NOW, sale_by=SALE_BY, deadline=DEADLINE)
    assert (sale.signal, sale.amount, sale.execute_at) == ("fallend", 3_200_000,
                                                           NOW + timedelta(minutes=30))
    assert sale.deadline == datetime(2026, 10, 2, 22, 0, tzinfo=BERLIN).astimezone(UTC)
    assert "fiel beim letzten Update" in sale.rationale and "+200 k" in sale.rationale


def test_rising_flat_or_unknown_steps_hold():
    holdings = [_holding("up", step=50_000), _holding("flat", step=0),
                _holding("unk", step=None)]
    assert plan_sales(holdings, now=NOW, sale_by=SALE_BY, deadline=DEADLINE) == []


def test_every_trade_player_goes_at_the_sale_deadline():
    before = SALE_BY - timedelta(hours=1, minutes=30)
    plans = plan_sales([_holding("up", step=50_000), _holding("unk", step=None)], now=before,
                       sale_by=SALE_BY, deadline=DEADLINE)
    assert {p.player_id: (p.signal, p.execute_at) for p in plans} == {
        "up": ("frist", SALE_BY), "unk": ("frist", SALE_BY)}
    assert plans[0].deadline == DEADLINE - timedelta(minutes=120)  # dann gilt die Kaskade
    late = plan_sales([_holding(step=50_000)], now=SALE_BY + H, sale_by=SALE_BY,
                      deadline=DEADLINE)
    assert late[0].execute_at == SALE_BY + H + timedelta(minutes=30)
    early = plan_sales([_holding(step=50_000)], now=SALE_BY - 3 * H, sale_by=SALE_BY,
                       deadline=DEADLINE)
    assert early == []  # erst zwei Stunden vorher


# ------------------------------------------------------------------ Vorhaben abgleichen


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    with Session(eng, future=True) as s:
        yield s


def _sync(session, *, bids=(), sales=(), skipped=None, now=NOW, held=()):
    return sync_trade_intents(session, now=now, league_id="L1", bids=list(bids),
                              sales=list(sales), skipped=skipped or {}, radar_report_id=7,
                              sale_by=SALE_BY, held=held)


def _rows(session, **filters):
    return session.scalars(select(Intent).filter_by(**filters).order_by(Intent.id)).all()


def test_sync_creates_trade_intents_once_and_marks_them(session):
    [bid], _ = _plan([_cand()])
    stats = _sync(session, bids=[bid])
    assert stats.created == ["Gebot Steiger a bis 3,90 M"]
    [row] = _rows(session)
    sources = row.sources
    assert (row.kind, row.status, row.amount) == ("bid", "planned", 3_900_000)
    assert sources["trade"] is True and sources["rule_id"] == ta.RULE_BID
    assert sources["purchase_review"]["purpose"] == "handel"
    assert sources["radar_report_id"] == 7 and sources["trade_signal"]["mv_at_radar"] == 3_000_000

    again = _sync(session, bids=[bid], now=NOW + timedelta(minutes=30))
    assert not again.changes and len(_rows(session)) == 1  # nichts Neues, nichts gemeldet


def test_sync_renews_the_purchase_review_before_it_runs_out(session):
    [bid], _ = _plan([_cand()])
    _sync(session, bids=[bid])
    later = NOW + timedelta(minutes=100)
    [fresh], _ = plan_bids([_cand()], {"a": _slot()}, now=later, sale_by=SALE_BY, capacity=ROOM)
    _sync(session, bids=[fresh], now=later)
    [row] = _rows(session)
    session.refresh(row)
    assert row.sources["purchase_review"]["observed_at"] == later.isoformat()
    assert row.status == "planned"


@pytest.mark.parametrize(("reason", "result"), [
    ("Marktwert-Update seit dem Radar", ta.RESULT_MV_MOVED),
    ("nicht mehr am Markt", "nicht mehr am Markt"),
    ("Handelsrahmen 20,00 M reicht nicht",
     "Handels-Autopilot: Handelsrahmen 20,00 M reicht nicht"),
    (None, ta.RESULT_RADAR_GONE),
])
def test_sync_clears_own_bids_that_are_no_longer_planned(session, reason, result):
    [bid], _ = _plan([_cand()])
    _sync(session, bids=[bid])
    stats = _sync(session, skipped={"a": reason} if reason else {})
    [row] = _rows(session)
    assert (row.status, row.result) == ("superseded", result)
    assert stats.superseded == [("Steiger a", result)]


def test_sync_never_touches_engine_or_manual_intents(session):
    session.add(Intent(created_at=NOW, updated_at=NOW, kind="bid", player_id="eng",
                       player_name="Engine", amount=1_000_000, sources={"league_id": "L1"},
                       listing_type="kickbase", execute_at=NOW + H, status="planned"))
    session.commit()
    _sync(session)
    [row] = _rows(session, player_id="eng")
    assert row.status == "planned"


def test_sync_keeps_emils_adjusted_amount(session):
    """Emil hob das Gebot in der Zentrale um 10 % — der Autopilot erneuert die Prüfung, ändert
    aber weder Betrag noch räumt er das Vorhaben (130 % MW bleiben die Grenze)."""
    [bid], _ = _plan([_cand(mv=3_000_000, max_bid=3_300_000)])
    _sync(session, bids=[bid])
    [row] = _rows(session)
    row.adjusted_from, row.amount = row.amount, 3_630_000
    session.commit()
    later = NOW + timedelta(minutes=100)
    [fresh], _ = plan_bids([_cand(mv=3_000_000, max_bid=3_300_000)], {"a": _slot()}, now=later,
                           sale_by=SALE_BY, capacity=ROOM)
    _sync(session, bids=[fresh], now=later)
    session.refresh(row)
    assert (row.status, row.amount) == ("planned", 3_630_000)
    assert row.sources["purchase_review"]["observed_at"] == later.isoformat()


def test_a_blocked_key_gets_no_new_intent(session):
    session.add(Intent(created_at=NOW - H, updated_at=NOW - H, kind="bid", player_id="a",
                       player_name="Steiger a", amount=3_900_000,
                       sources={"trade": True, "league_id": "L1"}, listing_type="kickbase",
                       execute_at=NOW - H, status="failed", result="refused: …"))
    session.commit()
    [bid], _ = _plan([_cand()])  # der Planer kennt die Sperre nicht (anderer Lauf)
    stats = _sync(session, bids=[bid])
    assert stats.created == [] and "gesperrt" in stats.skipped["a"]


def test_sync_creates_and_clears_trade_sales(session):
    [sale] = plan_sales([_holding(step=-80_000)], now=NOW, sale_by=SALE_BY, deadline=DEADLINE)
    stats = _sync(session, sales=[sale], held={"h"})
    assert stats.created and stats.created[0].startswith("Verkauf Handel h (MW fiel)")
    [row] = _rows(session)
    assert (row.kind, row.amount, row.sources["signal"]) == ("sell", 3_200_000, "fallend")
    assert row.sources["trade"] is True and row.sources["rule_id"] == ta.RULE_SELL
    # Signal weg, Spieler noch da → „entfallen“; Spieler weg → „kein Handelsspieler mehr“.
    _sync(session, held={"h"})
    assert _rows(session)[0].result == ta.RESULT_NO_SIGNAL
    _sync(session, sales=[sale], held={"h"}, now=NOW + timedelta(days=1))
    _sync(session, held=set(), now=NOW + timedelta(days=1))
    assert _rows(session)[-1].result == ta.RESULT_NOT_HELD


def test_trade_sales_respect_the_daily_sell_cap(session):
    for i in range(8):
        session.add(TransferAction(league_id="L1", action_type="sell", player_id=f"s{i}",
                                   amount=1, status="executed", idempotency_key=f"s{i}",
                                   rule_id="intent-sell", dry_run=False, created_at=NOW - H))
    session.commit()
    [sale] = plan_sales([_holding(step=-80_000)], now=NOW, sale_by=SALE_BY, deadline=DEADLINE)
    stats = _sync(session, sales=[sale], held={"h"})
    assert stats.created == [] and "Tages-Cap Verkäufe" in stats.skipped["h"]


# ------------------------------------------------------------------ Job


def _player(pid, last, mv, *, mv24=0, observed=True, prob=2, fitness=0,
            position=Position.MIDFIELDER):
    return Player(id=pid, first_name="", last_name=last, position=position,
                  market_value=mv, market_value_trend=0, team_id="9", lineup_prob=prob,
                  fitness_status=fitness, mv_change_24h=mv24, mv_change_24h_observed=observed)


#: Eine echte Elf (1 TW, 4 ABW, 4 MF, 2 ST) — besetzbare Plätze, nicht nur Köpfe.
ELEVEN = [Position.GOALKEEPER] + [Position.DEFENDER] * 4 + [Position.MIDFIELDER] * 4 + [
    Position.STRIKER] * 2


def _eleven(mv=5_000_000):
    return [SquadPlayer(player=_player(f"r{i}", f"Stamm{i}", mv, position=pos))
            for i, pos in enumerate(ELEVEN)]


class _ReadOnlyClient:
    def __init__(self, *, squad=(), market=(), budget=2_376_645, own_bids=(), used=13):
        self._squad, self._market, self._budget = list(squad), list(market), budget
        self._own_bids, self._used = list(own_bids), used

    async def get_squad(self, league_id):
        return self._squad

    async def get_market(self, league_id):
        return self._market

    async def get_budget(self, league_id):
        return self._budget

    async def get_own_bids(self, league_id, user_id):
        return self._own_bids

    async def get_matchdays(self):
        return [Fixture(matchday=5, home_team_id="3", away_team_id="4", kickoff=DEADLINE)]

    async def get_ranking(self, league_id):
        return [SimpleNamespace(user_id="U1", name="emil")]

    async def get_activity_feed(self, league_id, max_items=1000):
        return [{"t": 15, "data": {"byr": "emil"}} for _ in range(self._used)]

    def __getattr__(self, name: str):
        raise AssertionError(f"Handels-Autopilot darf {name!r} nicht aufrufen — er liest nur.")


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(ta, "SessionLocal", factory)
    # Engine zuerst (Befund 03.10.2026): Ohne frischen Mannschaftsplan handelt der Autopilot
    # nicht. Diese Welt hat einen Plan von vor 10 min, der nichts kaufen will — die Engine-
    # Fälle stehen in tests/worker/test_trade_engine_priority.py.
    with factory() as s:
        s.add(Report(created_at=(NOW - timedelta(minutes=10)).replace(tzinfo=None),
                     kind="engine_decision", title="Finanzierter Mannschaftsplan", body="Plan",
                     source="season-engine-v1",
                     meta={"league_id": "L1", "team_plan": {"purchase_ids": [], "notes": []}}))
        s.commit()
    return factory


def _world(monkeypatch, client):
    async def factory():
        return client, SimpleNamespace(league_id="L1", user_id="U1")

    monkeypatch.setattr(ta, "make_kickbase_client", factory)


def _radar(factory, at, *rows):
    with factory() as s:
        s.add(Report(created_at=at.astimezone(UTC).replace(tzinfo=None), kind="recherche",
                     title="Handels-Radar", body="x", source="worker:trade_radar",
                     meta={"kauf": list(rows)}))
        s.commit()


def _kauf(pid, mv, max_bid, expires):
    return {"player_id": pid, "name": pid.title(), "mv": mv, "max_bid": max_bid,
            "premium": max_bid / mv - 1, "r3": 0.16, "expected": 0.4, "horizon_days": 3,
            "basis": "Stufe ≥ 15 %", "expires_at": expires.isoformat(), "jev_return": False}


async def test_the_job_turns_the_radar_into_bid_intents_and_reports_once(db, monkeypatch):
    expires = NOW + 9 * H
    client = _ReadOnlyClient(
        squad=_eleven(2_000_000),
        market=[MarketPlayer(player=_player("riser", "Riser", 3_000_000), price=3_000_000,
                             expires_at=expires)],
    )
    _world(monkeypatch, client)
    _radar(db, NOW - timedelta(minutes=5), _kauf("riser", 3_000_000, 3_600_000, expires))

    assert await ta.run_trade_autopilot(now=NOW) == "ok:1/0/0"
    with db() as s:
        [row] = s.scalars(select(Intent)).all()
        reports = s.scalars(select(Report).where(Report.source == ta.SOURCE)).all()
    assert (row.kind, row.player_id, row.amount, row.status) == ("bid", "riser", 3_600_000,
                                                                  "planned")
    # Elf voll (11 ohne Handelsspieler): Verkaufstermin Frist − 6 h.
    assert row.sources["trade_signal"]["sale_by"] == (DEADLINE - 6 * H).isoformat()
    [report] = reports
    assert "Gebot Riser bis 3,60 M" in report.body
    # Der Rahmen zählt das Gebot dieses Laufs schon mit.
    assert "Handelsrahmen 3,60 M von 50,00 M" in report.body
    assert "0 laufende und 1 geplante Handelsgebote" in report.body

    assert await ta.run_trade_autopilot(now=NOW + timedelta(minutes=30)) == "ok:0/0/0"
    with db() as s:
        assert len(s.scalars(select(Report).where(Report.source == ta.SOURCE)).all()) == 1


@pytest.mark.parametrize("engine_bid", [False, True])
async def test_an_engine_bid_blocks_new_trades(db, monkeypatch, engine_bid):
    """Elf zuerst — mit Gegenprobe: Derselbe Kandidat bekommt ohne Engine-Gebot sein
    Vorhaben (Kader mit Teamwert, damit die 33-%-Grenze nicht stattdessen greift)."""
    expires = NOW + 9 * H
    client = _ReadOnlyClient(
        squad=_eleven(),
        market=[MarketPlayer(player=_player("riser", "Riser", 3_000_000), price=3_000_000,
                             expires_at=expires)],
        own_bids=[{"player_id": "elf", "amount": 1_500_000}] if engine_bid else [],
    )
    _world(monkeypatch, client)
    _radar(db, NOW - timedelta(minutes=5), _kauf("riser", 3_000_000, 3_600_000, expires))

    assert await ta.run_trade_autopilot(now=NOW) == ("ok:0/0/0" if engine_bid else "ok:1/0/0")
    with db() as s:
        assert len(s.scalars(select(Intent)).all()) == (0 if engine_bid else 1)


async def test_a_planned_engine_bid_blocks_new_trades_too(db, monkeypatch):
    expires = NOW + 9 * H
    client = _ReadOnlyClient(
        squad=_eleven(),
        market=[MarketPlayer(player=_player("riser", "Riser", 3_000_000), price=3_000_000,
                             expires_at=expires)],
    )
    _world(monkeypatch, client)
    _radar(db, NOW - timedelta(minutes=5), _kauf("riser", 3_000_000, 3_600_000, expires))
    with db() as s:
        s.add(Intent(created_at=NOW, updated_at=NOW, kind="bid", player_id="elf",
                     player_name="Elf", amount=1_500_000, sources={"league_id": "L1"},
                     listing_type="kickbase", execute_at=NOW + H, status="planned"))
        s.commit()

    assert await ta.run_trade_autopilot(now=NOW) == "ok:0/0/0"


async def test_a_stale_radar_report_bids_nothing(db, monkeypatch):
    expires = NOW + 9 * H
    client = _ReadOnlyClient(market=[MarketPlayer(player=_player("riser", "Riser", 3_000_000),
                                                  price=3_000_000, expires_at=expires)])
    _world(monkeypatch, client)
    _radar(db, NOW - 16 * H, _kauf("riser", 3_000_000, 3_600_000, expires))

    assert await ta.run_trade_autopilot(now=NOW) == "ok:0/0/0"


async def test_a_trade_player_with_a_falling_update_gets_a_sale(db, monkeypatch):
    client = _ReadOnlyClient(
        squad=[SquadPlayer(player=_player("h", "Handel", 3_200_000, mv24=-90_000))],
    )
    _world(monkeypatch, client)
    with db() as s:
        action = TransferAction(league_id="L1", action_type="bid", player_id="h",
                                amount=3_000_000, status="executed", idempotency_key="h",
                                rule_id="intent-bid", dry_run=False, created_at=NOW - 20 * H)
        s.add(action)
        s.flush()
        s.add(Intent(created_at=NOW - 21 * H, updated_at=NOW - 20 * H, kind="bid",
                     player_id="h", player_name="Handel", amount=3_000_000,
                     sources={"trade": True, "league_id": "L1"}, listing_type="kickbase",
                     deadline=NOW - 19 * H, execute_at=NOW - 20 * H, status="executed",
                     executed_action_id=action.id))
        s.commit()

    assert await ta.run_trade_autopilot(now=NOW) == "ok:1/0/0"
    with db() as s:
        sale = s.scalars(select(Intent).where(Intent.kind == "sell")).one()
    assert (sale.player_id, sale.amount, sale.sources["signal"]) == ("h", 3_200_000, "fallend")
    assert sale.sources["buy_price"] == 3_000_000


async def test_the_trade_stop_clears_planned_bids_but_keeps_selling(db, monkeypatch):
    """AGENT_TRADE_AUTOPILOT=0 hält nur den Handel an: kein neues Gebot, das geplante wird
    geräumt — der Handelsspieler mit fallendem Update wird trotzdem verkauft, sonst bliebe
    ein Minus aus dem Handel stehen."""
    import kickbase_agent.config as cfg

    expires = NOW + 9 * H
    client = _ReadOnlyClient(
        squad=[SquadPlayer(player=_player("h", "Handel", 3_200_000, mv24=-90_000))]
        + _eleven(),
        market=[MarketPlayer(player=_player("riser", "Riser", 3_000_000), price=3_000_000,
                             expires_at=expires)],
    )
    _world(monkeypatch, client)
    _radar(db, NOW - timedelta(minutes=5), _kauf("riser", 3_000_000, 3_600_000, expires))
    with db() as s:
        action = TransferAction(league_id="L1", action_type="bid", player_id="h",
                                amount=3_000_000, status="executed", idempotency_key="h",
                                rule_id="intent-bid", dry_run=False, created_at=NOW - 20 * H)
        s.add(action)
        s.flush()
        s.add(Intent(created_at=NOW - 21 * H, updated_at=NOW - 20 * H, kind="bid",
                     player_id="h", player_name="Handel", amount=3_000_000,
                     sources={"trade": True, "league_id": "L1"}, listing_type="kickbase",
                     deadline=NOW - 19 * H, execute_at=NOW - 20 * H, status="executed",
                     executed_action_id=action.id))
        s.add(Intent(created_at=NOW - H, updated_at=NOW - H, kind="bid", player_id="riser",
                     player_name="Riser", amount=3_600_000,
                     sources={"trade": True, "league_id": "L1"}, listing_type="kickbase",
                     deadline=expires, execute_at=expires - timedelta(minutes=15),
                     status="planned"))
        s.commit()
    monkeypatch.setenv("AGENT_TRADE_AUTOPILOT", "0")
    cfg._settings = None

    assert await ta.run_trade_autopilot(now=NOW) == "ok:1/0/1"
    with db() as s:
        bid = s.scalars(select(Intent).where(Intent.player_id == "riser")).one()
        sale = s.scalars(select(Intent).where(Intent.kind == "sell")).one()
        body = s.scalars(select(Report).where(Report.source == ta.SOURCE)).one().body
    assert (bid.status, bid.result) == ("superseded", ta.RESULT_STOPPED)
    assert (sale.player_id, sale.status) == ("h", "planned")
    assert "AGENT_TRADE_AUTOPILOT=0" in body


def test_sync_rechecks_the_engine_under_the_lock(session):
    """Codex-Review 02.10.2026 (P1): Plant die Engine ein Gebot, NACHDEM der Autopilot gelesen
    hat, darf der Abgleich (unter der Sperre) kein neues Handelsgebot mehr anlegen — ein schon
    geplantes Handelsvorhaben wird weiter erneuert."""
    [old], _ = _plan([_cand("old")])
    _sync(session, bids=[old])
    session.add(Intent(created_at=NOW, updated_at=NOW, kind="bid", player_id="elf",
                       player_name="Elf", amount=1_500_000, sources={"league_id": "L1"},
                       listing_type="kickbase", execute_at=NOW + H, status="planned"))
    session.commit()
    later = NOW + timedelta(minutes=100)
    bids, _ = plan_bids([_cand("old"), _cand("new")], {"old": _slot("old"), "new": _slot("new")},
                        now=later, sale_by=SALE_BY, capacity=ROOM, existing={"old"})
    assert [b.player_id for b in bids] == ["old", "new"]  # der Planer wusste es noch nicht

    stats = _sync(session, bids=bids, now=later)

    assert stats.created == [] and "Elf zuerst" in stats.skipped["new"]
    row = session.scalars(select(Intent).where(Intent.player_id == "old")).one()
    session.refresh(row)
    assert row.status == "planned" and row.sources["purchase_review"]["observed_at"] == (
        later.isoformat())


def test_a_running_engine_auction_counts_as_busy(session):
    """Ein ausgeführtes Engine-Gebot, dessen Auktion noch läuft, bindet Geld wie ein geplantes."""
    session.add(Intent(created_at=NOW - H, updated_at=NOW - H, kind="bid", player_id="elf",
                       player_name="Elf", amount=1_500_000, sources={"league_id": "L1"},
                       listing_type="kickbase", deadline=NOW + 2 * H, execute_at=NOW - H,
                       status="executed"))
    session.add(Intent(created_at=NOW - 30 * H, updated_at=NOW - 30 * H, kind="bid",
                       player_id="alt", player_name="Alt", amount=900_000,
                       sources={"league_id": "L1"}, listing_type="kickbase",
                       deadline=NOW - 20 * H, execute_at=NOW - 30 * H, status="executed"))
    session.commit()

    assert ta.engine_busy(session, NOW) is True
    session.query(Intent).filter(Intent.player_id == "elf").delete()
    session.commit()
    assert ta.engine_busy(session, NOW) is False  # Auktion „alt“ ist vorbei


# ------------------------------------------------------------------ Review 02.10.2026


def test_a_planned_bid_close_to_its_auction_end_is_kept():
    """Review (P1): Der Lauf :17 räumte Vorhaben, deren Auktion in < 20 min endet — genau die,
    die der Executor um :20 ausgeführt hätte. Die Grenze gilt nur für neue Vorhaben."""
    soon = NOW + timedelta(minutes=12)
    bids, skipped = _plan([_cand("old", expires=soon), _cand("new", expires=soon)],
                          existing={"old"})
    assert [b.player_id for b in bids] == ["old"] and skipped == {"new": "Auktion endet gleich"}


def test_bids_must_stay_covered_at_kickoff_if_market_values_stay_flat():
    """Review (P1) / Emil 03.11.2026: Konto 2,38 M, Gebote +30 % auf 3,8 M MW — bleibt der MW,
    bringt der Sofortverkauf 100 % des Marktwerts zurück (gleiche Anrechnung wie Wächter und
    Pfand-Einlöser). Jedes Gebot kostet Deckung = Betrag − MW (1,14 M); reicht sie nicht, kein
    Gebot (sonst verkaufte die Notbremse Stammspieler)."""
    capacity = Capacity(0, 0, 0, 0, 0, 2_376_645, -43_000_000, coverage=2_376_645)
    cands = [_cand(pid, 3_800_000, 4_940_000) for pid in ("a", "b", "c")]
    bids, skipped = _plan(cands, capacity=capacity)
    assert [b.player_id for b in bids] == ["a", "b"]  # 2,38 − 2 × (4,94 − 3,80) = 96,6 k ≥ 10 k
    assert skipped["c"].startswith("Deckung zum Anpfiff")


def test_no_trade_at_a_position_whose_regular_squad_is_below_the_minimum():
    """Review (P1): Stammkader mit 2 ABW (Minimum 3) — ein Handels-ABW wäre unverkäuflich (die
    Schreibstelle hält das Positions-Minimum). Also gar nicht erst kaufen."""
    capacity = Capacity(0, 0, 0, 0, 0, 30_000_000, -43_000_000,
                        regular_counts={1: 1, 2: 2, 3: 5, 4: 2})
    abw = _slot("abw")
    abw = Slot(**{**abw.__dict__, "position": 2})
    bids, skipped = plan_bids([_cand("abw"), _cand("mf")], {"abw": abw, "mf": _slot("mf")},
                              now=NOW, sale_by=SALE_BY, capacity=capacity)
    assert [b.player_id for b in bids] == ["mf"]
    assert skipped["abw"].startswith("Position unter dem Minimum")


def test_superseded_bids_name_their_real_reason(session):
    [bid], _ = _plan([_cand()])
    _sync(session, bids=[bid])
    _sync(session, skipped={"a": "Kickbases 33-%-Grenze (mit 1 M Abstand)"})
    assert _rows(session)[0].result == "Handels-Autopilot: Kickbases 33-%-Grenze (mit 1 M Abstand)"


def test_no_new_trade_bid_while_the_engine_cycle_runs(session, monkeypatch):
    """Review (P2): Der Engine-Lauf baut seinen Plan außerhalb der Vorhaben-Sperre — läuft er,
    entsteht kein neues Handelsgebot (er hätte es nicht eingerechnet)."""
    import asyncio

    from kickbase_agent.worker import engine_cycle

    lock = asyncio.Lock()
    monkeypatch.setattr(engine_cycle, "_cycle_lock", lock)

    async def held():
        async with lock:
            [bid], _ = _plan([_cand()])
            return _sync(session, bids=[bid])

    stats = asyncio.run(held())
    assert stats.created == [] and "Engine" in stats.skipped["a"]


def test_no_falling_sale_right_before_the_next_update():
    """Review (P2): Geplant 21:47 hieße Ausführung 22:17 — nach dem Update, mit neuem Preis und
    womöglich neuem Signal. Dann entscheidet der Lauf nach dem Update."""
    late = datetime(2026, 10, 2, 21, 47, tzinfo=BERLIN).astimezone(UTC)
    assert plan_sales([_holding(step=-80_000)], now=late, sale_by=SALE_BY,
                      deadline=DEADLINE) == []


def test_no_sale_intent_inside_the_kickoff_cascade_window():
    """Review (P2): In den letzten 2 h gehört das Konto der versiegelten Anpfiff-Kaskade —
    ein Vorhaben, das erst danach fällig wäre, liefe sofort ab (und entstünde jede halbe
    Stunde neu)."""
    deadline = SALE_BY + 3 * H
    plans = plan_sales([_holding(step=50_000)], now=deadline - timedelta(minutes=140),
                       sale_by=SALE_BY, deadline=deadline)
    assert plans == []


def test_a_vetoed_falling_sale_does_not_block_the_sale_deadline(session):
    """Review (P2): Überspringt Emil einen Verkauf nach fallendem Update, gilt das nicht für
    den Pflichtverkauf am Termin — der hat einen anderen Grund."""
    session.add(Intent(created_at=NOW - H, updated_at=NOW - H, kind="sell", player_id="h",
                       player_name="Handel h", amount=3_200_000,
                       sources={"trade": True, "league_id": "L1", "signal": "fallend"},
                       listing_type="kickbase", execute_at=NOW, status="vetoed"))
    session.commit()
    [fallend] = plan_sales([_holding(step=-80_000)], now=NOW, sale_by=SALE_BY,
                           deadline=DEADLINE)
    assert _sync(session, sales=[fallend], held={"h"}).created == []
    # Derselbe Morgen, eine Stunde vor dem Verkaufstermin: Das Veto (1 h alt) sperrt noch.
    [frist] = plan_sales([_holding(step=50_000)], now=NOW, sale_by=NOW + H, deadline=DEADLINE)
    assert frist.signal == "frist"
    stats = _sync(session, sales=[frist], held={"h"})
    assert stats.created and "Verkaufstermin" in stats.created[0]


# ------------------------------------------------------------------ Mutationsproben 02.10.2026


def test_a_vetoed_sale_at_the_deadline_stays_vetoed(session):
    """Gegenprobe: Hat Emil den Pflichtverkauf am Termin selbst übersprungen, bleibt es dabei
    (24 h) — nur ein Veto gegen „fallend“ gilt nicht für den Termin."""
    session.add(Intent(created_at=NOW - H, updated_at=NOW - H, kind="sell", player_id="h",
                       player_name="Handel h", amount=3_200_000,
                       sources={"trade": True, "league_id": "L1", "signal": "frist"},
                       listing_type="kickbase", execute_at=NOW, status="vetoed"))
    session.commit()
    [frist] = plan_sales([_holding(step=50_000)], now=NOW, sale_by=NOW + H, deadline=DEADLINE)
    stats = _sync(session, sales=[frist], held={"h"})
    assert stats.created == [] and "gesperrt" in stats.skipped["h"]


def _held(factory, pid, buy):
    """Handelsspieler im Buch: ausgeführtes Handelsgebot samt Vorhaben."""
    with factory() as s:
        action = TransferAction(league_id="L1", action_type="bid", player_id=pid, amount=buy,
                                status="executed", idempotency_key=pid, rule_id="intent-bid",
                                dry_run=False, created_at=NOW - 20 * H)
        s.add(action)
        s.flush()
        s.add(Intent(created_at=NOW - 21 * H, updated_at=NOW - 20 * H, kind="bid",
                     player_id=pid, player_name=pid.title(), amount=buy,
                     sources={"trade": True, "league_id": "L1"}, listing_type="kickbase",
                     deadline=NOW - 19 * H, execute_at=NOW - 20 * H, status="executed",
                     executed_action_id=action.id))
        s.commit()


def _riser(expires):
    return MarketPlayer(player=_player("riser", "Riser", 3_000_000), price=3_000_000,
                        expires_at=expires)


async def test_the_sale_of_held_trade_players_counts_for_the_kickoff_cover(db, monkeypatch):
    """Konto −2 M nach einem Handelskauf (MW 4 M): Sein Verkauf bringt 3,6 M zurück — damit
    trägt die Deckung ein weiteres Gebot (Kosten 3,6 M − 0,9 × 3 M = 0,9 M)."""
    expires = NOW + 9 * H
    client = _ReadOnlyClient(
        squad=_eleven() + [SquadPlayer(player=_player("h", "Handel", 4_000_000, mv24=50_000))],
        market=[_riser(expires)], budget=-2_000_000,
    )
    _world(monkeypatch, client)
    _held(db, "h", 3_800_000)
    _radar(db, NOW - timedelta(minutes=5), _kauf("riser", 3_000_000, 3_600_000, expires))

    assert await ta.run_trade_autopilot(now=NOW) == "ok:1/0/0"


async def test_with_two_keepers_the_eleven_has_a_gap_and_the_sale_comes_earlier(db, monkeypatch):
    """Elf Köpfe, aber zwei Torhüter: zehn besetzbare Plätze — Verkaufstermin T−96 h."""
    expires = NOW + 9 * H
    squad = [SquadPlayer(player=_player(f"r{i}", f"Stamm{i}", 2_000_000, position=pos))
             for i, pos in enumerate([Position.GOALKEEPER] * 2 + [Position.DEFENDER] * 4
                                     + [Position.MIDFIELDER] * 3 + [Position.STRIKER] * 2)]
    _world(monkeypatch, _ReadOnlyClient(squad=squad, market=[_riser(expires)]))
    _radar(db, NOW - timedelta(minutes=5), _kauf("riser", 3_000_000, 3_600_000, expires))

    assert await ta.run_trade_autopilot(now=NOW) == "ok:1/0/0"
    with db() as s:
        row = s.scalars(select(Intent)).one()
    assert row.sources["trade_signal"]["sale_by"] == (DEADLINE - 96 * H).isoformat()


async def test_a_planned_bid_close_to_its_end_stays_only_while_the_radar_still_names_it(
    db, monkeypatch
):
    """Codex-Review 02.10.2026: Der Radar nennt ein geplantes Handelsgebot auch in seinen
    letzten 30 min (dann läuft es weiter durch alle Prüfungen und zählt im Rahmen). Nennt er es
    nicht — etwa weil Jev „fehlt“ las —, wird es geräumt, auch kurz vor der Ausführung."""
    radar_at = NOW - timedelta(minutes=5)
    soon = radar_at + timedelta(minutes=25)
    _world(monkeypatch, _ReadOnlyClient(squad=_eleven(), market=[
        MarketPlayer(player=_player(pid, pid.title(), 3_000_000), price=3_000_000,
                     expires_at=soon) for pid in ("named", "dropped")
    ]))
    _radar(db, radar_at, _kauf("named", 3_000_000, 3_600_000, soon))
    with db() as s:
        for pid in ("named", "dropped"):
            s.add(Intent(created_at=NOW - H, updated_at=NOW - H, kind="bid", player_id=pid,
                         player_name=pid.title(), amount=3_600_000,
                         sources={"trade": True, "league_id": "L1"}, listing_type="kickbase",
                         deadline=soon, execute_at=soon - timedelta(minutes=15),
                         status="planned"))
        s.commit()

    out = await ta.run_trade_autopilot(now=NOW)
    assert out.startswith("ok:0/") and out.endswith("/1")
    with db() as s:
        status = {r.player_id: (r.status, r.result) for r in s.scalars(select(Intent))}
    assert status["named"][0] == "planned"
    assert status["dropped"] == ("superseded", ta.RESULT_RADAR_GONE)


class _CountingClient(_ReadOnlyClient):
    feed_reads = 0

    async def get_activity_feed(self, league_id, max_items=1000):
        self.feed_reads += 1
        return await super().get_activity_feed(league_id, max_items)


async def test_while_the_engine_cycle_runs_the_job_does_not_even_count_transfers(
    db, monkeypatch
):
    """Läuft der Engine-Zyklus, entsteht kein Handelsgebot — und der Autopilot liest dafür
    nicht erst die Transferzählung (jeder Kickbase-Abruf zählt gegen die Abrufgrenze)."""
    import asyncio

    from kickbase_agent.worker import engine_cycle

    expires = NOW + 9 * H
    client = _CountingClient(squad=_eleven(), market=[_riser(expires)])
    _world(monkeypatch, client)
    _radar(db, NOW - timedelta(minutes=5), _kauf("riser", 3_000_000, 3_600_000, expires))
    lock = asyncio.Lock()
    monkeypatch.setattr(engine_cycle, "_cycle_lock", lock)

    async with lock:
        assert await ta.run_trade_autopilot(now=NOW) == "ok:0/0/0"
    assert client.feed_reads == 0
    assert await ta.run_trade_autopilot(now=NOW) == "ok:1/0/0"  # Gegenprobe ohne Zyklus
    assert client.feed_reads == 1
