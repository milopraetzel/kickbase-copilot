"""Weekly-Finance-Cycle: Mo–Do investieren, Fr 17:00 Konto glattstellen (Purge)."""

from kickbase_agent.trading.finance_cycle import finance_phase, plan_purge

# --- Wochentag-Phase ---

def test_phase_invest_monday_to_thursday():
    for weekday in (0, 1, 2, 3):  # Mo–Do
        assert finance_phase(weekday) == "invest"


def test_phase_purge_friday():
    assert finance_phase(4) == "purge"


def test_phase_hold_weekend():
    assert finance_phase(5) == "hold"  # Sa
    assert finance_phase(6) == "hold"  # So


# --- Purge ---

def _holdings():
    # (id, name, mv, momentum €/Tag) — Mischung aus starken und schwachen Steigern
    return [
        ("1", "Stark", 5_000_000, 300_000),
        ("2", "Mittel", 4_000_000, 100_000),
        ("3", "Schwach", 3_000_000, 20_000),
    ]


def test_purge_sells_weakest_first_until_buffer_reached():
    # Konto -6M, Ziel +5k → braucht ~6M. Schwächste zuerst: Schwach(3M)+Mittel(4M)=7M.
    result = plan_purge(_holdings(), budget=-6_000_000, buffer=5_000)

    sold_ids = [s[0] for s in result.sells]
    assert sold_ids == ["3", "2"]           # schwächste zuerst
    assert "1" not in sold_ids              # stärkster Steiger bleibt
    assert result.final_balance >= 5_000
    assert result.shortfall == 0


def test_purge_nothing_when_already_above_buffer():
    result = plan_purge(_holdings(), budget=1_000_000, buffer=5_000)
    assert result.sells == []
    assert result.final_balance == 1_000_000


def test_purge_shortfall_when_not_enough_material():
    # Konto -50M, aber nur 12M Trading-Material → Failsafe nötig
    result = plan_purge(_holdings(), budget=-50_000_000, buffer=5_000)
    assert len(result.sells) == 3           # alles verkauft
    assert result.shortfall > 0             # reicht trotzdem nicht → Eskalation


def test_purge_falls_back_to_squad_when_trading_empty():
    """Review-Fix: kein Trading-Material + Minus → Notverkauf aus dem Kader."""
    result = plan_purge([], budget=-3_000_000, buffer=10_000,
                        fallback=[("a", "Fringe1", 2_000_000), ("b", "Fringe2", 2_000_000)])
    assert [n for _, n, _ in result.sells] == ["Fringe1", "Fringe2"]
    assert result.shortfall == 0 and result.final_balance >= 10_000


def test_trade_budget_counting_and_blocking():
    from kickbase_agent.trading.trade_budget import budget_line, count_my_trades, trades_blocked
    feed = [
        {"t": 15, "data": {"byr": "emil", "pn": "A", "trp": 1}},
        {"t": 15, "data": {"slr": "emil", "pn": "B", "trp": 1}},
        {"t": 15, "data": {"byr": "Rivale", "pn": "C", "trp": 1}},
        {"t": 3, "data": {"ln": "D"}},
    ]
    assert count_my_trades(feed, "emil") == 2
    assert not trades_blocked(3, 200)
    assert trades_blocked(195, 200)      # Notreserve 5
    assert "195/200" in budget_line(195, 200) and "🔴" in budget_line(195, 200)
    assert "🟢" in budget_line(3, 200)
