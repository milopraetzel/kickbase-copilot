"""Stufe 4: Status-Ableitung (Spec §5, §10).

Bewusst grob: Bei zweistelligen Fallzahlen waere jede feinere Statistik gelogen.
Geprueft werden die Grenzwerte jedes Uebergangs — und dass eine Panne oder ein Fakt
NIE einen Hypothesen-Status bekommt (Scheingenauigkeit ist die eigentliche Gefahr).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kickbase_agent.knowledge import status as st
from kickbase_agent.knowledge.learnings import ART_FAKT, ART_HYPOTHESE, ART_PANNE

NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


def _summary(confirm=0, refute=0, *, first_days_ago=0, auto=None, agent=0):
    total = confirm + refute
    return {
        "confirm": confirm,
        "refute": refute,
        "auto": total - agent if auto is None else auto,
        "agent": agent,
        "first_at": (NOW - timedelta(days=first_days_ago)).isoformat() if total else None,
        "last_at": NOW.isoformat() if total else None,
    }


# ----------------------------------------------------------------- Hypothese


@pytest.mark.parametrize(
    ("confirm", "refute", "first_days_ago", "expected"),
    [
        (0, 0, 0, st.UNBELEGT),        # keine Belege
        (1, 0, 0, st.BEOBACHTET),      # ein Beleg ist noch keine Bilanz
        (2, 0, 0, st.GESTUETZT),       # Grenzwert gestuetzt
        (3, 0, 30, st.GESTUETZT),      # alt genug, aber zu wenige
        (4, 0, 13, st.GESTUETZT),      # genug Belege, aber zu jung
        (4, 0, 14, st.BEWAEHRT),       # Grenzwert bewaehrt: 4 Belege UEBER 14 Tage
        (9, 0, 60, st.BEWAEHRT),
        (5, 1, 30, st.WACKELT),        # erster Gegenbeleg = Handlungsbedarf
        (1, 1, 0, st.WACKELT),         # Gleichstand bei einem Gegenbeleg
        (0, 1, 0, st.WACKELT),         # ein Gegenbeleg allein reicht noch nicht
        (2, 2, 0, st.WIDERLEGT),       # Gleichstand ab zwei Gegenbelegen
        (1, 3, 0, st.WIDERLEGT),
        (3, 2, 0, st.WACKELT),         # bestaetigt > widerlegt: noch nicht widerlegt
    ],
)
def test_hypothesis_thresholds(confirm, refute, first_days_ago, expected):
    out = st.derive(ART_HYPOTHESE, _summary(confirm, refute, first_days_ago=first_days_ago),
                    now=NOW)
    assert out.status == expected


def test_hypothesis_status_carries_a_label_and_tone():
    out = st.derive(ART_HYPOTHESE, _summary(4, 0, first_days_ago=20), now=NOW)
    assert out.status == st.BEWAEHRT and out.tone == "gut"
    assert "bestätigt" in out.label
    assert st.derive(ART_HYPOTHESE, _summary(0, 3), now=NOW).tone == "schlecht"
    assert st.derive(ART_HYPOTHESE, _summary(3, 1), now=NOW).tone == "warnung"
    assert st.derive(ART_HYPOTHESE, _summary(), now=NOW).tone == "neutral"


# ----------------------------------------------------------------- Fakt


def test_fact_is_never_given_a_hit_rate():
    for confirm, refute in ((0, 0), (5, 0), (9, 0)):
        assert st.derive(ART_FAKT, _summary(confirm, refute), now=NOW).status == st.GILT


def test_fact_becomes_outdated_only_through_an_agent_verdict():
    """Spec §2: Ein Fakt hat keinen Zaehler — er gilt oder ist ueberholt."""
    auto_only = _summary(0, 2, auto=2, agent=0)
    assert st.derive(ART_FAKT, auto_only, now=NOW).status == st.GILT
    by_agent = _summary(0, 1, auto=0, agent=1)
    out = st.derive(ART_FAKT, by_agent, now=NOW,
                    items=[{"verdict": "refute", "source": "agent",
                            "note": "ersetzt durch L-070", "ref": "probe:1"}])
    assert out.status == st.UEBERHOLT and out.successor == "L-070"


# ----------------------------------------------------------------- Panne


def test_incident_holds_until_it_happens_again():
    assert st.derive(ART_PANNE, _summary(3, 0), now=NOW).status == st.HAELT
    assert st.derive(ART_PANNE, _summary(), now=NOW).status == st.HAELT
    wieder = st.derive(ART_PANNE, _summary(3, 1), now=NOW)
    assert wieder.status == st.ERNEUT and wieder.tone == "schlecht"


def test_incident_never_gets_a_hypothesis_status():
    for confirm, refute in ((0, 0), (4, 0), (2, 2)):
        assert st.derive(ART_PANNE, _summary(confirm, refute), now=NOW).status in (
            st.HAELT, st.ERNEUT
        )


# ----------------------------------------------------------------- Bilanz


def _learning(lid, art=ART_HYPOTHESE, date="2026-08-01", title="T"):
    return {"id": lid, "art": art, "date": date, "title": title, "summary": "s", "rule": "r"}


def test_balance_answers_are_they_getting_better():
    learnings = [
        _learning("L-001"), _learning("L-002"), _learning("L-003"),
        _learning("L-009", date="2026-08-25"),          # neu in dieser Woche
        _learning("L-004", art=ART_FAKT), _learning("L-005", art=ART_PANNE),
    ]
    statuses = {
        "L-001": st.derive(ART_HYPOTHESE, _summary(4, 0, first_days_ago=20), now=NOW),
        "L-002": st.derive(ART_HYPOTHESE, _summary(1, 2), now=NOW),
        "L-003": st.derive(ART_HYPOTHESE, _summary(), now=NOW),
        "L-009": st.derive(ART_HYPOTHESE, _summary(2, 0), now=NOW),
    }
    week = {"L-001": {"confirm": 1, "refute": 0}, "L-002": {"confirm": 0, "refute": 2}}
    out = st.balance(learnings, statuses, week, now=NOW)
    assert out["confirmed_week"] == 1 and out["refuted_week"] == 1
    assert out["new_week"] == 1
    assert out["active_hypotheses"] == 4
    assert out["verteilung"][st.BEWAEHRT] == 1 and out["verteilung"][st.WIDERLEGT] == 1
    assert out["verteilung"][st.UNBELEGT] == 1


def test_entries_without_a_date_are_never_counted_as_new():
    """Sonst waere jeder undatierte Alt-Eintrag jede Woche wieder „neu"."""
    learnings = [_learning("L-001", date=None), _learning("L-002", date="2026-08-25")]
    out = st.balance(learnings, {}, {}, now=NOW)
    assert out["new_week"] == 1


def test_empty_state_is_honest_instead_of_all_proven():
    out = st.balance([], {}, {}, now=NOW)
    assert out["confirmed_week"] == 0 and out["refuted_week"] == 0
    assert out["active_hypotheses"] == 0
    assert set(out["verteilung"]) == set(st.HYPOTHESE_STATUSES)
    assert all(v == 0 for v in out["verteilung"].values())


def test_to_fix_lists_only_what_demands_an_action():
    statuses = {
        "L-001": st.derive(ART_HYPOTHESE, _summary(4, 0, first_days_ago=20), now=NOW),
        "L-002": st.derive(ART_HYPOTHESE, _summary(1, 2), now=NOW),    # widerlegt
        "L-003": st.derive(ART_HYPOTHESE, _summary(3, 1), now=NOW),    # wackelt
        "L-004": st.derive(ART_PANNE, _summary(1, 1), now=NOW),        # erneut aufgetreten
        "L-005": st.derive(ART_FAKT, _summary(), now=NOW),
    }
    assert st.to_fix_ids(statuses) == ["L-002", "L-003", "L-004"]
