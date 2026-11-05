"""T-120-Freeze: bis dahin gelten die normalen Kaufkraft-/Pfandregeln.

Alle Zeiten hängen am SELBEN relativen Anker (kein Produktionscode mit echter
Uhr im Spiel) — keine Zeitbomben (LEARNINGS 23.08.).
"""

from datetime import UTC, datetime, timedelta

from kickbase_agent.decision.kickoff_freeze import (
    BID_FREEZE_MINUTES,
    bid_freeze_reason,
)

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def _reason(delta: timedelta, amount: int = 5_000_000, pbaa: int | None = 50_000_000):
    return bid_freeze_reason(NOW, NOW + delta, amount=amount, budget_after_bids=pbaa)


def test_no_deadline_means_no_window():
    assert bid_freeze_reason(NOW, None, amount=99_000_000, budget_after_bids=None) is None


def test_past_deadline_is_ignored():
    """Defensive Doppelprüfung — matchday_deadline liefert ohnehin nur Zukunft."""
    assert _reason(timedelta(minutes=-5), pbaa=None) is None


def test_far_from_deadline_no_objection_even_uncovered():
    """24 h vor Anpfiff gelten die normalen 33-%-/Pfandregeln des Preflights."""
    assert _reason(timedelta(hours=24), pbaa=0) is None


def test_freeze_blocks_every_bid_within_t120():
    """Im Freeze-Fenster hilft auch volle Deckung nicht — kein neues Gebot."""
    reason = _reason(timedelta(minutes=90), amount=1, pbaa=999_000_000)
    assert reason is not None and "T-120" in reason


def test_freeze_boundary_exactly_120_minutes():
    reason = _reason(timedelta(minutes=BID_FREEZE_MINUTES), amount=1, pbaa=999_000_000)
    assert reason is not None and "T-120" in reason


def test_negative_worst_case_is_not_a_premature_freeze():
    """Die Anpfiffpflicht greift erst in der T-120-Kaskade, nicht schon 24 h vorher."""
    assert _reason(timedelta(hours=24), amount=5_000_000, pbaa=4_000_000) is None


def test_missing_pbaa_does_not_create_a_24h_freeze():
    """Die allgemeine 33-%-Prüfung bleibt zuständig; dieser Helper friert nur T-120."""
    assert _reason(timedelta(hours=24), pbaa=None) is None
