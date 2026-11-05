"""Treffer-Bilanz der Jev-Urteile (intel/jev_outcomes.py, Spec 2026-10-01 §3).

Jedes klare Rollen-Urteil wird am ersten Spiel des Spielers NACH dem Artikel gemessen,
jeder Rückkehr-Hinweis an Einsatz und Marktwert danach. Fehlende Daten sind nie ein
Fehlurteil: Was nicht entschieden ist, bleibt „offen".
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from kickbase_agent.intel.jev_outcomes import (
    Match,
    Verdict,
    check_return,
    check_role,
    first_match_after,
    summarize,
)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
ARTICLE = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 10, 9, 18, 30, tzinfo=UTC)


def _verdict(answer: str = "fehlt", outcome: str = "sperrt", kind: str = "rolle",
             published: datetime | None = ARTICLE) -> Verdict:
    return Verdict(id=7, kind=kind, outcome=outcome, answer=answer, probability=0.99,
                   player_id="9605", player_name="Albert Grønbæk", subject="fällt aus",
                   published_at=published, created_at=ARTICLE + timedelta(minutes=5))


def _match(minutes: int, when: datetime = KICKOFF, observed: bool = True) -> Match:
    return Match(match_date=when, minutes=minutes, observed=observed)


# ------------------------------------------------------------------ Anker


def test_the_first_settled_match_after_the_article_counts():
    earlier = _match(90, ARTICLE - timedelta(days=3))  # Spiel VOR dem Artikel zählt nicht
    later = _match(0, KICKOFF + timedelta(days=7))
    assert first_match_after([later, earlier, _match(12)], ARTICLE, NOW) == _match(12)


def test_a_match_that_is_not_settled_yet_leaves_the_case_open():
    running = _match(0, NOW - timedelta(hours=1))
    assert first_match_after([running], ARTICLE, NOW) is None


def test_unobserved_minutes_are_no_zero():
    assert first_match_after([_match(0, observed=False)], ARTICLE, NOW) is None


def test_a_match_far_after_the_article_is_not_its_next_match():
    # Fehlt die Zeile des eigentlichen nächsten Spiels, wäre ein späteres Spiel ein
    # falscher Maßstab — lieber offen.
    far = _match(0, ARTICLE + timedelta(days=20))
    assert first_match_after([far], ARTICLE, ARTICLE + timedelta(days=40)) is None
    near = _match(0, ARTICLE + timedelta(days=13))
    assert first_match_after([near], ARTICLE, ARTICLE + timedelta(days=40)) == near


# ------------------------------------------------------------------ Rolle


@pytest.mark.parametrize(
    ("answer", "minutes", "result"),
    [
        ("fehlt", 0, "treffer"),
        ("fehlt", 1, "daneben"),  # eine Einwechslung in der Nachspielzeit ist kein Fehlen
        ("fehlt", 17, "daneben"),
        ("startet", 90, "treffer"),
        ("startet", 45, "treffer"),
        ("startet", 44, "daneben"),
        ("startet", 0, "daneben"),
        ("bank", 12, "treffer"),
        ("bank", 0, "treffer"),
        ("bank", 63, "daneben"),
    ],
)
def test_role_verdicts_are_measured_by_minutes(answer, minutes, result):
    check = check_role(_verdict(answer), [_match(minutes)], NOW)
    assert check["result"] == result
    assert check["minutes"] == minutes
    assert check["match_date"] == KICKOFF.isoformat()


def test_a_role_verdict_without_a_settled_match_is_open():
    check = check_role(_verdict(), [], NOW)
    assert check["result"] == "offen" and check["minutes"] is None
    assert "noch" in check["why"]


def test_without_publication_time_the_question_time_anchors():
    check = check_role(_verdict(published=None), [_match(0)], NOW)
    assert check["result"] == "treffer"


# ------------------------------------------------------------------ Rückkehr


def _values(base: int = 5_000_000) -> list[tuple[date, int]]:
    """Gefragt am 01.10. um 11:05 Berlin: Es gilt der Wert vom 30.09. (Update 22:00);
    der Tageswert vom 01.10. entsteht erst abends und ist schon die erste Reaktion."""
    day = date(2026, 9, 30)
    return [
        (day - timedelta(days=1), base - 50_000),
        (day, base),
        (day + timedelta(days=1), base + 900_000),
        (day + timedelta(days=3), base + 300_000),
        (day + timedelta(days=7), base + 600_000),
    ]


def test_a_return_hint_counts_when_he_played_again_and_shows_market_value_moves():
    check = check_return(_verdict("rueckkehr", "kaufhinweis", "rueckkehr"), [_match(23)],
                         _values(), NOW)

    assert check["result"] == "treffer" and check["played"] is True
    assert (check["mv_0"], check["mv_3"], check["mv_7"]) == (5_000_000, 5_300_000, 5_600_000)
    assert check["d3_pct"] == pytest.approx(6.0) and check["d7_pct"] == pytest.approx(12.0)


def test_a_return_hint_misses_when_he_did_not_play():
    check = check_return(_verdict("rueckkehr", "kaufhinweis", "rueckkehr"), [_match(0)],
                         _values(), NOW)
    assert check["result"] == "daneben" and check["played"] is False


def test_missing_market_values_stay_unknown_not_zero():
    values = [(ARTICLE.date(), 5_000_000)]
    check = check_return(_verdict("rueckkehr", "kaufhinweis", "rueckkehr"), [], values, NOW)
    assert check["result"] == "offen"
    assert check["mv_3"] is None and check["d3_pct"] is None and check["d7_pct"] is None


def test_the_last_value_before_the_question_is_the_base():
    values = [(date(2026, 9, 28), 4_000_000), (date(2026, 10, 3), 4_400_000)]
    check = check_return(_verdict("rueckkehr", "kaufhinweis", "rueckkehr"), [], values, NOW)
    assert check["mv_0"] == 4_000_000 and check["d3_pct"] == pytest.approx(10.0)


def test_after_the_evening_update_the_same_days_value_is_the_base():
    """Review 01.10.: Gefragt um 23:30 Berlin gilt schon der Wert dieses Tages."""
    late = Verdict(id=1, kind="rueckkehr", outcome="kaufhinweis", answer="rueckkehr",
                   probability=0.99, player_id="43", player_name="W", subject="x",
                   published_at=None, created_at=datetime(2026, 10, 1, 21, 30, tzinfo=UTC))
    values = [(date(2026, 9, 30), 4_000_000), (date(2026, 10, 1), 5_000_000),
              (date(2026, 10, 4), 5_500_000)]
    check = check_return(late, [], values, NOW)
    assert check["mv_0"] == 5_000_000 and check["d3_pct"] == pytest.approx(10.0)


def test_a_row_captured_during_the_match_is_not_final():
    """Review 01.10.: Ein 20:30-Spiel erfasst der Snapshot um 22:05 mit Teilminuten."""
    early = Match(KICKOFF, 30, True, captured_at=KICKOFF + timedelta(minutes=95))
    assert first_match_after([early], ARTICLE, NOW) is None
    final = Match(KICKOFF, 48, True, captured_at=KICKOFF + timedelta(hours=26))
    assert first_match_after([final], ARTICLE, NOW) == final


def test_summarize_counts_each_result():
    checks = [{"result": "treffer"}, {"result": "treffer"}, {"result": "daneben"},
              {"result": "offen"}]
    assert summarize(checks) == {"treffer": 2, "daneben": 1, "offen": 1, "entschieden": 3}
