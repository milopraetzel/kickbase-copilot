"""Momentum-Doktrin in der Vorhaben-Pipeline (31.08.): Cap-Trennung, T-48h-Filter,
pbaa-Retry.

Eigene Datei neben test_intents.py — die Zusicherungen hier sind neu und sollen
den bestehenden (teils versiegelten) Bestand nicht anfassen (L-087).
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, Intent, TransferAction
from kickbase_agent.decision.guardrails import Guardrails
from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.worker.auto_trading import TodayCounts
from kickbase_agent.worker.intents import (
    PBAA_MAX_RETRIES,
    IntentDraft,
    cap_reason,
    execute_intent,
    filter_uncovered_bids,
    hysteresis_keep_pids,
    today_intent_counts,
    upsert_intents,
)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
H = timedelta(hours=1)


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    with Session(eng, future=True) as s:
        yield s


def _draft(kind="bid", pid="P1", amount=1_000_000, execute_at=NOW + H, deadline=None):
    return IntentDraft(
        kind=kind, player_id=pid, player_name=f"Spieler {pid}", team_id="T1",
        position=4, amount=amount, rationale="r", sources={"score": 0.6},
        listing_type="kickbase", deadline=deadline, execute_at=execute_at,
    )


def _intent_row(kind="bid", pid="P1", deadline=None, sources=None):
    return Intent(
        created_at=NOW - H, updated_at=NOW - H, kind=kind, player_id=pid,
        player_name="Spieler", team_id="T1", position=4, amount=1_000_000,
        rationale="r", sources=sources or {"score": 0.6}, listing_type="kickbase",
        deadline=deadline, execute_at=NOW - timedelta(minutes=1), status="planned",
    )


def _audit(action_type: str, *, key: str, status="executed", rule="intent-x"):
    return TransferAction(
        league_id="L1", action_type=action_type, player_id=key, amount=1,
        status=status, idempotency_key=key, rule_id=rule, dry_run=False,
        created_at=NOW,
    )


# --- Cap-Trennung (L-077 vollendet) -----------------------------------------


def test_today_counts_trennen_listings_von_verkaeufen(session):
    for i, kind in enumerate(["bid", "list", "list", "sell", "accept"]):
        session.add(_audit(kind, key=f"k{i}", rule="intent-" + kind))
    session.commit()
    counts = today_intent_counts(session, NOW)
    assert (counts.buys, counts.sells, counts.lists) == (1, 2, 2)


def test_cap_reason_prueft_drei_toepfe():
    g = Guardrails()
    voll_sells = TodayCounts(buys=0, sells=g.daily_sell_cap, lists=0)
    # Verkaufs-Topf voll ⇒ Listing läuft trotzdem (eigenes Kontingent).
    assert cap_reason(_intent_row(kind="list"), voll_sells, g) is None
    assert cap_reason(_intent_row(kind="sell"), voll_sells, g) is not None
    voll_lists = TodayCounts(buys=0, sells=0, lists=g.daily_list_cap)
    assert "Listings" in (cap_reason(_intent_row(kind="list"), voll_lists, g) or "")
    assert cap_reason(_intent_row(kind="sell"), voll_lists, g) is None


def test_upsert_laesst_listing_trotz_vollem_verkaufs_cap_zu(session):
    g = Guardrails()
    stats = upsert_intents(
        session, [_draft(kind="list", pid="L1")], NOW,
        counts=TodayCounts(sells=g.daily_sell_cap), guardrails=g,
    )
    assert len(stats.created) == 1 and not stats.skipped_cap


def test_upsert_deckelt_listings_am_eigenen_kontingent(session):
    g = Guardrails()
    stats = upsert_intents(
        session, [_draft(kind="list", pid="L1")], NOW,
        counts=TodayCounts(lists=g.daily_list_cap), guardrails=g,
    )
    assert not stats.created and len(stats.skipped_cap) == 1


# --- T-120-Draft-Filter ------------------------------------------------------


def test_filter_laesst_ungedeckte_drafts_bis_t120_zur_normalen_pruefung_durch():
    deadline = NOW + timedelta(hours=24)
    drafts = [
        _draft(pid="teuer", amount=5_000_000, execute_at=NOW + H),
        _draft(pid="billig", amount=400_000, execute_at=NOW + H),
        _draft(kind="sell", pid="verkauf", amount=9_000_000, execute_at=NOW + H),
    ]
    kept, dropped = filter_uncovered_bids(drafts, deadline=deadline, cash_frei=1_000_000)
    assert dropped == 0
    assert [d.player_id for d in kept] == ["teuer", "billig", "verkauf"]


def test_filter_verwirft_jedes_neue_gebot_innerhalb_t120():
    deadline = NOW + timedelta(minutes=90)
    drafts = [_draft(pid="gebot", amount=400_000, execute_at=NOW + H)]
    kept, dropped = filter_uncovered_bids(drafts, deadline=deadline, cash_frei=99_000_000)
    assert dropped == 1 and kept == []


def test_filter_laesst_gebote_vor_dem_fenster_unangetastet():
    deadline = NOW + timedelta(hours=80)
    drafts = [_draft(pid="frueh", amount=5_000_000, execute_at=NOW + H)]
    kept, dropped = filter_uncovered_bids(drafts, deadline=deadline, cash_frei=0)
    assert dropped == 0 and len(kept) == 1
    # Ausführung IM Fenster (Kickbase-Listing läuft kurz vor Anpfiff aus) ⇒ raus.
    spaet = [_draft(pid="spaet", amount=5_000_000, execute_at=deadline - H)]
    kept, dropped = filter_uncovered_bids(spaet, deadline=deadline, cash_frei=0)
    assert dropped == 1 and kept == []


def test_filter_ohne_deadline_ist_durchlass():
    drafts = [_draft(amount=99_000_000)]
    assert filter_uncovered_bids(drafts, deadline=None, cash_frei=0) == (drafts, 0)


# --- pbaa-Retry --------------------------------------------------------------


_PBAA_REFUSE = "Preflight: Kickbase lieferte kein Konto-nach-Geboten (pbaa) — fail-closed"


class _RefusingExecutor:
    def __init__(self, message=_PBAA_REFUSE):
        self.message = message
        self.calls = 0

    async def execute(self, action, dry_run, now):
        self.calls += 1
        return SimpleNamespace(status="refused", message=self.message, action_id=self.calls)


@pytest.mark.asyncio
async def test_pbaa_refuse_laesst_das_vorhaben_planned_und_verschiebt(session):
    row = _intent_row(deadline=NOW + timedelta(hours=6))
    session.add(row)
    session.commit()
    status = await execute_intent(session, row, _RefusingExecutor(), "L1", now=NOW)
    assert status == "planned"
    assert row.status == "planned"
    # SQLite liefert nach dem Commit naive Stempel zurück — als UTC deuten.
    got = row.execute_at if row.execute_at.tzinfo else row.execute_at.replace(tzinfo=UTC)
    assert got > NOW
    # Zaehler als SPALTE (Review 31.08.): sources bleibt planner-ueberschreibbar,
    # der Retry-Deckel nicht.
    assert row.pbaa_retries == 1
    assert (row.sources or {}).get("pbaa_retries") is None
    assert "pbaa" in (row.result or "")


@pytest.mark.asyncio
async def test_pbaa_retry_endet_nach_max_versuchen_ehrlich_failed(session):
    row = _intent_row()
    row.pbaa_retries = PBAA_MAX_RETRIES
    session.add(row)
    session.commit()
    status = await execute_intent(session, row, _RefusingExecutor(), "L1", now=NOW)
    assert status == "failed"
    assert "pbaa" in (row.result or "")


@pytest.mark.asyncio
async def test_vorgemerktes_veto_gewinnt_gegen_den_retry(session):
    """Review 31.08.: Klickt Emil Ueberspringen, waehrend der Versuch laeuft
    (veto_requested_at gesetzt), endet ein gescheiterter Versuch als vetoed —
    nie als planned (Retry) oder failed (Tagessperre)."""
    row = _intent_row(deadline=NOW + timedelta(hours=6))
    session.add(row)
    session.commit()
    row.veto_requested_at = NOW
    session.commit()
    status = await execute_intent(session, row, _RefusingExecutor(), "L1", now=NOW)
    assert status == "vetoed"
    assert row.status == "vetoed"
    assert "Veto" in (row.result or "")


@pytest.mark.asyncio
async def test_planner_wipe_nullt_den_retry_zaehler_nicht_mehr(session):
    """Review 31.08.: _apply_draft ersetzt sources wholesale — der Zaehler lebt
    jetzt als Spalte und ueberlebt das Stunden-Update des Planners."""
    row = _intent_row(deadline=NOW + timedelta(hours=6))
    session.add(row)
    session.commit()
    await execute_intent(session, row, _RefusingExecutor(), "L1", now=NOW)
    assert row.pbaa_retries == 1
    # Wie _apply_draft: sources wholesale ersetzen und committen — die Spalte
    # bleibt davon unberuehrt (frueher verschwand der Zaehler mit sources).
    row.sources = {"score": 0.7}
    session.commit()
    session.expire_all()
    assert session.get(type(row), row.id).pbaa_retries == 1


@pytest.mark.asyncio
async def test_pbaa_retry_respektiert_die_deadline(session):
    # Deadline in 12 min: Retry (+10 min) läge hinter Deadline − 15 min ⇒ failed.
    row = _intent_row(deadline=NOW + timedelta(minutes=12))
    session.add(row)
    session.commit()
    status = await execute_intent(session, row, _RefusingExecutor(), "L1", now=NOW)
    assert status == "failed"


@pytest.mark.asyncio
async def test_anderes_refused_bleibt_failed(session):
    row = _intent_row()
    session.add(row)
    session.commit()
    ex = _RefusingExecutor(message="Preflight: Spieler ist nicht (mehr) am Markt.")
    status = await execute_intent(session, row, ex, "L1", now=NOW)
    assert status == "failed"


def test_filter_ueberlaesst_summenrisiko_vor_t120_dem_executor():
    """Der Draft-Filter ist nur Freeze; 33-%-/Pfand-Summen prüft der Executor."""
    deadline = NOW + timedelta(hours=24)
    drafts = [
        _draft(pid="zuerst", amount=600_000, execute_at=NOW + H),
        _draft(pid="danach", amount=600_000, execute_at=NOW + 2 * H),
    ]
    kept, dropped = filter_uncovered_bids(drafts, deadline=deadline, cash_frei=1_000_000)
    assert dropped == 0
    assert [d.player_id for d in kept] == ["zuerst", "danach"]


def test_filter_verschont_drafts_mit_offenem_vorhaben():
    """Ein offenes Vorhaben bleibt erhalten; vor T-120 filtert die Draftstufe
    auch weitere Vorhaben nicht vorzeitig. Der Executor prüft die Gesamtexposure."""
    deadline = NOW + timedelta(hours=24)
    drafts = [
        _draft(pid="emils", amount=900_000, execute_at=NOW + H),
        _draft(pid="neu", amount=600_000, execute_at=NOW + 2 * H),
    ]
    kept, dropped = filter_uncovered_bids(
        drafts, deadline=deadline, cash_frei=1_000_000,
        existing_keys={("bid", "emils")},
    )
    assert dropped == 0
    assert [d.player_id for d in kept] == ["emils", "neu"]


def test_filter_kennt_das_t120_fenster():
    """Review 31.08.: Auch ein GEDECKTES Gebot, dessen Ausfuehrung ins
    T-120-Freeze-Fenster faellt, wird verworfen (der Executor wuerde es
    bedingungslos refusen und die Tagessperre ausloesen)."""
    deadline = NOW + timedelta(hours=24)
    drafts = [_draft(pid="freeze", amount=100_000, execute_at=deadline - timedelta(minutes=90))]
    kept, dropped = filter_uncovered_bids(drafts, deadline=deadline, cash_frei=50_000_000)
    assert dropped == 1 and kept == []


# --- Plan-Hysterese (01.09.) --------------------------------------------------


def _mkt_cand(pid: str, score: float, position: int = 3) -> ExplosionCandidate:
    return ExplosionCandidate(
        player_id=pid, name=f"Markt {pid}", team_id="T1", position=position,
        current_market_value=5_000_000, total_score=score, signals=[],
    )


def test_hysterese_haelt_wackler_in_der_marge():
    g = Guardrails()  # Schwelle 0.53, Marge 0.05 ⇒ Halte-Grenze 0.48
    keep = hysteresis_keep_pids(
        [_mkt_cand("wackler", 0.49), _mkt_cand("verfall", 0.40)], set(), g
    )
    assert keep == {"wackler"}


def test_hysterese_nutzt_die_not_schwelle_der_position():
    g = Guardrails()  # TW-Not: 0.53 − 0.07 = 0.46 ⇒ Halte-Grenze 0.41
    keep = hysteresis_keep_pids(
        [_mkt_cand("tw", 0.43, position=1), _mkt_cand("mf", 0.43, position=3)],
        {1}, g,
    )
    assert keep == {"tw"}


def test_upsert_haelt_gebot_ohne_entwurf_dank_hysterese(session):
    row = _intent_row(kind="bid", pid="P1")
    session.add(row)
    session.commit()
    stats = upsert_intents(
        session, [], NOW, market_pids={"P1"}, keep_pids={"P1"},
    )
    assert stats.superseded == [] and stats.unchanged == [row]
    assert row.status == "planned"


def test_upsert_raeumt_ohne_hysterese_wie_bisher(session):
    row = _intent_row(kind="bid", pid="P1")
    session.add(row)
    session.commit()
    stats = upsert_intents(session, [], NOW, market_pids={"P1"}, keep_pids=set())
    assert len(stats.superseded) == 1
    assert row.status == "superseded" and "nicht mehr im Plan" in (row.result or "")


def test_upsert_nicht_mehr_am_markt_schlaegt_hysterese(session):
    row = _intent_row(kind="bid", pid="P1")
    session.add(row)
    session.commit()
    stats = upsert_intents(session, [], NOW, market_pids=set(), keep_pids={"P1"})
    assert len(stats.superseded) == 1
    assert "nicht mehr am Markt" in (row.result or "")


def test_upsert_hysterese_gilt_nur_fuer_gebote(session):
    row = _intent_row(kind="list", pid="P1")
    session.add(row)
    session.commit()
    stats = upsert_intents(session, [], NOW, market_pids={"P1"}, keep_pids={"P1"})
    assert len(stats.superseded) == 1  # Verkaufsseite raeumt unveraendert
