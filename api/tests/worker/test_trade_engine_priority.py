"""Engine zuerst — auch vor ihren Vorhaben (Befund 03.10.2026, Spec 2026-10-01-handels-autopilot).

Am 02.10. 22:23 wählte der Mannschaftsplan einen Tausch (Vozar → Obermair), ein Vorhaben
entstand noch nicht. 24 min später band der Autopilot 15,7 M für Handel: Seine Regel „Elf
zuerst“ sah nur Engine-VORHABEN, nicht den Plan. Danach meldete die Engine „fehlen 13–15 M
Deckung“ und wählte nichts mehr — und der Autopilot bot selbst auf Obermair.

Eigene Datei neben tests/worker/test_trade_autopilot.py und test_trade_coexistence.py (L-087).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from kickbase_agent.cli.plan import Plan
from kickbase_agent.db.models import Base, Intent, Report
from kickbase_agent.kickbase.models import MarketPlayer
from kickbase_agent.worker import intents as wi
from kickbase_agent.worker import trade_autopilot as ta

from .test_trade_autopilot import NOW, _eleven, _kauf, _player, _radar, _ReadOnlyClient, _world

MIN = timedelta(minutes=1)
H = timedelta(hours=1)


def _engine_plan(factory, at: datetime, *, purchases=(), league="L1") -> None:
    """Gespeicherter Mannschaftsplan, wie ihn engine_cycle.record_team_plan schreibt."""
    with factory() as s:
        s.add(Report(created_at=at.astimezone(UTC).replace(tzinfo=None), kind="engine_decision",
                     title="Finanzierter Mannschaftsplan", body="Plan", source="season-engine-v1",
                     meta={"league_id": league,
                           "team_plan": {"purchase_ids": list(purchases), "notes": ["Plan"]}}))
        s.commit()


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(ta, "SessionLocal", factory)
    return factory


def _riser_world(monkeypatch, db):
    expires = NOW + 9 * timedelta(hours=1)
    _world(monkeypatch, _ReadOnlyClient(squad=_eleven(), market=[
        MarketPlayer(player=_player("riser", "Riser", 3_000_000), price=3_000_000,
                     expires_at=expires)]))
    _radar(db, NOW - 5 * MIN, _kauf("riser", 3_000_000, 3_600_000, expires))


def _trade_bids(factory) -> list[str]:
    with factory() as s:
        return [r.player_id for r in s.scalars(select(Intent).where(Intent.kind == "bid"))]


# ------------------------------------------------------------------ Autopilot


async def test_no_new_trade_bid_while_the_engine_plan_wants_to_buy(db, monkeypatch):
    """Der Plan von vor 10 min wählt einen Kauf, ein Vorhaben gibt es noch nicht."""
    _riser_world(monkeypatch, db)
    _engine_plan(db, NOW - 10 * MIN, purchases=["obermair"])

    assert await ta.run_trade_autopilot(now=NOW) == "ok:0/0/0"
    assert _trade_bids(db) == []


@pytest.mark.parametrize("plan_age", [None, timedelta(minutes=100), timedelta(hours=3)])
async def test_without_a_fresh_engine_plan_there_is_no_new_trade_bid(db, monkeypatch, plan_age):
    """Fail-closed: Ohne Plan der letzten 90 min weiß der Autopilot nicht, ob die Engine kaufen
    will (sie plant mindestens stündlich neu)."""
    _riser_world(monkeypatch, db)
    if plan_age is not None:
        _engine_plan(db, NOW - plan_age)

    assert await ta.run_trade_autopilot(now=NOW) == "ok:0/0/0"


@pytest.mark.parametrize("meta", [
    {}, {"league_id": "L1"}, {"league_id": "L1", "team_plan": None},
    {"league_id": "L1", "team_plan": {"notes": ["Plan"]}},
])
async def test_a_plan_without_a_readable_purchase_choice_stops_trade(db, monkeypatch, meta):
    """Codex-Review 03.10.2026: Auch ein frischer Bericht ohne lesbare Kaufauswahl lässt offen,
    ob die Engine kaufen will — dann kein neues Handelsgebot (fail-closed)."""
    _riser_world(monkeypatch, db)
    with db() as s:
        s.add(Report(created_at=(NOW - 5 * MIN).astimezone(UTC).replace(tzinfo=None),
                     kind="engine_decision", title="Plan", body="Plan",
                     source="season-engine-v1", meta=meta))
        s.commit()

    assert await ta.run_trade_autopilot(now=NOW) == "ok:0/0/0"
    with db() as s:
        assert ta.engine_plan_reason(s, NOW, "L1") == ta.ENGINE_PLAN_UNREADABLE


async def test_a_buying_plan_of_another_league_does_not_stop_trade(db, monkeypatch):
    _riser_world(monkeypatch, db)
    _engine_plan(db, NOW - 20 * MIN)
    _engine_plan(db, NOW - 5 * MIN, purchases=["fremd"], league="L2")

    assert await ta.run_trade_autopilot(now=NOW) == "ok:1/0/0"


def test_the_lock_rechecks_a_plan_that_wants_to_buy_since_the_job_read(db):
    """Unter der Sperre frisch: Wählt der Plan NACH dem Lesen des Autopiloten einen Kauf,
    entsteht kein neues Handelsgebot mehr."""
    from .test_trade_autopilot import _cand
    from .test_trade_autopilot import _plan as plan_bids

    [bid], _ = plan_bids([_cand("new")])
    _engine_plan(db, NOW - MIN, purchases=["obermair"])
    with db() as s:
        stats = ta.sync_trade_intents(s, now=NOW, league_id="L1", bids=[bid], sales=[],
                                      skipped={}, radar_report_id=7, sale_by=None)

    assert stats.created == [] and "Elf zuerst" in stats.skipped["new"]


async def test_trade_goes_on_when_the_fresh_engine_plan_buys_nothing(db, monkeypatch):
    """Gegenprobe: Will die Engine nichts kaufen, handelt der Autopilot wie bisher."""
    _riser_world(monkeypatch, db)
    _engine_plan(db, NOW - 10 * MIN)

    assert await ta.run_trade_autopilot(now=NOW) == "ok:1/0/0"
    assert _trade_bids(db) == ["riser"]


# ------------------------------------------------------------------ Planer


@pytest.fixture
def planner(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    world = SimpleNamespace(plan=None, factory=factory, during_build=None)

    async def fake_build_plan(**_kwargs):
        if world.during_build is not None:
            world.during_build()  # was zwischen Planbau und Planer-Sperre geschieht
        return world.plan

    async def _get(path):
        return {"it": []}

    async def fake_factory():
        return SimpleNamespace(_get=_get), SimpleNamespace(user_id="U1", league_id="L1")

    monkeypatch.setattr(wi, "build_plan", fake_build_plan)
    monkeypatch.setattr(wi, "make_kickbase_client", fake_factory)
    monkeypatch.setattr(wi, "SessionLocal", factory)
    return world


def _intent(factory, pid, kind="bid", *, trade=True, status="planned", league="L1",
            adjusted_from=None):
    with factory() as s:
        s.add(Intent(created_at=NOW - timedelta(hours=1), updated_at=NOW - timedelta(hours=1),
                     kind=kind, player_id=pid, player_name=pid, amount=5_000_000,
                     sources={"trade": True, "league_id": league} if trade
                     else {"league_id": league},
                     listing_type="kickbase", execute_at=datetime.now(UTC) + timedelta(hours=5),
                     status=status, adjusted_from=adjusted_from))
        s.commit()


def _state(factory, pid, kind="bid"):
    with factory() as s:
        row = s.scalars(select(Intent).where(Intent.player_id == pid, Intent.kind == kind)).one()
        return row.status, row.result or ""


def _plan(purchases=()) -> Plan:
    return Plan(league_id="L1", budget=2_576_645, buys=[], sells=[], squad=[], user_id="U1",
                market_candidates=[],
                team_plan={"purchase_ids": list(purchases), "notes": ["Plan"]})


async def test_the_planner_clears_planned_trade_bids_when_the_engine_wants_to_buy(planner):
    """Engine zuerst: Wählt der Plan einen Kauf, weichen geplante (noch nicht abgegebene)
    Handelsgebote im selben Lauf — ohne Tagessperre. Handelsverkäufe laufen weiter."""
    _intent(planner.factory, "guenther")
    _intent(planner.factory, "handelsspieler", "sell")
    planner.plan = _plan(purchases=["obermair"])

    await wi.run_intent_planner()

    status, result = _state(planner.factory, "guenther")
    assert status == "superseded" and "Elf zuerst" in result
    assert _state(planner.factory, "handelsspieler", "sell") == ("planned", "")


async def test_the_planner_leaves_trade_bids_alone_when_the_engine_buys_nothing(planner):
    _intent(planner.factory, "guenther")
    planner.plan = _plan()

    await wi.run_intent_planner()

    assert _state(planner.factory, "guenther") == ("planned", "")


async def test_emils_adjusted_trade_bid_stays_when_trade_yields(planner):
    """Ein Handelsgebot, dessen Betrag Emil angepasst hat, bleibt (sein Wille) — es bindet dafür
    weiter das Geld der Engine (trading/trade_book.firm)."""
    _intent(planner.factory, "guenther")
    _intent(planner.factory, "angepasst", adjusted_from=4_000_000)
    planner.plan = _plan(purchases=["obermair"])

    await wi.run_intent_planner()

    assert _state(planner.factory, "guenther")[0] == "superseded"
    assert _state(planner.factory, "angepasst") == ("planned", "")


def _file_db(tmp_path):
    """Datei statt Speicher: zwei Sitzungen mit eigener Verbindung (Planer und API)."""
    engine = create_engine(f"sqlite:///{tmp_path / 'intents.db'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def test_only_planned_trade_bids_of_the_league_yield(tmp_path):
    factory = _file_db(tmp_path)
    _intent(factory, "handel")
    _intent(factory, "engine", trade=False)
    _intent(factory, "verkauf", "sell")
    _intent(factory, "fremd", league="L2")
    _intent(factory, "angepasst", adjusted_from=4_000_000)

    with factory() as s:
        cleared = [row.player_id for row in wi.yield_trade_bids(s, "L1", NOW)]
        s.commit()

    assert cleared == ["handel"]
    assert _state(factory, "handel") == ("superseded", wi.RESULT_TRADE_YIELDS)
    assert _state(factory, "engine") == ("planned", "")
    assert _state(factory, "verkauf", "sell") == ("planned", "")
    assert _state(factory, "fremd") == ("planned", "")
    assert _state(factory, "angepasst") == ("planned", "")


@pytest.mark.parametrize("decision", ["adjust", "veto"])
def test_emils_decision_during_the_clearing_wins(tmp_path, monkeypatch, decision):
    """Konditional wie die API: Passt Emil den Betrag an oder überspringt er das Gebot, nachdem
    der Planer gelesen hat und bevor er räumt, gewinnt Emils Entscheidung."""
    factory = _file_db(tmp_path)
    _intent(factory, "guenther")
    read = wi.open_intents

    def decide_after_read(session):
        rows = read(session)
        with factory() as api:  # eigene Verbindung, wie die Web-App
            row = api.scalars(select(Intent)).one()
            if decision == "adjust":
                row.adjusted_from, row.amount = row.amount, 6_000_000
            else:
                row.status, row.result = "vetoed", "Emil: überspringen"
            api.commit()
        return rows

    monkeypatch.setattr(wi, "open_intents", decide_after_read)
    with factory() as s:
        cleared = wi.yield_trade_bids(s, "L1", NOW)
        s.commit()

    assert cleared == []
    assert _state(factory, "guenther") == (
        ("planned", "") if decision == "adjust" else ("vetoed", "Emil: überspringen"))


def _obermair_buy(planner, monkeypatch, *, counted=None) -> None:
    """Plan mit einem Engine-Kauf (Obermair) und seinem Markt-Listing; `counted` = die
    Handelsgebote, deren Geld der Plan als gebunden zählte (`Plan.trade_bound`)."""
    from kickbase_agent.agent.executor import PlannedAction
    from kickbase_agent.decision.models import Proposal

    async def _get(path):
        return {"it": [{"i": "obermair", "fn": "Raphael", "n": "Obermair", "pos": 3,
                        "mv": 5_204_912, "prc": 5_204_912, "exs": 20 * 3600, "tid": "T1",
                        "prob": 2, "ofs": []}]}

    async def fake_factory():
        return SimpleNamespace(_get=_get), SimpleNamespace(user_id="U1", league_id="L1")

    monkeypatch.setattr(wi, "make_kickbase_client", fake_factory)
    plan = _plan(purchases=["obermair"])
    plan.buys = [Proposal(
        action=PlannedAction(action_type="bid", league_id="L1", player_id="obermair",
                             amount=5_548_122, rule_id="team_plan"),
        rule_id="team_plan", rationale="Tausch", score=0.7, requires_approval=True,
        player_name="Raphael Obermair",
    )]
    plan.trade_bound = dict(counted or {})
    planner.plan = plan


def _engine_bids(factory) -> list[str]:
    with factory() as s:
        return [r.player_id for r in s.scalars(select(Intent).where(Intent.kind == "bid"))
                if not (r.sources or {}).get("trade")]


async def test_the_engine_can_bid_on_the_player_the_autopilot_had_planned(planner, monkeypatch):
    """Obermair-Fall: Der Autopilot hatte ihn zum Handel geplant, die Engine will ihn für die
    Mannschaft. Das Handelsgebot weicht VOR dem Abgleich — das Engine-Vorhaben entsteht im
    selben Lauf (sonst blockierte das Handelsvorhaben den Schlüssel (bid, Spieler))."""
    _intent(planner.factory, "obermair")
    _obermair_buy(planner, monkeypatch)

    summary = await wi.run_intent_planner()

    with planner.factory() as s:
        rows = s.scalars(select(Intent).where(Intent.player_id == "obermair")
                         .order_by(Intent.id)).all()
        states = [(r.status, bool((r.sources or {}).get("trade"))) for r in rows]
    assert states == [("superseded", True), ("planned", False)]
    assert summary.startswith("1 neu") and "Handel geräumt: 1" in summary


# ------------------------------------------------------------------ Planer: Plan überholt


def _during_build(planner, change: str):
    """Was zwischen dem Merken vor dem Planbau und der Planer-Sperre mit „guenther“ geschieht."""
    def change_trade_bid():
        with planner.factory() as s:
            row = s.scalars(select(Intent).where(Intent.player_id == "guenther")).one()
            if change == "execute":
                # Der Executor stempelt den Beginn seines Takts — der lag VOR dem Merken
                # (Codex-Nachprüfung): Der Zeitstempel darf nicht entscheiden.
                row.status = "executed"
                row.updated_at = datetime.now(UTC) - 10 * MIN
            elif change in ("adjust", "raise"):  # Emils erste oder weitere Erhöhung
                row.adjusted_from = row.adjusted_from or row.amount
                row.amount += 500_000
                row.updated_at = datetime.now(UTC)
            s.commit()
    planner.during_build = change_trade_bid


@pytest.mark.parametrize(("change", "adjusted", "counted"), [
    ("adjust", None, {}),                           # unangepasst gezählt als frei, dann fest
    ("execute", None, {}),                          # während des Planbaus ausgeführt
    ("raise", 4_500_000, {"guenther": 5_000_000}),  # fest gezählt, dann erhöht
    # Senken vor, Erhöhen nach dem Lesen des Plans: Der Plan zählte 4 M, gebunden sind 5 M.
    ("none", 4_500_000, {"guenther": 4_000_000}),
])
async def test_a_trade_bid_binding_more_than_the_plan_counted_stops_the_run(
    planner, monkeypatch, change, adjusted, counted,
):
    """Codex-Review 03.10.2026 (P0/P1) und Nachprüfung: Passt Emil ein Handelsgebot während des
    Planbaus an oder wird es ausgeführt, rechnete der Plan dessen Geld als frei. Kein
    Engine-Vorhaben aus diesem Plan — sonst wäre das Geld doppelt verplant bzw. scheiterte das
    Gebot beim Write und sperrte das Ziel für den Tag. Der nächste Lauf plant neu."""
    _intent(planner.factory, "guenther", adjusted_from=adjusted)
    _obermair_buy(planner, monkeypatch, counted=counted)
    _during_build(planner, change)

    summary = await wi.run_intent_planner()

    assert _engine_bids(planner.factory) == []
    assert "Plan überholt" in summary and "guenther" in summary
    assert _state(planner.factory, "guenther")[0] == (
        "executed" if change == "execute" else "planned")
    with planner.factory() as s:
        [report] = s.scalars(select(Report).where(Report.kind == "engine_decision")).all()
    assert "Nicht angewendet" in report.body


async def test_a_trade_bid_the_plan_counted_does_not_stop_the_run(planner, monkeypatch):
    """Gegenprobe: Zählte der Plan das feste Handelsgebot mit (und ein längst ausgeführtes sah
    er als Live-Gebot oder gar nicht mehr), legt der Lauf das Engine-Vorhaben an."""
    _intent(planner.factory, "guenther", adjusted_from=4_500_000)
    _intent(planner.factory, "alt", status="executed")
    _obermair_buy(planner, monkeypatch, counted={"guenther": 5_000_000})

    summary = await wi.run_intent_planner()

    assert _engine_bids(planner.factory) == ["obermair"]
    assert "Plan überholt" not in summary


def test_late_firm_trade_bids_compare_with_what_the_plan_counted(tmp_path):
    """Die Regel einzeln: Spät ist ein festes Handelsgebot der Liga (angepasst, in Ausführung,
    während des Planbaus ausgeführt), das mehr bindet, als der Plan für den Spieler zählte."""
    factory = _file_db(tmp_path)
    cases = {
        "frei": {},                                       # geplant, unangepasst: weicht
        "angepasst": {"adjusted_from": 4_000_000},        # Plan zählte nichts
        "erhoeht": {"adjusted_from": 4_000_000},          # Plan zählte 4,5 M
        "gezaehlt": {"adjusted_from": 4_000_000},         # Plan zählte 5 M
        "laeuft": {"status": "executing"},                # Plan zählte nichts
        "alt_ab": {"status": "executed"},                 # schon vor dem Merken ausgeführt
        "offen_ab": {"status": "executed"},               # beim Merken offen, dann ausgeführt
        "live_ab": {"status": "executed"},                # dito, aber als Live-Gebot gezählt
        "fremd": {"adjusted_from": 4_000_000, "league": "L2"},
        "engine": {"adjusted_from": 4_000_000, "trade": False},
    }
    ids = {}
    with factory() as s:
        for pid, opts in cases.items():
            row = Intent(created_at=NOW - H, updated_at=NOW - H, kind="bid", player_id=pid,
                         player_name=pid, amount=5_000_000,
                         sources={"league_id": opts.get("league", "L1"),
                                  **({"trade": True} if opts.get("trade", True) else {})},
                         listing_type="kickbase", execute_at=NOW + H,
                         status=opts.get("status", "planned"),
                         adjusted_from=opts.get("adjusted_from"))
            s.add(row)
            s.flush()
            ids[pid] = row.id
        s.commit()
    with factory() as s:  # nach dem Merken angelegt und gleich ausgeführt
        s.add(Intent(created_at=NOW, updated_at=NOW - H, kind="bid", player_id="neu_ab",
                     player_name="neu_ab", amount=5_000_000,
                     sources={"league_id": "L1", "trade": True}, listing_type="kickbase",
                     execute_at=NOW, status="executed"))
        s.commit()
    open_before = frozenset({ids["frei"], ids["angepasst"], ids["erhoeht"], ids["gezaehlt"],
                             ids["laeuft"], ids["offen_ab"], ids["live_ab"], ids["fremd"]})
    counted = {"erhoeht": 4_500_000, "gezaehlt": 5_000_000, "live_ab": 5_000_000}

    with factory() as s:
        late = wi.late_firm_trade_bids(s, "L1", counted, open_before, max(ids.values()))

    assert sorted(r.player_id for r in late) == [
        "angepasst", "erhoeht", "laeuft", "neu_ab", "offen_ab"]


def test_the_snapshot_keeps_open_trade_bids_and_the_last_id(tmp_path):
    factory = _file_db(tmp_path)
    _intent(factory, "geplant")
    _intent(factory, "laeuft", status="executing")
    _intent(factory, "fertig", status="executed")
    _intent(factory, "engine", trade=False)

    with factory() as s:
        open_ids, top = wi.trade_bid_snapshot(s)
        names = {r.id: r.player_id for r in s.scalars(select(Intent))}

    assert sorted(names[i] for i in open_ids) == ["geplant", "laeuft"]
    assert top == max(names)


# ------------------------------------------------------------------ Planer: verworfen sichtbar


def test_a_dropped_engine_bid_shows_up_in_the_run_result_and_the_log(caplog):
    """Am 02.10. verwarf der Abgleich das Obermair-Gebot stumm (der gekoppelte Verkauf fehlte)
    — weder Lauf-Ergebnis noch Log sagten, warum. Jetzt stehen Spieler und Grund in beidem."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    draft = wi.IntentDraft(
        kind="bid", player_id="obermair", player_name="Obermair", team_id="9", position=3,
        amount=5_548_122, rationale="r", sources={"league_id": "L1"}, listing_type="kickbase",
        deadline=NOW + 20 * timedelta(hours=1), execute_at=NOW + 19 * timedelta(hours=1),
    )
    with Session(engine, future=True) as s, caplog.at_level(logging.WARNING):
        stats = wi.upsert_intents(s, [draft], NOW, purchase_reviews={"obermair": {}})

    assert stats.created == []
    assert "1 verworfen" in stats.summary()
    assert [pid for pid, _reason in stats.dropped] == ["obermair"]
    assert "obermair" in caplog.text and "verworfen" in caplog.text


async def test_swap_backed_full_lineup_allows_non_conflicting_trades(db, monkeypatch):
    """Emil 02.11.2026: Ein reiner 1-zu-1-Dispo-Tausch bei voller 11er-Elf (noch `planned`)
    sperrt den Handels-Autopiloten nicht für unbeteiligte kurzfristige Markt-Flips."""
    _riser_world(monkeypatch, db)
    with db() as s:
        s.add(
            Report(
                created_at=(NOW - 5 * MIN).astimezone(UTC).replace(tzinfo=None),
                kind="engine_decision",
                title="Finanzierter Mannschaftsplan",
                body="Plan",
                source="season-engine-v1",
                meta={
                    "league_id": "L1",
                    "team_plan": {
                        "baseline_filled_slots": 11,
                        "purchase_ids": ["onyedika"],
                        "swap_sales": [
                            {
                                "player_id": "rothe",
                                "for_player_id": "onyedika",
                                "market_value_eur": 9_730_000,
                                "proceeds_eur": 8_757_000,
                            }
                        ],
                        "notes": ["Tausch"],
                    },
                },
            )
        )
        s.add(
            Intent(
                created_at=NOW,
                updated_at=NOW,
                kind="bid",
                player_id="onyedika",
                player_name="Onyedika",
                amount=10_500_000,
                sources={
                    "league_id": "L1",
                    "swap_out": "rothe",
                    "purchase_review": {
                        "swap_sale": {
                            "player_id": "rothe",
                            "market_value_eur": 9_730_000,
                            "proceeds_eur": 8_757_000,
                        }
                    },
                },
                listing_type="kickbase",
                execute_at=NOW + 10 * H,
                status="planned",
            )
        )
        s.commit()

    assert await ta.run_trade_autopilot(now=NOW) == "ok:1/0/0"
    assert sorted(_trade_bids(db)) == ["onyedika", "riser"]


def test_dispo_sale_couples_when_planned_swap_target_upgrades():
    """Emil 02.11.2026: Aktualisiert ein neuer Plan das Ziel eines bereits geplanten
    Tausch-Verkaufs (`swap_for` von `sirch` auf `onyedika`), koppelt `_dispo_sale_coupled`
    das neue Tauschgebot über den aktualisierten Verkaufs-Entwurf sauber."""
    sell_row = Intent(
        created_at=NOW,
        updated_at=NOW,
        kind="sell",
        player_id="rothe",
        player_name="Rothe",
        amount=9_730_000,
        sources={"league_id": "L1", "swap_for": "sirch", "swap_dispo": True},
        listing_type="kickbase",
        execute_at=NOW + 10 * H,
        status="planned",
    )
    sell_ony = wi.IntentDraft(
        kind="sell",
        player_id="rothe",
        player_name="Rothe",
        team_id="9",
        position=2,
        amount=9_730_000,
        rationale="Tauschverkauf",
        sources={"league_id": "L1", "swap_for": "onyedika", "swap_dispo": True},
        listing_type="kickbase",
        deadline=None,
        execute_at=NOW + 20 * H,
    )
    rev_ony = {
        "swap_sale": {
            "player_id": "rothe",
            "market_value_eur": 9_730_000,
            "proceeds_eur": 8_757_000,
        }
    }
    assert wi._dispo_sale_coupled(
        rev_ony, "onyedika", [sell_ony], {("sell", "rothe"): sell_row}, set()
    ) is True
    # Manuell angepasster Verkauf bleibt an sein bisheriges Ziel gebunden.
    sell_row.adjusted_from = 9_500_000
    assert wi._dispo_sale_coupled(
        rev_ony, "onyedika", [sell_ony], {("sell", "rothe"): sell_row}, set()
    ) is False


async def test_live_dispo_swap_bid_keeps_trading_into_deep_minus_when_covered(db, monkeypatch):
    """Emil 10.11.2026: Sobald ein 1-zu-1-Dispo-Swap-Gebot (z. B. Mohya 12,00 M €) live steht
    und der gekoppelte Tausch-Verkauf (z. B. Adeline 4,41 M €, after_squad_size=13 wegen eines
    gehaltenen Handelsspielers Gouweleeuw 8,52 M €) offen wartet, darf `busy` nicht zuschlagen
    und `Capacity.coverage` muss den 95-%-Tauscherlös anrechnen, damit der zinsfreie Dispo bis
    zum 50-M-€-Rahmen für weitere Marktwert-Steiger genutzt wird."""
    from kickbase_agent.db.models import TransferAction
    from kickbase_agent.kickbase.models import SquadPlayer

    expires = NOW + 9 * H
    # Reguläre 11er-Elf inkl. Adeline (4,41 M €) + 1 Handelsspieler Gouweleeuw (8,52 M €) = 13.
    squad = (
        _eleven()
        + [SquadPlayer(player=_player("adeline", "Adeline", 4_409_057))]
        + [SquadPlayer(player=_player("gouweleeuw", "Gouweleeuw", 8_517_716))]
    )
    market = [
        MarketPlayer(
            player=_player("hack", "Robin Hack", 6_500_000),
            price=6_500_000,
            expires_at=expires,
        )
    ]
    client = _ReadOnlyClient(
        squad=squad,
        market=market,
        budget=1_320_297,
        own_bids=[{"player_id": "mohya", "amount": 12_000_000}],
    )
    _world(monkeypatch, client)
    _radar(db, NOW - 5 * MIN, _kauf("hack", 6_500_000, 7_150_000, expires))
    _engine_plan(db, NOW - 5 * MIN, purchases=[])

    with db() as s:
        action = TransferAction(
            league_id="L1",
            action_type="bid",
            player_id="gouweleeuw",
            amount=7_930_000,
            status="executed",
            idempotency_key="gouweleeuw-buy",
            rule_id="intent-bid",
            dry_run=False,
            created_at=NOW - 24 * H,
        )
        s.add(action)
        s.flush()
        s.add(
            Intent(
                created_at=NOW - 24 * H,
                updated_at=NOW - 24 * H,
                kind="bid",
                player_id="gouweleeuw",
                player_name="Gouweleeuw",
                amount=7_930_000,
                sources={"trade": True, "league_id": "L1"},
                listing_type="kickbase",
                deadline=NOW - 23 * H,
                execute_at=NOW - 24 * H,
                status="executed",
                executed_action_id=action.id,
            )
        )
        # Laufendes Dispo-Swap-Gebot (auch mit deadline > NOW für Kickbase-Auktionsfall):
        s.add(
            Intent(
                created_at=NOW - 2 * H,
                updated_at=NOW - 2 * H,
                kind="bid",
                player_id="mohya",
                player_name="Wael Mohya",
                amount=12_000_000,
                sources={"league_id": "L1", "swap_out": "adeline"},
                listing_type="manager",
                deadline=NOW + 4 * H,
                execute_at=NOW - 2 * H,
                status="executed",
            )
        )
        # Bereits erledigtes Manager-Listing-Gebot (z. B. Petkov/Behrens, nicht mehr in own_bids,
        # aber deadline steht auf Spieltags-Anpfiff in 10 Tagen) darf NICHT mehr sperren:
        s.add(
            Intent(
                created_at=NOW - 48 * H,
                updated_at=NOW - 48 * H,
                kind="bid",
                player_id="petkov",
                player_name="Lukas Petkov",
                amount=3_500_000,
                sources={"league_id": "L1"},
                listing_type="manager",
                deadline=NOW + 240 * H,
                execute_at=NOW - 48 * H,
                status="executed",
            )
        )
        # Gekoppelter Tausch-Verkauf mit after_squad_size=13 (12 Reguläre + 1 Handelsspieler):
        s.add(
            Intent(
                created_at=NOW - 2 * H,
                updated_at=NOW - 2 * H,
                kind="sell",
                player_id="adeline",
                player_name="Adeline",
                amount=4_409_057,
                sources={
                    "league_id": "L1",
                    "rule_id": "swap_sell",
                    "after_squad_size": 13,
                    "swap_for": "mohya",
                    "swap_dispo": True,
                },
                listing_type="kickbase",
                execute_at=NOW + 12 * H,
                status="planned",
            )
        )
        s.commit()
        assert ta.engine_busy(s, NOW, live_bids=["mohya"]) is False

    # Ohne `live_swap_cover` wäre Freitagsdeckung 1,32 M - 12,00 M + 8,52 M = -2,16 M € (< +10k €)
    # und `busy` wäre True wegen `mohya` in `live_bids`. Mit `live_swap_cover` (+4,19 M €) liegt
    # die Freitagsdeckung bei +2,03 M € und schaltet das 7,15-M-€-Gebot tief ins Minus frei!
    assert await ta.run_trade_autopilot(now=NOW) == "ok:1/0/0"
    with db() as s:
        hack_bid = s.scalars(select(Intent).where(Intent.player_id == "hack")).one()
    assert hack_bid.status == "planned" and hack_bid.amount == 7_150_000


