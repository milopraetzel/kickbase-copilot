"""Overdraft-Mathematik nach der offiziellen 33%-Regel (bereinigter Teamwert)."""

from kickbase_agent.trading.finance import (
    can_afford,
    max_single_bid,
    overdraft_floor,
    team_value_after_buy,
)


def test_overdraft_floor_is_third_of_team_value_at_zero_balance():
    assert overdraft_floor(99_000_000, balance=0) == -33_000_000


def test_overdraft_floor_ignores_positive_balance():
    # Ein Plus auf dem Konto hebt das Limit nicht zusätzlich an.
    assert overdraft_floor(99_000_000, balance=50_000_000) == -33_000_000


def test_overdraft_floor_nets_negative_balance_official_example():
    # Offizielles Beispiel (help.kickbase.com): Teamwert 100 M, Konto −10 M →
    # bereinigter Teamwert 90 M → Limit −30 M.
    assert overdraft_floor(100_000_000, balance=-10_000_000) == -30_000_000


def test_overdraft_floor_never_positive_when_debt_exceeds_team_value():
    # Pathologischer Zustand: mehr Schulden als Teamwert → Limit 0, nie > 0.
    assert overdraft_floor(5_000_000, balance=-20_000_000) == 0


def test_can_afford_within_limit():
    # Konto 50 M, Teamwert 99 M → Limit −33 M. Kauf 60 M: neuer Stand −10 M ≥ −33 M.
    assert can_afford(budget=50_000_000, team_value=99_000_000, price=60_000_000) is True


def test_can_afford_rejects_over_limit():
    # Kauf 140 M: neuer Stand −90 M < −33 M — das Limit wächst beim Bieten NICHT
    # mit dem Kauf mit (früherer Fehler: Teamwert + Kaufpreis).
    assert can_afford(budget=50_000_000, team_value=99_000_000, price=140_000_000) is False


def test_can_afford_counts_open_bids_together():
    # Kickbase prüft "nach Annahme aller Gebote": Ein Gebot, das allein passt,
    # scheitert, wenn schon offene Gebote das Limit aufbrauchen.
    assert can_afford(budget=50_000_000, team_value=99_000_000, price=80_000_000) is True
    assert (
        can_afford(budget=50_000_000, team_value=99_000_000, price=80_000_000, open_bids=10_000_000)
        is False
    )


def test_max_single_bid_does_not_grow_with_the_purchase():
    # Konto 53,8 M, Teamwert 98 M → Limit −32,666 M → Höchstgebot 86,466 M.
    # Die frühere Formel (3·Budget + Teamwert)/2 hätte 129,7 M erlaubt.
    result = max_single_bid(budget=53_800_000, team_value=98_000_000)
    assert result == 86_466_666
    assert result < 100_000_000


def test_max_single_bid_official_minus_example():
    # Teamwert 100 M, Konto −10 M → Limit −30 M → noch 20 M Spielraum.
    assert max_single_bid(budget=-10_000_000, team_value=100_000_000) == 20_000_000


def test_max_single_bid_exactly_affordable():
    tv, b = 98_000_000, 53_800_000
    mx = max_single_bid(b, tv)
    assert can_afford(b, tv, mx) is True
    assert can_afford(b, tv, mx + 1) is False


def test_max_single_bid_never_negative():
    # Konto bereits unter dem Limit → 0 = kein Gebot möglich.
    assert max_single_bid(budget=-40_000_000, team_value=90_000_000) == 0


def test_team_value_after_buy_rises_by_market_value_not_price():
    # Kauf für 15 M bei Marktwert 10 M: Teamwert +10 M — der Overpay verpufft.
    # (Der Kaufpreis geht bewusst gar nicht in die Funktion ein.)
    assert team_value_after_buy(team_value=90_000_000, market_value=10_000_000) == 100_000_000
