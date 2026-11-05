"""Trading-Pipeline: Bidding-Schleife (Kauf) + Wendepunkt-Verkauf."""

from kickbase_agent.trading.pipeline import (
    TradingConfig,
    plan_risk_sells,
    plan_trading_buys,
    plan_trading_sells,
)
from kickbase_agent.trading.scanner import rank_risers

CFG = TradingConfig(hold_days=4, overpay_fraction=0.5, min_pct_per_day=0.005)


def _risers():
    market = [
        ("1", "Steiler", 3_000_000, [3_000_000, 3_150_000, 3_300_000]),  # +150k/Tag
        ("2", "Solide", 4_000_000, [4_000_000, 4_080_000, 4_160_000]),   # +80k/Tag
        ("3", "Sanft", 2_000_000, [2_000_000, 2_030_000, 2_060_000]),    # +30k/Tag
    ]
    return rank_risers(market, min_pct_per_day=CFG.min_pct_per_day)


def test_bidding_fills_open_slots_with_top_risers():
    buys = plan_trading_buys(_risers(), budget=100_000_000, team_value=100_000_000,
                             open_slots=2, cfg=CFG)
    assert len(buys) == 2
    assert buys[0].player_id == "1"  # stärkstes Momentum zuerst


def test_bidding_overpay_stays_below_expected_gain():
    buys = plan_trading_buys(_risers(), budget=100_000_000, team_value=100_000_000,
                             open_slots=1, cfg=CFG)
    b = buys[0]
    overpay = b.bid - b.market_value
    assert 0 < overpay < b.expected_gain  # Overpay kleiner als erwarteter Gewinn → Profit


def test_bidding_respects_overdraft_limit():
    # Winziges Budget + Kader → nur wenige Käufe passen ins Minus-Limit
    buys = plan_trading_buys(_risers(), budget=1_000_000, team_value=3_000_000,
                             open_slots=5, cfg=CFG)
    assert len(buys) < 3  # Overdraft bremst


def test_bidding_wave_shares_one_fixed_limit():
    """Alle Gebote einer Runde zusammen gegen EIN Limit — es wächst nicht mit.

    Budget 0, Teamwert 21 M → Limit −7 M. Gebote: 3,3 M / 4,16 M / 2,06 M.
    Nach Nr. 1 passt Nr. 2 nicht mehr (3,3 + 4,16 > 7), Nr. 3 aber schon.
    Die frühere Rechnung hätte den Teamwert je Gebot mitwachsen lassen und
    Nr. 2 fälschlich zugelassen.
    """
    buys = plan_trading_buys(_risers(), budget=0, team_value=21_000_000,
                             open_slots=5, cfg=CFG)
    assert [b.player_id for b in buys] == ["1", "3"]


def test_squad_limit_defaults_to_settings():
    """Kaderlimit kommt aus den Settings (kickbase_squad_limit), nicht hartkodiert."""
    from kickbase_agent.trading.orchestrator import resolve_squad_limit

    assert TradingConfig().max_squad_size is None                       # kein Hardcode
    assert resolve_squad_limit(TradingConfig()) == 18                   # Settings-Default
    assert resolve_squad_limit(TradingConfig(max_squad_size=22)) == 22  # expliziter Wert


def test_no_slots_no_buys():
    assert plan_trading_buys(_risers(), 100_000_000, 100_000_000, 0, CFG) == []


def test_sell_flattening_trader():
    holdings = [
        ("9", "Knickt", 5_000_000, [5_000_000, 5_300_000, 5_450_000, 5_480_000]),  # flacht ab
        ("8", "Steigt", 4_000_000, [4_000_000, 4_100_000, 4_300_000, 4_600_000]),  # beschleunigt
    ]
    sells = plan_trading_sells(holdings, CFG)
    ids = [s.player_id for s in sells]
    assert "9" in ids      # Wendepunkt → verkaufen
    assert "8" not in ids  # läuft noch → halten


def test_core_risk_players_are_not_passively_held():
    """Vollautomation: jeder fallende ungeschützte Core braucht Rotation."""
    core = [
        # pid, name, MW, Verlauf, KB-Startelfstufe
        ("v", "Vogt", 1_600_000, [1_900_000, 1_800_000, 1_700_000, 1_600_000], 5),
        ("s", "Starter", 8_000_000, [8_500_000, 8_300_000, 8_100_000, 8_000_000], 1),
        ("o", "Olise", 65_000_000, [66_000_000, 65_800_000, 65_300_000, 65_000_000], 1),
    ]

    sells = plan_risk_sells(core, CFG, protected_ids={"o"})

    assert [s.player_id for s in sells] == ["v", "s"]
    assert "Kapitalverlust stoppen" in sells[0].reason
    assert "KB-Stufe 1" in sells[1].reason
