"""Gemeinsamer Aktions-Service (intents/actions.py, Spec 2026-08-31 §4.2/§7).

Veto/Adjust/Execute-now gegen echte SQLite-Session (Muster tests/worker/):
happy path, rowcount-0-Pfade (Konkurrenz), Adjust-Grenze, nicht-adjustierbare
Kinds, actor-Spur in emil_note. Die HTTP-Übersetzung der Fehler testen die
UNVERÄNDERTEN Routen-Tests (tests/server/test_intents_api.py).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, Intent
from kickbase_agent.intents import actions

# Berlin ist im August UTC+2: 12:00 UTC = 14:00 Berlin (actor-Spur-Zeitstempel).
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
H = timedelta(hours=1)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _intent(session: Session, **over) -> Intent:
    base = dict(
        created_at=NOW - H,
        updated_at=NOW - H,
        kind="bid",
        player_id="B1",
        player_name="Kaufmann",
        team_id="T1",
        position=4,
        amount=10_000_000,
        rationale="r",
        sources={"score": 0.62},
        listing_type="kickbase",
        deadline=None,
        execute_at=NOW + 2 * H,
        status="planned",
    )
    base.update(over)
    row = Intent(**base)
    session.add(row)
    session.commit()
    return row


# ----------------------------------------------------------------- veto


def test_veto_happy_path_web_keeps_note_semantics(session):
    _intent(session)
    row = actions.veto_intent(
        session, 1, note="  Khannouss bleibt  ", actor=actions.ACTOR_WEB, now=NOW
    )
    assert (row.status, row.emil_note) == ("vetoed", "Khannouss bleibt")
    assert row.updated_at.replace(tzinfo=UTC) == NOW
    # Web hinterlässt KEINE Zusatzspur (Verhaltensneutralität der Extraktion)
    assert "[" not in row.emil_note


def test_veto_web_without_note_leaves_emil_note_none(session):
    _intent(session)
    row = actions.veto_intent(session, 1, note=None, actor=actions.ACTOR_WEB, now=NOW)
    assert row.status == "vetoed" and row.emil_note is None


def test_veto_discord_appends_actor_line(session):
    _intent(session, emil_note="alte Notiz")
    row = actions.veto_intent(session, 1, note=None, actor=actions.ACTOR_DISCORD, now=NOW)
    assert row.status == "vetoed"
    assert row.emil_note == "alte Notiz\n[Discord] Veto 14:00"


def test_veto_discord_without_existing_note_writes_only_trace(session):
    _intent(session)
    row = actions.veto_intent(session, 1, note=None, actor=actions.ACTOR_DISCORD, now=NOW)
    assert row.emil_note == "[Discord] Veto 14:00"


def test_veto_not_found_and_not_planned(session):
    with pytest.raises(actions.IntentNotFound) as e404:
        actions.veto_intent(session, 42, note=None, actor=actions.ACTOR_WEB, now=NOW)
    assert e404.value.detail == "Vorhaben 42 nicht gefunden"

    _intent(session, status="executed")
    with pytest.raises(actions.IntentNotPlanned) as e409:
        actions.veto_intent(session, 1, note=None, actor=actions.ACTOR_WEB, now=NOW)
    assert e409.value.status == "executed"
    assert "bereits 'executed' (ausgeführt)" in e409.value.detail
    assert "Überspringen" in e409.value.detail


def test_veto_loses_race_to_concurrent_execution(session):
    """`now` als Callable: die Konkurrenz-Mutation zwischen Lesen und bedingtem
    UPDATE gewinnt — rowcount 0 ⇒ IntentNotPlanned, nichts überschrieben."""
    _intent(session)

    def now_with_race() -> datetime:
        session.execute(update(Intent).where(Intent.id == 1).values(status="executed"))
        session.commit()
        return NOW

    with pytest.raises(actions.IntentNotPlanned):
        actions.veto_intent(session, 1, note="stop", actor=actions.ACTOR_WEB, now=now_with_race)
    session.expire_all()
    assert session.get(Intent, 1).status == "executed"
    assert session.get(Intent, 1).emil_note is None


# ----------------------------------------------------------------- execute-now


def test_execute_now_pulls_execute_at_to_now(session):
    _intent(session, execute_at=NOW + 5 * H)
    row = actions.execute_intent_now(session, 1, actor=actions.ACTOR_WEB, now=NOW)
    assert row.status == "planned"  # ausgeführt wird erst beim Executor-Tick
    assert row.execute_at.replace(tzinfo=UTC) == NOW
    assert row.emil_note is None


def test_execute_now_discord_leaves_trace(session):
    _intent(session)
    row = actions.execute_intent_now(session, 1, actor=actions.ACTOR_DISCORD, now=NOW)
    assert row.emil_note == "[Discord] Sofort ausführen 14:00"


def test_execute_now_only_from_planned(session):
    _intent(session, status="failed")
    with pytest.raises(actions.IntentNotPlanned):
        actions.execute_intent_now(session, 1, actor=actions.ACTOR_WEB, now=NOW)
    with pytest.raises(actions.IntentNotFound):
        actions.execute_intent_now(session, 77, actor=actions.ACTOR_WEB, now=NOW)


# ----------------------------------------------------------------- adjust


def test_adjust_pct_sets_adjusted_from_once(session):
    _intent(session)
    row = actions.adjust_intent(session, 1, pct=10, actor=actions.ACTOR_WEB, now=NOW)
    assert (row.amount, row.adjusted_from) == (11_000_000, 10_000_000)

    row = actions.adjust_intent(session, 1, pct=20, actor=actions.ACTOR_WEB, now=NOW)
    assert (row.amount, row.adjusted_from) == (13_200_000, 10_000_000)


def test_adjust_absolute_amount(session):
    _intent(session)
    row = actions.adjust_intent(
        session, 1, amount=12_000_000, actor=actions.ACTOR_WEB, now=NOW
    )
    assert (row.amount, row.adjusted_from) == (12_000_000, 10_000_000)


def _purchase_review():
    """Interner Entscheidungsvertrag, kein erfundener Kickbase-Datensatz."""
    return {
        "schema_version": 1, "player_id": "B1", "selected": True,
        "max_bid_eur": 11_000_000, "market_value_eur": 10_000_000,
        "observed_at": NOW.isoformat(), "valid_until": (NOW + 2 * H).isoformat(),
        "purpose": "starter", "reasons": ["Startrolle und Preis für diesen Spieler geprüft."],
    }


def test_adjust_keeps_individual_review_and_rejects_amount_above_its_limit(session):
    review = _purchase_review()
    _intent(session, sources={"purchase_review": review})
    row = actions.adjust_intent(session, 1, amount=11_000_000,
                                actor=actions.ACTOR_WEB, now=NOW)
    assert row.sources["purchase_review"] == review
    with pytest.raises(actions.IntentAdjustError, match="Preisobergrenze") as error:
        actions.adjust_intent(session, 1, amount=11_000_001,
                              actor=actions.ACTOR_WEB, now=NOW)
    assert error.value.ceiling == 11_000_000
    session.expire_all()
    assert session.get(Intent, 1).amount == 11_000_000
    assert session.get(Intent, 1).sources["purchase_review"] == review


def test_adjust_requires_still_valid_existing_purchase_review(session):
    _intent(session, sources={"purchase_review": _purchase_review()})
    with pytest.raises(actions.IntentAdjustError, match="abgelaufen"):
        actions.adjust_intent(session, 1, amount=10_500_000,
                              actor=actions.ACTOR_DISCORD, now=NOW + 2 * H)
    session.expire_all()
    row = session.get(Intent, 1)
    assert (row.amount, row.adjusted_from, row.emil_note) == (10_000_000, None, None)


def test_adjust_discord_appends_actor_line(session):
    _intent(session)
    row = actions.adjust_intent(session, 1, pct=10, actor=actions.ACTOR_DISCORD, now=NOW)
    assert row.amount == 11_000_000
    assert row.emil_note == "[Discord] Anpassung 14:00"


def test_engine_adjustments_share_one_budget_reserve(session):
    plan = {
        "plan_id": "team-1", "observed_at": NOW.isoformat(), "budget_eur": 21_000_000,
        "open_bid_exposure_eur": 0, "reserved_cash_eur": 20_000_000,
        "owned_player_ids": [], "open_bids": [], "baseline_filled_slots": 9,
        "purchases": [{"player_id": pid, "amount_eur": 10_000_000, "max_bid_eur": 11_000_000}
                      for pid in ("B1", "B2")],
    }
    for pid in ("B1", "B2"):
        review = {
            **_purchase_review(), "player_id": pid, "purpose": "lineup_completion",
            "engine_version": "season-v1", "plan_id": "team-1", "team_plan": plan,
            "bid_scenarios": [
                {"amount": 10_000_000, "win_probability": None, "selected": True},
                {"amount": 11_000_000, "win_probability": None, "selected": False},
            ],
        }
        _intent(session, player_id=pid, sources={"purchase_review": review})
    actions.adjust_intent(session, 1, amount=11_000_000, actor=actions.ACTOR_WEB, now=NOW)
    with pytest.raises(actions.IntentAdjustError, match="gemeinsame Gebotsanpassungen"):
        actions.adjust_intent(session, 2, amount=11_000_000, actor=actions.ACTOR_WEB, now=NOW)
    session.expire_all()
    assert session.get(Intent, 1).amount == 11_000_000
    assert session.get(Intent, 2).amount == 10_000_000


def test_adjust_ceiling_carries_limit_for_discord_reply(session):
    """Über 1,5 × Ursprungsbetrag: IntentAdjustError mit `ceiling` (Discord-Kurztext)
    und dem heutigen Routen-Wortlaut in `detail`."""
    _intent(session)
    with pytest.raises(actions.IntentAdjustError) as err:
        actions.adjust_intent(session, 1, amount=20_000_000, actor=actions.ACTOR_WEB, now=NOW)
    assert err.value.ceiling == 15_000_000
    assert err.value.conflict is False
    assert "Anpass-Grenze" in err.value.detail
    session.expire_all()
    assert session.get(Intent, 1).amount == 10_000_000


def test_adjust_rejects_non_adjustable_kinds(session):
    _intent(session, kind="sell", amount=0)
    with pytest.raises(actions.IntentAdjustError) as err:
        actions.adjust_intent(session, 1, pct=10, actor=actions.ACTOR_WEB, now=NOW)
    assert err.value.ceiling is None and err.value.conflict is False
    assert "'sell'" in err.value.detail


def test_adjust_rejects_amount_below_one_euro(session):
    _intent(session, amount=10)
    with pytest.raises(actions.IntentAdjustError) as err:
        actions.adjust_intent(session, 1, pct=-99.99, actor=actions.ACTOR_WEB, now=NOW)
    assert "mindestens 1 €" in err.value.detail


def test_adjust_conflict_on_concurrent_amount_change(session):
    """Konkurrenz-Änderung des Betrags zwischen Lesen und bedingtem UPDATE:
    IntentAdjustError(conflict=True), fremder Stand bleibt (kein blindes +10 %)."""
    _intent(session)

    def now_with_race() -> datetime:
        session.execute(update(Intent).where(Intent.id == 1).values(amount=12_000_000))
        session.commit()
        return NOW

    with pytest.raises(actions.IntentAdjustError) as err:
        actions.adjust_intent(session, 1, pct=10, actor=actions.ACTOR_WEB, now=now_with_race)
    assert err.value.conflict is True
    assert "12,00 M" in err.value.detail
    session.expire_all()
    row = session.get(Intent, 1)
    assert (row.amount, row.adjusted_from) == (12_000_000, None)


def test_adjust_not_planned_and_not_found(session):
    _intent(session, status="vetoed")
    with pytest.raises(actions.IntentNotPlanned) as err:
        actions.adjust_intent(session, 1, pct=10, actor=actions.ACTOR_WEB, now=NOW)
    assert "Anpassen" in err.value.detail
    with pytest.raises(actions.IntentNotFound):
        actions.adjust_intent(session, 9, amount=1, actor=actions.ACTOR_WEB, now=NOW)


def test_veto_im_executing_fenster_wird_vorgemerkt(session):
    """Review 31.08.: Statt spurlosem 409 merkt das Veto sich vor — der
    Executor loest es beim Scheitern des Versuchs als vetoed ein."""
    _intent(session, status="executing")
    row = actions.veto_intent(
        session, 1, note=None, actor=actions.ACTOR_DISCORD, now=NOW
    )
    assert row.status == "executing"
    assert row.veto_requested_at is not None


def test_veto_auf_finalem_vorhaben_wirft_weiterhin(session):
    _intent(session, status="vetoed")
    with pytest.raises(actions.IntentNotPlanned):
        actions.veto_intent(session, 1, note=None, actor=actions.ACTOR_WEB, now=NOW)
