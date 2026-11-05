"""Jev-Rolle am echten build_plan-Aufruf (Emil 29.09.2026: „bau die Rolle mit Jev").

Ein klares „fehlt" aus einem kicker-Artikel sperrt einen NEUEN Kauf und ein eigenes,
noch nicht ausgeführtes Engine-Vorhaben; der Artikel steht als Grund im Beleg. Live-Gebote
und manuelle/angepasste Vorhaben fragt der Plan gar nicht erst, und selbst ein Urteil zu
ihnen änderte nichts. Ein Fehler der News-Strecke kostet nur die Sperre, nie den Plan.
Welche Urteile sperren (nur „fehlt" ab 95 %), prüft tests/intel/test_role_judge.py an der
echten Jev-Antwort; hier stehen reine Domain-Testdaten.
"""

from datetime import UTC, datetime, timedelta

import pytest

from kickbase_agent.cli import plan as plan_mod
from kickbase_agent.intel.role_judge import RoleVerdict
from kickbase_agent.news.models import NewsArticle

from .test_plan import _bid
from .test_plan_dispo import WEAK, _only_swap, _pending_swap_bid, _swap_world
from .test_plan_paket1 import TEN
from .test_plan_roster_value import _forecast, _prepare, _replacement

_TITLE = "Ersatz fällt vorerst aus"


def _judge(
    monkeypatch, *, blocked: frozenset = frozenset(), always: frozenset = frozenset()
) -> list[tuple[list[str], list[str], dict]]:
    """Ersetzt nur das Urteil; Artikel und Spielerauswahl kommen aus build_plan.

    `blocked`: gegebene Spieler mit klarem „fehlt". `always`: „fehlt" auch für Spieler,
    die der Plan gar nicht zur Prüfung gab — so zeigt sich, ob die Sperre selbst
    Live-Gebote und manuelle Vorhaben verschont (zweite Sicherung)."""
    article = NewsArticle(source="kicker", url="https://www.kicker.de/x", title=_TITLE, body="",
                          published_at=datetime.now(UTC) - timedelta(hours=5))
    monkeypatch.setattr(plan_mod, "load_recent_articles", lambda session, since: [article])
    seen: list[tuple[list[str], list[str], dict]] = []

    async def judge(players, articles, team_names, *, now, **kwargs):
        players, articles = list(players), list(articles)
        seen.append(([p.id for p in players], [a.title for a in articles], kwargs))
        ids = {p.id for p in players if p.id in blocked} | set(always)
        return {
            pid: RoleVerdict(pid, "fehlt", 0.97, a.title, a.url, a.published_at)
            for pid in ids
            for a in articles
        }

    monkeypatch.setattr(plan_mod, "judge_roles", judge)
    return seen


def _planned(
    pid: str, *, manual: bool = False, status: str = "planned", amount: int = 1_020_000,
    intent_id: int = 7,
) -> list[dict]:
    """Offenes Vorhaben in der Form von `load_pending_bid_commitments`."""
    return [{
        "player_id": pid, "amount": amount, "team_id": "replacement-club", "position": 2,
        "intent_id": intent_id, "status": status, "manual": manual, "listing_type": "kickbase",
        "purchase_review": None,
    }]


async def test_without_a_verdict_the_gap_is_filled(monkeypatch):
    client = await _prepare(monkeypatch, positions=TEN, cash=10_000_000)
    client.market = [_replacement(mv=1_020_000, pid="kb")]
    seen = _judge(monkeypatch)

    plan = await plan_mod.build_plan()

    assert [b.action.player_id for b in plan.buys] == ["kb"]
    [(players, titles, kwargs)] = seen
    assert (players, titles) == (["kb"], [_TITLE])
    assert "fixtures" in kwargs  # Zeitanker: letztes Spiel je Verein


async def test_a_clear_absence_blocks_the_new_target(monkeypatch):
    client = await _prepare(monkeypatch, positions=TEN, cash=10_000_000)
    client.market = [_replacement(mv=1_020_000, pid="kb")]
    _judge(monkeypatch, blocked=frozenset({"kb"}))

    plan = await plan_mod.build_plan()

    assert plan.buys == []
    review = plan.purchase_reviews["kb"]
    assert review["eligible"] is False
    [reason] = review["reasons"]
    assert reason.startswith("kicker ") and _TITLE in reason and "fehlt" in reason


async def test_a_live_bid_is_not_judged_and_keeps_its_place_even_with_a_verdict(monkeypatch):
    client = await _prepare(monkeypatch, positions=TEN, cash=10_000_000)
    client.market = [_replacement(mv=1_020_000, pid="kb")]
    client.own_bids = [_bid("kb", "replacement-club", 1_020_000)]
    seen = _judge(monkeypatch, always=frozenset({"kb"}))

    plan = await plan_mod.build_plan()

    assert all("kb" not in players for players, _, _ in seen)
    assert plan.team_plan["filled_slots"] == 11
    assert not any(_TITLE in r for r in plan.purchase_reviews["kb"]["reasons"])


async def test_a_planned_engine_intent_is_judged_and_loses_its_place(monkeypatch):
    # Review 29.09.2026: Ein geplantes Vorhaben ist noch kein Gebot. Kommt vor der
    # Ausführung ein klares „fehlt", räumt der Planner es wie bei einem LI-Ausfall.
    client = await _prepare(monkeypatch, positions=TEN, cash=10_000_000)
    client.market = [_replacement(mv=1_020_000, pid="kb")]
    monkeypatch.setattr(plan_mod, "load_pending_bid_commitments", lambda *a: _planned("kb"))
    seen = _judge(monkeypatch, blocked=frozenset({"kb"}))

    plan = await plan_mod.build_plan()

    assert seen[0][0] == ["kb"]
    review = plan.purchase_reviews["kb"]
    assert review["eligible"] is False
    assert _TITLE in review["reasons"][0] and "fehlt" in review["reasons"][0]
    assert plan.team_plan["filled_slots"] == 10


async def test_a_manual_or_adjusted_intent_is_not_judged(monkeypatch):
    # `manual` steht auch für ein von Emil angepasstes Vorhaben (adjusted_from gesetzt).
    client = await _prepare(monkeypatch, positions=TEN, cash=10_000_000)
    client.market = [_replacement(mv=1_020_000, pid="kb")]
    monkeypatch.setattr(
        plan_mod, "load_pending_bid_commitments", lambda *a: _planned("kb", manual=True)
    )
    seen = _judge(monkeypatch, always=frozenset({"kb"}))

    plan = await plan_mod.build_plan()

    assert all("kb" not in players for players, _, _ in seen)
    assert plan.team_plan["filled_slots"] == 11
    assert not any(_TITLE in r for r in plan.purchase_reviews["kb"]["reasons"])


@pytest.mark.parametrize("broken", ["load_recent_articles", "judge_roles"])
async def test_a_news_failure_never_breaks_the_plan(monkeypatch, broken):
    client = await _prepare(monkeypatch, positions=TEN, cash=10_000_000)
    client.market = [_replacement(mv=1_020_000, pid="kb")]
    _judge(monkeypatch, blocked=frozenset({"kb"}))

    def boom(*args, **kwargs):
        raise RuntimeError("Datenbank weg")

    monkeypatch.setattr(plan_mod, broken, boom)

    plan = await plan_mod.build_plan()

    assert [b.action.player_id for b in plan.buys] == ["kb"]  # wie ohne News


async def test_a_planned_intent_merged_with_a_live_bid_is_not_judged(monkeypatch):
    # Review 29.09.2026: Entscheidend ist das Live-Gebot selbst, nicht ob seine Offer-ID
    # gefüllt ist (fehlt `uoid`, wäre sie leer).
    client = await _prepare(monkeypatch, positions=TEN, cash=10_000_000)
    client.market = [_replacement(mv=1_020_000, pid="kb")]
    client.own_bids = [{**_bid("kb", "replacement-club", 1_020_000), "offer_id": ""}]
    monkeypatch.setattr(plan_mod, "load_pending_bid_commitments", lambda *a: _planned("kb"))
    seen = _judge(monkeypatch, always=frozenset({"kb"}))

    plan = await plan_mod.build_plan()

    assert all("kb" not in players for players, _, _ in seen)
    assert plan.team_plan["filled_slots"] == 11


async def test_an_executing_intent_is_not_judged(monkeypatch):
    client = await _prepare(monkeypatch, positions=TEN, cash=10_000_000)
    client.market = [_replacement(mv=1_020_000, pid="kb")]
    monkeypatch.setattr(
        plan_mod, "load_pending_bid_commitments", lambda *a: _planned("kb", status="executing")
    )
    seen = _judge(monkeypatch, always=frozenset({"kb"}))

    plan = await plan_mod.build_plan()

    assert all("kb" not in players for players, _, _ in seen)
    assert plan.team_plan["filled_slots"] == 11


async def test_a_rejected_planned_swap_bid_does_not_take_other_planned_bids_down(monkeypatch):
    """Nach-Review 29.09.2026: Ein Dispo-Tauschgebot liegt über dem Konto und ist nur über
    seinen gekoppelten Verkauf gedeckt. Sperrt „fehlt" es, darf sein Betrag nicht weiter
    reserviert bleiben — sonst wird der Plan unlösbar und räumt jedes andere geplante
    Gebot mit ab."""
    client = await _swap_world(monkeypatch)
    first = await plan_mod.build_plan()
    pair, review = _only_swap(first)
    client.market = [*client.market, _replacement(mv=500_000, pid="d1")]
    _forecast(monkeypatch, {WEAK: 0.3, "st": 0.9, "d1": 0.9})
    pending = [
        *_pending_swap_bid(review, pair.bid.action.amount),
        *_planned("d1", amount=500_000, intent_id=42),
    ]
    monkeypatch.setattr(plan_mod, "load_pending_bid_commitments", lambda *a: pending)
    monkeypatch.setattr(plan_mod, "load_swap_couplings",
                        lambda *a: [{"player_id": WEAK, "for_player_id": "st"}])

    _judge(monkeypatch)
    control = await plan_mod.build_plan()
    assert control.purchase_reviews["st"]["selected"] is True
    assert control.purchase_reviews["d1"]["selected"] is True

    _judge(monkeypatch, blocked=frozenset({"st"}))
    plan = await plan_mod.build_plan()

    assert "fehlt" in plan.purchase_reviews["st"]["reasons"][0]
    assert plan.purchase_reviews["d1"]["selected"] is True
    # Das geräumte Vorhaben ist auch für die Deckungsreparatur kein Posten mehr: Sie muss
    # nichts freigeben (sonst könnte sie das falsche Gebot wählen).
    assert plan.team_plan["funding_recovery"]["released_pending_ids"] == []
