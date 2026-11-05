"""Intra-Day-Puls: leise wenn nichts zu tun, kompakt wenn Handlungsbedarf."""

from kickbase_agent.cli.plan import Plan
from kickbase_agent.trading.orchestrator import TradingPlan
from kickbase_agent.trading.pipeline import TradingBuy, TradingSell
from kickbase_agent.worker.pulse import format_pulse


def _plan() -> Plan:
    return Plan(league_id="L", budget=1, buys=[], sells=[])


def _trading(buys=(), sells=()) -> TradingPlan:
    return TradingPlan(
        buys=list(buys), sells=list(sells), open_slots=3, holdings=0,
        phase="invest", overdraft_available=1, purge=None,
    )


def test_pulse_is_quiet_when_nothing_to_do():
    assert format_pulse("13:00", _plan(), _trading()) is None
    assert format_pulse("09:00", _plan(), None) is None   # kein Trading-Plan → leise


def test_pulse_reports_trading_buys():
    b = TradingBuy(player_id="1", name="Keidel", market_value=5_000_000,
                   bid=6_000_000, expected_gain=1_000_000, rationale="steigt")
    msg = format_pulse("13:00", _plan(), _trading(buys=[b]))
    assert msg is not None
    assert "Keidel" in msg and "Trading kaufen" in msg and "6.0M" in msg


def test_pulse_reports_sells():
    s = TradingSell(player_id="2", name="Neuhaus", market_value=2_000_000, reason="Wendepunkt")
    msg = format_pulse("18:00", _plan(), _trading(sells=[s]))
    assert msg is not None
    assert "Neuhaus" in msg and "Verkaufen" in msg
