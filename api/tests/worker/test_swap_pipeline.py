"""Tausch-Paare im Planner (02.09.2026): Gebot + wartender Verkauf als Entwürfe.

Neue Datei (L-087). plan_intents macht aus plan.swaps zwei Entwürfe je Paar;
jedes Paar wartet auf seinen Ersatz und einen zusätzlichen Kaderplatz;
ohne Deadline kein Tausch; action_for
hebt die Tausch-Bedingung als `swap` in die PlannedAction.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from kickbase_agent.cli.plan import Plan
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS
from kickbase_agent.decision.policies import SwapPair, propose_swaps
from kickbase_agent.intel.models import ExplosionCandidate, Signal
from kickbase_agent.worker.intents import (
    EXECUTE_BEFORE_DEADLINE,
    MarketRow,
    action_for,
    plan_intents,
)

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
DEADLINE = datetime(2026, 9, 4, 18, 30, tzinfo=UTC)


def _cand(pid, *, pos=2, mv=8_000_000, score=0.6, elf=0.5, trend=0.5, name=None):
    return ExplosionCandidate(
        player_id=pid, name=name or f"Spieler {pid}", team_id="T" + pid, position=pos,
        current_market_value=mv, total_score=score, lineup_score=elf,
        signals=[Signal(name="trend", score=trend, weight=0.25, reason=f"Trend {trend:.2f}.")],
    )


def _row(pid, *, exs=40_000, price=8_000_000):
    return MarketRow(
        player_id=pid, price=price, expires_in_s=exs, seller_id="", seller_name="",
        player_name=f"Markt {pid}", team_id="T" + pid, position=2, kb_prob=2,
        our_bid=None, is_ours=False,
    )


def _squad(n: int):
    return [SimpleNamespace(player=SimpleNamespace(id=f"s{i}", position=2, first_name="S",
                                                   last_name=str(i), team_id="T", lineup_prob=2))
            for i in range(n)]


def _pairs(fallers, ersatz):
    return propose_swaps(fallers, ersatz, DEFAULT_GUARDRAILS, bench_ids=frozenset(),
                         taken_pids=set(), budget=50_000_000, league_id="L1")


def _plan(swaps: list[SwapPair], *, deadline=DEADLINE, squad_n=11, cands=()):
    return Plan(league_id="L1", budget=20_000_000, buys=[], sells=[], squad=_squad(squad_n),
                market_candidates=list(cands), matchday_deadline_at=deadline, swaps=swaps)


def test_tauschpaar_wird_zu_gebot_und_wartendem_verkauf() -> None:
    faller = _cand("deman", mv=9_500_000, score=0.26, elf=0.34, trend=0.1, name="Deman")
    ersatz = _cand("keidel", score=0.61, elf=0.55, name="Keidel")
    swaps = _pairs([faller], [ersatz])
    drafts = plan_intents(_plan(swaps, cands=[ersatz]), [_row("keidel")], NOW)

    kinds = [(d.kind, d.player_id) for d in drafts]
    assert kinds == [("bid", "keidel"), ("sell", "deman")]
    bid, sell = drafts
    assert bid.sources["swap_out"] == "deman" and bid.sources["rule_id"] == "swap_bid"
    assert sell.sources["after_squad_size"] == 12  # 11 + 1 Zugang
    assert sell.sources["swap_for"] == "keidel" and sell.sources["rule_id"] == "swap_sell"
    assert sell.amount == 9_500_000 and sell.deadline == DEADLINE - EXECUTE_BEFORE_DEADLINE
    assert sell.execute_at == NOW and sell.listing_type == "manager"


def test_zweites_paar_nutzt_nach_erstem_abgang_denselben_kaderfloor() -> None:
    f1 = _cand("f1", score=0.2, elf=0.3, trend=0.1)
    f2 = _cand("f2", score=0.2, elf=0.3, trend=0.2)
    e1, e2 = _cand("e1", score=0.7, elf=0.6), _cand("e2", score=0.65, elf=0.6)
    swaps = _pairs([f1, f2], [e1, e2])
    drafts = plan_intents(_plan(swaps), [_row("e1"), _row("e2")], NOW)
    needs = [d.sources["after_squad_size"] for d in drafts if d.kind == "sell"]
    assert needs == [12, 12]


def test_ohne_deadline_oder_ohne_markt_zeile_kein_tausch() -> None:
    faller = _cand("f", score=0.2, elf=0.3, trend=0.1)
    ersatz = _cand("e", score=0.7, elf=0.6)
    swaps = _pairs([faller], [ersatz])
    assert plan_intents(_plan(swaps, deadline=None), [_row("e")], NOW) == []
    assert plan_intents(_plan(swaps), [], NOW) == []


def test_eigenes_gebot_auf_den_ersatz_blockt_das_paar() -> None:
    faller = _cand("f", score=0.2, elf=0.3, trend=0.1)
    ersatz = _cand("e", score=0.7, elf=0.6)
    swaps = _pairs([faller], [ersatz])
    row = MarketRow(player_id="e", price=8_000_000, expires_in_s=40_000, seller_id="",
                    seller_name="", player_name="E", team_id="T", position=2, kb_prob=2,
                    our_bid=8_100_000, is_ours=False)
    assert plan_intents(_plan(swaps), [row], NOW) == []


def test_action_for_hebt_tausch_bedingung_als_swap() -> None:
    it = SimpleNamespace(kind="sell", player_id="f", amount=9_000_000, rationale="Tausch",
                         adjusted_from=None, emil_note=None,
                         sources={"after_squad_size": 12, "rule_id": "swap_sell"})
    a = action_for(it, "L1")
    assert a.swap is True and a.rule_id == "intent-sell" and a.action_type == "sell"
    plain = SimpleNamespace(kind="sell", player_id="g", amount=1, rationale="x",
                            adjusted_from=None, emil_note=None, sources={})
    assert action_for(plain, "L1").swap is False


def test_deadline_naht_verkauf_bekommt_trotzdem_deadline_minus_puffer() -> None:
    faller = _cand("f", score=0.2, elf=0.3, trend=0.1)
    ersatz = _cand("e", score=0.7, elf=0.6)
    swaps = _pairs([faller], [ersatz])
    bald = NOW + timedelta(hours=3)
    drafts = plan_intents(_plan(swaps, deadline=bald), [_row("e", exs=7_200)], NOW)
    sell = [d for d in drafts if d.kind == "sell"][0]
    assert sell.deadline == bald - EXECUTE_BEFORE_DEADLINE
