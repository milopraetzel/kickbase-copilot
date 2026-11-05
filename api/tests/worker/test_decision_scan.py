"""Schatten-Decision-Scan: Gate-Klassifikation, quiet, Staging-Bilanz, keine Buttons."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import kickbase_agent.worker.decision_scan as ds
from kickbase_agent.agent.executor import PlannedAction
from kickbase_agent.cli.plan import Plan
from kickbase_agent.db.models import Base, TransferAction
from kickbase_agent.decision.models import Proposal
from kickbase_agent.worker.decision_scan import format_shadow_report, run_decision_scan

TMAX = 1_500_000  # Default von AGENT_TRADING_MAX_PER_PLAYER


def _prop(pid: str, amount: int, kind: str = "bid", *, approval: bool = False,
          score: float = 0.59, name: str | None = None) -> Proposal:
    return Proposal(
        action=PlannedAction(action_type=kind, league_id="L1", player_id=pid,
                             amount=amount, rule_id="r"),
        rule_id="r",
        rationale=f"{kind} {pid}",
        score=score,
        requires_approval=approval,
        player_name=name or f"P{pid}",
    )


def _plan(buys=(), sells=(), saturated: bool = False) -> Plan:
    return Plan(league_id="L1", budget=10_000_000, buys=list(buys), sells=list(sells),
                trade_count_saturated=saturated)


# ------------------------------------------------------- format_shadow_report

def test_format_auto_under_max_without_approval():
    """≤ Trading-Max, kein Groß-Vorhaben, Ampel grün → ✅ wäre Stufe-2-autonom."""
    msg = format_shadow_report(_plan(buys=[_prop("1", 900_000)]), [], TMAX)
    line = next(ln for ln in msg.splitlines() if "P1" in ln)
    assert "JA, 0,9M ≤ 1,5M ⇒ ✅ wäre autonom gelaufen" in line
    assert "📋" not in line
    assert "1× ✅ wäre Stufe-2-autonom · 0× 📋 über den Vorhaben-Pfad" in msg


def test_format_locks_over_trading_max():
    """Keidel-Beispiel: 6,13M > 1,5M → 📋 Vorhaben-Pfad (Zeile exakt wie spezifiziert)."""
    msg = format_shadow_report(
        _plan(buys=[_prop("7", 6_130_000, name="Keidel", approval=True)]), [], TMAX
    )
    assert "• Keidel: 6,13M · Score 0.59 (✅ auto: Trading ≤1,5M — NEIN, "\
           "6,13M > 1,5M · Groß-Vorhaben (> 5 M) ⇒ 📋 Vorhaben-Pfad)" in msg
    assert "0× ✅ wäre Stufe-2-autonom · 1× 📋 über den Vorhaben-Pfad" in msg


def test_format_locks_requires_approval_even_under_max():
    """Unter Trading-Max, aber requires_approval → trotzdem 📋 Vorhaben-Pfad."""
    msg = format_shadow_report(_plan(buys=[_prop("2", 1_000_000, approval=True)]), [], TMAX)
    line = next(ln for ln in msg.splitlines() if "P2" in ln)
    assert "NEIN, Groß-Vorhaben (> 5 M) ⇒ 📋 Vorhaben-Pfad" in line
    assert "0× ✅ wäre Stufe-2-autonom · 1× 📋 über den Vorhaben-Pfad" in msg


def test_format_saturated_ampel_locks_everything():
    """trade_count_saturated → ALLES 📋, auch kleine Beträge ohne Groß-Vorhaben-Marke."""
    msg = format_shadow_report(
        _plan(buys=[_prop("1", 500_000)], sells=[_prop("9", 400_000, "list")],
              saturated=True),
        [], TMAX,
    )
    for pid in ("P1", "P9"):
        line = next(ln for ln in msg.splitlines() if pid in ln)
        assert "Trade-Ampel saturiert" in line and "📋 Vorhaben-Pfad" in line
    assert "0× ✅ wäre Stufe-2-autonom · 2× 📋 über den Vorhaben-Pfad" in msg


def test_format_sells_show_way():
    """Verkäufe erscheinen unter VERKÄUFE mit way (list/sell); keine leere KÄUFE-Sektion."""
    msg = format_shadow_report(
        _plan(sells=[_prop("9", 800_000, "list"), _prop("8", 700_000, "sell")]), [], TMAX
    )
    assert "VERKÄUFE (2):" in msg
    assert not any(ln.startswith("KÄUFE") for ln in msg.splitlines())
    assert "• P9 (list): 0,8M" in msg
    assert "• P8 (sell): 0,7M" in msg


def test_format_counts_candidates_and_footer_without_staging():
    """Bilanz nennt die Kandidaten und sagt ehrlich: nichts gestaged, nichts blockiert."""
    buys = [_prop("1", 900_000), _prop("2", 800_000)]
    msg = format_shadow_report(_plan(buys=buys), [], TMAX)
    assert msg.startswith("🤖 **Schatten-Scan** — was der Automat (Phase 2) heute getan HÄTTE")
    assert "2 Kandidaten, nichts gestaged." in msg
    assert "dieser Lauf blockiert keine Aktion" in msg


# ----------------------------------------------------------- run_decision_scan

class _InfoOnlyChannel:
    """Bewusst OHNE send_approval: würde der Job Buttons verschicken, knallt es."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.kinds: list[str | None] = []

    async def send_info(self, text: str, *, kind: str | None = None, title=None) -> None:
        self.sent.append(text)
        self.kinds.append(kind)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine, future=True) as s:
        yield s


def _patch_world(monkeypatch, plan: Plan, session) -> None:
    async def fake_build_plan():
        return plan

    async def fake_factory():
        return SimpleNamespace(), SimpleNamespace(user_id="u1", league_id="L1")

    class _SessionCtx:
        def __enter__(self):
            return session

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(ds, "build_plan", fake_build_plan)
    # Seit 23.08. (Review P0) staged der Schatten-Lauf nichts mehr — Client/Session
    # werden nicht mehr angefasst; Fakes bleiben tolerant, falls das Modul sie haelt.
    if hasattr(ds, "make_kickbase_client"):
        monkeypatch.setattr(ds, "make_kickbase_client", fake_factory)
    if hasattr(ds, "SessionLocal"):
        monkeypatch.setattr(ds, "SessionLocal", lambda: _SessionCtx())


async def test_run_quiet_without_candidates(monkeypatch, session):
    """Leerer Plan → 'quiet', kein Post, kein Client-Aufbau, kein Staging."""
    _patch_world(monkeypatch, _plan(), session)

    async def boom():
        raise AssertionError("ohne Kandidaten darf kein Client gebaut werden")

    if hasattr(ds, "make_kickbase_client"):
        monkeypatch.setattr(ds, "make_kickbase_client", boom)
    channel = _InfoOnlyChannel()
    assert await run_decision_scan(channel) == "quiet"
    assert channel.sent == []
    assert session.query(TransferAction).count() == 0


async def test_run_posts_report_without_staging(monkeypatch, session):
    """Report nur via send_info — und KEINE transfer_actions-Zeile (Review-Fund P0:
    proposed-Zeilen blockierten das Intent desselben Spielers als duplicate)."""
    plan = _plan(buys=[_prop("1", 900_000)], sells=[_prop("9", 6_130_000, "list",
                                                          approval=True, name="Keidel")])
    _patch_world(monkeypatch, plan, session)
    channel = _InfoOnlyChannel()

    reports = []
    monkeypatch.setattr(ds, "save_report", lambda **kw: reports.append(kw) or 1)
    assert await run_decision_scan(channel) == "archived"
    # Meldungs-Umbau 31.08.: Archiv statt Discord — kein Post, eine Berichtszeile.
    assert channel.sent == []
    assert [r["kind"] for r in reports] == ["schatten"]
    msg = reports[0]["body"]
    assert "2 Kandidaten, nichts gestaged." in msg
    assert "1× ✅ wäre Stufe-2-autonom · 1× 📋 über den Vorhaben-Pfad" in msg
    assert session.query(TransferAction).count() == 0  # nichts vorgemerkt, nichts blockiert


async def test_run_twice_never_creates_action_rows(monkeypatch, session):
    """Mehrfache Laeufe am selben Tag bleiben reine Reports — keine Zeile, kein duplicate."""
    plan = _plan(buys=[_prop("1", 900_000), _prop("2", 800_000)])
    _patch_world(monkeypatch, plan, session)
    channel = _InfoOnlyChannel()

    reports = []
    monkeypatch.setattr(ds, "save_report", lambda **kw: reports.append(kw) or 1)
    assert await run_decision_scan(channel) == "archived"
    assert await run_decision_scan(channel) == "archived"
    assert "2 Kandidaten, nichts gestaged." in reports[1]["body"]
    assert channel.sent == []
    assert session.query(TransferAction).count() == 0
