"""Steuerzentrale (worker/intents.py): Planner-Regeln, Idempotenz, Executor, Arming.

Alle Tests laufen ohne echte API: Fakes nach dem Muster von tests/worker/
test_auto_trading.py (FakeClient/_patch_world) und tests/agent/test_executor.py.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import kickbase_agent.worker.intents as wi
from kickbase_agent.agent.executor import ActionExecutor, PlannedAction
from kickbase_agent.cli.plan import Plan
from kickbase_agent.db.models import Base, Intent, TransferAction
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS, Guardrails
from kickbase_agent.decision.models import Proposal
from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.worker.auto_trading import TodayCounts
from kickbase_agent.worker.intents import (
    RESULT_EXPIRED,
    RESULT_KILL_SWITCH,
    RESULT_NOT_IN_PLAN,
    RESULT_NOT_ON_MARKET,
    IntentDraft,
    MarketRow,
    action_for,
    blocked_keys,
    cap_reason,
    due_intents,
    execute_intent,
    expire_intents,
    market_rows_from_raw,
    plan_intents,
    run_intent_executor,
    run_intent_planner,
    schedule_for,
    today_intent_counts,
    upsert_intents,
)

# FESTER Anker, mittags, fern jeder Tagesgrenze — und zwar aus zwei Gründen, die in
# entgegengesetzte Richtungen ziehen (beide sind uns schon um die Ohren geflogen):
# 1. Wanduhr verboten, wo Kalendertage zählen: Die Tagesregeln (heute executed/failed,
#    Tages-Cap) vergleichen UTC-KALENDERTAGE, keine Abstände. Mit `datetime.now(UTC)`
#    fiel „NOW - 1 h" zwischen 00:00 und 01:00 UTC auf den VORTAG — der Test war jede
#    Nacht eine Stunde rot, unabhängig vom Code (verifiziert am 26.08.2026, 00:02 UTC).
# 2. Fester Anker verboten, wo die ECHTE Uhr läuft: `run_intent_executor`/`-planner`
#    holen sich ihr `now` selbst per `datetime.now(UTC)`. Bekämen sie Offsets auf diesen
#    Anker, kippten „Zukunfts"-Intents (NOW + 10 h) in die Vergangenheit, sobald die reale
#    Zeit den Anker überholt (passiert am 23.08.2026 um 20:25 UTC).
# Auflösung: NOW steht fest und wird NUR an Code gegeben, dem wir `now` explizit
# durchreichen. Die Runner-Tests (echte Uhr) rechnen bewusst lokal mit `datetime.now(UTC)`.
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
H = timedelta(hours=1)
MIN = timedelta(minutes=1)


def _review(pid="B1", *, now=NOW, cap=1_500_000):
    """Interne Kaufentscheidung zum bestehenden Testspieler, kein API-Payload."""
    return {
        "schema_version": 1, "player_id": pid, "selected": True, "max_bid_eur": cap,
        "observed_at": now.isoformat(), "valid_until": (now + 2 * H).isoformat(),
        "market_value_eur": 1_200_000, "purpose": "starter",
        "reasons": ["Belegter Starter für die fehlende Sturmposition; Preislimit geprüft."],
    }


def _team_reviews(*pids, budget=3_700_000):
    """Interner Paketbeleg; keine neuen API-Fixtures."""
    plan = {
        "plan_id": "team-1", "observed_at": NOW.isoformat(), "budget_eur": budget,
        "open_bid_exposure_eur": 0, "reserved_cash_eur": 1_200_000 * len(pids),
        "owned_player_ids": [], "open_bids": [], "baseline_filled_slots": 8,
        "purchases": [{"player_id": pid, "amount_eur": 1_200_000, "max_bid_eur": 1_300_000}
                      for pid in pids],
    }
    return {pid: {
        **_review(pid, cap=1_300_000), "purpose": "lineup_completion",
        "engine_version": "season-v1", "plan_id": "team-1", "team_plan": plan,
        "bid_scenarios": [
            {"amount": 1_200_000, "win_probability": None, "selected": True},
            {"amount": 1_300_000, "win_probability": None, "selected": False},
        ],
    } for pid in pids}


# ------------------------------------------------------------------ Bausteine


def _prop(pid="B1", amount=1_200_000, action_type="bid", approval=False, name="Kaufmann",
          score=0.62, rationale="r", squad_need=False):
    rule = "buy_explosion" if action_type == "bid" else "sell_weak"
    return Proposal(
        action=PlannedAction(
            action_type=action_type, league_id="L1", player_id=pid, amount=amount, rule_id=rule,
            squad_need=squad_need,
        ),
        rule_id=rule,
        rationale=rationale,
        score=score,
        requires_approval=approval,
        player_name=name,
    )


def _squad_member(pid: str, mv: int = 1_000_000, *, name="Spieler", prob=None):
    player = SimpleNamespace(
        id=pid, market_value=mv, first_name="Test", last_name=name, team_id="T9",
        position=3, lineup_prob=prob,
    )
    return SimpleNamespace(player=player)


def _cand(pid: str, *, score=0.62, elf=0.7, injury=None, reason=""):
    return ExplosionCandidate(
        player_id=pid, name=f"Cand{pid}", team_id="T1", position=4,
        current_market_value=1_200_000, total_score=score, lineup_score=elf, signals=[],
        injury_reason=reason, injury_severity=injury,
    )


def _plan(buys=(), sells=(), squad=(), cands=()) -> Plan:
    return Plan(
        league_id="L1", budget=20_000_000, buys=list(buys), sells=list(sells),
        squad=list(squad), user_id="U1", market_candidates=list(cands),
    )


def _row(pid="B1", *, exs=None, seller="", price=1_150_000, our_bid=None, is_ours=False,
         prob=2):
    return MarketRow(
        player_id=pid, price=price, expires_in_s=exs, seller_id="S9" if seller else "",
        seller_name=seller, player_name=f"Markt{pid}", team_id="T1", position=4,
        kb_prob=prob, our_bid=our_bid, is_ours=is_ours,
    )


def _draft(kind="bid", pid="B1", amount=1_200_000, *, execute_at=NOW + 2 * H,
           deadline=None, listing_type="kickbase", sniper=False, sources=None,
           name="Kaufmann") -> IntentDraft:
    return IntentDraft(
        kind=kind, player_id=pid, player_name=name, team_id="T1", position=4,
        amount=amount, rationale="r", sources=sources or {"score": 0.62},
        listing_type=listing_type, deadline=deadline, execute_at=execute_at, sniper=sniper,
    )


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine, future=True) as s:
        yield s


@pytest.fixture
def env(monkeypatch):
    def _set(dry="1", intents="0", allow_live="0", test_league=""):
        monkeypatch.setenv("AGENT_DRY_RUN", dry)
        monkeypatch.setenv("AGENT_TEST_LEAGUE_ID", test_league)
        monkeypatch.setenv("AGENT_ALLOW_LIVE", allow_live)
        monkeypatch.setenv("AGENT_AUTO_INTENTS", intents)
        monkeypatch.setenv("AGENT_AUTO_TRADING", "0")
        monkeypatch.setenv("AGENT_AUTO_PURGE", "0")
        monkeypatch.setenv("AGENT_AUTO_BONUS", "0")
        monkeypatch.setenv("AGENT_AUTO_LINEUP", "0")
        monkeypatch.setenv("AGENT_APPROVE_LIVE", "0")
        monkeypatch.setenv("AGENT_WRITE_SPACING_SECONDS", "0")  # Tests warten nicht 2 s
        import kickbase_agent.config as cfg

        cfg._settings = None

    yield _set
    import kickbase_agent.config as cfg

    cfg._settings = None


def _intent(session, *, kind="bid", pid="B1", amount=1_200_000, status="planned",
            execute_at=NOW - MIN, deadline=None, adjusted_from=None, note=None,
            updated_at=None, name="Kaufmann", sources=None, review_now=NOW) -> Intent:
    if sources is None:
        sources = {"score": 0.62}
        if kind == "bid":
            sources["purchase_review"] = _review(pid, now=review_now)
    row = Intent(
        created_at=NOW - H, updated_at=updated_at or NOW - H, kind=kind, player_id=pid,
        player_name=name, team_id="T1", position=4, amount=amount, rationale="r",
        sources=sources or {"score": 0.62}, listing_type="kickbase", deadline=deadline,
        execute_at=execute_at, status=status, adjusted_from=adjusted_from, emil_note=note,
    )
    session.add(row)
    session.commit()
    return row


# ------------------------------------------------------------ Markt-Parsing


def test_market_rows_from_raw_classifies_kickbase_and_manager_listings():
    """exs>0 ohne `u` = Kickbase-Listing; `u` = Manager-Listing (exs 0, live 23.08.);
    eigenes Gebot aus ofs[].u, eigenes Listing aus u.i."""
    raw = {"it": [
        {"i": "99", "fn": "Hidden", "n": "Gem", "pos": 4, "mv": 5_000_000, "prc": 5_500_000,
         "exs": 3600, "tid": "11", "prob": 1,
         "ofs": [{"u": "U1", "uop": 5_120_000, "uoid": "U1"}]},
        {"i": "88", "fn": "Cheap", "n": "Striker", "pos": 4, "mv": 3_000_000, "prc": 3_200_000,
         "exs": 0, "tid": "5", "u": {"i": "U7", "n": "Felix K."}, "ofs": []},
        {"i": "3", "fn": "Jamal", "n": "Musiala", "pos": 3, "mv": 25_000_000, "prc": 26_000_000,
         "exs": 0, "tid": "2", "u": {"i": "U1", "n": "emil"}},
    ]}
    rows = {r.player_id: r for r in market_rows_from_raw(raw, "U1")}
    kb, mgr, mine = rows["99"], rows["88"], rows["3"]
    assert (kb.expires_in_s, kb.is_manager, kb.our_bid, kb.price) == (3600, False, 5_120_000,
                                                                      5_500_000)
    assert (kb.player_name, kb.team_id, kb.position, kb.kb_prob) == ("Hidden Gem", "11", 4, 1)
    assert (mgr.is_manager, mgr.seller_name, mgr.our_bid, mgr.is_ours) == (True, "Felix K.",
                                                                           None, False)
    assert mine.is_ours is True and mine.is_manager is True


# ---------------------------------------------------------- Planner-Regeln


def test_kickbase_listing_deadline_and_execute_at_15min_before():
    """exs 10 h → deadline = jetzt + 10 h, execute_at = deadline − 15 min, kickbase."""
    [d] = plan_intents(_plan(buys=[_prop()]), [_row(exs=10 * 3600)], NOW)
    assert (d.kind, d.player_id, d.amount, d.listing_type) == ("bid", "B1", 1_200_000, "kickbase")
    assert d.deadline == NOW + 10 * H
    assert d.execute_at == NOW + 10 * H - 15 * MIN
    assert d.sniper is False and "sniper" not in d.sources


def test_squad_need_marker_travels_from_planner_into_the_intent():
    """Kader-Bedarf (26.08.): Der Planner-Marker muss in den Quellen landen,
    sonst fällt das Vorhaben beim Ausführen auf die 110-%-Kappe zurück."""
    [d] = plan_intents(_plan(buys=[_prop(squad_need=True)]), [_row(exs=10 * 3600)], NOW)
    assert d.sources["squad_need"] is True
    # Gegenprobe: ohne Bedarf steht der Schlüssel gar nicht erst drin
    [plain] = plan_intents(_plan(buys=[_prop()]), [_row(exs=10 * 3600)], NOW)
    assert "squad_need" not in plain.sources


def test_manager_listing_gets_default_two_hour_window():
    """Manager-Listing (Verkäufer-Objekt, kein Countdown): keine Deadline, jetzt + 2 h."""
    [d] = plan_intents(_plan(buys=[_prop()]), [_row(exs=0, seller="Felix K.")], NOW)
    assert (d.listing_type, d.deadline, d.execute_at) == ("manager", None, NOW + 2 * H)
    assert d.sources["seller"] == "Felix K." and d.sources["listing_price"] == 1_150_000


def test_sniper_under_30_min_is_due_immediately():
    """< 30 min Restzeit → execute_at = jetzt, Marker sources.sniper (Karte: „bereits
    ausgeführt (Sniper-Fenster)")."""
    [d] = plan_intents(_plan(buys=[_prop()]), [_row(exs=20 * 60)], NOW)
    assert d.execute_at == NOW and d.sniper is True and d.sources["sniper"] is True
    assert d.deadline == NOW + 20 * MIN
    # genau 30 min ist KEIN Sniper mehr → deadline − 15 min
    [d2] = plan_intents(_plan(buys=[_prop()]), [_row(exs=30 * 60)], NOW)
    assert d2.sniper is False and d2.execute_at == NOW + 15 * MIN


def test_big_amount_minimum_window_never_after_deadline_minus_15():
    """≥ 5 M: Mindest-Veto-Fenster 2 h — aber nie nach deadline − 15 min; Sniper gewinnt
    nur unter 30 min; Manager-Listing/Verkauf: jetzt + 2 h."""
    big = 6_000_000
    # 1 h Restzeit: 2 h wären NACH dem Ablauf → deadline − 15 min (= jetzt + 45 min)
    s = schedule_for(NOW, big, _row(exs=3600))
    assert s.execute_at == NOW + 45 * MIN and s.sniper is False
    # 10 h Restzeit: deadline − 15 min liegt ohnehin ≥ 2 h voraus
    assert schedule_for(NOW, big, _row(exs=10 * 3600)).execute_at == NOW + 10 * H - 15 * MIN
    # 20 min Restzeit: Sniper schlägt das Mindestfenster
    s = schedule_for(NOW, big, _row(exs=20 * 60))
    assert s.execute_at == NOW and s.sniper is True
    # Manager-Listing und Verkauf ohne Listing-Bezug: Default-Fenster
    assert schedule_for(NOW, big, _row(exs=0, seller="X")).execute_at == NOW + 2 * H
    assert schedule_for(NOW, big, None) == wi.Schedule("kickbase", None, NOW + 2 * H)


def test_sells_become_list_or_sell_with_default_window():
    """Verkaufsvorschläge: kind aus action_type; kein Listing-Bezug → jetzt + 2 h, keine
    Deadline; Sofortverkauf trägt den Planungs-MW (Preflight-Drift-Check)."""
    squad = [_squad_member("S1", 900_000, name="Lister", prob=5),
             _squad_member("S2", 4_000_000, name="Injured")]
    plan = _plan(
        sells=[_prop(pid="S1", amount=900_000, action_type="list", name="Lister"),
               _prop(pid="S2", amount=4_000_000, action_type="sell", name="Injured")],
        squad=squad,
    )
    lst, sell = plan_intents(plan, [], NOW)
    assert (lst.kind, lst.amount, lst.execute_at, lst.deadline) == ("list", 900_000,
                                                                    NOW + 2 * H, None)
    assert (lst.team_id, lst.position, lst.sources["kb_prob"]) == ("T9", 3, 5)
    assert (sell.kind, sell.amount, sell.execute_at) == ("sell", 4_000_000, NOW + 2 * H)
    assert "kb_prob" not in sell.sources  # None-Werte fallen raus


def test_plan_skips_gone_from_market_own_bid_and_own_listing():
    """Kein Vorhaben ohne Markt-Listing, bei eigenem offenen Gebot oder eigenem Listing;
    eigener gelisteter Kaderspieler bekommt kein zweites Listing-Vorhaben."""
    plan = _plan(
        buys=[_prop(pid="GONE"), _prop(pid="MINE"), _prop(pid="OWNLIST"), _prop(pid="OK")],
        sells=[_prop(pid="S1", action_type="list"), _prop(pid="S2", action_type="list")],
        squad=[_squad_member("S1"), _squad_member("S2")],
    )
    rows = [_row("MINE", exs=3600, our_bid=1_100_000), _row("OWNLIST", exs=0, is_ours=True),
            _row("OK", exs=3600), _row("S1", exs=0, is_ours=True, seller="emil")]
    drafts = plan_intents(plan, rows, NOW)
    assert [(d.kind, d.player_id) for d in drafts] == [("bid", "OK"), ("list", "S2")]


def test_sources_carry_score_rule_flags_and_candidate_facts():
    """sources: score/rule_id/requires_approval immer; elf_score + li_status aus den
    Markt-Kandidaten, kb_prob + listing_price aus dem Markt-Listing."""
    plan = _plan(
        buys=[_prop(pid="B1", approval=True, score=0.71),
              _prop(pid="B2", name="Hurt")],
        cands=[_cand("B1", elf=0.66), _cand("B2", injury="long", reason="Kreuzbandriss")],
    )
    d1, d2 = plan_intents(plan, [_row("B1", exs=7200, prob=1), _row("B2", exs=7200)], NOW)
    assert d1.sources == {
        "score": 0.71, "rule_id": "buy_explosion", "requires_approval": True,
        "league_id": "L1",
        "elf_score": 0.66, "kb_prob": 1, "listing_price": 1_150_000,
    }
    assert d2.sources["li_status"] == "out (long): Kreuzbandriss"
    assert d1.player_name == "Kaufmann" and d1.team_id == "T1" and d1.position == 4


# ------------------------------------------------------- upsert / Idempotenz


def test_upsert_creates_once_and_updates_amount_without_duplicating(session):
    """Zweiter Lauf mit anderem Betrag aktualisiert das offene Vorhaben (kein Duplikat);
    execute_at wandert nie nach hinten (Manager-Fenster bleibt), Sekunden-Drift zählt
    nicht, eine näher rückende Deadline zieht vor."""
    stats = upsert_intents(session, [_draft(amount=1_200_000, execute_at=NOW + 2 * H)], NOW)
    assert stats.summary() == "1 neu, 0 aktualisiert, 0 superseded"
    row = session.query(Intent).one()
    assert (row.status, row.amount, wi._as_utc(row.created_at)) == ("planned", 1_200_000, NOW)

    later = NOW + H
    stats = upsert_intents(
        session, [_draft(amount=1_300_000, execute_at=later + 2 * H)], later
    )  # neu gerechnetes Manager-Fenster läge SPÄTER → bleibt
    assert stats.summary() == "0 neu, 1 aktualisiert, 0 superseded"
    row = session.query(Intent).one()
    assert (row.amount, wi._as_utc(row.execute_at), wi._as_utc(row.updated_at)) == (
        1_300_000, NOW + 2 * H, later
    )
    # identischer Entwurf (nur 20 s Drift) → unverändert, updated_at bleibt
    stats = upsert_intents(
        session, [_draft(amount=1_300_000, execute_at=NOW + 2 * H - timedelta(seconds=20))],
        later + MIN,
    )
    assert stats.summary() == "0 neu, 0 aktualisiert, 0 superseded" and stats.unchanged
    # Deadline rückt näher (Sniper) → execute_at zieht vor
    stats = upsert_intents(session, [_draft(amount=1_300_000, execute_at=later + 2 * MIN,
                                             deadline=later + 20 * MIN, sniper=True,
                                             sources={"score": 0.62, "sniper": True})],
                           later + 2 * MIN)
    row = session.query(Intent).one()
    assert stats.updated and wi._as_utc(row.execute_at) == later + 2 * MIN
    assert wi._as_utc(row.deadline) == later + 20 * MIN and row.sources["sniper"] is True
    assert session.query(Intent).count() == 1


def test_upsert_keeps_emils_adjustment_but_pulls_execute_at_forward(session):
    """adjusted_from gesetzt: Betrag/Begründung/Quellen bleiben Emils Stand; nur
    execute_at (näher rückende Deadline/Sniper) zieht vor."""
    row = _intent(session, amount=1_500_000, adjusted_from=1_200_000,
                  execute_at=NOW + 5 * H, deadline=NOW + 5 * H + 15 * MIN)
    row.rationale, row.sources = "emil", {"score": 0.5}
    session.commit()

    stats = upsert_intents(
        session,
        [_draft(amount=1_250_000, execute_at=NOW + 5 * H, deadline=NOW + 5 * H + 15 * MIN)],
        NOW,
    )
    assert stats.unchanged and not stats.updated
    row = session.query(Intent).one()
    assert (row.amount, row.rationale, row.sources) == (1_500_000, "emil", {"score": 0.5})

    stats = upsert_intents(
        session,
        [_draft(amount=1_250_000, execute_at=NOW + H, deadline=NOW + H + 15 * MIN)],
        NOW + MIN,
    )
    row = session.query(Intent).one()
    assert stats.updated and wi._as_utc(row.execute_at) == NOW + H
    assert (row.amount, row.adjusted_from) == (1_500_000, 1_200_000)


def test_upsert_supersedes_vanished_proposals_with_honest_reason(session):
    """Offenes Vorhaben ohne Entwurf → superseded: „nicht mehr am Markt" (Spieler weg),
    sonst „nicht mehr im Plan"; ausgeführte/vetoed Zeilen bleiben unberührt."""
    _intent(session, pid="GONE")
    _intent(session, pid="STILL")
    _intent(session, pid="DONE", status="executed")
    _intent(session, kind="list", pid="S1")
    stats = upsert_intents(session, [_draft(pid="NEW")], NOW, market_pids={"STILL", "NEW"})
    by = {(r.kind, r.player_id): r for r in session.query(Intent)}
    assert stats.summary() == "1 neu, 0 aktualisiert, 3 superseded"
    assert (by[("bid", "GONE")].status, by[("bid", "GONE")].result) == (
        "superseded", RESULT_NOT_ON_MARKET
    )
    assert by[("bid", "STILL")].result == RESULT_NOT_IN_PLAN
    assert by[("list", "S1")].result == RESULT_NOT_IN_PLAN
    assert by[("bid", "DONE")].status == "executed"
    assert by[("bid", "NEW")].status == "planned"


def test_upsert_respects_veto_block_done_today_and_daily_caps(session):
    """Frisches Veto (24 h / laufende Deadline) und heute executed/failed blocken eine
    Neuanlage; gestriger failed darf neu; Tages-Cap (heute executed + offen) stoppt."""
    _intent(session, pid="VETO", status="vetoed", updated_at=NOW - 3 * H)
    _intent(session, pid="VETOOLD", status="vetoed", updated_at=NOW - 30 * H)
    _intent(session, pid="VETODL", status="vetoed", updated_at=NOW - 30 * H,
            deadline=NOW + H)
    _intent(session, pid="DONE", status="executed", updated_at=NOW - H)
    _intent(session, pid="FAILOLD", status="failed", updated_at=NOW - 26 * H)
    _intent(session, kind="list", pid="LFAIL", status="failed", updated_at=NOW - MIN)
    assert blocked_keys(session, NOW) == {
        ("bid", "VETO"), ("bid", "VETODL"), ("bid", "DONE"), ("list", "LFAIL")
    }
    drafts = [_draft(pid=p) for p in ("VETO", "VETOOLD", "VETODL", "DONE", "FAILOLD", "X1")]
    drafts += [_draft(kind="list", pid="LFAIL"), _draft(kind="list", pid="L2")]
    stats = upsert_intents(session, drafts, NOW, counts=TodayCounts(buys=3),
                           guardrails=Guardrails(daily_buy_cap=5, daily_sell_cap=1))
    created = [(r.kind, r.player_id) for r in stats.created]
    # Käufe: 3 heute + VETOOLD + FAILOLD = 5 → X1 über Cap; Verkäufe: Cap 1 → L2 passt
    assert created == [("bid", "VETOOLD"), ("bid", "FAILOLD"), ("list", "L2")]
    assert [d.player_id for d in stats.skipped_cap] == ["X1"]
    assert sorted(d.player_id for d in stats.skipped_blocked) == ["DONE", "LFAIL", "VETO",
                                                                  "VETODL"]
    assert "1 über Tages-Cap" in stats.summary()
    # Befund 03.10.2026: gesperrte Gebote stehen mit Spieler und Grund im Lauf-Ergebnis.
    assert sorted(pid for pid, _reason in stats.dropped) == ["DONE", "VETO", "VETODL"]


def test_upsert_never_overwrites_concurrent_veto_or_adjust(engine, monkeypatch):
    """API-Mutation (Veto/Adjust per UPDATE … WHERE status='planned') zwischen Laden und
    Schreiben des Planners gewinnt: kein superseded über ein Veto, kein Plan-Betrag über
    Emils Anpassung (konditionale UPDATEs)."""
    with Session(engine, future=True) as s1, Session(engine, future=True) as s2:
        a = _intent(s1, pid="A")
        b = _intent(s1, pid="B", amount=1_000_000)
        real = wi.open_intents

        def sneaky(session):
            rows = real(session)  # Planner hat geladen … jetzt kommt die API dazwischen
            s2.query(Intent).filter(Intent.id == a.id, Intent.status == "planned").update(
                {"status": "vetoed"}, synchronize_session=False
            )
            s2.query(Intent).filter(Intent.id == b.id, Intent.status == "planned").update(
                {"amount": 1_500_000, "adjusted_from": 1_000_000}, synchronize_session=False
            )
            s2.commit()
            return rows

        monkeypatch.setattr(wi, "open_intents", sneaky)
        stats = upsert_intents(s1, [_draft(pid="B", amount=1_100_000)], NOW)
        s1.expire_all()
        assert stats.superseded == [] and stats.updated == []
        assert s1.get(Intent, a.id).status == "vetoed"
        row_b = s1.get(Intent, b.id)
        assert (row_b.status, row_b.amount, row_b.adjusted_from) == ("planned", 1_500_000,
                                                                      1_000_000)


# ------------------------------------------------------------ Executor-Kern


@pytest.mark.parametrize("swap", [False, True])
def test_purchase_review_survives_draft_storage_and_execution_action(session, swap):
    """Kauf- und Tauschpfad behalten dieselbe datierte Spielerentscheidung."""
    from kickbase_agent.decision.policies import SwapPair

    proposal = _prop()
    review = _review()
    proposal.action.purchase_review = review
    plan = _plan(buys=[] if swap else [proposal])
    if swap:
        plan.matchday_deadline_at = NOW + 24 * H
        plan.swaps = [SwapPair(faller=_cand("F1"), bid=proposal, sell_amount=1_000_000)]
    drafts = plan_intents(plan, [_row(exs=3600)], NOW)
    bid = next(d for d in drafts if d.kind == "bid")
    assert bid.sources["purchase_review"] == review
    assert bid.sources["purchase_review"] is not review
    upsert_intents(session, [bid], NOW, purchase_reviews={"B1": review})
    row = session.query(Intent).one()
    action = action_for(row, "L1")
    assert action.purchase_review == review
    assert row.sources["league_id"] == "L1"
    action.purchase_review["max_bid_eur"] = 9_000_000
    assert row.sources["purchase_review"]["max_bid_eur"] == 1_500_000


def test_empty_curated_selection_supersedes_old_auto_bid_despite_hysteresis(session):
    row = _intent(session)
    stats = upsert_intents(
        session, [], NOW, purchase_reviews={}, keep_pids={"B1"}, market_pids={"B1"},
    )
    assert stats.superseded == [row]
    assert row.status == "superseded" and row.result == wi.RESULT_NOT_CURATED
    assert wi.hysteresis_keep_pids([_cand("B1", score=0.9)], set(),
                                   purchase_reviews={}, now=NOW) == set()


def test_selected_pending_bid_refreshes_review_without_replacing_adjusted_amount(session):
    old = _review(now=NOW - 3 * H, cap=1_400_000)
    row = _intent(session, amount=1_320_000, adjusted_from=1_200_000,
                  note="mein Betrag", sources={"purchase_review": old, "league_id": "L1"})
    fresh = _review(cap=1_500_000)
    stats = upsert_intents(session, [], NOW, purchase_reviews={"B1": fresh},
                           market_pids={"B1"})
    assert stats.unchanged == [row] and not stats.created
    assert (row.status, row.amount, row.adjusted_from, row.emil_note) == (
        "planned", 1_320_000, 1_200_000, "mein Betrag",
    )
    assert row.sources["purchase_review"] == {**fresh, "max_bid_eur": 1_400_000}
    assert fresh["max_bid_eur"] == 1_500_000  # Eingangsdaten unverändert


@pytest.mark.parametrize("old_cap,new_cap", [(1_300_000, 1_500_000),
                                             (1_500_000, 1_300_000)])
def test_adjusted_amount_must_fit_both_old_and_new_individual_limits(session, old_cap, new_cap):
    row = _intent(session, amount=1_320_000, adjusted_from=1_200_000,
                  sources={"purchase_review": _review(cap=old_cap)})
    stats = upsert_intents(session, [_draft(amount=1_200_000)], NOW,
                           purchase_reviews={"B1": _review(cap=new_cap)})
    assert stats.superseded == [row] and stats.created == []
    assert row.amount == 1_320_000 and row.status == "superseded"


def test_manual_pending_bid_is_not_replaced_by_same_player_automatic_draft(session):
    sources = {"manual": True, "league_id": "L1"}
    row = _intent(session, amount=1_250_000, sources=sources)
    stats = upsert_intents(session, [_draft(amount=1_200_000)], NOW, purchase_reviews={})
    assert stats.superseded == [] and stats.created == []
    assert (row.status, row.amount, row.sources) == ("planned", 1_250_000, sources)


def test_live_and_manual_commitments_do_not_impose_a_target_count_limit(session):
    _intent(session, pid="MANUAL", sources={"manual": True})
    stats = upsert_intents(session, [_draft()], NOW, purchase_reviews={"B1": _review()},
                           active_bid_targets={"LIVE": "manager"})
    assert [row.player_id for row in stats.created] == ["B1"]
    assert session.query(Intent).count() == 2


def test_selected_purchases_can_include_multiple_managers_and_three_targets(session):
    drafts = [_draft(pid="M2", listing_type="manager"), _draft(pid="B1"), _draft(pid="B2")]
    reviews = {pid: _review(pid) for pid in ("M2", "B1", "B2")}
    stats = upsert_intents(session, drafts, NOW, purchase_reviews=reviews,
                           active_bid_targets={"M1": "manager"})
    assert [row.player_id for row in stats.created] == ["M2", "B1", "B2"]


def test_draft_above_individual_limit_is_not_created(session):
    stats = upsert_intents(session, [_draft(amount=1_400_000)], NOW,
                           purchase_reviews={"B1": _review(cap=1_300_000)})
    assert stats.created == [] and session.query(Intent).count() == 0


def test_financed_engine_package_creates_three_buys_including_two_managers(session):
    reviews = _team_reviews("M1", "M2", "B1")
    drafts = [_draft(pid=pid, listing_type="manager" if pid.startswith("M") else "kickbase")
              for pid in reviews]
    stats = upsert_intents(session, drafts, NOW, purchase_reviews=reviews)
    assert [row.player_id for row in stats.created] == ["M1", "M2", "B1"]
    assert all(row.sources["purchase_review"]["plan_id"] == "team-1" for row in stats.created)


def test_three_individually_valid_adjustments_cannot_spend_same_team_reserve(session):
    reviews = _team_reviews("B1", "B2", "B3")
    drafts = [_draft(pid=pid, amount=1_300_000) for pid in reviews]
    stats = upsert_intents(session, drafts, NOW, purchase_reviews=reviews)
    assert stats.created == []
    assert session.query(Intent).count() == 0  # 3.9 M > 3.7 M, each alone would fit
    # Befund 03.10.2026: verworfene Engine-Gebote stehen mit Grund im Lauf-Ergebnis.
    assert [pid for pid, _reason in stats.dropped] == ["B1", "B2", "B3"]
    assert all(reason.startswith("Mannschaftsplan") for _pid, reason in stats.dropped)


def test_engine_package_rejects_inconsistent_shared_plan(session):
    reviews = _team_reviews("B1", "B2", "B3")
    reviews["B3"]["team_plan"] = {**reviews["B3"]["team_plan"], "budget_eur": 4_000_000}
    stats = upsert_intents(session, [_draft(pid=pid) for pid in reviews], NOW,
                           purchase_reviews=reviews)
    assert stats.created == []


def test_review_refresh_rechecks_adjust_that_arrives_after_plan_read(engine, monkeypatch):
    with Session(engine) as planner, Session(engine) as other:
        row = _intent(planner, sources={"purchase_review": _review(cap=1_300_000)})
        original = wi.open_intents

        def adjust_after_read(session):
            rows = original(session)
            other.query(Intent).filter(Intent.id == row.id).update(
                {"amount": 1_320_000, "adjusted_from": 1_200_000}, synchronize_session=False,
            )
            other.commit()
            return rows

        monkeypatch.setattr(wi, "open_intents", adjust_after_read)
        stats = upsert_intents(planner, [], NOW,
                               purchase_reviews={"B1": _review(cap=1_500_000)})
        assert stats.superseded == [row]
        assert row.status == "superseded" and row.amount == 1_320_000
        assert row.sources["purchase_review"]["max_bid_eur"] == 1_300_000


def _stored_swap(session, *, now=NOW):
    from kickbase_agent.decision.policies import SwapPair

    bid = _prop()
    bid.action.purchase_review = _review(now=now)
    plan = _plan(squad=[_squad_member("S1")] + [_squad_member(f"existing-{i}") for i in range(10)])
    plan.matchday_deadline_at = now + 24 * H
    plan.swaps = [SwapPair(faller=_cand("S1"), bid=bid, sell_amount=900_000)]
    drafts = plan_intents(plan, [_row(exs=6 * 3600)], now)
    upsert_intents(session, drafts, now, purchase_reviews={"B1": _review(now=now)},
                   squad_pids={sp.player.id for sp in plan.squad})
    rows = {row.kind: row for row in session.query(Intent).all()}
    return rows["bid"], rows["sell"]


@pytest.mark.asyncio
async def test_swap_survives_replanning_and_sells_only_after_its_exact_target_arrives(session, env):
    """Plan → Vorhaben → Neuplanung → Gebot → fremder Zuschlag → echter Ersatz → Verkauf."""
    env(dry="0", intents="1")
    bid, sell = _stored_swap(session)
    client = _FakeClient()
    client.squad = client.squad[:11]
    executor = ActionExecutor(client, session)
    owned = {sp.player.id for sp in client.squad}
    # Das Kaufziel wird reserviert; der Folgeplan erzeugt keine neuen Tausch-Drafts.
    stats = upsert_intents(session, [], NOW + MIN, purchase_reviews={"B1": _review()},
                           market_pids={"B1"}, squad_pids=owned)
    assert not stats.superseded and {row.kind for row in stats.unchanged} == {"bid", "sell"}
    assert sell.sources["swap_for"] == "B1"
    assert await execute_intent(session, bid, executor, "L1", now=NOW + 2 * MIN) == "executed"
    # Abgegeben heißt noch nicht gewonnen; das aktuelle offene Gebot trägt das Paar weiter.
    stats = upsert_intents(session, [], NOW + 3 * MIN, purchase_reviews={"B1": _review()},
                           active_bid_targets={"B1": "kickbase"}, market_pids={"B1"},
                           squad_pids=owned)
    assert not stats.superseded and sell.status == "planned"
    client.squad.append(_squad_member("UNRELATED"))
    assert await execute_intent(session, sell, executor, "L1", now=NOW + 4 * MIN) == "planned"
    assert "B1" in sell.result and "nicht im Kader" in sell.result
    assert client.calls == [("place_bid", "L1", "B1", 1_200_000)]
    # Der richtige Zugang steht jetzt im Kader. Seine alte Kaufprüfung darf
    # ablaufen; für den bereits finanzierten Abgang zählt der echte Besitz.
    client.squad[-1] = _squad_member("B1")
    owned = {sp.player.id for sp in client.squad}
    stats = upsert_intents(
        session, [_draft(kind="sell", pid="S1", amount=900_000)], NOW + 4 * H,
        purchase_reviews={}, squad_pids=owned, market_pids=set(),
    )
    assert not stats.superseded and sell.sources["swap_for"] == "B1"
    assert sell.sources["after_squad_size"] == 12
    assert await execute_intent(session, sell, executor, "L1", now=NOW + 4 * H) == "executed"
    assert client.calls[-1] == ("sell", "L1", "S1")


@pytest.mark.parametrize("case", ["veto", "failed", "no_offer", "expired", "no_deadline",
                                 "wrong_link", "missing_faller", "unselected"])
def test_waiting_swap_sale_does_not_survive_broken_or_expired_binding(session, case):
    bid, sell = _stored_swap(session)
    reviews = {"B1": _review()}
    owned = {"S1"}
    active = {"B1": "kickbase"}
    if case in ("veto", "failed"):
        bid.status = "vetoed" if case == "veto" else "failed"
    elif case == "no_offer":
        bid.status = "executed"
        active = {}
    elif case == "expired":
        sell.deadline = NOW
    elif case == "no_deadline":
        sell.deadline = None
    elif case == "wrong_link":
        bid.sources = {**bid.sources, "swap_out": "OTHER"}
    elif case == "missing_faller":
        owned = set()
    elif case == "unselected":
        reviews = {}
    session.commit()
    upsert_intents(session, [], NOW + MIN, purchase_reviews=reviews,
                   active_bid_targets=active, squad_pids=owned, market_pids={"B1"})
    assert sell.status == "superseded"


@pytest.mark.asyncio
async def test_swap_sell_without_explicit_target_or_after_deadline_cannot_execute(session, env):
    env(dry="0", intents="1")
    _, sell = _stored_swap(session)
    client = _FakeClient()
    client.squad.append(_squad_member("B1"))
    executor = ActionExecutor(client, session)
    assert await wi.squad_condition_unmet(sell, executor, "L1", now=NOW + 25 * H) == (
        "failed", "Tausch-Deadline überschritten — Verkauf verworfen.",
    )
    complete_sources = dict(sell.sources)
    sell.sources = {key: value for key, value in complete_sources.items()
                    if key != "after_squad_size"}
    status, reason = await wi.squad_condition_unmet(sell, executor, "L1", now=NOW)
    assert status == "failed" and "Kadergröße" in reason
    sell.sources = {key: value for key, value in complete_sources.items() if key != "swap_for"}
    status, reason = await wi.squad_condition_unmet(sell, executor, "L1", now=NOW)
    assert status == "failed" and "eindeutig" in reason
    assert client.calls == []


@pytest.mark.asyncio
async def test_second_swap_can_finish_after_first_departure_without_another_purchase():
    from kickbase_agent.decision.policies import SwapPair

    client = _FakeClient()
    client.squad = client.squad[:11]
    plan = _plan(squad=list(client.squad))
    plan.matchday_deadline_at = NOW + 24 * H
    plan.swaps = [
        SwapPair(faller=_cand("S1"), bid=_prop("B1"), sell_amount=900_000),
        SwapPair(faller=_cand("m1"), bid=_prop("B2"), sell_amount=500_000),
    ]
    drafts = plan_intents(plan, [_row("B1", exs=3600), _row("B2", exs=3600)], NOW)
    first, second = [d for d in drafts if d.kind == "sell"]
    executor = SimpleNamespace(_client=client)
    client.squad += [_squad_member("B1"), _squad_member("B2")]
    assert await wi.squad_condition_unmet(first, executor, "L1", now=NOW) is None
    client.squad = [sp for sp in client.squad if sp.player.id != "S1"]
    assert len(client.squad) == 12
    assert await wi.squad_condition_unmet(second, executor, "L1", now=NOW) is None
    # Dieselbe Kopfzahl ohne den zweiten konkreten Ersatz reicht weiterhin nicht.
    client.squad[-1] = _squad_member("UNRELATED")
    status, reason = await wi.squad_condition_unmet(second, executor, "L1", now=NOW)
    assert status == "planned" and "B2" in reason


class _FakeClient:
    """Write-Client nach Muster tests/agent: Reads füttern den Default-Preflight."""

    def __init__(self, fail_bid: bool = False):
        self.calls: list[tuple] = []
        self.fail_bid = fail_bid
        self.budget = 20_000_000
        # MW 1,25 M: Emils +10-%-Adjust (1,32 M) bleibt unter der zentralen
        # Overpay-Kappe (110 % MW = 1,375 M) — die Kappe testet ein eigener Test.
        # MW = Gebot (1,2 M): der Nachanker (02.09.) greift so nicht — er hat
        # eigene Tests (tests/agent/test_bid_reanchor.py).
        self.market = [SimpleNamespace(
            player=SimpleNamespace(id="B1", market_value=1_200_000, team_id="T1",
                                   fitness_status=0, lineup_prob=2),
            price=1_200_000,
        )]
        # Deckungsfaehiger 12er-Kader (zentraler Deckungs-Preflight, 24.08.):
        # nach EINEM Verkauf bleiben 11 + stellbare Formation; S1 ist der
        # Spieler der sell/list/accept-Tests.
        self.squad = [_squad_member("S1", 900_000)] + [
            _squad_member(pid, 500_000) for pid in
            ("gk1", "d1", "d2", "d3", "d4", "m1", "m2", "m3", "s1", "s2", "x1")
        ]
        for sp, pos in zip(self.squad, (3, 1, 2, 2, 2, 2, 3, 3, 3, 4, 4, 2), strict=True):
            sp.player.position = pos
        # Accept-Preflight (24.08.): das Angebot muss im rohen Markt am Listing
        # des Spielers stehen (uoid + Betrag) — Default deckt die Test-uoids.
        self.raw_market: dict = {"it": [
            {"i": "S1", "ofs": [
                {"u": "COMP", "uop": 930_000, "uoid": "OID7"},
                {"u": "COMP", "uop": 930_000, "uoid": "OID8"},
            ]},
        ]}

    async def _get(self, path):
        # Pfadbasiert: der Starter-Schutz liest den ROHEN Squad (`lo`), der
        # Accept-Preflight den rohen Markt (ofs) — Default: keine Starter.
        if "squad" in str(path):
            return {"it": []}
        return self.raw_market

    async def get_league_rules(self, league_id):
        return {"max_players_per_club": 11}

    async def get_own_bids(self, league_id, user_id):
        return []

    async def get_budget(self, league_id):
        return self.budget

    async def get_budget_fields(self, league_id):
        # pbaa = Konto nach offenen Geboten (Fake ohne offene Gebote) —
        # seit 23.08. verlangt der Bid-Preflight dieses Feld (fail-closed).
        return {"b": self.budget, "pbaa": self.budget}

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
        player = next(row.player for row in self.market if row.player.id == player_id)
        return SimpleNamespace(
            id=player_id, market_value=player.market_value, fitness_status=0,
            lineup_prob=2, mv_change_24h=20_000,
        )

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

    async def sell_to_kickbase(self, league_id, player_id):
        self.calls.append(("sell", league_id, player_id))
        return {"sold": True}

    async def accept_offer(self, league_id, player_id, offer_id):
        self.calls.append(("accept", league_id, player_id, offer_id))
        return {"accepted": True}


class _FakeChannel:
    def __init__(self):
        self.sent: list[str] = []
        self.kinds: list[str | None] = []

    async def send_info(self, text: str, *, kind: str | None = None, title=None) -> None:
        self.sent.append(text)
        self.kinds.append(kind)


def test_due_intents_filters_and_sorts_by_execute_at(session):
    a = _intent(session, pid="A", execute_at=NOW - MIN)
    b = _intent(session, pid="B", execute_at=NOW - 10 * MIN)
    _intent(session, pid="C", execute_at=NOW + MIN)
    _intent(session, pid="D", execute_at=NOW - H, status="vetoed")
    assert [r.id for r in due_intents(session, NOW)] == [b.id, a.id]


def test_expire_intents_marks_past_deadline_only(session):
    gone = _intent(session, pid="GONE", execute_at=NOW - 20 * MIN, deadline=NOW - MIN)
    _intent(session, pid="LIVE", execute_at=NOW - MIN, deadline=NOW + 10 * MIN)
    _intent(session, pid="NODL", execute_at=NOW - MIN)
    assert [r.id for r in expire_intents(session, NOW)] == [gone.id]
    session.expire_all()
    by = {r.player_id: r for r in session.query(Intent)}
    assert (by["GONE"].status, by["GONE"].result) == ("expired", RESULT_EXPIRED)
    assert by["LIVE"].status == "planned" and by["NODL"].status == "planned"
    assert wi._as_utc(by["GONE"].updated_at) == NOW


def test_action_for_carries_prefix_adjustment_and_note(session):
    bid = _intent(session, amount=1_320_000, adjusted_from=1_200_000, note="ok +10 %")
    a = action_for(bid, "L1")
    assert (a.action_type, a.league_id, a.player_id, a.amount, a.rule_id) == (
        "bid", "L1", "B1", 1_320_000, "intent-bid"
    )
    assert "Emil-Anpassung: 1,20 M → 1,32 M" in a.rationale and "Notiz: ok +10 %" in a.rationale
    sell = _intent(session, kind="sell", pid="S2", amount=4_000_000)
    assert action_for(sell, "L1").amount == 4_000_000  # Planungs-MW für den Drift-Check
    zero = _intent(session, kind="sell", pid="S3", amount=0)
    assert action_for(zero, "L1").amount is None


def test_action_for_marks_manual_intents_with_suffix(session):
    """Manuelle Remote-Vorhaben (sources.manual) tragen `-manual`: vom intent-
    Präfix geärmt, aber NICHT autonom — der Starter-Schutz gilt nur für
    Engine-Pfade (Codex-Folge-Review 24.08., P2: Transport darf die
    Autorisierungs-Semantik nicht ändern)."""
    manual = _intent(session, kind="sell", pid="M1", amount=0,
                     sources={"manual": True, "via": "mcp"})
    assert action_for(manual, "L1").rule_id == "intent-sell-manual"
    engine = _intent(session, kind="sell", pid="M2", amount=0)
    assert action_for(engine, "L1").rule_id == "intent-sell"


@pytest.mark.asyncio
async def test_execute_intent_armed_runs_live_write_and_links_audit(session, env):
    """AGENT_AUTO_INTENTS=1 (ohne allow_live, ohne Test-Liga): echter Client-Write über
    den ActionExecutor, Intent executed, executed_action_id = Audit-Zeile 'intent-bid'."""
    env(dry="0", intents="1")
    client = _FakeClient()
    intent = _intent(session)
    status = await execute_intent(
        session, intent, ActionExecutor(client, session), "L1", now=NOW, counts=TodayCounts()
    )
    assert status == "executed" and client.calls == [("place_bid", "L1", "B1", 1_200_000)]
    assert (intent.status, intent.result) == ("executed", "executed")
    audit = session.get(TransferAction, intent.executed_action_id)
    assert (audit.rule_id, audit.status, audit.dry_run) == ("intent-bid", "executed", False)
    assert today_intent_counts(session, NOW) == TodayCounts(buys=1, sells=0)


@pytest.mark.asyncio
async def test_execute_intent_not_armed_fails_honestly_with_refusal(session, env):
    """Flag aus (Default): Executor verweigert → Intent failed, result nennt den Schalter."""
    env(dry="0", intents="0")
    client = _FakeClient()
    intent = _intent(session)
    status = await execute_intent(session, intent, ActionExecutor(client, session), "L1", now=NOW)
    assert status == "failed" and client.calls == []
    assert intent.result.startswith("refused: ") and "AGENT_AUTO_INTENTS=0" in intent.result
    assert session.get(TransferAction, intent.executed_action_id).status == "refused"


@pytest.mark.asyncio
async def test_execute_intent_kill_switch_is_failed_with_reason(session, env):
    """AGENT_DRY_RUN=1 gewinnt immer: kein Write, Intent failed mit Kill-Switch-Wortlaut."""
    env(dry="1", intents="1", allow_live="1")
    client = _FakeClient()
    intent = _intent(session)
    status = await execute_intent(session, intent, ActionExecutor(client, session), "L1", now=NOW)
    assert status == "failed" and client.calls == []
    assert intent.result == RESULT_KILL_SWITCH
    assert "Kill-Switch AGENT_DRY_RUN=1" in intent.result
    assert session.get(TransferAction, intent.executed_action_id).status == "skipped_dry_run"


@pytest.mark.asyncio
async def test_execute_intent_api_error_and_daily_cap(session, env):
    """API-Fehler → failed mit Exception-Text; Tages-Cap voll → failed ohne Write."""
    env(dry="0", intents="1")
    client = _FakeClient(fail_bid=True)
    intent = _intent(session)
    status = await execute_intent(session, intent, ActionExecutor(client, session), "L1", now=NOW)
    assert status == "failed" and intent.result == "failed: RuntimeError: API kaputt"

    capped = _intent(session, pid="B2")
    cap = DEFAULT_GUARDRAILS.daily_buy_cap
    assert cap_reason(capped, TodayCounts(buys=cap), Guardrails()) is not None
    status = await execute_intent(
        session, capped, ActionExecutor(client, session), "L1", now=NOW,
        counts=TodayCounts(buys=DEFAULT_GUARDRAILS.daily_buy_cap),
    )
    assert status == "failed" and f"Tages-Cap Käufe erreicht ({cap}/{cap})" in capped.result
    assert capped.executed_action_id is None and len(client.calls) == 1


@pytest.mark.asyncio
async def test_execute_intent_loses_claim_to_concurrent_veto(engine, env):
    """Veto (API: UPDATE … WHERE status='planned') zwischen Laden und Write gewinnt:
    kein Claim, kein Write, Status bleibt vetoed (Rückgabe nennt ihn)."""
    env(dry="0", intents="1")
    client = _FakeClient()
    with Session(engine, future=True) as s1, Session(engine, future=True) as s2:
        intent = _intent(s1)
        loaded = due_intents(s1, NOW)[0]
        # zweite Session = API-Request: Veto landet VOR dem Claim des Executors
        s2.query(Intent).filter(Intent.id == intent.id, Intent.status == "planned").update(
            {"status": "vetoed", "emil_note": "nein"}, synchronize_session=False
        )
        s2.commit()
        status = await execute_intent(
            s1, loaded, ActionExecutor(client, s1), "L1", now=NOW, counts=TodayCounts()
        )
        assert status == "vetoed" and client.calls == []
        s1.expire_all()
        row = s1.query(Intent).one()
        assert (row.status, row.emil_note, row.result) == ("vetoed", "nein", None)


@pytest.mark.asyncio
async def test_execute_intent_uses_amount_adjusted_just_before_claim(engine, env):
    """Adjust (amount + adjusted_from) kurz vor dem Claim fließt in den Write ein —
    die Zeile wird nach dem Claim frisch gelesen."""
    env(dry="0", intents="1")
    client = _FakeClient()
    client.market = [SimpleNamespace(
        player=SimpleNamespace(id="B1", market_value=1_200_000, team_id="T1",
                               fitness_status=0, lineup_prob=2),
        price=1_200_000,
    )]
    with Session(engine, future=True) as s1, Session(engine, future=True) as s2:
        intent = _intent(s1)
        loaded = due_intents(s1, NOW)[0]
        s2.query(Intent).filter(Intent.id == intent.id, Intent.status == "planned").update(
            {"amount": 1_320_000, "adjusted_from": 1_200_000}, synchronize_session=False
        )
        s2.commit()
        status = await execute_intent(s1, loaded, ActionExecutor(client, s1), "L1", now=NOW)
        assert status == "executed" and client.calls == [("place_bid", "L1", "B1", 1_320_000)]
        audit = s1.get(TransferAction, loaded.executed_action_id)
        assert audit.amount == 1_320_000 and "Emil-Anpassung" in audit.rationale


def test_recover_stale_executing_becomes_failed(session):
    """Verwaiste `executing`-Zeile (Absturz mitten im Write) → failed mit Hinweis; eine
    frische `executing`-Zeile (laufender Write) bleibt."""
    stale = _intent(session, pid="STALE", status="executing", updated_at=NOW - 11 * MIN)
    fresh = _intent(session, pid="FRESH", status="executing", updated_at=NOW - MIN)
    assert [r.id for r in wi.recover_stale_executing(session, NOW)] == [stale.id]
    session.expire_all()
    assert session.get(Intent, stale.id).status == "failed"
    assert session.get(Intent, stale.id).result == wi.RESULT_STALE_EXECUTING
    assert session.get(Intent, fresh.id).status == "executing"
    # executing blockt eine Neuanlage derselben (kind, player_id) heute
    assert ("bid", "FRESH") in blocked_keys(session, NOW)


# ------------------------------------------------------- Runner (gemockt)


def _patch_world(monkeypatch, engine, *, plan: Plan | None = None,
                 client: _FakeClient | None = None, league_id="L1") -> dict:
    world = {"factory_calls": 0, "client": client or _FakeClient()}

    async def fake_build_plan(**kwargs):
        world["build_plan_kwargs"] = kwargs
        return plan

    async def fake_factory():
        world["factory_calls"] += 1
        return world["client"], SimpleNamespace(user_id="U1", league_id=league_id)

    monkeypatch.setattr(wi, "build_plan", fake_build_plan)
    monkeypatch.setattr(wi, "make_kickbase_client", fake_factory)
    monkeypatch.setattr(wi, "SessionLocal", sessionmaker(bind=engine, future=True))
    return world


@pytest.mark.asyncio
async def test_planner_persists_team_decision_when_no_buy_is_affordable(monkeypatch, engine):
    from kickbase_agent.db.models import Report

    plan = _plan()
    plan.team_plan = {"plan_id": "team-empty", "notes": ["Kein finanzierbares Paket."]}
    world = _patch_world(monkeypatch, engine, plan=plan)
    world["client"].raw_market = {"it": []}
    monkeypatch.setattr(wi, "buy_blocker_reason", lambda *args: pytest.fail("Legacy diagnosis"))
    await run_intent_planner()
    with Session(engine) as session:
        record = session.query(Report).filter(Report.kind == "engine_decision").one()
        assert record.meta["team_plan"]["plan_id"] == "team-empty"
        assert record.body == "Kein finanzierbares Paket."
        assert session.query(Intent).count() == 0


def test_blocked_bid_targets_are_vetoed_or_failed_but_never_executed(session):
    """Paket 1 (P0-1): Vor dem Optimierer fallen nur Veto- und Fehlschlag-Ziele
    raus. Heute ausgeführte/laufende Gebote bleiben gebundene Option — ihr
    Ausschluss erzeugte einen Doppelkauf (blocked_keys sperrt sie zu Recht nur
    für NEUE Vorhaben, nicht für den Plan)."""
    _intent(session, pid="VETO", status="vetoed", updated_at=NOW - H)
    _intent(session, pid="FAIL", status="failed", updated_at=NOW - MIN)
    _intent(session, pid="DONE", status="executed", updated_at=NOW - MIN)
    _intent(session, pid="RUNS", status="executing", updated_at=NOW - MIN)
    _intent(session, pid="OLDV", status="vetoed", updated_at=NOW - 30 * H)
    _intent(session, kind="sell", pid="SELL", status="failed", updated_at=NOW - MIN,
            sources={"score": 0.1})
    assert wi.blocked_bid_target_ids(session, NOW) == frozenset({"VETO", "FAIL"})


@pytest.mark.asyncio
async def test_planner_hands_blocked_targets_to_the_plan(monkeypatch, engine):
    with Session(engine, future=True) as s:
        _intent(s, pid="FAIL", status="failed", updated_at=datetime.now(UTC))
    world = _patch_world(monkeypatch, engine, plan=_plan())
    world["client"].raw_market = {"it": []}
    await run_intent_planner()
    assert world["build_plan_kwargs"] == {"blocked_target_ids": frozenset({"FAIL"})}


@pytest.mark.asyncio
async def test_planner_legt_dispo_glaettungen_an(monkeypatch, engine):
    """Review 23.09.2026, Befund 2b: Der Planner-Lauf übernimmt die Glättungsentwürfe."""
    from kickbase_agent.worker.intents import DISPO_SMOOTHING_RULE, IntentDraft

    now = datetime.now(UTC)
    draft = IntentDraft(
        kind="sell", player_id="S1", player_name="S1", team_id="T1", position=4,
        amount=9_000_000, rationale="Dispo-Glättung", listing_type="manager",
        sources={"league_id": "L1", "rule_id": DISPO_SMOOTHING_RULE, "swap_for": "Z1",
                 "after_squad_size": 12, "dispo_smoothing": True},
        deadline=now + 48 * H, execute_at=now + 30 * MIN,
    )
    seen = []

    def smoothing(session, plan, when):
        seen.append(plan)
        return [draft]

    monkeypatch.setattr(wi, "dispo_smoothing_drafts", smoothing)
    plan = _plan()
    world = _patch_world(monkeypatch, engine, plan=plan)
    world["client"].raw_market = {"it": []}
    await run_intent_planner()
    assert seen == [plan]
    with Session(engine) as session:
        row = session.query(Intent).one()
        assert (row.kind, row.player_id, row.sources["rule_id"]) == (
            "sell", "S1", DISPO_SMOOTHING_RULE,
        )


@pytest.mark.asyncio
async def test_run_executor_quiet_when_nothing_due(monkeypatch, engine, env):
    """Nicht fällig → unberührt, kein Client, kein Post, 'quiet'."""
    env(dry="0", intents="1")
    with Session(engine, future=True) as s:
        # Echte Uhr: run_intent_executor holt sein `now` selbst — Offset daher auf jetzt.
        _intent(s, execute_at=datetime.now(UTC) + 10 * H)
    world = _patch_world(monkeypatch, engine)
    channel = _FakeChannel()
    assert await run_intent_executor(channel) == "quiet"
    assert world["factory_calls"] == 0 and channel.sent == []
    with Session(engine, future=True) as s:
        row = s.query(Intent).one()
        assert row.status == "planned" and row.result is None


@pytest.mark.asyncio
async def test_run_executor_executes_due_expires_stale_and_reports(monkeypatch, engine, env):
    """Fälliges Vorhaben → executed (Client sieht den Write); Deadline vorbei → expired;
    Vetoed bleibt; Report per send_info."""
    env(dry="0", intents="1")
    with Session(engine, future=True) as s:
        _intent(s, pid="B1", execute_at=datetime.now(UTC) - MIN, review_now=datetime.now(UTC))
        _intent(s, pid="OLD", execute_at=datetime.now(UTC) - H,
                deadline=datetime.now(UTC) - MIN, name="Alt")
        _intent(s, pid="VET", execute_at=datetime.now(UTC) - H, status="vetoed")
    world = _patch_world(monkeypatch, engine)
    channel = _FakeChannel()

    assert await run_intent_executor(channel) == "1 ausgeführt, 0 fehlgeschlagen, 1 abgelaufen"
    assert channel.kinds == ["executor"]  # Berichte-Ablage: explizite Berichtsart

    assert world["client"].calls == [("place_bid", "L1", "B1", 1_200_000)]
    with Session(engine, future=True) as s:
        by = {r.player_id: r for r in s.query(Intent)}
    assert by["B1"].status == "executed" and by["B1"].executed_action_id is not None
    assert by["OLD"].status == "expired" and by["OLD"].result == RESULT_EXPIRED
    assert by["VET"].status == "vetoed"
    msg = channel.sent[0]
    # Meldungs-Umbau 31.08.: Erfolge stehen NUR auf der Karte (Kehrer) —
    # der Report nennt die Bilanz plus fehlgeschlagene/abgelaufene Zeilen.
    assert "Steuerzentrale" in msg and "1 ausgeführt" in msg
    assert "OK " not in msg  # keine Erfolgs-Zeile mehr
    assert "Alt" in msg and RESULT_EXPIRED in msg


@pytest.mark.asyncio
async def test_run_executor_unarmed_marks_failed_and_explains(monkeypatch, engine, env):
    """Default-Ärmung (AGENT_AUTO_INTENTS=0): fällige Vorhaben enden failed/refused —
    Karte und Report nennen den Schalter; kein Write."""
    env(dry="0", intents="0")
    with Session(engine, future=True) as s:
        _intent(s, execute_at=datetime.now(UTC) - MIN)
    world = _patch_world(monkeypatch, engine)
    channel = _FakeChannel()
    assert await run_intent_executor(channel) == "0 ausgeführt, 1 fehlgeschlagen, 0 abgelaufen"
    assert world["client"].calls == []
    with Session(engine, future=True) as s:
        row = s.query(Intent).one()
    assert row.status == "failed" and "AGENT_AUTO_INTENTS=0" in row.result
    assert "AGENT_AUTO_INTENTS=0" in channel.sent[0]


@pytest.mark.asyncio
async def test_run_planner_end_to_end_creates_then_stays_quiet(monkeypatch, engine, env):
    """Planner: Plan + Roh-Markt → Vorhaben in der DB, Report per send_info; zweiter Lauf
    ohne Änderung bleibt leise; KEIN Kickbase-Write."""
    env(dry="0", intents="0")
    client = _FakeClient()
    client.raw_market = {"it": [
        {"i": "B1", "fn": "Kauf", "n": "Mann", "pos": 4, "mv": 1_150_000, "prc": 1_150_000,
         "exs": 10 * 3600, "tid": "T1", "prob": 2, "ofs": []},
        {"i": "M1", "fn": "Manager", "n": "Ware", "pos": 2, "mv": 900_000, "prc": 950_000,
         "exs": 0, "tid": "T2", "u": {"i": "U7", "n": "Felix K."}},
    ]}
    plan = _plan(
        buys=[_prop(pid="B1"), _prop(pid="M1", amount=950_000, name="Ware")],
        sells=[_prop(pid="S1", amount=900_000, action_type="list", name="Lister")],
        squad=[_squad_member("S1", 900_000)],
    )
    world = _patch_world(monkeypatch, engine, plan=plan, client=client)
    channel = _FakeChannel()

    assert await run_intent_planner(channel) == "3 neu, 0 aktualisiert, 0 superseded"
    assert channel.kinds == ["planner"]  # Berichte-Ablage: explizite Berichtsart

    assert world["client"].calls == []  # Planner schreibt nie
    with Session(engine, future=True) as s:
        by = {(r.kind, r.player_id): r for r in s.query(Intent)}
    assert set(by) == {("bid", "B1"), ("bid", "M1"), ("list", "S1")}
    assert by[("bid", "B1")].listing_type == "kickbase" and by[("bid", "B1")].deadline
    assert by[("bid", "M1")].listing_type == "manager" and by[("bid", "M1")].deadline is None
    assert by[("bid", "M1")].sources["seller"] == "Felix K."
    msg = channel.sent[0]
    # Meldungs-Umbau 31.08.: je-Vorhaben-Zeilen sind raus — Details stehen
    # auf den Veto-Karten; der Post ist Bilanz + Unarmed-Hinweis.
    assert "Steuerzentrale" in msg and "Karten unten" in msg and "AGENT_AUTO_INTENTS=0" in msg
    assert "NEU " not in msg
    assert len(msg) <= 1900

    assert await run_intent_planner(channel) == "0 neu, 0 aktualisiert, 0 superseded"
    assert len(channel.sent) == 1  # Updates allein posten nicht


@pytest.mark.asyncio
async def test_run_planner_supersedes_when_player_leaves_market(monkeypatch, engine, env):
    env(dry="0", intents="0")
    with Session(engine, future=True) as s:
        # Echte Uhr: run_intent_planner holt sein `now` selbst — Offset daher auf jetzt.
        _intent(s, pid="GONE", execute_at=datetime.now(UTC) + 5 * H)
    plan = _plan(buys=[])
    world = _patch_world(monkeypatch, engine, plan=plan)
    channel = _FakeChannel()
    summary = await run_intent_planner(channel)
    # Seit 28.08. haengt der Planner den Grund an, wenn der Kaufpfad leer bleibt
    # (hier: der Spieler ist vom Markt, das Kauf-Universum damit leer).
    assert summary.startswith("0 neu, 0 aktualisiert, 1 superseded")
    assert "kein Kauf: Kauf-Universum leer" in summary
    with Session(engine, future=True) as s:
        row = s.query(Intent).one()
    assert (row.status, row.result) == ("superseded", RESULT_NOT_ON_MARKET)
    # Meldungs-Umbau 31.08.: WEG zieht der Kehrer auf die Karte — der Post
    # traegt nur noch die Bilanz.
    assert "1 superseded" in channel.sent[0] and world["client"].calls == []


@pytest.mark.asyncio
async def test_run_executor_same_error_is_damped_for_an_hour(monkeypatch, engine, env):
    """Client-Aufbau scheitert: erster Fehler geht nach oben (Discord via _safe), der
    identische Wiederholungsfehler im 5-Minuten-Takt wird gedämpft; Vorhaben bleiben
    planned (nächster Tick versucht erneut)."""
    env(dry="0", intents="1")
    wi._error_memo.clear()
    with Session(engine, future=True) as s:
        _intent(s, execute_at=datetime.now(UTC) - MIN)
    _patch_world(monkeypatch, engine)

    async def broken_factory():
        raise RuntimeError("Auth kaputt")

    monkeypatch.setattr(wi, "make_kickbase_client", broken_factory)
    with pytest.raises(RuntimeError):
        await run_intent_executor(_FakeChannel())
    out = await run_intent_executor(_FakeChannel())
    assert out == "error (gedämpft): RuntimeError: Auth kaputt"
    with Session(engine, future=True) as s:
        assert s.query(Intent).one().status == "planned"
    wi._error_memo.clear()


def test_reports_are_clipped_to_discord_limit():
    # Meldungs-Umbau 31.08.: Der Planner-Post ist eine Bilanzzeile plus
    # CAP-Ausfaelle — geclippt wird er ueber die CAP-Zeilen; der
    # Executor-Report clippt ueber fehlgeschlagene Zeilen.
    stats = wi.PlannerStats()
    stats.created.append(Intent(
        created_at=NOW, updated_at=NOW, kind="bid", player_id="x",
        player_name="Kaufmann", team_id="T", position=4, amount=1_000_000,
        rationale="r", sources={}, listing_type="kickbase",
        deadline=NOW + H, execute_at=NOW, status="planned",
    ))
    stats.skipped_cap.extend(
        wi.IntentDraft(
            kind="bid", player_id=str(i), player_name=f"Langer Spielername Nummer {i}",
            team_id="T", position=4, amount=1_000_000 + i, rationale="r",
            sources={}, listing_type="kickbase", deadline=NOW + H, execute_at=NOW,
        )
        for i in range(200)
    )
    msg = wi.format_planner_report(stats, armed=False)
    assert msg is not None and len(msg) <= 1900 and msg.endswith("…")


def test_planner_report_without_discord_controls_points_to_autonomy():
    """Vollautomatik darf auf Discord keine Freigabe-/Veto-Karten ankündigen."""
    stats = wi.PlannerStats()
    stats.created.append(Intent(
        created_at=NOW, updated_at=NOW, kind="bid", player_id="x",
        player_name="Kaufmann", team_id="T", position=4, amount=1_000_000,
        rationale="r", sources={}, listing_type="kickbase",
        deadline=NOW + H, execute_at=NOW, status="planned",
    ))

    msg = wi.format_planner_report(stats, armed=True, discord_controls=False)

    assert msg is not None
    assert "Karten unten" not in msg
    assert "automatisch" in msg

    rows = [
        (Intent(
            created_at=NOW, updated_at=NOW, kind="bid", player_id=str(i),
            player_name=f"Langer Spielername Nummer {i}", team_id="T", position=4,
            amount=1_000_000 + i, rationale="r", sources={}, listing_type="kickbase",
            deadline=NOW + H, execute_at=NOW, status="failed",
            result="refused: Testgrund mit ordentlich Laenge im Text",
        ), "failed")
        for i in range(200)
    ]
    emsg = wi.format_executor_report(rows, [], armed=False)
    assert emsg is not None and len(emsg) <= 1900 and emsg.endswith("…")


# --------------------------------------------------------------- Arming


def _intent_action(kind="bid", rule="intent-bid", pid="B1", amount=1_200_000):
    return PlannedAction(action_type=kind, league_id="REAL", player_id=pid, amount=amount,
                         rule_id=rule, purchase_review=_review(pid) if kind == "bid" else None)


@pytest.mark.asyncio
async def test_intent_prefix_armed_only_with_flag(session, env):
    """AGENT_AUTO_INTENTS=1 ärmt bid/list/sell GENAU mit rule_id-Präfix 'intent-' —
    ohne allow_live, ohne Test-Liga; fremde rule_ids und lineup bleiben refused."""
    env(dry="0", intents="1")
    client = _FakeClient()
    ex = ActionExecutor(client, session)
    r_bid = await ex.execute(_intent_action(), dry_run=False, now=NOW)
    r_list = await ex.execute(
        _intent_action("list", "intent-list", pid="S1", amount=900_000), dry_run=False, now=NOW
    )
    r_sell = await ex.execute(
        _intent_action("sell", "intent-sell", pid="S1", amount=900_000), dry_run=False,
        now=NOW + MIN,
    )
    assert (r_bid.status, r_list.status, r_sell.status) == ("executed", "executed", "executed")
    assert [c[0] for c in client.calls] == ["place_bid", "list", "sell"]

    r_foreign = await ex.execute(
        _intent_action(rule="buy_explosion", amount=1_199_000), dry_run=False, now=NOW
    )
    r_lineup = await ex.execute(
        PlannedAction(action_type="lineup", league_id="REAL", formation="4-4-2",
                      player_ids=["1"], rule_id="intent-lineup"),
        dry_run=False, now=NOW,
    )
    assert (r_foreign.status, r_lineup.status) == ("refused", "refused")
    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_intent_prefix_refused_without_flag_names_the_switch(session, env):
    env(dry="0", intents="0")
    client = _FakeClient()
    res = await ActionExecutor(client, session).execute(_intent_action(), dry_run=False, now=NOW)
    assert res.status == "refused" and client.calls == []
    assert "AGENT_AUTO_INTENTS=0" in res.message


def test_scheduler_main_wires_intent_jobs():
    """Ein Ereigniszyklus plant, der vorhandene Executor führt die Vorhaben aus."""
    from kickbase_agent.worker import __main__ as main
    from kickbase_agent.worker.engine_cycle import run_engine_cycle

    assert main.run_engine_cycle is run_engine_cycle
    assert main.run_intent_executor is run_intent_executor


# ------------------------------------------------- Accept-Pfad (24.08.)


def test_incoming_offers_from_raw_reads_own_listings_only():
    """Angebote NUR aus eigenen Listings (u.i == wir); Feldnamen wie get_own_bids
    (u/uop/uoid, live 20.08.); Einträge ohne uoid sind nicht annehmbar."""
    raw = {"it": [
        {"i": "S1", "fn": "Weg", "n": "A", "pos": 4, "mv": 10_000_000, "prc": 10_300_000,
         "tid": "9", "u": {"i": "ME", "n": "emil"},
         "ofs": [
             {"u": "COMP", "uop": 10_300_000, "uoid": "OID1"},
             {"u": "RIVAL", "uop": 10_100_000, "uoid": "OID2"},
             {"u": "BROKEN", "uop": 10_400_000},
         ]},
        {"i": "F1", "fn": "Fremd", "n": "Ling", "pos": 2, "mv": 5_000_000, "prc": 5_100_000,
         "tid": "3", "u": {"i": "OTHER", "n": "Moritz W."},
         "ofs": [{"u": "ME", "uop": 5_200_000, "uoid": "ME"}]},
    ]}
    offers = wi.incoming_offers_from_raw(raw, "ME")
    assert [(o.player_id, o.amount, o.offer_id) for o in offers] == [
        ("S1", 10_300_000, "OID1"), ("S1", 10_100_000, "OID2"),
    ]
    assert offers[0].market_value == 10_000_000 and offers[0].bidder_id == "COMP"


def test_accept_drafts_windows_and_sources():
    """< 5 M → 45-min-Fenster; ≥ 5 M → Emils 2-h-Mindestfenster; offer_id in sources."""
    from kickbase_agent.decision.guardrails import Guardrails
    from kickbase_agent.decision.policies import IncomingOffer, propose_accepts

    small = IncomingOffer("A", "Klein", "1", 3, 1_000_000, 1_040_000, "O1")
    big = IncomingOffer("B", "Gross", "1", 3, 20_000_000, 20_800_000, "O2", bidder_id="X")
    squad = {"A": 3, "B": 3, "gk": 1, "d1": 2, "d2": 2, "d3": 2,
             "m1": 3, "m2": 3, "s1": 4, "s2": 4, "x1": 2, "x2": 3, "x3": 3}
    props = propose_accepts([small, big], squad, "REAL", Guardrails())
    drafts = wi.accept_drafts(props, {"A": small, "B": big}, NOW)
    by_pid = {d.player_id: d for d in drafts}
    assert by_pid["A"].execute_at == NOW + wi.ACCEPT_VETO_WINDOW
    assert by_pid["B"].execute_at == NOW + wi.VETO_WINDOW
    assert by_pid["B"].sources["offer_id"] == "O2"
    assert by_pid["B"].sources["bidder_id"] == "X"
    assert all(d.kind == "accept" and d.listing_type == "offer" for d in drafts)
    # Ohne bekannten Listungspreis bleibt es ein autonomes Vorhaben.
    assert "manual" not in by_pid["B"].sources and "emil_go" not in by_pid["B"].sources


# ------------------------------------------------- Listungspreis (16.09.2026)


def test_incoming_offers_carry_our_listing_price():
    raw = {"it": [
        {"i": "S1", "fn": "Weg", "n": "A", "pos": 4, "mv": 65_000_000, "prc": 100_000_000,
         "tid": "9", "u": {"i": "ME", "n": "emil"},
         "ofs": [{"u": "RIVAL", "uop": 70_000_000, "uoid": "OID1"}]},
    ]}
    (offer,) = wi.incoming_offers_from_raw(raw, "ME")
    assert offer.listing_price == 100_000_000
    assert offer.meets_asking_price is False


def test_accept_draft_at_asking_price_runs_as_emils_manual_intent():
    """Preis erreicht → sources.manual + emil_go: der Executor führt es als
    `intent-accept-manual` aus — Starter-Schutz und T-120-Floor autonomer
    Pfade greifen nicht, denn der Preis war Emils Entscheidung."""
    from kickbase_agent.decision.guardrails import Guardrails
    from kickbase_agent.decision.policies import IncomingOffer, propose_accepts

    hit = IncomingOffer("B", "Star", "1", 3, 65_000_000, 100_000_000, "O9",
                        bidder_id="X", listing_price=100_000_000)
    squad = {"B": 3, "gk": 1, "d1": 2, "d2": 2, "d3": 2, "d4": 2,
             "m1": 3, "m2": 3, "m3": 3, "m4": 3, "s1": 4, "s2": 4}
    props = propose_accepts([hit], squad, "REAL", Guardrails())
    (draft,) = wi.accept_drafts(props, {"B": hit}, NOW)
    # Review 17.09.: KEIN manual-Marker — manuelle Zeilen fasst der Planner nie
    # an, ein zurückgezogenes Angebot würde sonst als failed die Annahme sperren.
    assert "manual" not in draft.sources
    assert draft.sources["asking_price"] == 100_000_000
    assert "100,00 M" in draft.sources["emil_go"] and "O9" in draft.sources["emil_go"]
    assert draft.execute_at == NOW + wi.VETO_WINDOW  # ≥ 5 M: 2-h-Fenster bleibt


def test_action_for_asking_price_accept_runs_as_manual_rule(session):
    """`asking_price` in den Quellen → rule_id intent-accept-manual (Starter-
    Schutz/T-120-Floor autonomer Pfade greifen nicht), emil_go wird durchgereicht."""
    row = _intent(session, kind="accept", pid="B", amount=100_000_000,
                  sources={"offer_id": "O9", "asking_price": 100_000_000,
                           "emil_go": "Listungspreis 100,00 M erreicht"})
    action = wi.action_for(row, "REAL")
    assert action.rule_id == "intent-accept-manual"
    assert action.offer_id == "O9" and action.emil_go == "Listungspreis 100,00 M erreicht"
    plain = _intent(session, kind="accept", pid="C", amount=1_000_000,
                    sources={"offer_id": "O1"})
    assert wi.action_for(plain, "REAL").rule_id == "intent-accept"


def test_accept_squad_floor_follows_the_t120_window():
    from datetime import timedelta

    assert wi.accept_squad_floor(NOW, None) == 0
    far = NOW + timedelta(hours=wi.EXPOSURE_GUARD_HOURS + 1)
    assert wi.accept_squad_floor(NOW, far) == 0
    near = NOW + timedelta(hours=wi.EXPOSURE_GUARD_HOURS - 1)
    assert wi.accept_squad_floor(NOW, near) == wi.MIN_SQUAD_SIZE == 11


def test_record_below_asking_reports_each_offer_once(session):
    from sqlalchemy import select

    from kickbase_agent.db.models import Report
    from kickbase_agent.decision.policies import IncomingOffer

    low = IncomingOffer("B", "Star", "1", 3, 65_000_000, 70_000_000, "O1",
                        bidder_id="X", listing_price=100_000_000)
    assert [o.offer_id for o in wi.record_below_asking([low], session)] == ["O1"]
    assert wi.record_below_asking([low], session) == []  # zweiter Lauf: still
    rows = list(session.scalars(select(Report).where(
        Report.source == wi.BELOW_ASKING_SOURCE
    )))
    assert len(rows) == 1 and rows[0].kind == "planner"
    assert rows[0].meta["offer_id"] == "O1" and rows[0].meta["asking_price"] == 100_000_000
    assert "70,00 M" in rows[0].body and "100,00 M" in rows[0].body
    # Nachgebessertes Angebot (neuer Betrag) wird erneut gemeldet.
    better = IncomingOffer("B", "Star", "1", 3, 65_000_000, 80_000_000, "O1",
                           bidder_id="X", listing_price=100_000_000)
    assert [o.amount for o in wi.record_below_asking([better], session)] == [80_000_000]
    text = wi.format_below_asking_notice([better])
    assert "80,00 M" in text and "Preis 100,00 M" in text and "O1" in text


@pytest.mark.asyncio
async def test_accept_intent_executes_live_with_auto_intents(session, env):
    """AGENT_AUTO_INTENTS ärmt auch 'accept' (intent-Präfix): Dispatch ruft
    accept_offer mit der uoid aus den Quellen; Audit verknüpft."""
    env(dry="0", intents="1")
    client = _FakeClient()
    row = _intent(session, kind="accept", pid="S1", amount=930_000,
                  sources={"score": 1.03, "offer_id": "OID7"})
    status = await wi.execute_intent(
        session, row, ActionExecutor(client, session), "REAL", now=NOW
    )
    assert status == wi.STATUS_EXECUTED
    assert ("accept", "REAL", "S1", "OID7") in client.calls
    assert row.executed_action_id is not None


@pytest.mark.asyncio
async def test_accept_counts_against_sell_cap(session, env):
    """Annahmen zählen als Verkäufe: bei vollem Tages-Cap wird nicht ausgeführt."""
    env(dry="0", intents="1")
    from kickbase_agent.worker.auto_trading import TodayCounts

    client = _FakeClient()
    row = _intent(session, kind="accept", pid="S1", amount=930_000,
                  sources={"offer_id": "OID8"})
    status = await wi.execute_intent(
        session, row, ActionExecutor(client, session), "REAL", now=NOW,
        counts=TodayCounts(buys=0, sells=DEFAULT_GUARDRAILS.daily_sell_cap),
    )
    assert status == wi.STATUS_FAILED
    assert "Tages-Cap Verkäufe" in row.result
    assert client.calls == []


# ------------------------------------------------------------ Delist-Weg (22.09.2026)


def test_action_for_delist_traegt_typ_manuelle_rule_id_und_keinen_betrag(session):
    """Emils Auftrag 21.09. („bau den delist-weg sauber nach"): Ein Delist-Vorhaben
    wird zur PlannedAction(action_type="delist"); der Dispatch ruft
    client.delist_player(lg, pid) — ohne Betrag, ohne Offer-ID. Manuell (MCP/Emil)
    trägt '-manual', ein Engine-Delist bliebe `intent-delist`; emil_go läuft mit."""
    manual = _intent(
        session, kind="delist", pid="D1", amount=0,
        sources={"manual": True, "via": "mcp", "emil_go": "Emil 21.09.: Olise halten"},
    )
    a = action_for(manual, "L1")
    assert (a.action_type, a.league_id, a.player_id) == ("delist", "L1", "D1")
    assert a.rule_id == "intent-delist-manual"
    assert a.amount is None and a.offer_id is None
    assert a.emil_go == "Emil 21.09.: Olise halten"
    assert a.swap is False and a.squad_need is False
    engine = _intent(session, kind="delist", pid="D2", amount=0, sources={"score": 0.1})
    assert action_for(engine, "L1").rule_id == "intent-delist"


def test_kind_label_delist():
    assert wi._kind_label("delist") == "Listing-Rückzug"


def test_manual_delist_wird_vom_planner_nie_supersedet(session):
    """Manuelle Vorhaben gehören nicht dem Planner — auch ein Delist bleibt
    stehen, wenn der Planner mit leerem oder fremdem Entwurfsstand läuft."""
    sources = {"manual": True, "via": "mcp"}
    row = _intent(session, kind="delist", pid="D1", amount=0, sources=sources)
    stats = upsert_intents(session, [], NOW, purchase_reviews={})
    assert stats.superseded == [] and row.status == "planned"
    stats = upsert_intents(session, [_draft(amount=1_200_000)], NOW,
                           purchase_reviews={"B1": _review()})
    assert row not in stats.superseded and row.status == "planned"
    assert row.sources == sources


def test_cap_reason_kennt_keinen_delist_cap(session):
    row = _intent(session, kind="delist", pid="D1", amount=0,
                  sources={"manual": True, "via": "mcp"})
    full = TodayCounts(buys=5, sells=5, lists=12)
    assert cap_reason(row, full, DEFAULT_GUARDRAILS) is None
