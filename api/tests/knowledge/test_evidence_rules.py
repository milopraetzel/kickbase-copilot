"""Stufe 3: die drei Auto-Regeln (Spec §4.1, §10).

Alle drei lesen ausschliesslich Spuren, die der Betrieb ohnehin erzeugt — keine neue
Datenquelle, kein zusaetzlicher API-Verkehr. Der wichtigste Test ist der ueber die
Selbstbestaetigung: Ein Preflight, der unsere EIGENE Regel anwendet, ist kein Beleg
fuer die Regel. Nur eine Antwort von Kickbase zaehlt.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, TransferAction
from kickbase_agent.knowledge import evidence as ev
from kickbase_agent.knowledge import evidence_rules as rules

NOW = datetime(2026, 8, 26, 7, 20, tzinfo=UTC)
SINCE = NOW - timedelta(days=7)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _action(session: Session, **over) -> TransferAction:
    base = dict(
        league_id="L1", action_type="bid", player_id="15589", amount=9_000_000,
        status="failed", idempotency_key=f"k-{over.get('idempotency_key', id(over))}",
        rule_id="intent-bid", rationale="", dry_run=False,
        created_at=NOW - timedelta(days=1), resolved_at=NOW - timedelta(days=1),
    )
    base.update(over)
    row = TransferAction(**base)
    session.add(row)
    session.commit()
    return row


# ----------------------------------------------------------------- Registry


def test_registry_exposes_exactly_the_three_start_rules():
    """Spec §4.1: bewusst klein anfangen — weitere Regeln nur, wenn sie es verdienen."""
    assert [r.key for r in rules.all_rules()] == [
        "underpay-5080", "manager-deal-abgelehnt", "praemie-gutschrift"
    ]
    assert all(r.learning_id.startswith("L-") for r in rules.all_rules())


# ----------------------------------------------------------------- Underpay


def test_underpay_rule_makes_one_evidence_per_api_refusal(session):
    row = _action(
        session, status="failed", idempotency_key="a",
        rationale='HTTP 500 {"err":5080,"errMsg":"UnderpayNotAllowed"}',
    )
    drafts = rules.underpay_5080(session, SINCE, NOW)
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.learning_id == rules.L_UNDERPAY
    assert draft.verdict == ev.CONFIRM and draft.ref == f"action:{row.id}"
    assert "5080" in draft.note


def test_underpay_rule_ignores_our_own_preflight(session):
    """Der Preflight WENDET die Regel an — er bestaetigt sie nicht. Sonst belegte
    sich die Behauptung selbst und die Bilanz waere ein Echo."""
    _action(
        session, status="refused", idempotency_key="b",
        rationale="Preflight: Manager-Listing verlangt >= 100 % MW (err 5080 …)",
    )
    assert rules.underpay_5080(session, SINCE, NOW) == []


def test_underpay_rule_ignores_unrelated_failures_and_old_events(session):
    _action(session, status="failed", idempotency_key="c", rationale="HTTP 502 Bad Gateway")
    _action(
        session, status="failed", idempotency_key="d",
        rationale='{"err":5080}', created_at=NOW - timedelta(days=30),
        resolved_at=NOW - timedelta(days=30),
    )
    assert rules.underpay_5080(session, SINCE, NOW) == []


# ----------------------------------------------------------------- Manager-Deals


def _bid_with_outcome(session, *, kind: str, result: str, key: str) -> TransferAction:
    return _action(
        session, status="executed", idempotency_key=key,
        payload={
            "expectation": {"p": 0.15, "why": "Manager", "kind": kind, "ratio": 1.0},
            "outcome": {"result": result, "at": (NOW - timedelta(days=2)).isoformat()},
        },
    )


def test_manager_rule_counts_a_lost_manager_bid_as_confirmation(session):
    row = _bid_with_outcome(session, kind="manager", result="lost", key="m1")
    drafts = rules.manager_deal_abgelehnt(session, SINCE, NOW)
    assert [(d.verdict, d.ref) for d in drafts] == [(ev.CONFIRM, f"action:{row.id}")]
    assert drafts[0].learning_id == rules.L_BESCHAFFUNG


def test_manager_rule_counts_a_won_manager_bid_as_counter_evidence(session):
    _bid_with_outcome(session, kind="manager", result="won", key="m2")
    drafts = rules.manager_deal_abgelehnt(session, SINCE, NOW)
    assert [d.verdict for d in drafts] == [ev.REFUTE]


def test_manager_rule_ignores_kickbase_listings_and_open_bids(session):
    _bid_with_outcome(session, kind="kickbase", result="lost", key="k1")
    _action(session, status="executed", idempotency_key="offen",
            payload={"expectation": {"p": 0.5, "kind": "manager"}})   # kein outcome
    assert rules.manager_deal_abgelehnt(session, SINCE, NOW) == []


@pytest.mark.parametrize("settled_at", [SINCE - timedelta(seconds=1), NOW + timedelta(seconds=1)])
def test_manager_rule_ignores_outcomes_outside_the_window(session, settled_at):
    row = _bid_with_outcome(session, kind="manager", result="lost", key="outside")
    row.payload = {**row.payload, "outcome": {"result": "lost", "at": settled_at.isoformat()}}
    session.commit()
    assert rules.manager_deal_abgelehnt(session, SINCE, NOW) == []


@pytest.mark.parametrize("settled_at", [SINCE, NOW])
def test_manager_rule_includes_outcomes_at_the_window_boundaries(session, settled_at):
    row = _bid_with_outcome(session, kind="manager", result="lost", key="boundary")
    row.payload = {**row.payload, "outcome": {"result": "lost", "at": settled_at.isoformat()}}
    session.commit()
    assert len(rules.manager_deal_abgelehnt(session, SINCE, NOW)) == 1


def test_manager_rule_uses_action_timestamp_for_legacy_outcomes(session):
    row = _bid_with_outcome(session, kind="manager", result="won", key="legacy")
    row.payload = {**row.payload, "outcome": {"result": "won"}}
    session.commit()
    drafts = rules.manager_deal_abgelehnt(session, SINCE, NOW)
    assert len(drafts) == 1 and drafts[0].verdict == ev.REFUTE


# ----------------------------------------------------------------- Prämie


def test_bonus_rule_confirms_a_real_credit_and_refutes_a_failure(session):
    good = _action(session, action_type="bonus", status="executed", idempotency_key="p1",
                   player_id=None, amount=60_000, rationale="Prämie +60.000 €")
    bad = _action(session, action_type="bonus", status="failed", idempotency_key="p2",
                  player_id=None, amount=None, rationale="HTTP 500")
    drafts = {d.ref: d for d in rules.praemie_gutschrift(session, SINCE, NOW)}
    assert drafts[f"action:{good.id}"].verdict == ev.CONFIRM
    assert drafts[f"action:{bad.id}"].verdict == ev.REFUTE
    assert all(d.learning_id == rules.L_PRAEMIE for d in drafts.values())


def test_bonus_rule_treats_noop_as_no_evidence(session):
    """Ein leerer Griff nach einer Mitternachts-Einsammlung ist korrekt, kein Ausfall
    (LEARNINGS 24.08.) — er darf die Bilanz weder heben noch senken."""
    _action(session, action_type="bonus", status="noop", idempotency_key="p3",
            player_id=None, rationale="200, aber 0 € Delta")
    assert rules.praemie_gutschrift(session, SINCE, NOW) == []


def test_bonus_rule_ignores_dry_runs(session):
    _action(session, action_type="bonus", status="skipped_dry_run", idempotency_key="p4",
            player_id=None, dry_run=True)
    assert rules.praemie_gutschrift(session, SINCE, NOW) == []


# ----------------------------------------------------------------- Sammellauf


def test_collect_writes_once_and_stays_quiet_on_the_second_pass(session):
    _action(session, status="failed", idempotency_key="x",
            rationale='500 {"err":5080,"errMsg":"UnderpayNotAllowed"}')
    _bid_with_outcome(session, kind="manager", result="lost", key="y")

    first = rules.collect(session, since=SINCE, now=NOW)
    assert first.written == 2
    assert set(first.by_rule) == {"underpay-5080", "manager-deal-abgelehnt"}

    second = rules.collect(session, since=SINCE, now=NOW)
    assert second.written == 0 and second.by_rule == {}
    assert sum(s["confirm"] for s in ev.summaries(session).values()) == 2


def test_collect_on_an_empty_database_writes_nothing(session):
    result = rules.collect(session, since=SINCE, now=NOW)
    assert result.written == 0 and result.by_rule == {}


def test_a_broken_rule_never_stops_the_others(session, monkeypatch):
    """Eine Regel ist eine Vermutung ueber Datenformate — sie darf den Sammler
    nicht mitreissen, sonst faellt die ganze Bilanz wegen eines Feldes aus."""
    def boom(session, since, now):
        raise ValueError("Feld weg")

    monkeypatch.setattr(
        rules, "REGISTRY",
        [rules.Rule("kaputt", rules.L_UNDERPAY, boom), *rules.REGISTRY[1:]],
    )
    _bid_with_outcome(session, kind="manager", result="lost", key="z")
    result = rules.collect(session, since=SINCE, now=NOW)
    assert result.written == 1 and result.failed == ["kaputt"]
