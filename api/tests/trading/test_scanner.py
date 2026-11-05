"""Momentum-Scanner: Marktwert-Steiger finden, Wendepunkt erkennen."""

from kickbase_agent.trading.scanner import momentum, rank_risers


def test_momentum_rising_series():
    m = momentum([5_000_000, 5_150_000, 5_320_000, 5_500_000])
    assert m.daily_change > 0
    assert m.rising is True


def test_momentum_flat_series_not_rising():
    m = momentum([5_000_000, 5_000_000, 5_000_000])
    assert m.rising is False
    assert m.daily_change == 0


def test_momentum_detects_flattening_turn():
    # Anstieg lässt nach: +300k, +150k, +30k → Kurve knickt (Verkaufssignal)
    m = momentum([5_000_000, 5_300_000, 5_450_000, 5_480_000])
    assert m.rising is True
    assert m.flattening is True


def test_momentum_still_accelerating_not_flattening():
    m = momentum([5_000_000, 5_100_000, 5_300_000, 5_600_000])  # +100,+200,+300
    assert m.flattening is False


def test_momentum_ignores_single_spike():
    # Einmaliger Sprung (+5M an einem Tag), sonst flach → kein nachhaltiges Momentum
    m = momentum([3_000_000, 3_000_000, 8_000_000, 8_000_000, 8_000_000])
    assert m.spike is True
    assert m.daily_change < 500_000  # Median glättet den Sprung weg


def test_spike_player_filtered_from_risers():
    market = [
        ("1", "Strohfeuer", 3_000_000, [3_000_000, 3_000_000, 8_000_000, 8_000_000, 8_000_000]),
        ("2", "Echt", 3_000_000, [3_000_000, 3_150_000, 3_300_000, 3_450_000, 3_600_000]),
    ]
    ids = [r.player_id for r in rank_risers(market, min_pct_per_day=0.005)]
    assert ids == ["2"]  # Spike-Spieler raus, echter Steiger bleibt


def test_rank_risers_sorts_by_momentum_and_filters_flat():
    market = [
        ("1", "Schnell", 3_000_000, [3_000_000, 3_200_000, 3_500_000]),   # stark
        ("2", "Mittel", 4_000_000, [4_000_000, 4_050_000, 4_100_000]),    # schwach
        ("3", "Flach", 2_000_000, [2_000_000, 2_000_000, 2_000_000]),     # kein Anstieg
    ]
    risers = rank_risers(market, min_pct_per_day=0.005)

    ids = [r.player_id for r in risers]
    assert ids[0] == "1"          # stärkstes Momentum zuerst
    assert "3" not in ids         # flacher Spieler rausgefiltert
