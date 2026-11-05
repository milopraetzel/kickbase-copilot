"""Stufe 2 der Lern-Bilanz: Belege schreiben und zusammenfassen (Spec §3.2, §10).

Zwei Eigenschaften tragen alles Weitere:
1. **Ein Ereignis zählt genau einmal.** Ohne `(learning_id, ref)`-Unique wüchse jede
   Bilanz mit der Zahl der Sammler-Läufe statt mit der Zahl der Ereignisse.
2. **`source` setzt der Aufrufer nie.** Emil muss im UI sehen können, ob gemessen
   oder von mir beurteilt wurde — ein Agenten-Beleg, der sich als `auto` ausgibt,
   zerstört genau diese Trennung.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Base, LearningEvidence
from kickbase_agent.knowledge import enrich_learnings
from kickbase_agent.knowledge import evidence as ev
from kickbase_agent.knowledge import status as st
from kickbase_agent.knowledge.learnings import ART_FAKT

NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def test_agent_evidence_is_written_with_source_agent(session):
    row = ev.record_agent_evidence(
        session, "L-042", "confirm", note="Ulrich-Gebot abgelehnt (100 % MW)",
        ref="bid:34", observed_at=NOW,
    )
    assert row is not None
    assert row.source == ev.SOURCE_AGENT and row.verdict == ev.CONFIRM
    assert row.rule_key == ""          # Agenten-Belege tragen keine Regel
    assert row.learning_id == "L-042"


def test_duplicate_ref_does_not_create_a_second_row(session):
    first = ev.record_agent_evidence(session, "L-042", "confirm", note="a", ref="bid:34")
    again = ev.record_agent_evidence(session, "L-042", "refute", note="b", ref="bid:34")
    assert first is not None and again is None
    rows = session.execute(select(LearningEvidence)).scalars().all()
    assert len(rows) == 1
    # Der ERSTE Beleg gilt: ein zweiter Lauf darf ein Urteil nie umschreiben.
    assert rows[0].verdict == ev.CONFIRM and rows[0].note == "a"


def test_same_ref_for_a_different_learning_is_allowed(session):
    """Ein Ereignis kann zwei Behauptungen betreffen — die Sperre gilt je Behauptung."""
    assert ev.record_agent_evidence(session, "L-042", "confirm", note="x", ref="action:9")
    assert ev.record_agent_evidence(session, "L-065", "refute", note="y", ref="action:9")
    assert len(session.execute(select(LearningEvidence)).scalars().all()) == 2


def test_verdict_and_id_are_validated(session):
    with pytest.raises(ValueError, match="verdict"):
        ev.record_agent_evidence(session, "L-042", "vielleicht", note="x", ref="r:1")
    with pytest.raises(ValueError, match="learning_id"):
        ev.record_agent_evidence(session, "042", "confirm", note="x", ref="r:1")
    with pytest.raises(ValueError, match="note"):
        ev.record_agent_evidence(session, "L-042", "confirm", note="  ", ref="r:1")
    with pytest.raises(ValueError, match="ref"):
        ev.record_agent_evidence(session, "L-042", "confirm", note="x", ref=" ")


def test_drafts_are_stored_with_source_auto_and_rule_key(session):
    drafts = [
        ev.EvidenceDraft("L-054", ev.CONFIRM, "action:7", "500 mit err 5080", NOW),
        ev.EvidenceDraft("L-054", ev.CONFIRM, "action:8", "500 mit err 5080", NOW),
    ]
    written = ev.record_drafts(session, drafts, rule_key="underpay-5080")
    assert len(written) == 2
    assert all(r.source == ev.SOURCE_AUTO and r.rule_key == "underpay-5080" for r in written)
    # Zweiter Lauf über dasselbe Fenster: nichts Neues.
    assert ev.record_drafts(session, drafts, rule_key="underpay-5080") == []


def test_a_duplicate_in_the_middle_does_not_discard_the_new_ones(session):
    """Der Sammler laeuft ueber ueberlappende Fenster: Ein bekannter Beleg mitten im
    Stapel darf die NEUEN nicht mitreissen (ein Rollback wuerde genau das tun)."""
    ev.record_drafts(
        session, [ev.EvidenceDraft("L-054", ev.CONFIRM, "action:1", "alt", NOW)],
        rule_key="underpay-5080",
    )
    written = ev.record_drafts(
        session,
        [
            # BEIDE Reihenfolgen: der Rollback-Fehler zeigt sich nur, wenn der
            # bekannte Beleg NACH einem neuen kommt.
            ev.EvidenceDraft("L-054", ev.CONFIRM, "action:2", "neu", NOW),    # neu
            ev.EvidenceDraft("L-054", ev.CONFIRM, "action:1", "alt", NOW),    # bekannt
            ev.EvidenceDraft("L-054", ev.CONFIRM, "action:3", "neu2", NOW),   # neu
        ],
        rule_key="underpay-5080",
    )
    assert [r.ref for r in written] == ["action:2", "action:3"]
    refs = {r.ref for r in session.execute(select(LearningEvidence)).scalars().all()}
    assert refs == {"action:1", "action:2", "action:3"}


def test_summary_counts_by_verdict_and_source(session):
    ev.record_drafts(
        session,
        [
            ev.EvidenceDraft("L-054", ev.CONFIRM, "action:1", "a", NOW - timedelta(days=3)),
            ev.EvidenceDraft("L-054", ev.CONFIRM, "action:2", "b", NOW - timedelta(days=1)),
            ev.EvidenceDraft("L-054", ev.REFUTE, "action:3", "c", NOW),
        ],
        rule_key="underpay-5080",
    )
    ev.record_agent_evidence(
        session, "L-054", "confirm", note="von Hand", ref="bid:9", observed_at=NOW,
    )
    summary = ev.summaries(session)["L-054"]
    assert summary == {
        "confirm": 3, "refute": 1, "auto": 3, "agent": 1, "agent_refute": 0,
        "first_at": (NOW - timedelta(days=3)).isoformat(),
        "last_at": NOW.isoformat(),
    }


def test_summaries_are_empty_without_evidence(session):
    assert ev.summaries(session) == {}
    assert ev.items_for(session, ["L-001"]) == {}


def test_items_are_newest_first_and_capped(session):
    ev.record_drafts(
        session,
        [
            ev.EvidenceDraft("L-054", ev.CONFIRM, f"action:{i}", f"Beleg {i}",
                             NOW - timedelta(hours=i))
            for i in range(15)
        ],
        rule_key="underpay-5080",
    )
    items = ev.items_for(session, ["L-054"], limit=10)["L-054"]
    assert len(items) == 10
    assert [i["note"] for i in items[:3]] == ["Beleg 0", "Beleg 1", "Beleg 2"]
    assert set(items[0]) == {
        "observed_at", "verdict", "source", "ref", "note", "rule_key"
    }


def test_since_window_only_returns_recent_evidence(session):
    ev.record_drafts(
        session,
        [
            ev.EvidenceDraft("L-054", ev.CONFIRM, "alt", "alt", NOW - timedelta(days=30)),
            ev.EvidenceDraft("L-054", ev.REFUTE, "neu", "neu", NOW - timedelta(days=1)),
        ],
        rule_key="k",
    )
    recent = ev.summaries(session, since=NOW - timedelta(days=7))["L-054"]
    assert recent["confirm"] == 0 and recent["refute"] == 1


@pytest.mark.parametrize("agent_verdict", [ev.CONFIRM, ev.REFUTE])
@pytest.mark.parametrize("auto_count", [1, ev.ITEM_LIMIT + 1])
def test_fact_status_keeps_the_source_of_counter_evidence(session, agent_verdict, auto_count):
    """Nur ein Agenten-Gegenbeleg darf einen Fakt ablösen, auch außerhalb der Vorschau."""
    ev.record_agent_evidence(
        session, "L-054", agent_verdict, note="Von Hand geprüft", ref="review:1",
        observed_at=NOW - timedelta(days=2),
    )
    ev.record_drafts(
        session,
        [ev.EvidenceDraft("L-054", ev.REFUTE, f"action:{i}", "Gegenbeleg", NOW)
         for i in range(auto_count)],
        rule_key="gegenprobe",
    )
    enriched, _, _ = enrich_learnings(
        session, [{"id": "L-054", "art": ART_FAKT}], NOW,
    )
    if auto_count > ev.ITEM_LIMIT:
        assert all(item["source"] == ev.SOURCE_AUTO for item in enriched[0]["evidence_items"])
    expected = st.UEBERHOLT if agent_verdict == ev.REFUTE else st.GILT
    assert enriched[0]["status"]["status"] == expected
