"""Selbstverbesserungen als EINE Liste (Emils Wunsch 26.08.).

Emil wörtlich: „Ich hätte auch ganz gern die Sachen, die du immer änderst oder
diese Selbstverbesserungen immer ändert, dokumentiert werden in einer Tabelle
'Learnings' in der Zentrale."

Zwei Quellen, eine Zeitleiste: Parameter-Änderungen (Decision-Records aus
``parameter_history.json``) und die Urteile des Lernzyklus (am Audit-Eintrag
vermerkt, ``payload["surprise"]``). Was hier zählt, ist die Rückverfolgbarkeit:
jede Zeile nennt WER, WANN, ALT→NEU, GRUND, BELEG und den auslösenden Fall.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, TransferAction
from kickbase_agent.knowledge import self_improvement as si

NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _action(session, aid: int, payload: dict, *, created: datetime = NOW) -> TransferAction:
    row = TransferAction(
        id=aid, league_id="L", action_type="bid", player_id="99", amount=1_000_000,
        status="failed", idempotency_key=f"k{aid}", rule_id="r1", dry_run=False,
        payload=payload, created_at=created,
    )
    session.add(row)
    session.commit()
    return row


# ------------------------------------------------------------------ Urteile


def test_verdict_carries_its_triggering_case(session):
    _action(session, 39, {"surprise": {
        "urteil": "parameter", "erklaerung": "157 % MW gezahlt, wir boten 108 %",
        "stellschraube": "max_bid_premium_need", "neuer_wert": 1.35,
        "beleg": "feed:12364455874", "learning": "L-069", "at": NOW.isoformat(),
    }})

    rows = si.load_verdicts(session)

    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == si.KIND_VERDICT
    assert row["fall"] == "action:39"            # der auslösende Fall, verlinkbar
    assert row["beleg"] == "feed:12364455874"
    assert row["learning"] == "L-069"
    assert row["old"] is None and row["new"] == "1.35"


def test_actions_without_a_verdict_are_not_listed(session):
    _action(session, 40, {"expectation": {"p": 0.6}})
    assert si.load_verdicts(session) == []


def test_broken_verdict_payload_is_skipped_instead_of_crashing(session):
    _action(session, 41, {"surprise": "kaputt"})
    _action(session, 42, {"surprise": {"urteil": "einzelfall", "at": NOW.isoformat()}})

    rows = si.load_verdicts(session)

    assert [r["fall"] for r in rows] == ["action:42"]


def test_verdicts_are_newest_first(session):
    for aid, day in ((1, 20), (2, 24), (3, 22)):
        _action(session, aid, {"surprise": {
            "urteil": "einzelfall",
            "at": datetime(2026, 8, day, 9, 0, tzinfo=UTC).isoformat(),
        }})

    assert [r["fall"] for r in si.load_verdicts(session)] == [
        "action:2", "action:3", "action:1",
    ]


# ------------------------------------------------- Parameter + Zusammenführung


def test_parameter_changes_without_a_source_belong_to_the_orchestrator():
    """Alt-Einträge tragen kein `quelle` — sie stammen aus meiner Hand, nicht aus
    dem Selbstumbau. Raten wäre hier gefährlich: Emil muss sehen, wer geändert hat."""
    rows = si.from_history([{
        "date": "2026-08-24", "parameter": "buy_score_threshold", "old": "0.5",
        "new": "0.55", "reason": "zu viele Fehlkäufe", "beleg": "L-012", "commit": "abc1234",
    }])

    assert rows[0]["wer"] == si.WER[si.SOURCE_ORCHESTRATOR]
    assert rows[0]["kind"] == si.KIND_PARAMETER
    assert rows[0]["fall"] == "L-012"       # Beleg ist der auslösende Fall
    assert rows[0]["old"] == "0.5" and rows[0]["new"] == "0.55"


def test_a_self_change_is_labelled_as_such():
    rows = si.from_history([{
        "date": "2026-08-26", "parameter": "max_bid_premium_need", "old": "1.10",
        "new": "1.35", "reason": "Ljubicic", "beleg": "surprise:action:39",
        "commit": "def5678", "quelle": si.SOURCE_SELF,
    }])

    assert rows[0]["wer"] == si.WER[si.SOURCE_SELF]
    assert rows[0]["fall"] == "action:39"   # „surprise:"-Präfix wird abgestreift


def test_both_sources_merge_into_one_timeline_newest_first(session):
    _action(session, 39, {"surprise": {
        "urteil": "parameter", "erklaerung": "x",
        "at": datetime(2026, 8, 25, 9, 0, tzinfo=UTC).isoformat(),
    }})
    history = [
        {"date": "2026-08-26", "parameter": "a", "new": "2", "reason": "r", "beleg": "L-1"},
        {"date": "2026-08-20", "parameter": "b", "new": "3", "reason": "r", "beleg": "L-2"},
    ]

    rows = si.merge(si.from_history(history), si.load_verdicts(session))

    assert [r["at"][:10] for r in rows] == ["2026-08-26", "2026-08-25", "2026-08-20"]


def test_the_limit_keeps_the_newest_rows(session):
    history = [{"date": f"2026-08-{d:02d}", "parameter": "a", "new": str(d),
                "reason": "r", "beleg": "L-1"} for d in range(10, 25)]

    rows = si.merge(si.from_history(history), [], limit=3)

    assert [r["new"] for r in rows] == ["24", "23", "22"]


def test_an_empty_ledger_is_honest_not_invented(session):
    assert si.merge([], []) == []
    assert si.load_self_improvements(session, history=[]) == []
