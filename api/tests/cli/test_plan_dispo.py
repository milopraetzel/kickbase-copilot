"""Planungs-Dispo als Tausch-Finanzierung (Emil 22./23.09.2026), befristet bis 05.10.2026.

Emils Entscheidung vom 22.09.: „volle 33 ohne Bindung aber clever mit plan. außer
olise"; am 23.09. auf den Vorschlag „Dispo als Tausch-Finanzierung": „ok yallah weiter".
Die Engine darf bis zur Frist einen BESSEREN Starter über das vorhandene Geld hinaus
kaufen, wenn derselbe Plan den bedingten Sofortverkauf des schwächeren Spielers koppelt.
Netto muss aus echtem Geld gedeckt sein; Lücken füllt weiter nur echtes Geld.

Die Domain-Welt von tests/cli/test_plan.py schaltet den Dispo ab (Frist in der
Vergangenheit); hier wird er ausdrücklich eingeschaltet — im Plan UND im Kaufbeleg.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import kickbase_agent.decision.purchase_review as review_mod
from kickbase_agent.cli import plan as plan_mod
from kickbase_agent.decision.kickoff_freeze import KICKOFF_MIN_BALANCE
from kickbase_agent.decision.overdraft import (
    PLANNING_OVERDRAFT_UNTIL,
    overdraft_active,
    planning_overdraft,
)
from kickbase_agent.decision.purchase_review import purchase_review_veto
from kickbase_agent.kickbase.models import Player, Position, SquadPlayer

from .test_plan_roster_value import _forecast, _prepare, _replacement

_BERLIN = ZoneInfo("Europe/Berlin")
#: Dispo an: Frist weit in der Zukunft. Dispo aus: Frist in der Vergangenheit.
DISPO_AN = datetime(2099, 12, 31, 0, 0, tzinfo=_BERLIN)
DISPO_AUS = datetime(2000, 1, 1, 0, 0, tzinfo=_BERLIN)

# Neun Spieler, zwei Plätze offen (ABW + ST für 4-4-2): TW 1 · ABW 3 · MF 4 · ST 1.
NINE = [1, 2, 2, 2, 3, 3, 3, 3, 4]
# Volle Elf in 4-5-1; der einzige Stürmer ist roster-10.
ELEVEN = [1, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4]
WEAK = "roster-10"


def _striker(mv: int, pid: str):
    listing = _replacement(mv=mv, pid=pid)
    return listing.model_copy(
        update={
            "player": listing.player.model_copy(
                update={"position": Position.STRIKER, "team_id": f"club-{pid}"}
            )
        }
    )


def _kader(specs: list[tuple[int, int]]) -> list[SquadPlayer]:
    return [
        SquadPlayer(
            player=Player(
                id=f"roster-{index}",
                first_name="",
                last_name=f"Kader {index}",
                position=Position(position),
                market_value=mv,
                team_id=str(index),
                lineup_prob=1,
                fitness_status=0,
                mv_change_24h=100_000,
            ),
            is_starter=False,
        )
        for index, (position, mv) in enumerate(specs)
    ]


async def _prepare_dispo(monkeypatch, *, specs: list[tuple[int, int]], cash: int):
    client = await _prepare(monkeypatch, positions=[pos for pos, _ in specs], cash=cash)
    squad = _kader(specs)

    async def get_squad(league_id):
        return squad

    client.get_squad = get_squad
    monkeypatch.setattr(plan_mod, "PLANNING_OVERDRAFT_UNTIL", DISPO_AN)
    # Der Kaufbeleg prüft dieselbe Frist unabhängig vom Plan (Executor-Weg).
    monkeypatch.setattr(review_mod, "PLANNING_OVERDRAFT_UNTIL", DISPO_AN)
    monkeypatch.setenv("PROTECTED_PLAYER_IDS", "8329")
    return client


async def _swap_world(monkeypatch, *, weak_mv=13_000_000, cash=2_000_000,
                      target_mv=12_000_000, specs=None):
    """Volle Elf, 2 M Konto; am Markt ein klar besserer Stürmer für 12 M. Der schwache
    eigene Stürmer (13 M × 0,90 = 11,7 M) deckt netto: 12 M − 11,7 M ≤ 1,99 M."""
    specs = specs or [(pos, 13_000_000) for pos in ELEVEN[:-1]] + [(4, weak_mv)]
    client = await _prepare_dispo(monkeypatch, specs=specs, cash=cash)
    _forecast(monkeypatch, {WEAK: 0.3, "st": 0.9})
    client.market = [_striker(target_mv, "st")]
    return client


def _only_swap(plan):
    [pair] = plan.swaps
    return pair, pair.bid.action.purchase_review


# ------------------------------------------------------------ reine Funktion


def test_planning_overdraft_is_a_third_of_the_team_value_until_the_deadline():
    until = datetime(2026, 10, 5, 0, 0, tzinfo=_BERLIN)
    kurz_davor = datetime(2026, 10, 4, 21, 59, tzinfo=UTC)  # 04.10. 23:59 Berlin
    assert planning_overdraft(kurz_davor, 117_000_000, 2_000_000, until=until) == 39_000_000
    # Kickbase nettet ein Minus gegen den Teamwert (117 M − 9 M = 108 M → 36 M).
    assert planning_overdraft(kurz_davor, 117_000_000, -9_000_000, until=until) == 36_000_000
    assert planning_overdraft(kurz_davor, 0, 5_000_000, until=until) == 0
    mitternacht = datetime(2026, 10, 4, 22, 0, tzinfo=UTC)  # 05.10. 00:00 Berlin
    assert planning_overdraft(mitternacht, 117_000_000, 2_000_000, until=until) == 0
    danach = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
    assert planning_overdraft(danach, 117_000_000, 2_000_000, until=until) == 0


def test_default_deadline_is_the_end_of_saturday_31_october_2026_berlin():
    """Emil 20.10.2026 (Profi-Audit): Planungs-Dispo läuft über die gesamte Saison 2026/27
    bis 01.06.2027 00:00 Berlin (Record plan.planning_overdraft)."""
    assert PLANNING_OVERDRAFT_UNTIL == datetime(2027, 6, 1, 0, 0, tzinfo=_BERLIN)
    assert plan_mod.PLANNING_OVERDRAFT_UNTIL is PLANNING_OVERDRAFT_UNTIL
    assert review_mod.PLANNING_OVERDRAFT_UNTIL is PLANNING_OVERDRAFT_UNTIL
    assert overdraft_active(datetime(2026, 10, 5, 9, 0, tzinfo=_BERLIN))
    assert overdraft_active(datetime(2027, 5, 31, 23, 59, tzinfo=_BERLIN))
    assert not overdraft_active(datetime(2027, 6, 1, 0, 0, tzinfo=_BERLIN))


# ------------------------------------------------------------ Tausch auf Dispo


async def test_better_starter_is_bought_on_overdraft_with_a_coupled_sale(monkeypatch):
    await _swap_world(monkeypatch)

    plan = await plan_mod.build_plan()

    pair, review = _only_swap(plan)
    assert plan.buys == []  # das Gebot steht nur als Tausch-Paar im Plan
    assert pair.faller.player_id == WEAK and pair.bid.action.player_id == "st"
    assert pair.sell_amount == 13_000_000
    assert review["swap_sale"] == {
        "player_id": WEAK, "market_value_eur": 13_000_000, "proceeds_eur": 11_700_000,
    }
    team_plan = review["team_plan"]
    assert team_plan["swap_sales"] == [{**review["swap_sale"], "for_player_id": "st"}]
    assert team_plan["team_value_eur"] == 11 * 13_000_000
    amount = pair.bid.action.amount
    assert amount >= 12_000_000
    # Netto aus echtem Geld gedeckt (mit Puffer), brutto über dem Konto.
    assert amount - 11_700_000 <= 2_000_000 - KICKOFF_MIN_BALANCE
    assert team_plan["reserved_cash_eur"] > team_plan["budget_eur"]
    assert purchase_review_veto(
        review, player_id="st", amount=amount, now=datetime.now(UTC)
    ) is None
    limits = team_plan["technical_limits"]
    assert limits["planning_overdraft_eur"] == 11 * 13_000_000 // 3
    assert limits["overdraft_until"] == DISPO_AN.isoformat()
    assert limits["swap_credit_eur"] == 11_700_000
    assert WEAK not in team_plan["lineup_ids"] and "st" in team_plan["lineup_ids"]
    assert any("Tausch auf Planungs-Dispo" in r for r in review["reasons"])


async def test_without_overdraft_the_same_swap_is_not_financed(monkeypatch):
    await _swap_world(monkeypatch)
    monkeypatch.setattr(plan_mod, "PLANNING_OVERDRAFT_UNTIL", DISPO_AUS)

    plan = await plan_mod.build_plan()

    assert plan.swaps == [] and plan.buys == []
    limits = plan.team_plan["technical_limits"]
    assert limits["planning_overdraft_eur"] == 0 and limits["swap_credit_eur"] == 0
    assert plan.team_plan["swap_sales"] == []


async def test_a_protected_player_is_never_the_sale_side(monkeypatch):
    await _swap_world(monkeypatch)
    monkeypatch.setenv("PROTECTED_PLAYER_IDS", f"8329,{WEAK}")

    plan = await plan_mod.build_plan()

    assert plan.swaps == [] and plan.buys == []


async def test_swap_whose_net_is_not_covered_by_real_money_is_not_planned(monkeypatch):
    # 5 M × 0,90 = 4,5 M: netto 7,5 M > 1,99 M gedecktes Planungsgeld.
    await _swap_world(monkeypatch, weak_mv=5_000_000)

    plan = await plan_mod.build_plan()

    assert plan.swaps == [] and plan.buys == []


async def test_swap_beyond_the_33_percent_limit_is_not_planned(monkeypatch):
    """Teamwert 40 M (Grenze −13,3 M). Verkauf 30 M deckt netto, aber 2 M − 20 M = −18 M
    reißt die Kickbase-Kaufkraft."""
    specs = [(pos, 1_000_000) for pos in ELEVEN[:-1]] + [(4, 30_000_000)]
    await _swap_world(monkeypatch, specs=specs, target_mv=20_000_000)

    plan = await plan_mod.build_plan()

    assert plan.swaps == [] and plan.buys == []


async def test_auction_ending_inside_t_minus_120_gets_no_overdraft(monkeypatch):
    """Die T-120-Kaskade (versiegelt) storniert/friert Gebote, die das Konto ins Minus
    reißen können — ein Dispo-Tausch muss vor T-120 entschieden sein."""
    client = await _swap_world(monkeypatch)
    [listing] = client.market
    deadline = datetime.now(UTC) + timedelta(days=3)
    monkeypatch.setattr(plan_mod, "matchday_deadline", lambda fixtures, now: (3, deadline))
    client.market = [listing.model_copy(update={"expires_at": deadline - timedelta(hours=1)})]

    plan = await plan_mod.build_plan()

    assert plan.swaps == [] and plan.buys == []


async def test_gap_filling_gets_no_overdraft(monkeypatch):
    """Kader 9, 2 M Konto, zwei 6-M-Listings: Lücken füllt nur echtes Geld — keiner."""
    client = await _prepare_dispo(
        monkeypatch, specs=[(pos, 13_000_000) for pos in NINE], cash=2_000_000
    )
    client.market = [_replacement(mv=6_000_000, pid="abw"), _striker(6_000_000, "st")]

    plan = await plan_mod.build_plan()

    assert plan.buys == [] and plan.swaps == []
    limits = plan.team_plan["technical_limits"]
    assert limits["planning_cash_eur"] == 2_000_000 - KICKOFF_MIN_BALANCE
    assert limits["swap_credit_eur"] == 0


# ------------------------------------------------------------ Neuplanung


def _pending_swap_bid(review, amount):
    return [{
        "player_id": "st", "amount": amount, "team_id": "club-st", "position": 4,
        "intent_id": 41, "status": "planned", "manual": False,
        "listing_type": "kickbase", "purchase_review": review,
    }]


async def test_replanning_keeps_the_coupled_bid_financed(monkeypatch):
    """Nach dem Anlegen ist das Gebot eine Verpflichtung über dem Konto. Der nächste
    Plan rechnet den gekoppelten, noch wartenden Verkauf an: das Gebot bleibt gewählt,
    der Verkaufsspieler steht nicht mehr in der Elf, die Deckungsreparatur gibt nichts
    frei."""
    await _swap_world(monkeypatch)
    first = await plan_mod.build_plan()
    pair, review = _only_swap(first)
    amount = pair.bid.action.amount
    monkeypatch.setattr(plan_mod, "load_pending_bid_commitments",
                        lambda *args: _pending_swap_bid(review, amount))
    monkeypatch.setattr(plan_mod, "load_swap_couplings",
                        lambda *args: [{"player_id": WEAK, "for_player_id": "st"}])

    plan = await plan_mod.build_plan()

    again = plan.purchase_reviews["st"]
    assert again["selected"] is True
    assert again["swap_sale"]["player_id"] == WEAK
    assert purchase_review_veto(again, player_id="st", amount=amount,
                                now=datetime.now(UTC)) is None
    assert WEAK not in plan.team_plan["lineup_ids"]
    recovery = plan.team_plan["funding_recovery"]
    assert recovery["status"] == "funded" and recovery["released_pending_ids"] == []
    assert plan.swaps == [] and plan.buys == []  # kein zweites Paar auf denselben Spieler


async def test_replanning_without_the_coupled_sale_releases_the_bid(monkeypatch):
    await _swap_world(monkeypatch)
    first = await plan_mod.build_plan()
    pair, review = _only_swap(first)
    monkeypatch.setattr(plan_mod, "load_pending_bid_commitments",
                        lambda *args: _pending_swap_bid(review, pair.bid.action.amount))

    plan = await plan_mod.build_plan()

    assert plan.purchase_reviews["st"].get("selected") is not True
    assert plan.team_plan["funding_recovery"]["released_pending_ids"] == [41]


async def test_replanning_after_the_deadline_releases_the_bid(monkeypatch):
    await _swap_world(monkeypatch)
    first = await plan_mod.build_plan()
    pair, review = _only_swap(first)
    monkeypatch.setattr(plan_mod, "load_pending_bid_commitments",
                        lambda *args: _pending_swap_bid(review, pair.bid.action.amount))
    monkeypatch.setattr(plan_mod, "load_swap_couplings",
                        lambda *args: [{"player_id": WEAK, "for_player_id": "st"}])
    monkeypatch.setattr(plan_mod, "PLANNING_OVERDRAFT_UNTIL", DISPO_AUS)

    plan = await plan_mod.build_plan()

    assert plan.purchase_reviews["st"].get("selected") is not True
    assert plan.team_plan["funding_recovery"]["released_pending_ids"] == [41]


async def test_replanning_does_not_count_the_coupled_seller_for_the_eleven(monkeypatch):
    """Schwache Mittelfeldspieler: ohne Kopplung stünde der Verkaufsspieler im 4-4-2
    neben dem neuen Stürmer. Er wird aber verkauft, sobald der Zuschlag kommt — der Plan
    darf ihn deshalb nicht mehr als Elf-Spieler zählen."""
    await _swap_world(monkeypatch)
    weak_mids = {f"roster-{i}": 0.2 for i in range(5, 10)}
    _forecast(monkeypatch, {WEAK: 0.3, "st": 0.9, **weak_mids})
    first = await plan_mod.build_plan()
    pair, review = _only_swap(first)
    monkeypatch.setattr(plan_mod, "load_pending_bid_commitments",
                        lambda *args: _pending_swap_bid(review, pair.bid.action.amount))
    monkeypatch.setattr(plan_mod, "load_swap_couplings",
                        lambda *args: [{"player_id": WEAK, "for_player_id": "st"}])

    plan = await plan_mod.build_plan()

    assert plan.purchase_reviews["st"]["selected"] is True
    assert WEAK not in plan.team_plan["lineup_ids"]
    assert plan.team_plan["formation"] != "4-4-2"


# ------------------------------------------------------------ Review 23.09.: Befund 1


def _live_swap_bid(amount):
    return [{
        "player_id": "st", "name": "st", "team_id": "club-st", "amount": amount,
        "offer_id": "o-st", "expires_in_s": 3600,
    }]


def _after_deadline(client, monkeypatch):
    monkeypatch.setattr(plan_mod, "PLANNING_OVERDRAFT_UNTIL", DISPO_AUS)


def _target_on_bench(client, monkeypatch):
    [listing] = client.market
    client.market = [listing.model_copy(
        update={"player": listing.player.model_copy(update={"lineup_prob": 4})})]


@pytest.mark.parametrize("trigger", [_after_deadline, _target_on_bench])
async def test_a_live_coupled_bid_keeps_its_sale_credit_whatever_the_new_plan_says(
    monkeypatch, trigger,
):
    """Ein ausgeführtes Dispo-Gebot und sein wartender Verkauf sind eine Einheit: Der
    Verkauf bleibt bis zum Auktionsende stehen (worker/intents), also rechnet der Plan
    ihn weiter an — sonst sähe die Deckungsreparatur ein ungedecktes Gebot."""
    client = await _swap_world(monkeypatch)
    first = await plan_mod.build_plan()
    pair, _ = _only_swap(first)
    client.own_bids = _live_swap_bid(pair.bid.action.amount)
    monkeypatch.setattr(plan_mod, "load_swap_couplings",
                        lambda *args: [{"player_id": WEAK, "for_player_id": "st"}])
    trigger(client, monkeypatch)

    plan = await plan_mod.build_plan()

    limits = plan.team_plan["technical_limits"]
    assert limits["swap_credit_eur"] == 11_700_000
    assert plan.team_plan["funding_recovery"]["status"] == "funded"
    assert plan.team_plan["funding_recovery"]["actions"] == []
    assert WEAK not in plan.team_plan["lineup_ids"]


# ------------------------------------------------------------ Review 23.09.: Befund 6


def _trades_used(monkeypatch, used):
    import kickbase_agent.trading.trade_budget as budget_mod

    async def usage(client, league_id, user_id):
        return budget_mod.TradeUsage(used=used, my_name="emil")

    monkeypatch.setattr(budget_mod, "get_trade_usage", usage)


async def test_a_swap_needs_two_transfers_of_the_reserve(monkeypatch):
    """Ein Tausch sind zwei Transfers (Kauf + Sofortverkauf). Bei 193/200 (Notreserve 5)
    passt einer noch vor die Reserve, zwei nicht."""
    from kickbase_agent.trading.trade_budget import EMERGENCY_RESERVE

    await _swap_world(monkeypatch)
    _trades_used(monkeypatch, 200 - EMERGENCY_RESERVE - 2)

    plan = await plan_mod.build_plan()

    assert plan.swaps == []


async def test_a_swap_with_room_for_two_transfers_is_planned(monkeypatch):
    from kickbase_agent.trading.trade_budget import EMERGENCY_RESERVE

    await _swap_world(monkeypatch)
    _trades_used(monkeypatch, 200 - EMERGENCY_RESERVE - 3)

    plan = await plan_mod.build_plan()

    assert len(plan.swaps) == 1


async def test_midweek_swap_bridge_finances_upgrade_of_non_starter_or_falling_player(monkeypatch):
    """Hebel F (Unter-der-Woche-Upgrade-Brücke): >= 50 h vor der Spieltags-Frist darf ein
    gekoppelter Tausch eines Nicht-Starters oder fallenden Spielers bis zu 3,5 M € über dem
    freien Konto liegen (innerhalb der 33-%-Grenze) und bleibt bei Neuplanung gedeckt."""
    # Eigener Spieler (6,16 M × 0,90 = 5,544 M) fällt (-268k/d); Konto 533k €;
    # Zielspieler kostet 6,84 M -> Netto-Brücke ~0,78 M <= 3,5 M.
    client = await _swap_world(
        monkeypatch, weak_mv=6_160_000, cash=533_000, target_mv=6_840_000
    )
    squad = await client.get_squad("L1")
    for sp in squad:
        if sp.player.id == WEAK:
            sp.player = sp.player.model_copy(update={"mv_change_24h": -268_000, "mv_trend": 2})

    async def get_squad(_league_id):
        return squad

    client.get_squad = get_squad

    first = await plan_mod.build_plan()
    pair, review = _only_swap(first)
    amount = pair.bid.action.amount
    assert pair.faller.player_id == WEAK and pair.bid.action.player_id == "st"
    assert review["team_plan"]["technical_limits"]["midweek_swap_bridge_eur"] > 0
    assert purchase_review_veto(
        review, player_id="st", amount=amount, now=datetime.now(UTC)
    ) is None

    # Neuplanung mit dem geplanten Gebot hält die Deckung über Verkauf + Brücke aufrecht.
    monkeypatch.setattr(
        plan_mod,
        "load_pending_bid_commitments",
        lambda *args: _pending_swap_bid(review, amount),
    )
    monkeypatch.setattr(
        plan_mod,
        "load_swap_couplings",
        lambda *args: [{"player_id": WEAK, "for_player_id": "st"}],
    )
    second = await plan_mod.build_plan()
    assert second.purchase_reviews["st"]["selected"] is True
    assert second.team_plan["funding_recovery"]["status"] == "funded"


async def test_swap_respects_blocked_sell_ids_and_omits_bridge_when_cash_covers_bid(monkeypatch):
    """Ein gesperrter Verkaufsspieler (`blocked_sell_ids`) wird nicht als Tauschpartner
    gewählt; reicht echtes Geld + 90-%-Erlös bereits für >= 105 % des Ziel-MW, bleibt
    `midweek_swap_bridge_eur == 0`."""
    await _swap_world(monkeypatch, weak_mv=13_000_000, cash=2_000_000, target_mv=12_000_000)

    blocked = await plan_mod.build_plan(blocked_sell_ids=frozenset({WEAK}))
    assert blocked.swaps == []

    funded = await plan_mod.build_plan()
    pair, review = _only_swap(funded)
    assert pair.faller.player_id == WEAK
    assert review["team_plan"]["technical_limits"]["midweek_swap_bridge_eur"] == 0


async def test_deficit_healing_swap_sells_bench_hack_before_tigges_when_account_negative(
    monkeypatch,
):
    """Emil 21.10.2026: Bei leichtem Konto-Minus (-1,31 M €) darf ein teurer Bankspieler
    (Hack, 5,36 M € -> 4,824 M € Erlös) ohne Dispo-Brücke gegen einen günstigeren Starter
    (3,0 M €) getauscht werden, sodass das Konto vor Freitag von selbst ins Plus dreht
    und der formstarke Allein-Stürmer Tigges geschützt bleibt."""
    from kickbase_agent.decision.purchase_review import team_funding_veto
    from kickbase_agent.intel.role_judge import RoleVerdict

    specs = [(pos, 10_000_000) for pos in ELEVEN[:-2]] + [
        (3, 5_360_000),  # roster-9: Hack (Bank)
        (4, 4_600_000),  # roster-10: Tigges (Starter, +205k/Tag, Allein-Stürmer)
    ]
    client = await _prepare_dispo(monkeypatch, specs=specs, cash=-1_314_036)
    squad = await client.get_squad("L1")
    for sp in squad:
        if sp.player.id == "roster-9":
            sp.player = sp.player.model_copy(
                update={
                    "last_name": "Hack",
                    "lineup_prob": 3,
                    "points": -31,
                    "mv_change_24h": 85_000,
                }
            )
        elif sp.player.id == "roster-10":
            sp.player = sp.player.model_copy(
                update={
                    "last_name": "Tigges",
                    "lineup_prob": 3,
                    "points": 412,
                    "mv_change_24h": 205_000,
                    "mv_trend": 1,
                }
            )

    async def get_squad(_league_id):
        return squad

    client.get_squad = get_squad
    _forecast(monkeypatch, {"roster-9": 0.0, "roster-10": 0.85, "m_new": 0.75})
    mid_listing = _replacement(mv=3_000_000, pid="m_new")
    client.market = [
        mid_listing.model_copy(
            update={
                "player": mid_listing.player.model_copy(
                    update={
                        "position": Position.MIDFIELDER,
                        "team_id": "club-mnew",
                        "lineup_prob": 1,
                    }
                )
            }
        )
    ]

    async def fake_judge_roles(targets, *args, **kwargs):
        return {
            p.id: RoleVerdict(
                player_id=p.id,
                verdict="starter" if p.id != "roster-9" else "bench",
                confidence=0.9,
                reason="Test",
            )
            for p in targets
            if p.id in {"roster-9", "roster-10", "m_new"}
        }

    monkeypatch.setattr(plan_mod, "judge_roles", fake_judge_roles)

    plan = await plan_mod.build_plan()
    pair, review = _only_swap(plan)
    amount = pair.bid.action.amount
    assert pair.faller.player_id == "roster-9"
    assert pair.bid.action.player_id == "m_new"
    assert "roster-10" in review["team_plan"]["lineup_ids"]
    assert "roster-9" not in review["team_plan"]["lineup_ids"]
    assert review["team_plan"]["technical_limits"]["midweek_swap_bridge_eur"] == 0
    assert review["team_plan"]["remaining_cash"] >= 0
    assert purchase_review_veto(
        review, player_id="m_new", amount=amount, now=datetime.now(UTC)
    ) is None
    assert team_funding_veto(
        review,
        player_id="m_new",
        amount=amount,
        budget=-1_314_036,
        open_bids=[],
        owned_player_ids={f"roster-{i}" for i in range(11)},
        now=datetime.now(UTC),
    ) is None


async def test_kb3_swap_target_is_capped_at_ten_percent_premium(monkeypatch):
    """Emil 21.10.2026 (Hendriks-Lehre): Ein KB-Stufe-3-Ziel ('Unsicher') darf im Tausch
    höchstens +10 % Aufschlag (KB3_PREMIUM_CAP) erhalten, selbst wenn der 24h-Marktwert
    stark steigt (+359k auf 7,57 M €)."""
    client = await _swap_world(
        monkeypatch, weak_mv=10_000_000, cash=2_000_000, target_mv=7_570_000
    )
    st_listing = client.market[0]
    client.market = [
        st_listing.model_copy(
            update={
                "player": st_listing.player.model_copy(
                    update={
                        "lineup_prob": 3,
                        "mv_change_24h": 359_000,
                        "mv_change_24h_observed": True,
                    }
                )
            }
        )
    ]

    plan = await plan_mod.build_plan()
    pair, review = _only_swap(plan)
    assert pair.bid.action.amount == round(7_570_000 * 1.10)
    assert review["bid_premium"]["premium_pct"] == pytest.approx(10.0)


async def test_surging_starter_shield_depends_on_next_two_opponents(monkeypatch):
    """Emil 22.10.2026: Ein steigender Starter (+205k/Tag) ist vor einem gewöhnlichen Starter
    geschützt, solange die nächsten 2 Gegner machbar sind (horizon=2 >= 0,96); stehen zwei
    schwere Brocken bevor (< 0,96), fällt der Schutz weg und er darf auf dem Peak getauscht
    werden."""
    from kickbase_agent.kickbase.models import Fixture

    specs = [(pos, 10_000_000) for pos in ELEVEN[:-2]] + [
        (4, 10_000_000),  # roster-9: gewöhnlicher Starter (+50k/Tag)
        (4, 10_000_000),  # roster-10: Tigges-Profil (+205k/Tag)
    ]
    client = await _prepare_dispo(monkeypatch, specs=specs, cash=2_000_000)
    squad = await client.get_squad("L1")
    for sp in squad:
        if sp.player.id == "roster-9":
            sp.player = sp.player.model_copy(
                update={"mv_change_24h": 50_000, "mv_change_24h_observed": True}
            )
        elif sp.player.id == "roster-10":
            sp.player = sp.player.model_copy(
                update={"mv_change_24h": 205_000, "mv_change_24h_observed": True, "mv_trend": 1}
            )

    async def get_squad(_league_id):
        return squad

    client.get_squad = get_squad
    _forecast(monkeypatch, {"roster-9": 0.45, "roster-10": 0.40, "st": 0.90})
    client.market = [_striker(10_000_000, "st")]

    # Fall A: Machbare nächste 2 Spiele (Default-Quoten -> mult_2 >= 0,96) -> roster-10 geschützt,
    # stattdessen wird der gewöhnliche Starter roster-9 getauscht.
    plan_good_sched = await plan_mod.build_plan()
    pair_good, _ = _only_swap(plan_good_sched)
    assert pair_good.faller.player_id == "roster-9"

    # Fall B: Zwei sehr schwere Gegner für Team "10" (roster-10) -> mult_2 < 0,96 -> Schutz
    # erlischt, roster-10 (niedrigere Punkte als roster-9) wird auf dem Peak getauscht.
    now = datetime.now(UTC)
    tough_fixtures = [
        Fixture(
            id="f1",
            matchday=7,
            home_team_id="10",
            away_team_id="99",
            kickoff=now + timedelta(days=3),
            is_finished=False,
            home_win_odds=6.5,
            draw_odds=4.5,
            away_win_odds=1.40,
        ),
        Fixture(
            id="f2",
            matchday=8,
            home_team_id="98",
            away_team_id="10",
            kickoff=now + timedelta(days=10),
            is_finished=False,
            home_win_odds=1.35,
            draw_odds=5.0,
            away_win_odds=8.0,
        ),
    ]

    async def get_matchdays():
        return tough_fixtures

    client.get_matchdays = get_matchdays
    plan_tough_sched = await plan_mod.build_plan()
    pair_tough, _ = _only_swap(plan_tough_sched)
    assert pair_tough.faller.player_id == "roster-10"


async def test_deficit_healing_downgrade_swap_replaces_falling_expensive_starter_to_keep_eleven(
    monkeypatch,
):
    """Emil 25.10.2026: Steht der 11er-Kader tief im Minus (-5,65 M €) und enthält keinen
    Bankspieler mehr, darf ein teurer fallender Starter (Rothe, 10,08 M € -> 9,07 M € Erlös)
    gegen einen günstigeren verifizierten Starter (Haberer, 3,54 M €) getauscht werden,
    selbst wenn dessen Punktprognose etwas niedriger liegt — damit das Konto vor Freitag
    geheilt wird, ohne den 11. Startelf-Platz (-100 Punkte Leerplatz) zu verlieren."""
    from kickbase_agent.decision.purchase_review import team_funding_veto

    # 1-5-4-1 Formation (5 Verteidiger, 4 Mittelfeld, 1 Sturm); roster-2 ist Rothe (10,08 M €)
    specs = [
        (1, 12_690_000),
        (2, 17_910_000),
        (2, 10_080_500),  # roster-2: Rothe (fallend -13k/d)
        (2, 8_717_000),
        (2, 8_553_000),
        (2, 7_146_000),
        (3, 65_020_000),
        (3, 8_399_000),
        (3, 5_919_000),
        (3, 4_609_000),
        (4, 5_808_000),
    ]
    client = await _prepare_dispo(monkeypatch, specs=specs, cash=-5_651_975)
    squad = await client.get_squad("L1")
    for sp in squad:
        if sp.player.id == "roster-2":
            sp.player = sp.player.model_copy(
                update={
                    "last_name": "Rothe",
                    "mv_change_24h": -12_951,
                    "mv_change_24h_observed": True,
                }
            )

    async def get_squad(_league_id):
        return squad

    client.get_squad = get_squad
    # Alle 11 eigenen Starter haben höhere Horizontpunkte (0.85 -> 340 Pkt) als Haberer
    # (0.64 -> 256 Pkt); nur der fallende Rothe (roster-2, 10,08 M € -> 9,07 M € Erlös)
    # darf als Konto-heilender Downgrade-Tausch das -5,65-M-€-Defizit abbauen.
    _forecast(
        monkeypatch,
        {**{f"roster-{i}": 0.85 for i in range(11)}, "haberer": 0.64},
    )
    mid_listing = _replacement(mv=3_544_301, pid="haberer")
    client.market = [
        mid_listing.model_copy(
            update={
                "player": mid_listing.player.model_copy(
                    update={
                        "last_name": "Haberer",
                        "position": Position.MIDFIELDER,
                        "team_id": "club-hab",
                        "lineup_prob": 2,
                    }
                )
            }
        )
    ]

    first = await plan_mod.build_plan()
    pair, review = _only_swap(first)
    amount = pair.bid.action.amount
    assert pair.faller.player_id == "roster-2"
    assert pair.bid.action.player_id == "haberer"
    assert "haberer" in review["team_plan"]["lineup_ids"]
    assert "roster-2" not in review["team_plan"]["lineup_ids"]
    assert purchase_review_veto(
        review, player_id="haberer", amount=amount, now=datetime.now(UTC)
    ) is None
    assert team_funding_veto(
        review,
        player_id="haberer",
        amount=amount,
        budget=-5_651_975,
        open_bids=[],
        owned_player_ids={f"roster-{i}" for i in range(11)},
        now=datetime.now(UTC),
    ) is None

    # Auch bei der nächsten Neuplanung bleibt das Downgrade-Tauschgebot voll gedeckt.
    monkeypatch.setattr(
        plan_mod,
        "load_pending_bid_commitments",
        lambda *args: [
            {
                "player_id": "haberer",
                "amount": amount,
                "team_id": "club-hab",
                "position": 3,
                "intent_id": 99,
                "status": "planned",
                "manual": False,
                "listing_type": "kickbase",
                "purchase_review": review,
            }
        ],
    )
    monkeypatch.setattr(
        plan_mod,
        "load_swap_couplings",
        lambda *args: [{"player_id": "roster-2", "for_player_id": "haberer"}],
    )
    second = await plan_mod.build_plan()
    assert second.purchase_reviews["haberer"]["selected"] is True
    assert second.team_plan["funding_recovery"]["status"] == "funded"
    assert second.team_plan["funding_recovery"]["released_pending_ids"] == []


async def test_emergency_deficit_swap_heals_friday_balance_when_all_starters_rise(monkeypatch):
    """B4: Steht das Konto im Minus (-4,5 M €) und steigen alle 11 Starter >= +60k/Tag,
    findet emergency_deficit_relief trotzdem einen Konto-heilenden Downgrade-Tausch."""
    specs = [(pos, 10_000_000) for pos in ELEVEN]
    client = await _prepare_dispo(monkeypatch, specs=specs, cash=-4_500_000)
    squad = await client.get_squad("L1")
    for sp in squad:
        sp.player = sp.player.model_copy(
            update={"mv_change_24h": 60_000, "mv_change_24h_observed": True}
        )

    async def get_squad(_league_id):
        return squad

    client.get_squad = get_squad
    _forecast(
        monkeypatch,
        {**{f"roster-{i}": 0.80 for i in range(11)}, "anchor": 0.65},
    )
    mid_listing = _replacement(mv=3_500_000, pid="anchor")
    client.market = [
        mid_listing.model_copy(
            update={
                "player": mid_listing.player.model_copy(
                    update={
                        "last_name": "Anchor",
                        "position": Position.MIDFIELDER,
                        "team_id": "club-anc",
                        "lineup_prob": 2,
                    }
                )
            }
        )
    ]

    plan = await plan_mod.build_plan()
    pair, review = _only_swap(plan)
    assert pair.bid.action.player_id == "anchor"
    assert purchase_review_veto(
        review, player_id="anchor", amount=pair.bid.action.amount, now=datetime.now(UTC)
    ) is None


async def test_manager_listing_swap_and_friday_deficit_filler_keep_eleven_slots(monkeypatch):
    """Emil 30.10.2026: (1) Manager-Listings sind auch über _plan_overdraft_swap als
    gekoppelter Tausch bis 101,5 % MW wählbar, wenn das freie Planungsgeld allein nicht
    reicht. (2) Steht das Konto am Freitag (< 24 h bis Anpfiff, vor T-120) im Minus (-2,35 M €)
    und gibt es keinen günstigen Starter am Markt, tauscht die Engine einen fallenden
    Spieler (Ullrich 6,43 M €) gegen einen günstigen fitten Mitspieler-Feldspieler
    (Skov 500k €) und hält das Paar beim nächsten Lauf stabil in der 11er-Elf."""
    now = datetime.now(UTC)
    specs = [(pos, 10_000_000) for pos in ELEVEN]
    specs[2] = (Position.DEFENDER, 6_431_357)  # Ullrich: 90 % = 5.788.221 €
    client = await _prepare_dispo(monkeypatch, specs=specs, cash=-2_352_975)
    monkeypatch.setattr(
        plan_mod, "matchday_deadline", lambda fixtures, cur: (8, cur + timedelta(hours=6))
    )
    squad = await client.get_squad("L1")
    for idx, sp in enumerate(squad):
        sp.player = sp.player.model_copy(
            update={
                "mv_change_24h": -104_370 if idx == 2 else 90_000,
                "mv_change_24h_observed": True,
            }
        )

    async def get_squad(_league_id):
        return squad

    client.get_squad = get_squad
    _forecast(
        monkeypatch,
        {**{f"roster-{i}": 0.80 for i in range(11)}, "roster-2": 0.65, "skov": 0.0},
    )
    skov_listing = _replacement(mv=500_000, pid="skov").model_copy(
        update={
            "seller_id": "2464959",
            "price": 500_000,
            "expires_at": now - timedelta(hours=6),
            "player": _replacement(mv=500_000, pid="skov").player.model_copy(
                update={
                    "last_name": "Skov",
                    "position": Position.MIDFIELDER,
                    "team_id": "club-fcub",
                    "lineup_prob": 5,
                    "fitness_status": 2,
                }
            ),
        }
    )
    client.market = [skov_listing]

    plan = await plan_mod.build_plan()
    pair, review = _only_swap(plan)
    assert pair.bid.action.player_id == "skov"
    assert pair.faller.player_id == "roster-2"
    assert pair.bid.action.amount == 500_000
    assert review["listing_type"] == "manager"
    assert review["seller_id"] == "2464959"
    assert review["swap_sale"]["player_id"] == "roster-2"
    assert purchase_review_veto(
        review, player_id="skov", amount=500_000, now=datetime.now(UTC)
    ) is None

    # Zweiter Lauf (Replanning mit aktivem Vorhaben): Skov bleibt als 11. Mann gesetzt.
    monkeypatch.setattr(
        plan_mod,
        "load_pending_bid_commitments",
        lambda *args: [
            {
                "player_id": "skov",
                "amount": 500_000,
                "team_id": "club-fcub",
                "position": 3,
                "intent_id": 361,
                "status": "planned",
                "manual": False,
                "listing_type": "manager",
                "purchase_review": review,
            }
        ],
    )
    monkeypatch.setattr(
        plan_mod,
        "load_swap_couplings",
        lambda *args: [{"player_id": "roster-2", "for_player_id": "skov"}],
    )
    second = await plan_mod.build_plan()
    assert second.purchase_reviews["skov"]["selected"] is True
    assert second.team_plan["filled_slots"] == 11
    assert second.team_plan["funding_recovery"]["status"] == "funded"


async def test_unexecuted_planned_swap_upgrades_when_stronger_target_appears(monkeypatch):
    """Emil 02.11.2026: Solange ein geplantes Dispo-Tauschgebot noch nicht bei Kickbase
    abgegeben wurde (`status == 'planned'`, nicht manuell), ersetzt `_plan_overdraft_swap`
    es durch ein neu gelistetes Ziel, das mindestens +15 Horizontpunkte mehr bringt."""
    specs = [(pos, 10_000_000) for pos in ELEVEN]
    specs[2] = (Position.DEFENDER, 9_730_000)
    client = await _prepare_dispo(monkeypatch, specs=specs, cash=1_050_000)
    _forecast(
        monkeypatch,
        {
            **{f"roster-{i}": 0.80 for i in range(11)},
            "roster-2": 0.35,
            "sirch": 0.50,
            "onyedika": 0.95,
        },
    )
    sirch_listing = _replacement(mv=3_580_000, pid="sirch").model_copy(
        update={
            "player": _replacement(mv=3_580_000, pid="sirch").player.model_copy(
                update={
                    "last_name": "Sirch",
                    "position": Position.DEFENDER,
                    "team_id": "club-sir",
                    "lineup_prob": 2,
                }
            )
        }
    )
    client.market = [sirch_listing]

    first = await plan_mod.build_plan()
    first_pair, first_review = _only_swap(first)
    assert first_pair.bid.action.player_id == "sirch"
    assert first_pair.faller.player_id == "roster-2"

    # Zweiter Lauf: Sirch steht als geplant (noch nicht live), Onyedika erscheint am Markt.
    onyedika_listing = _replacement(mv=9_500_000, pid="onyedika").model_copy(
        update={
            "player": _replacement(mv=9_500_000, pid="onyedika").player.model_copy(
                update={
                    "last_name": "Onyedika",
                    "position": Position.DEFENDER,
                    "team_id": "club-ony",
                    "lineup_prob": 1,
                }
            )
        }
    )
    client.market = [sirch_listing, onyedika_listing]
    monkeypatch.setattr(
        plan_mod,
        "load_pending_bid_commitments",
        lambda *args: [
            {
                "player_id": "sirch",
                "amount": first_pair.bid.action.amount,
                "team_id": "club-sir",
                "position": 2,
                "intent_id": 368,
                "status": "planned",
                "manual": False,
                "listing_type": "kickbase",
                "purchase_review": first_review,
            }
        ],
    )
    monkeypatch.setattr(
        plan_mod,
        "load_swap_couplings",
        lambda *args: [{"player_id": "roster-2", "for_player_id": "sirch"}],
    )

    second = await plan_mod.build_plan()
    upgraded_pair, upgraded_review = _only_swap(second)
    assert upgraded_pair.bid.action.player_id == "onyedika"
    assert upgraded_pair.faller.player_id == "roster-2"
    assert second.purchase_reviews["sirch"]["selected"] is False
    assert any(
        "abgelöst" in r for r in second.purchase_reviews["sirch"]["reasons"]
    )
    assert purchase_review_veto(
        upgraded_review,
        player_id="onyedika",
        amount=upgraded_pair.bid.action.amount,
        now=datetime.now(UTC),
    ) is None


async def test_midweek_deficit_within_bridge_does_not_force_downgrade_swap(monkeypatch):
    """Emil 02.11.2026: Am Wochenanfang (> 48 h bis Anpfiff) wird ein kleines Minus
    innerhalb der 1,5-M-€-Midweek-Brücke nicht durch einen punkteschwächeren
    Downgrade-Tausch (`gain <= 0`) abgebaut."""
    specs = [(pos, 10_000_000) for pos in ELEVEN]
    client = await _prepare_dispo(monkeypatch, specs=specs, cash=-1_250_000)
    monkeypatch.setattr(
        plan_mod, "matchday_deadline", lambda fixtures, cur: (9, cur + timedelta(days=4))
    )
    _forecast(
        monkeypatch,
        {**{f"roster-{i}": 0.80 for i in range(11)}, "downgrade": 0.60},
    )
    downgrade_listing = _replacement(mv=3_500_000, pid="downgrade").model_copy(
        update={
            "player": _replacement(mv=3_500_000, pid="downgrade").player.model_copy(
                update={
                    "last_name": "Downgrade",
                    "position": Position.MIDFIELDER,
                    "team_id": "club-dwn",
                    "lineup_prob": 2,
                }
            )
        }
    )
    client.market = [downgrade_listing]

    plan = await plan_mod.build_plan()
    assert plan.swaps == []
    assert list(plan.team_plan["purchase_ids"]) == []


async def test_held_trade_player_cover_keeps_swap_planned_and_replannable(monkeypatch):
    """Ein gehaltener Bank-Handelsspieler (z. B. Sirch 3,86 M €), der das Rohkonto ins Minus
    drückt (-2,09 M €), dessen Verkaufserlös (+3,48 M €) das Nicht-Handels-Konto aber positiv
    hält (+1,38 M €), darf weder die Erstplanung noch die Neuplanung eines Dispo-Tauschs
    blockieren."""
    from kickbase_agent.trading.trade_book import TradeBook, TradePosition

    specs = [(pos, 13_000_000) for pos in ELEVEN] + [(2, 3_860_000)]
    await _swap_world(monkeypatch, specs=specs, cash=-2_090_000, target_mv=12_000_000)
    trade_book = TradeBook(
        positions={
            "roster-11": TradePosition(
                "roster-11",
                3_801_000,
                datetime.now(UTC) - timedelta(hours=12),
                1,
                1,
            )
        },
        live_bids={},
        pending={},
        firm={},
    )
    monkeypatch.setattr(plan_mod, "load_trade_book", lambda *args, **kwargs: trade_book)

    first = await plan_mod.build_plan()
    pair, review = _only_swap(first)
    assert pair.faller.player_id == WEAK
    assert pair.bid.action.player_id == "st"

    # Neuplanung mit bestehender Kopplung darf das Gebot nicht wegen Rohkonto < 0 freigeben.
    amount = pair.bid.action.amount
    monkeypatch.setattr(
        plan_mod, "load_pending_bid_commitments", lambda *args: _pending_swap_bid(review, amount)
    )
    monkeypatch.setattr(
        plan_mod,
        "load_swap_couplings",
        lambda *args: [{"player_id": WEAK, "for_player_id": "st"}],
    )

    second = await plan_mod.build_plan()
    again = second.purchase_reviews["st"]
    assert again["selected"] is True
    assert second.team_plan["funding_recovery"]["released_pending_ids"] == []




