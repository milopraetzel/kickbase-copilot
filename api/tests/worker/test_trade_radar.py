"""Handels-Radar (worker/trade_radar.py, Spec docs/superpowers/specs/2026-10-01-handels-radar.md).

Emil 01.10.2026: Pause nutzen, steigende Spieler kaufen und mit Gewinn verkaufen — der Radar
liest nur: Kaufkandidaten (Kickbase-Auktionen mit laufender Welle) mit Höchstgebot aus den
eigenen Daten, Handelsspieler mit Verkaufssignal und die Konto-Frist bis zum Anpfiff.
Handels-Autopilot (Emil 01.10.2026): Handelsspieler kommen aus dem Handelsbuch (jüngstes
echtes Gebot vom Autopiloten), jede Haltezeit endet am Verkaufstermin (T−96 h bei offener
Elf, sonst T−50 h), gezählt wird je Spieler.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from kickbase_agent.db.models import Base, Intent, PlayerHistory, Report, TransferAction
from kickbase_agent.intel import role_judge
from kickbase_agent.intel.role_judge import RoleVerdict
from kickbase_agent.kickbase.models import (
    Fixture,
    MarketPlayer,
    MarketValueSnapshot,
    Player,
    Position,
    SquadPlayer,
)
from kickbase_agent.news.models import NewsArticle
from kickbase_agent.trading.trade_book import TradePosition
from kickbase_agent.worker import trade_radar as tr
from kickbase_agent.worker.trade_radar import (
    ClassStats,
    Listing,
    Momentum,
    OwnPlayer,
    account_summary,
    buy_candidates,
    class_stats,
    daily_values,
    format_trade_radar,
    max_premium,
    momentum_at,
    positions,
    price_class,
    summary_lines,
    updates_between,
)

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 1, 6, 12, tzinfo=UTC)  # 08:12 Berlin
TODAY = date(2026, 9, 30)  # jüngster Tageswert vor dem Lauf (Update 30.09. 22:00)
DEADLINE = datetime(2026, 10, 9, 18, 30, tzinfo=UTC)  # Fr 09.10. 20:30 Berlin
#: Ein Verkaufstermin für die reinen Funktionen (Do 21:30, vor dem Update um 22:00).
SALE_BY = datetime(2026, 10, 8, 21, 30, tzinfo=BERLIN)
#: Verkaufstermin des Jobs bei offener Elf: Frist − 96 h = Mo 05.10. 20:30 Berlin.
SALE_BY_GAP = DEADLINE - timedelta(hours=96)


# ------------------------------------------------------------------ Bausteine


def test_daily_values_keep_the_latest_value_per_day():
    points = [(datetime(2026, 9, 30, 0, 0, tzinfo=UTC), 100),
              (datetime(2026, 9, 30, 20, 5, tzinfo=UTC), 110),
              (datetime(2026, 9, 29, 0, 0, tzinfo=UTC), 90)]
    assert daily_values(points) == {date(2026, 9, 30): 110, date(2026, 9, 29): 90}


def test_momentum_compares_with_one_three_and_seven_days_before():
    values = {TODAY: 120, TODAY - timedelta(1): 115, TODAY - timedelta(3): 100,
              TODAY - timedelta(7): 80}
    m = momentum_at(values, TODAY)
    assert m.mv == 120
    assert m.r1 == pytest.approx(120 / 115 - 1)
    assert m.r3 == pytest.approx(0.20) and m.r7 == pytest.approx(0.50)


def test_missing_days_are_unknown_not_zero():
    m = momentum_at({TODAY: 120, TODAY - timedelta(3): 100}, TODAY)
    assert m.r1 is None and m.r7 is None and m.r3 == pytest.approx(0.2)
    assert momentum_at({TODAY - timedelta(3): 100}, TODAY) is None


@pytest.mark.parametrize(
    ("mv", "cls"),
    [(499_999, None), (500_000, "billig"), (4_999_999, "billig"), (5_000_000, "mittel"),
     (18_500_000, "mittel"), (18_500_001, None)],
)
def test_price_classes(mv, cls):
    assert price_class(mv) == cls


def _rising(start: int, daily: float, days: int = 25) -> dict[date, int]:
    """Gleichmäßig steigender Tageswert bis TODAY."""
    return {TODAY - timedelta(d): round(start * (1 + daily) ** (days - d)) for d in range(days + 1)}


def test_class_stats_measure_seven_day_rises_after_a_three_day_rise():
    history = {f"b{i}": _rising(2_000_000, 0.05) for i in range(20)}  # billig, +5 %/Tag
    history |= {f"m{i}": _rising(8_000_000, 0.02) for i in range(20)}  # mittel, +2 %/Tag
    stats = class_stats(history, TODAY)
    assert (stats["billig"].n, stats["mittel"].n) == (20, 20)  # Spieler, nicht Spieler-Tage
    assert stats["billig"].median_f7 == pytest.approx(1.05 ** 7 - 1, rel=1e-3)
    assert stats["mittel"].median_f7 == pytest.approx(1.02 ** 7 - 1, rel=1e-3)


def test_class_stats_know_every_horizon_up_to_seven_days():
    stats = class_stats({f"b{i}": _rising(2_000_000, 0.05) for i in range(20)}, TODAY)
    assert stats["billig"].at(1) == (pytest.approx(0.05, rel=1e-3), 20)
    assert stats["billig"].at(3)[0] == pytest.approx(1.05 ** 3 - 1, rel=1e-3)
    assert stats["billig"].at(9) == stats["billig"].at(7)  # mehr als 7 Tage misst niemand


def test_evidence_counts_players_not_overlapping_days():
    """Review 01.10.: Zwei Steiger mit je acht Signal-Tagen waren „16 Fälle" und gaben den
    vollen Aufschlag frei. Ein Spieler ist ein Beleg."""
    stats = class_stats({"a": _rising(2_000_000, 0.05), "b": _rising(2_000_000, 0.05)}, TODAY)
    assert stats["billig"].n == 2
    assert max_premium(stats["billig"], "billig") == (tr.FALLBACK_PREMIUM, "zu wenig Daten")


def test_class_stats_ignore_values_after_the_reference_day():
    """Stichtag vor dem Datenende (Rückblick, Nachrechnen): Werte nach dem Stichtag sind
    Zukunft und zählen nie — auch wenn sie in der Historie stehen."""
    history = {"x": {TODAY - timedelta(d): 1_000_000 + 100_000 * (10 - d) for d in range(11)}}
    assert class_stats(history, TODAY - timedelta(3), lookback=14)["billig"].n == 0


def test_class_stats_never_use_the_future():
    """Nur Startpunkte, deren 7-Tage-Wert schon bekannt ist (t + 7 ≤ heute)."""
    history = {"x": {TODAY - timedelta(d): 1_000_000 + 100_000 * (10 - d) for d in range(11)}}
    stats = class_stats(history, TODAY, lookback=14)
    # Startpunkte t ≤ TODAY − 7 mit bekanntem t − 3: nur TODAY − 7 → ein Spieler.
    assert stats["billig"].n == 1


@pytest.mark.parametrize(
    ("stats", "cls", "expected"),
    [
        (ClassStats(median_f7=0.87, n=83), "billig", 0.30),  # Deckel billig
        (ClassStats(median_f7=0.134, n=67), "mittel", 0.067),  # Hälfte des Medians
        (ClassStats(median_f7=0.40, n=67), "mittel", 0.20),  # Hälfte, unter dem Deckel
        (ClassStats(median_f7=0.80, n=67), "mittel", 0.30),  # Deckel 30 % (130-%-Stufe)
        (ClassStats(median_f7=0.50, n=14), "billig", tr.FALLBACK_PREMIUM),  # zu wenig Daten
        (ClassStats(median_f7=0.50, n=15), "billig", 0.25),  # genau genug Spieler
        (ClassStats(median_f7=0.0, n=40), "mittel", None),  # kein Anstieg ist kein Anstieg
        (None, "billig", tr.FALLBACK_PREMIUM),
        (ClassStats(median_f7=-0.02, n=40), "mittel", None),  # Welle trägt nicht
    ],
)
def test_max_premium(stats, cls, expected):
    premium, _ = max_premium(stats, cls)
    assert premium == (pytest.approx(expected) if expected is not None else None)


def test_max_premium_for_a_shorter_horizon_uses_that_horizon():
    stats = ClassStats(median_f7=0.87, n=30, by_days={1: (0.04, 30), 2: (-0.01, 30)})
    assert max_premium(stats, "billig", days=1)[0] == pytest.approx(0.02)
    assert max_premium(stats, "billig", days=2)[0] is None  # nach zwei Updates im Minus
    assert max_premium(stats, "billig", days=3) == (tr.FALLBACK_PREMIUM, "zu wenig Daten")


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        (datetime(2026, 10, 1, 10, 0, tzinfo=BERLIN), 7),  # 01.–07.10. je 22:00
        (datetime(2026, 10, 7, 15, 0, tzinfo=BERLIN), 1),  # nur Mi 22:00
        (datetime(2026, 10, 7, 22, 30, tzinfo=BERLIN), 0),  # nach dem letzten Update
        # Endet die Auktion genau um 22:00, zählt dieses Update nicht (vorsichtig: lieber ein
        # Update zu wenig als ein zu hohes Gebot).
        (datetime(2026, 10, 7, 22, 0, tzinfo=BERLIN), 0),
        (datetime(2026, 10, 8, 23, 0, tzinfo=BERLIN), 0),  # nach dem Verkaufstermin
    ],
)
def test_updates_between_counts_the_22_oclock_updates_before_the_sale(start, expected):
    assert updates_between(start, SALE_BY) == expected


def _listing(pid: str, mv: int, *, seller: str = "", name: str | None = None,
             expires: datetime | None = None, lineup_prob: int | None = 3,
             fitness: int | None = 0) -> Listing:
    return Listing(player_id=pid, name=name or f"Spieler {pid}", team="Team", position=2, mv=mv,
                   price=mv, expires_at=expires or NOW + timedelta(hours=10), seller_id=seller,
                   lineup_prob=lineup_prob, fitness_status=fitness)


STATS = {"billig": ClassStats(median_f7=0.87, n=83), "mittel": ClassStats(median_f7=0.134, n=67)}
WAVE = Momentum(mv=3_000_000, r1=0.03, r3=0.12, r7=0.40)


def test_buy_candidates_only_kickbase_auctions_with_a_running_wave():
    listings = [
        _listing("ok", 3_000_000),
        _listing("manager", 3_000_000, seller="rival"),
        _listing("own", 3_000_000),
        _listing("flat", 3_000_000),
        _listing("turned", 3_000_000),
        _listing("big", 20_000_000),
        _listing("nohistory", 3_000_000),
        _listing("soon", 3_000_000, expires=NOW + timedelta(minutes=20)),  # endet gleich
    ]
    momentum = {
        "ok": WAVE, "manager": WAVE, "own": WAVE, "soon": WAVE,
        "flat": Momentum(mv=3_000_000, r1=0.01, r3=0.04, r7=0.05),
        "turned": Momentum(mv=3_000_000, r1=-0.01, r3=0.12, r7=0.40),  # Welle gebrochen
        "big": Momentum(mv=20_000_000, r1=0.03, r3=0.12, r7=0.40),
    }
    rows, _ = buy_candidates(listings, momentum, STATS, own_ids={"own"}, jev_returns=set(),
                             now=NOW)
    assert [r.player_id for r in rows] == ["ok"]
    [row] = rows
    assert row.premium == pytest.approx(0.30)
    assert row.max_bid == 3_900_000  # MW × 1,30, auf 1.000 € aufgerundet
    assert row.expected == pytest.approx(0.87) and row.evidence_n == 83
    assert (row.horizon_days, row.forced_sale) == (7, False)


def _mittel_history(start: int, daily: float, count: int, prefix: str) -> dict[str, dict]:
    """Steigende Werte, die im ganzen Fenster in der mittleren Klasse (5–15 M) bleiben."""
    return {f"{prefix}{i}": _rising(start, daily) for i in range(count)}


def test_bucket_stats_split_the_class_by_the_three_day_rise():
    """Emil 01.10.2026: „ein Spieler der 12% steigt muss noch höher angesetzt werden“ — die
    Erwartung hängt an der Stärke der Welle, nicht nur an der Preisklasse."""
    history = (_mittel_history(8_000_000, 0.02, 20, "slow")
               | _mittel_history(3_260_000, 0.06, 20, "fast"))
    buckets = tr.bucket_stats(history, TODAY)
    slow, fast = buckets[("mittel", "5-10")], buckets[("mittel", ">=15")]
    assert (slow.n, fast.n) == (20, 20)
    assert slow.median_f7 == pytest.approx(1.02 ** 7 - 1, rel=1e-3)
    assert fast.median_f7 == pytest.approx(1.06 ** 7 - 1, rel=1e-3)
    assert tr.momentum_bucket(0.06) == "5-10" and tr.momentum_bucket(0.12) == "10-15"
    assert tr.momentum_bucket(0.15) == ">=15"


def test_a_strong_riser_is_bid_from_its_own_bucket():
    stats = {"mittel": ClassStats(median_f7=0.07, n=100)}
    buckets = {("mittel", ">=15"): ClassStats(median_f7=0.226, n=35)}
    strong = Momentum(mv=8_000_000, r1=0.04, r3=0.18, r7=0.30)
    rows, _ = buy_candidates([_listing("s", 8_000_000)], {"s": strong}, stats, own_ids=set(),
                             jev_returns=set(), buckets=buckets)
    [row] = rows
    assert row.premium == pytest.approx(0.113) and row.basis == "Stufe ≥ 15 %"
    assert row.max_bid == 8_904_000


def test_a_thin_bucket_falls_back_to_the_class():
    stats = {"mittel": ClassStats(median_f7=0.07, n=100)}
    buckets = {("mittel", "10-15"): ClassStats(median_f7=0.40, n=9)}  # zu wenige Spieler
    m = Momentum(mv=8_000_000, r1=0.02, r3=0.12, r7=0.2)
    rows, _ = buy_candidates([_listing("t", 8_000_000)], {"t": m}, stats, own_ids=set(),
                             jev_returns=set(), buckets=buckets)
    assert rows[0].premium == pytest.approx(0.035) and rows[0].basis == "Klasse"


def test_unfit_or_bench_players_are_no_candidates():
    """Die Schreibstelle lehnt Handelsgebote auf nicht fitte Spieler und KB-Stufe 4/5 ab —
    der Radar schlägt sie gar nicht erst vor."""
    listings = [_listing("fit", 3_000_000), _listing("hurt", 3_000_000, fitness=2),
                _listing("bench", 3_000_000, lineup_prob=4),
                _listing("unknown", 3_000_000, lineup_prob=None)]
    rows, _ = buy_candidates(listings, {pid: WAVE for pid in ("fit", "hurt", "bench", "unknown")},
                             STATS, own_ids=set(), jev_returns=set())
    assert [r.player_id for r in rows] == ["fit"]


def test_a_flat_last_day_is_not_a_running_wave():
    rows, _ = buy_candidates([_listing("flat1", 3_000_000)],
                             {"flat1": Momentum(mv=3_000_000, r1=0.0, r3=0.12, r7=0.4)},
                             STATS, own_ids=set(), jev_returns=set())
    assert rows == []


def test_a_jev_return_signal_moves_a_candidate_to_the_front():
    listings = [_listing("a", 3_000_000), _listing("b", 6_000_000)]
    momentum = {"a": Momentum(mv=3_000_000, r1=0.03, r3=0.20, r7=0.5),
                "b": Momentum(mv=6_000_000, r1=0.01, r3=0.06, r7=0.1)}
    rows, _ = buy_candidates(listings, momentum, STATS, own_ids=set(), jev_returns={"b"})
    assert [r.player_id for r in rows] == ["b", "a"]
    assert rows[0].jev_return is True


def test_max_bid_rounds_up_to_whole_thousands():
    rows, _ = buy_candidates([_listing("m", 6_123_456)],
                             {"m": Momentum(mv=6_123_456, r1=0.01, r3=0.06, r7=0.1)},
                             STATS, own_ids=set(), jev_returns=set())
    assert rows[0].max_bid == 6_534_000  # 6.123.456 × 1,067 = 6.533.727,6 → 6.534.000


def test_the_holding_period_ends_at_the_sale_deadline():
    """Review 01.10.: Mi 07.10. 15:00 gekauft, Verkaufstermin Do 21:30 — es bleibt EIN Update.
    Dann zählt der 1-Tage-Median, nicht der 7-Tage-Median — unabhängig vom Konto."""
    stats = {"billig": ClassStats(median_f7=0.87, n=30, by_days={1: (0.06, 30)})}
    late = _listing("late", 3_000_000, expires=datetime(2026, 10, 7, 15, 0, tzinfo=BERLIN))
    rows, skipped = buy_candidates([late], {"late": WAVE}, stats, own_ids=set(),
                                   jev_returns=set(), sale_by=SALE_BY)
    [row] = rows
    assert (row.horizon_days, row.forced_sale, row.sell_by) == (1, True, SALE_BY)
    assert row.premium == pytest.approx(0.03) and row.max_bid == 3_090_000
    assert row.expected == pytest.approx(0.06) and skipped == {}


def test_a_planned_trade_bid_stays_a_candidate_past_the_auction_cutoff():
    """Codex-Review 02.10.2026: Ein schon geplantes Handelsgebot auf eine Auktion, die in
    weniger als 30 min endet, bleibt im Radar — sonst räumte der Autopilot es Minuten vor der
    Ausführung, oder er hielte es ohne Jev-, Fitness- und Rahmenprüfung fest."""
    soon = _listing("soon", 3_000_000, expires=NOW + timedelta(minutes=20))
    rows, _ = buy_candidates([soon], {"soon": WAVE}, STATS, own_ids=set(), jev_returns=set(),
                             now=NOW)
    assert rows == []
    rows, _ = buy_candidates([soon], {"soon": WAVE}, STATS, own_ids=set(), jev_returns=set(),
                             now=NOW, planned={"soon"})
    assert [r.player_id for r in rows] == ["soon"]
    hurt = _listing("soon", 3_000_000, expires=NOW + timedelta(minutes=20), fitness=1)
    rows, _ = buy_candidates([hurt], {"soon": WAVE}, STATS, own_ids=set(), jev_returns=set(),
                             now=NOW, planned={"soon"})
    assert rows == []  # alle anderen Prüfungen gelten weiter


def test_without_an_update_before_the_sale_deadline_there_is_no_candidate():
    after = _listing("after", 3_000_000, expires=datetime(2026, 10, 7, 23, 0, tzinfo=BERLIN))
    rows, skipped = buy_candidates([after], {"after": WAVE}, STATS, own_ids=set(),
                                   jev_returns=set(), sale_by=SALE_BY)
    assert rows == [] and skipped == {"keine_zeit": 1}


def test_a_far_sale_deadline_or_none_keeps_seven_days():
    early = _listing("early", 3_000_000, expires=datetime(2026, 9, 30, 15, 0, tzinfo=BERLIN))
    far, _ = buy_candidates([early], {"early": WAVE}, STATS, own_ids=set(), jev_returns=set(),
                            sale_by=SALE_BY)
    assert (far[0].horizon_days, far[0].forced_sale) == (7, False)
    none, _ = buy_candidates([early], {"early": WAVE}, STATS, own_ids=set(), jev_returns=set())
    assert (none[0].horizon_days, none[0].sell_by) == (7, None)


# ------------------------------------------------------------------ Handelsspieler


def _own(pid: str, mv: int, last_step: int | None, *, in_lineup: bool = False) -> OwnPlayer:
    return OwnPlayer(player_id=pid, name=f"Eigener {pid}", mv=mv, last_step=last_step,
                     in_lineup=in_lineup)


def _book(**prices: int) -> dict[str, TradePosition]:
    """Handelsbuch für die reinen Funktionen: Spieler → Kaufpreis (gekauft gestern)."""
    return {pid: TradePosition(pid, price, NOW - timedelta(days=1), 1, 1)
            for pid, price in prices.items()}


def test_positions_signal_sell_after_a_falling_update_and_hold_otherwise():
    squad = [_own("up", 4_000_000, 120_000), _own("down", 3_500_000, -80_000),
             _own("open", 3_000_000, None), _own("core", 60_000_000, -10_000),
             _own("zero", 2_000_000, 0)]
    buys = _book(up=3_600_000, down=3_800_000, open=2_900_000, zero=2_100_000)
    ordered = positions(squad, buys, NOW, SALE_BY)
    # Verkaufen zuerst, dann nach Gewinn/Verlust aufsteigend (das größte Minus oben).
    assert [r.player_id for r in ordered] == ["down", "zero", "open", "up"]
    rows = {r.player_id: r for r in ordered}
    assert rows["zero"].signal == "halten"  # unverändert ist nicht gefallen
    assert (rows["up"].signal, rows["up"].pnl) == ("halten", 400_000)
    assert (rows["down"].signal, rows["down"].pnl) == ("verkaufen", -300_000)
    # Ein unbekannter letzter Schritt ist kein Fall — und die Zeile sagt das.
    assert rows["open"].signal == "halten" and rows["open"].last_step is None
    text = format_trade_radar([], list(rows.values()),
                              account_summary(budget=0, rows=list(rows.values())),
                              deadline=DEADLINE, stats=STATS, now=NOW)
    assert "• Halten: Eigener open" in text and "letztes Update unbekannt" in text
    assert "• Halten: Eigener up" in text and "MW stieg beim letzten Update" in text
    assert "MW unverändert beim letzten Update" in text


ON_THE_DAY = datetime(2026, 10, 8, 6, 12, tzinfo=UTC)  # Do 08.10. 08:12, Verkauf bis 21:30


def test_on_the_sale_day_every_trade_player_is_sold_whatever_the_account():
    """Handelsspieler stehen nie in der Elf (Emil 01.10.2026: „sie bleiben Bank“) — am
    Verkaufstermin gehen deshalb alle, nicht nur so viele, wie das Konto braucht."""
    squad = [_own("fall", 4_000_000, -50_000), _own("rise", 5_000_000, 90_000),
             _own("small", 1_000_000, 20_000), _own("core", 9_000_000, 10_000)]
    rows = {r.player_id: r.signal for r in positions(
        squad, _book(fall=3_000_000, rise=3_000_000, small=900_000), ON_THE_DAY, SALE_BY)}
    assert rows == {"fall": "verkaufen (Frist)", "rise": "verkaufen (Frist)",
                    "small": "verkaufen (Frist)"}  # „core“ gehört nicht zum Handel


def test_no_forced_sale_before_the_sale_day():
    rows = positions([_own("up", 4_000_000, 120_000)], _book(up=3_600_000), NOW, SALE_BY)
    assert rows[0].signal == "halten"
    assert positions([_own("up", 4_000_000, 120_000)], _book(up=3_600_000), ON_THE_DAY,
                     None)[0].signal == "halten"  # ohne Frist kein Termin


def test_a_forced_sale_names_its_time_warns_about_a_lineup_hole_and_says_now_when_late():
    rows = positions([_own("elf", 4_000_000, 10_000, in_lineup=True)], _book(elf=3_600_000),
                     ON_THE_DAY, SALE_BY)
    text = format_trade_radar([], rows, account_summary(budget=-1_000_000, rows=rows),
                              deadline=DEADLINE, stats=STATS, now=ON_THE_DAY, sale_by=SALE_BY)
    assert "Verkaufen (Frist): Eigener elf" in text and "spätestens Do 08.10. 21:30" in text
    assert "Verkaufstermin aller Handelsspieler: spätestens Do 08.10. 21:30" in text
    assert "Elf" in text and "100" in text  # von Hand aufgestellt: Ersatz, sonst −100
    on_deadline = datetime(2026, 10, 9, 6, 12, tzinfo=UTC)
    late = positions([_own("elf", 4_000_000, 10_000)], _book(elf=3_600_000), on_deadline,
                     SALE_BY)
    late_text = format_trade_radar([], late, account_summary(budget=-1_000_000, rows=late),
                                   deadline=DEADLINE, stats=STATS, now=on_deadline,
                                   sale_by=SALE_BY)
    assert "sofort" in late_text and "Verkaufen (Frist): Eigener elf · gekauft" in late_text


def test_account_summary_shows_what_selling_everything_brings_back():
    rows = positions([_own("up", 4_000_000, 120_000)], _book(up=3_600_000), NOW, SALE_BY)
    acc = account_summary(budget=-1_200_000, rows=rows)
    assert acc["after_sale"] == 2_800_000 and acc["open_bids"] == 0
    # Offene eigene Gebote zählen, als gewännen sie alle (Konto ≥ 0 beim Anpfiff).
    with_bids = account_summary(budget=-1_200_000, rows=rows, open_bids=2_500_000)
    assert (with_bids["after_sale"], with_bids["open_bids"]) == (300_000, 2_500_000)
    assert account_summary(budget=-1_200_000, rows=rows, open_bids=2_900_000)["covered"] is False
    assert acc["exposure"] == 3_600_000 and acc["room"] == tr.EXPOSURE_CAP - 3_600_000
    assert tr.EXPOSURE_CAP == 50_000_000  # Emil 03.11.2026: „bis 50 M ins Minus“
    assert acc["covered"] is True
    assert account_summary(budget=-5_000_000, rows=rows)["covered"] is False
    assert account_summary(budget=-4_000_000, rows=rows)["covered"] is True  # Konto ≥ 0 reicht
    # Handelsgebote (live und geplant) belegen den Handelsrahmen schon jetzt.
    assert account_summary(budget=0, rows=rows, trade_bids=4_000_000)["exposure"] == 7_600_000
    # Gekoppelter Dispo-Tausch-Verkauf deckt ein laufendes Engine-Tauschgebot am Freitag mit ab.
    swap_acc = account_summary(
        budget=1_320_297,
        rows=positions([_own("gouweleeuw", 8_517_716, 587_716)], _book(gouweleeuw=7_930_000),
                       NOW, SALE_BY),
        open_bids=12_000_000,
        swap_cover=4_188_604,
    )
    assert swap_acc["covered"] is True and swap_acc["after_sale"] == 2_026_617


# ------------------------------------------------------------------ Texte


def test_the_report_names_bid_limits_signals_account_and_the_rule():
    buys, _ = buy_candidates([_listing("ok", 3_000_000, name="Bilal Nadir")], {"ok": WAVE},
                             STATS, own_ids=set(), jev_returns={"ok"})
    pos = positions([_own("down", 3_500_000, -80_000)], _book(down=3_800_000), NOW, SALE_BY)
    text = format_trade_radar(buys, pos,
                              account_summary(budget=2_376_645, rows=pos, open_bids=1_000_000),
                              deadline=DEADLINE, stats=STATS, now=NOW,
                              notes=["Verlauf: 1 von Kickbase, 0 aus der DB"])
    assert "offene Gebote 1,00 M" in text
    assert "Handels-Radar" in text and "• Kandidat: Bilal Nadir" in text
    assert "höchstens 3,90 M" in text and "Rückkehr laut kicker" in text
    assert "• Verkaufen: Eigener down" in text and "der Autopilot verkauft vor 22:00" in text
    assert "Fr 09.10. 20:30" in text  # Konto-Frist
    assert "Verlauf: 1 von Kickbase" in text
    assert "≥ +5 %" in text and "T−96 h" in text  # Regel aus den Konstanten
    assert "Handels-Autopilot bietet und verkauft selbst" in text  # niemand muss bieten
    assert "App" not in text
    assert len(text) <= tr.MAX_TEXT_LEN


def test_a_short_holding_candidate_line_names_the_sale_date_and_the_updates():
    stats = {"billig": ClassStats(median_f7=0.87, n=30, by_days={1: (0.06, 30)})}
    late = _listing("late", 3_000_000, expires=datetime(2026, 10, 7, 15, 0, tzinfo=BERLIN))
    buys, _ = buy_candidates([late], {"late": WAVE}, stats, own_ids=set(), jev_returns=set(),
                             sale_by=SALE_BY)
    text = format_trade_radar(buys, [], account_summary(budget=0, rows=[]), deadline=DEADLINE,
                              stats=stats, now=NOW, sale_by=SALE_BY)
    assert "Verkauf spätestens Do 08.10. 21:30 (1 Update)" in text


def test_a_long_report_is_cut_but_keeps_the_closing_line():
    pos = positions([_own(f"p{i:02d}", 3_000_000 + i, -1_000) for i in range(40)],
                    _book(**{f"p{i:02d}": 3_100_000 for i in range(40)}), NOW, SALE_BY)
    text = format_trade_radar([], pos, account_summary(budget=0, rows=pos), deadline=DEADLINE,
                              stats=STATS, now=NOW)
    assert len(text) <= tr.MAX_TEXT_LEN
    assert text.endswith(tr.CLOSING_LINE) and "…" in text


def test_an_empty_radar_says_so_honestly():
    text = format_trade_radar([], [], account_summary(budget=2_376_645, rows=[]),
                              deadline=DEADLINE, stats=STATS, now=NOW)
    assert "kein Kandidat" in text and "keine Handelsspieler" in text


def _meta_row(name: str, signal: str, pnl: int = -100_000, **extra) -> dict:
    return {"name": name, "signal": signal, "mv": 3_000_000, "pnl": pnl, **extra}


def test_summary_lines_sort_themselves_and_show_only_what_to_do():
    meta = {
        "positionen": [  # absichtlich unsortiert
            _meta_row("B", "halten", 400_000),
            _meta_row("A", "verkaufen"),
            _meta_row("F", "verkaufen (Frist)", in_lineup=True),
        ],
        "kauf": [{"name": f"K{i}", "mv": 3_000_000, "max_bid": 3_900_000, "r3": 0.12,
                  "jev_return": False, "forced_sale": False,
                  "expires_at": (NOW + timedelta(hours=5)).isoformat()} for i in range(4)],
        "konto": {"budget": 2_376_645, "after_sale": 9_876_645, "covered": True,
                  "exposure": 9_000_000},
        "frist": DEADLINE.isoformat(),
    }
    lines = summary_lines(meta)
    assert lines[0].startswith("• Konto nach Verkauf aller Handelsspieler 9,88 M")
    assert lines[1].startswith("• Verkaufen (Frist): F") and "Elf" in lines[1]
    assert lines[2].startswith("• Verkaufen: A")
    assert "1 Handelsspieler halten" in lines[3]  # nur gezählt, steht im Cockpit
    buys = [line for line in lines if line.startswith("• Kandidat:")]
    assert len(buys) == tr.SUMMARY_MAX_BUY and "Auktion bis Do 13:12" in buys[0]
    assert lines[-1] == "• … 2 weitere Kandidaten im Cockpit"


def test_summary_lines_flag_an_uncovered_account_even_without_trade_players():
    meta = {"positionen": [], "kauf": [],
            "konto": {"budget": -500_000, "after_sale": -500_000, "covered": False,
                      "exposure": 0},
            "frist": DEADLINE.isoformat()}
    assert summary_lines(meta)[0].startswith("• Konto nach Verkauf aller Handelsspieler -500 k")
    assert "NICHT gedeckt" in summary_lines(meta)[0]


def test_summary_lines_name_trade_players_beyond_the_limit():
    meta = {"positionen": [_meta_row(f"P{i}", "verkaufen") for i in range(tr.SUMMARY_MAX_POS + 2)]}
    lines = summary_lines(meta)
    assert sum(line.startswith("• Verkaufen") for line in lines) == tr.SUMMARY_MAX_POS
    assert lines[-1] == "• … 2 weitere Handelsspieler im Cockpit"
    exact = summary_lines({"positionen": meta["positionen"][:tr.SUMMARY_MAX_POS]})
    assert not any("weitere" in line for line in exact)  # genau am Limit: nichts verschwiegen


def test_summary_lines_say_when_the_report_is_unreadable():
    assert summary_lines({"kauf": [{"name": None}], "positionen": ["kaputt"]}) == [
        "• Handels-Radar-Bericht nicht lesbar — Cockpit prüfen"
    ]
    assert summary_lines({"kauf": [], "positionen": []}) == [
        "• kein Kandidat, keine Handelsspieler"
    ]


# ------------------------------------------------------------------ Job


class _ReadOnlyClient:
    def __init__(self, *, squad, market, budget=2_376_645, fixtures=(), mv_history=None,
                 own_bids=()):
        self._squad, self._market, self._budget = squad, market, budget
        self._fixtures, self._mv_history = list(fixtures), mv_history or {}
        self._own_bids = list(own_bids)
        self.history_calls: list[str] = []

    async def get_squad(self, league_id):
        return self._squad

    async def get_market(self, league_id):
        return self._market

    async def get_budget(self, league_id):
        return self._budget

    async def get_matchdays(self):
        return self._fixtures

    async def get_own_bids(self, league_id, user_id):
        return self._own_bids

    async def get_team_names(self):
        return {"3": "Dortmund"}

    async def get_player_mv_history(self, league_id, player_id, timeframe=92):
        self.history_calls.append(player_id)
        return self._mv_history.get(player_id, [])

    def __getattr__(self, name: str):
        raise AssertionError(f"Handels-Radar darf {name!r} nicht aufrufen — er liest nur.")


def _player(pid: str, last: str, mv: int, *, mv24: int = 0, trend: int = 0,
            observed: bool = True) -> Player:
    return Player(id=pid, first_name="", last_name=last, position=Position.DEFENDER,
                  market_value=mv, market_value_trend=trend, team_id="3", lineup_prob=3,
                  fitness_status=0, mv_change_24h=mv24, mv_change_24h_observed=observed)


@pytest.fixture(autouse=True)
def no_jev_by_default(monkeypatch):
    """Ohne Schlüssel prüft Jev nichts — und kein Test geht ins Netz (kicker, TypeSafe)."""
    monkeypatch.setattr(tr, "_jev_available", lambda: False)

    async def no_articles(stamp):  # nie ins Netz (kicker) — und nie verschluckt
        return [], False

    monkeypatch.setattr(tr, "gather_articles", no_articles)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(tr, "SessionLocal", factory)
    return factory


def _seed_history(factory, pid: str, values: dict[date, int]) -> None:
    with factory() as s:
        s.add_all(PlayerHistory(player_id=pid, recorded_at=datetime(d.year, d.month, d.day),
                                market_value=v) for d, v in values.items())
        s.commit()


def _bid_placed(factory, pid: str, amount: int, at: datetime, *, trade: bool = True) -> None:
    """Ein echtes Gebot samt Vorhaben, wie der Intent-Executor es hinterlässt — mit Quelle
    `trade` vom Handels-Autopiloten, sonst von der Engine."""
    with factory() as s:
        action = TransferAction(
            league_id="L1", action_type="bid", player_id=pid, amount=amount, status="executed",
            idempotency_key=f"bid-{pid}-{at.isoformat()}", rule_id="intent-bid", dry_run=False,
            created_at=at,
        )
        s.add(action)
        s.flush()
        s.add(Intent(
            created_at=at, updated_at=at, kind="bid", player_id=pid, player_name=pid,
            amount=amount, sources={"trade": True, "league_id": "L1"} if trade else
            {"league_id": "L1"}, listing_type="kickbase", deadline=at + timedelta(minutes=15),
            execute_at=at, status="executed", executed_action_id=action.id,
        ))
        s.commit()


def _news(factory, at: datetime, *pids: str) -> None:
    with factory() as s:
        s.add(Report(created_at=at.astimezone(UTC).replace(tzinfo=None), kind="recherche",
                     title="News-Radar", body="x", source="worker:news_radar",
                     meta={"kauf": [{"player_id": pid} for pid in pids], "kader": []}))
        s.commit()


def _world(monkeypatch, client):
    async def factory():
        return client, SimpleNamespace(league_id="L1", user_id="U1")

    monkeypatch.setattr(tr, "make_kickbase_client", factory)


async def test_the_job_reports_candidates_positions_and_account_without_writing(db, monkeypatch):
    riser = _player("rise", "Nadir", 3_990_000)
    unknown = _player("new", "Neu", 2_000_000)
    position = _player("pos", "Handel", 3_500_000, mv24=-80_000, trend=2)
    # Echt (Scheller, 01.10.2026): Kickbase-Trend 2, aber +101.503 € beim letzten Update —
    # der Trend-Wert ist nicht die Richtung des letzten Updates.
    rising = _player("sch", "Scheller", 5_580_536, mv24=101_503, trend=2)
    # tfhmvt nicht beobachtet: Der letzte Schritt kommt aus der eigenen Historie.
    unseen = _player("hist", "Ohnepfeil", 2_000_000, trend=0, observed=False)
    bought = NOW - timedelta(days=1)
    client = _ReadOnlyClient(
        squad=[SquadPlayer(player=position), SquadPlayer(player=rising),
               SquadPlayer(player=unseen)],
        market=[MarketPlayer(player=riser, price=3_990_000, expires_at=NOW + timedelta(hours=8)),
                MarketPlayer(player=unknown, price=2_000_000, expires_at=NOW + timedelta(hours=8))],
        fixtures=[Fixture(matchday=5, home_team_id="3", away_team_id="4", kickoff=DEADLINE)],
        mv_history={"new": [MarketValueSnapshot(date=datetime(2026, 9, 27), value=1_500_000),
                            MarketValueSnapshot(date=datetime(2026, 9, 29), value=1_900_000),
                            MarketValueSnapshot(date=datetime(2026, 9, 30), value=2_000_000)]},
        own_bids=[{"player_id": "x", "amount": 1_000_000},     # Engine-Gebot
                  {"player_id": "rad", "amount": 1_000_000}],  # Handelsgebot (Autopilot)
    )
    _world(monkeypatch, client)
    _seed_history(db, "rise", {TODAY - timedelta(d): round(3_990_000 / 1.05 ** d)
                               for d in range(25)})
    _seed_history(db, "hist", {TODAY - timedelta(1): 2_100_000, TODAY: 2_000_000})
    # Handelsbuch: 1,0 + 1,5 + 0,5 M Kaufpreise + 1,0 M Handelsgebot = 4,0 von 20 M belegt.
    for pid, price in (("pos", 1_000_000), ("sch", 1_500_000), ("hist", 500_000)):
        _bid_placed(db, pid, price, bought)
    _bid_placed(db, "rad", 1_000_000, NOW - timedelta(hours=2))
    _bid_placed(db, "x", 1_000_000, NOW - timedelta(hours=2), trade=False)
    _news(db, NOW - timedelta(minutes=1), "rise")

    assert await tr.run_trade_radar(None, now=NOW) == "ok:2/3"

    with db() as s:
        report = s.scalars(select(Report).where(Report.source == tr.SOURCE)
                           .order_by(Report.created_at.desc())).first()
    meta = report.meta
    kauf = {row["player_id"]: row for row in meta["kauf"]}
    assert set(kauf) == {"rise", "new"} and kauf["rise"]["jev_return"] is True
    # Jede Kickbase-Auktion der Preisspanne fragt Kickbase; ohne Antwort ("rise") die DB.
    assert client.history_calls == ["rise", "new"]
    assert "Verlauf: 1 von Kickbase, 1 aus der DB" in report.body
    pos = {row["player_id"]: row for row in meta["positionen"]}
    assert (pos["pos"]["signal"], pos["pos"]["buy_price"]) == ("verkaufen", 1_000_000)
    assert (pos["sch"]["signal"], pos["sch"]["last_step"]) == ("halten", 101_503)
    assert (pos["hist"]["signal"], pos["hist"]["last_step"]) == ("verkaufen", -100_000)
    assert meta["konto"]["open_bids"] == 2_000_000
    assert meta["konto"]["trade_bids"] == 1_000_000
    assert meta["konto"]["exposure"] == 1_000_000 + 1_500_000 + 500_000 + 1_000_000
    assert meta["konto"]["after_sale"] == (2_376_645 - 2_000_000 + 3_500_000 + 5_580_536
                                           + 2_000_000)
    # Kader ohne Handelsspieler 0 < 11: Verkaufstermin Frist − 96 h (Mo 05.10. 20:30). Vom
    # Auktionsende Do 01.10. 16:12 aus bleiben vier Updates (01.–04.10. um 22:00).
    assert meta["verkauf_bis"] == SALE_BY_GAP.isoformat()
    assert all(row["forced_sale"] and row["horizon_days"] == 4 for row in meta["kauf"])
    assert "Verkaufstermin aller Handelsspieler: spätestens Mo 05.10. 20:30" in report.body
    assert meta["frist"] == DEADLINE.isoformat()
    assert report.kind == "recherche"


async def test_without_a_league_the_job_says_so(db, monkeypatch):
    async def factory():
        return _ReadOnlyClient(squad=[], market=[]), SimpleNamespace(league_id=None, user_id=None)

    monkeypatch.setattr(tr, "make_kickbase_client", factory)
    assert await tr.run_trade_radar(None, now=NOW) == "no_league"


async def test_kickbase_history_beats_a_stale_last_day_in_the_db(db, monkeypatch):
    """Echt (30.09.2026): Der Snapshot 22:05 schrieb für 9 von 16 geprüften Spielern den
    Vortageswert unter das neue Datum (Kickbases Update kam später) — mit der DB allein sähe
    ein Steiger flach aus. Kickbases Verlauf ist maßgeblich, die DB nur Rückfall."""
    days = {TODAY - timedelta(d): round(3_000_000 / 1.05 ** d) for d in range(25)}
    stale = {**days, TODAY: days[TODAY - timedelta(1)]}  # Vortageswert unter heutigem Datum
    _seed_history(db, "late", stale)
    client = _ReadOnlyClient(
        squad=[],
        market=[MarketPlayer(player=_player("late", "Spaet", 3_000_000), price=3_000_000,
                             expires_at=NOW + timedelta(hours=8))],
        mv_history={"late": [MarketValueSnapshot(date=datetime(d.year, d.month, d.day, 2),
                                                 value=v) for d, v in sorted(days.items())]},
    )
    _world(monkeypatch, client)

    assert await tr.run_trade_radar(None, now=NOW) == "ok:1/0"
    with db() as s:
        [row] = s.scalars(select(Report).where(Report.source == tr.SOURCE)).one().meta["kauf"]
    assert row["r1"] == pytest.approx(0.05, abs=1e-6)


async def test_only_autopilot_purchases_count_and_only_todays_news_and_fresh_history(
    db, monkeypatch
):
    """Handelsspieler nur aus dem Handelsbuch (ein Engine-Kauf zählt nicht, ein Spieler ohne
    Gebot auch nicht); Jev-Rückkehr nur aus dem HEUTIGEN News-Radar; ein Verlauf, der drei
    Tage alt endet, ist keine laufende Welle; teure Auktionen fragen Kickbase gar nicht erst."""
    squad = [SquadPlayer(player=_player(pid, pid.title(), 3_000_000, mv24=-50_000))
             for pid in ("core", "back")]
    fresh = {TODAY - timedelta(d): round(3_000_000 / 1.05 ** d) for d in range(25)}
    old = {day - timedelta(3): value for day, value in fresh.items()}  # endet vor 3 Tagen

    def snaps(values):
        return [MarketValueSnapshot(date=datetime(d.year, d.month, d.day, 2), value=v)
                for d, v in sorted(values.items())]

    client = _ReadOnlyClient(
        squad=squad,
        market=[MarketPlayer(player=_player(pid, pid.title(), mv), price=mv,
                             expires_at=NOW + timedelta(hours=8))
                for pid, mv in (("fresh", 3_000_000), ("old", 3_000_000),
                                ("star", 20_000_000))],
        mv_history={"fresh": snaps(fresh), "old": snaps(old), "star": snaps(fresh)},
    )
    _world(monkeypatch, client)
    _bid_placed(db, "core", 2_900_000, NOW - timedelta(days=2), trade=False)  # Engine-Kauf
    _news(db, NOW - timedelta(days=1), "fresh")

    assert await tr.run_trade_radar(None, now=NOW) == "ok:1/0"
    with db() as s:
        report = s.scalars(select(Report).where(Report.source == tr.SOURCE)
                           .order_by(Report.created_at.desc())).first()
    [row] = report.meta["kauf"]
    assert (row["player_id"], row["jev_return"]) == ("fresh", False)
    assert client.history_calls == ["fresh", "old"]
    assert "News-Radar von heute fehlt" in report.body  # ehrlich statt still ohne Jev


async def test_the_history_loop_stops_at_its_time_budget(db, monkeypatch):
    """Review 01.10.: 25 Abrufe à 10 s hätten 08:15 gerissen. Nach dem Zeitbudget gilt die
    DB — und der Bericht sagt, wie viele Verläufe daher stammen."""
    ticks = iter([0.0, 0.0, tr.HISTORY_BUDGET_S + 1, tr.HISTORY_BUDGET_S + 2,
                  tr.HISTORY_BUDGET_S + 3])
    monkeypatch.setattr(tr, "_clock", lambda: next(ticks, tr.HISTORY_BUDGET_S + 9))
    values = {TODAY - timedelta(d): round(3_000_000 / 1.05 ** d) for d in range(25)}
    for pid in ("a", "b"):
        _seed_history(db, pid, values)
    client = _ReadOnlyClient(
        squad=[],
        market=[MarketPlayer(player=_player(pid, pid, 3_000_000), price=3_000_000,
                             expires_at=NOW + timedelta(hours=8)) for pid in ("a", "b")],
    )
    _world(monkeypatch, client)

    await tr.run_trade_radar(None, now=NOW)
    assert client.history_calls == ["a"]
    with db() as s:
        body = s.scalars(select(Report).where(Report.source == tr.SOURCE)).one().body
    assert "Verlauf: 0 von Kickbase, 2 aus der DB" in body


async def test_candidates_beyond_the_trading_frame_are_left_out_and_counted(db, monkeypatch):
    values = {TODAY - timedelta(d): round(12_000_000 / 1.02 ** d) for d in range(25)}
    _seed_history(db, "big", values)
    bought = NOW - timedelta(days=1)
    client = _ReadOnlyClient(
        squad=[SquadPlayer(player=_player("t", "Trade", 9_000_000, mv24=10_000))],
        market=[MarketPlayer(player=_player("big", "Gross", 12_000_000), price=12_000_000,
                             expires_at=NOW + timedelta(hours=8))],
        budget=50_000_000,
    )
    _world(monkeypatch, client)
    _bid_placed(db, "t", 38_500_000, bought)  # 50 M − 38,5 M = 11,5 M frei, „big“ braucht mehr

    assert await tr.run_trade_radar(None, now=NOW) == "ok:0/1"
    with db() as s:
        body = s.scalars(select(Report).where(Report.source == tr.SOURCE)
                         .order_by(Report.created_at.desc())).first().body
    assert "über dem Handelsrahmen" in body


# ------------------------------------------------------------------ Jev-Check (01.10.)


def _jev_world(monkeypatch, db, *, verdicts=None, rss_failed=False):
    """Zwei laufende Wellen am Markt („ok“ und „hurt“); Jev liest für „hurt“ ein klares „fehlt“."""
    values = {TODAY - timedelta(d): round(3_000_000 / 1.05 ** d) for d in range(25)}
    for pid in ("ok", "hurt"):
        _seed_history(db, pid, values)
    _seed_history(db, "flat", {TODAY - timedelta(d): 3_000_000 for d in range(25)})
    client = _ReadOnlyClient(
        squad=[],
        market=[MarketPlayer(player=_player(pid, pid.title(), 3_000_000), price=3_000_000,
                             expires_at=NOW + timedelta(hours=8))
                for pid in ("ok", "hurt", "flat")],  # „flat“: keine Welle, kein Kandidat
    )
    _world(monkeypatch, client)
    monkeypatch.setattr(tr, "_jev_available", lambda: True)
    seen: dict = {}

    async def articles(stamp):
        seen["articles_at"] = stamp
        return [], rss_failed

    async def judge(players, articles, team_names, *, now, fixtures, **kwargs):
        seen["players"] = sorted(p.id for p in players)
        seen["kwargs"] = kwargs
        seen["team_names"] = team_names
        return dict(verdicts or {})

    monkeypatch.setattr(tr, "gather_articles", articles)
    monkeypatch.setattr(tr, "judge_roles", judge)
    return seen


_HURT = RoleVerdict(
    player_id="hurt", role="fehlt", probability=0.98,
    title="Muskelfaserriss: Hurt fällt mehrere Wochen aus",
    url="https://www.kicker.de/hurt", published_at=datetime(2026, 9, 30, 15, 0, tzinfo=UTC),
)


async def test_jev_takes_a_reported_absence_out_of_the_candidates(db, monkeypatch):
    """Emil 01.10.2026: „ja, bau den Jev-Check danach ein“ — dieselbe Rollenfrage und Schwelle
    wie die Kaufprüfung der Engine; ein klares „fehlt“ nimmt den Steiger heraus, der Artikel
    steht im Bericht."""
    seen = _jev_world(monkeypatch, db, verdicts={"hurt": _HURT})

    assert await tr.run_trade_radar(None, now=NOW) == "ok:1/0"

    assert seen["players"] == ["hurt", "ok"]  # nur die Kandidaten
    assert seen["kwargs"] == {"pause_on_failure": False, "purpose": "handel"}
    with db() as s:
        report = s.scalars(select(Report).where(Report.source == tr.SOURCE)).one()
    assert [row["player_id"] for row in report.meta["kauf"]] == ["ok"]
    [out] = report.meta["jev_raus"]
    assert (out["player_id"], out["title"]) == ("hurt", _HURT.title)
    assert report.meta["jev_check"]["status"] == "ok"
    assert "Ohne Kauf — kicker meldet Ausfall (Jev):" in report.body
    assert "„Muskelfaserriss: Hurt fällt mehrere Wochen aus“ — Jev liest „fehlt“ (98 %)" in (
        report.body)


async def test_without_a_typesafe_key_the_candidates_stay_but_are_marked_unchecked(
    db, monkeypatch
):
    _jev_world(monkeypatch, db, verdicts={"hurt": _HURT})
    monkeypatch.setattr(tr, "_jev_available", lambda: False)

    assert await tr.run_trade_radar(None, now=NOW) == "ok:2/0"
    with db() as s:
        report = s.scalars(select(Report).where(Report.source == tr.SOURCE)).one()
    assert "Jev-Check aus" in report.body and "ungeprüft" in report.body
    assert report.meta["jev_check"]["status"] == "aus"
    assert any("Jev-Check aus" in line for line in summary_lines(report.meta))


def _latest(db) -> Report:
    with db() as s:
        return s.scalars(select(Report).where(Report.source == tr.SOURCE)
                         .order_by(Report.created_at.desc())).first()


async def test_a_missing_kicker_makes_the_check_partial(db, monkeypatch):
    _jev_world(monkeypatch, db, rss_failed=True)
    assert await tr.run_trade_radar(None, now=NOW) == "ok:2/0"
    report = _latest(db)
    assert "kicker nicht erreichbar" in report.body
    assert report.meta["jev_check"]["status"] == "teilweise"


async def test_a_paused_jev_still_applies_the_verdicts_it_already_has(db, monkeypatch):
    """Review 01.10.: In der Pause fragt `judge_roles` nicht neu, liefert aber die Urteile aus
    dem Cache — genau die, mit denen die Engine gerade sperrt. Die gelten auch im Radar."""
    _jev_world(monkeypatch, db, verdicts={"hurt": _HURT})
    monkeypatch.setattr(tr.role_judge, "is_paused", lambda: True)

    assert await tr.run_trade_radar(None, now=NOW) == "ok:1/0"
    report = _latest(db)
    assert [r["player_id"] for r in report.meta["jev_raus"]] == ["hurt"]
    assert report.meta["jev_check"]["status"] == "pause"
    assert "nur bereits beurteilte Meldungen" in report.body


async def test_a_failing_jev_step_keeps_the_candidates_and_says_so(db, monkeypatch):
    _jev_world(monkeypatch, db)

    async def broken(stamp):
        raise RuntimeError("kicker 503")

    monkeypatch.setattr(tr, "gather_articles", broken)
    assert await tr.run_trade_radar(None, now=NOW) == "ok:2/0"
    report = _latest(db)
    assert report.meta["jev_check"]["status"] == "fehler"
    assert "Jev-Check nicht möglich (RuntimeError)" in report.body


async def test_a_slow_jev_step_is_cut_off_and_named(db, monkeypatch):
    import asyncio

    _jev_world(monkeypatch, db)
    monkeypatch.setattr(tr, "JEV_STEP_TIMEOUT_S", 0.01)

    async def slow(stamp):
        await asyncio.sleep(1)
        return [], False

    monkeypatch.setattr(tr, "gather_articles", slow)
    assert await tr.run_trade_radar(None, now=NOW) == "ok:2/0"
    report = _latest(db)
    assert report.meta["jev_check"]["status"] == "fehler"
    assert "zu langsam" in report.body


async def test_unjudged_reports_make_the_check_partial(db, monkeypatch):
    _jev_world(monkeypatch, db)
    monkeypatch.setattr(tr.role_judge, "unjudged", lambda *args, **kwargs: 2)
    assert await tr.run_trade_radar(None, now=NOW) == "ok:2/0"
    report = _latest(db)
    assert report.meta["jev_check"]["status"] == "teilweise"
    assert "2 Meldungen ohne Urteil" in report.body


async def test_jev_only_asks_about_candidates_inside_the_trading_frame(db, monkeypatch):
    """Review 01.10.: Erst der Handelsrahmen, dann Jev — sonst zählte „Jev nahm N raus“ auch
    Spieler, die der Rahmen ohnehin ausschließt."""
    seen = _jev_world(monkeypatch, db)
    values = {TODAY - timedelta(d): round(12_000_000 / 1.02 ** d) for d in range(25)}
    _seed_history(db, "big", values)
    bought = NOW - timedelta(days=1)
    client = _ReadOnlyClient(
        squad=[SquadPlayer(player=_player("t", "Trade", 9_500_000, mv24=10_000))],
        market=[MarketPlayer(player=_player(pid, pid.title(), mv), price=mv,
                             expires_at=NOW + timedelta(hours=8))
                for pid, mv in (("ok", 3_000_000), ("big", 12_000_000))],
        budget=50_000_000,
    )
    _world(monkeypatch, client)
    _bid_placed(db, "t", 39_000_000, bought)  # 11 M frei: „big“ passt nicht mehr hinein

    await tr.run_trade_radar(None, now=NOW)
    assert seen["players"] == ["ok"]


async def test_players_with_a_running_bid_are_no_candidates(db, monkeypatch):
    """Ein Spieler mit laufendem Gebot gehört der Engine (oder sein Handelsgebot steht schon)
    — der Radar nennt ihn nicht noch einmal."""
    _jev_world(monkeypatch, db)
    client = _ReadOnlyClient(
        squad=[],
        market=[MarketPlayer(player=_player(pid, pid.title(), 3_000_000), price=3_000_000,
                             expires_at=NOW + timedelta(hours=8)) for pid in ("ok", "hurt")],
        own_bids=[{"player_id": "hurt", "amount": 3_100_000}],
    )
    _world(monkeypatch, client)

    assert await tr.run_trade_radar(None, now=NOW) == "ok:1/0"
    assert [row["player_id"] for row in _latest(db).meta["kauf"]] == ["ok"]


async def test_a_planned_trade_intent_is_not_counted_twice_against_the_frame(db, monkeypatch):
    """Das geplante Handelsvorhaben steckt schon im Handelsrahmen — sein Kandidat bleibt."""
    _jev_world(monkeypatch, db)
    with db() as s:
        s.add(Intent(created_at=NOW, updated_at=NOW, kind="bid", player_id="ok",
                     player_name="Ok", amount=49_000_000, sources={"trade": True},
                     listing_type="kickbase", execute_at=NOW + timedelta(hours=7),
                     status="planned"))
        s.commit()

    assert await tr.run_trade_radar(None, now=NOW) == "ok:1/0"
    report = _latest(db)
    assert [row["player_id"] for row in report.meta["kauf"]] == ["ok"]  # „hurt“: Rahmen voll
    assert report.meta["konto"]["exposure"] == 49_000_000


async def test_the_real_role_judge_answers_the_radars_question(db, monkeypatch, jev_rows):
    """Review 01.10.: Beide Judges waren in den Radar-Tests ersetzt. Hier fragt der echte
    `judge_roles` (Paarbildung, Schwelle, Protokoll) — nur TypeSafe ist eine Attrappe."""
    _jev_world(monkeypatch, db)
    monkeypatch.setattr(tr, "judge_roles", role_judge.judge_roles)
    article = NewsArticle(source="kicker", url="https://www.kicker.de/hurt",
                          title="Muskelfaserriss: Hurt fällt mehrere Wochen aus", body="",
                          published_at=NOW - timedelta(hours=10))

    async def articles(stamp):
        return [article], False

    async def ask(state, questions):
        return SimpleNamespace(choices={
            key: SimpleNamespace(choice="fehlt", confidence=0.98,
                                 probabilities={"fehlt": 0.98, "kein_hinweis": 0.02})
            for key in questions
        })

    monkeypatch.setattr(tr, "gather_articles", articles)
    monkeypatch.setattr(role_judge, "_default_ask", lambda: ask)
    role_judge.clear_cache()
    try:
        assert await tr.run_trade_radar(None, now=NOW) == "ok:1/0"
    finally:
        role_judge.clear_cache()
    assert [(r.purpose, r.outcome, r.player_id) for r in jev_rows] == [
        ("handel", "sperrt", "hurt")
    ]
    assert [r["player_id"] for r in _latest(db).meta["jev_raus"]] == ["hurt"]


def test_jev_status_and_removals_survive_the_length_limit():
    """Review 01.10.: Hinweise und der Jev-Abschnitt standen hinten und fielen der
    1900-Zeichen-Kürzung zum Opfer — jetzt stehen sie vor Handelsspielern und Kandidaten."""
    pos = positions([_own(f"p{i:02d}", 3_000_000 + i, -1_000) for i in range(40)],
                    _book(**{f"p{i:02d}": 3_100_000 for i in range(40)}), NOW, SALE_BY)
    out = [{"player_id": "hurt", "name": "Hurt", "note": role_judge.role_note(_HURT)}]
    text = format_trade_radar([], pos, account_summary(budget=0, rows=pos), deadline=DEADLINE,
                              stats=STATS, now=NOW, jev_out=out,
                              jev_check={"status": "teilweise",
                                         "text": "Jev-Check unvollständig — 2 ohne Urteil"})
    assert len(text) <= tr.MAX_TEXT_LEN
    assert "Jev-Check unvollständig" in text and "Muskelfaserriss" in text


def test_when_jev_took_every_candidate_the_report_does_not_claim_there_was_no_wave():
    out = [{"player_id": "hurt", "name": "Hurt", "note": role_judge.role_note(_HURT)}]
    text = format_trade_radar([], [], account_summary(budget=0, rows=[]), deadline=DEADLINE,
                              stats=STATS, now=NOW, jev_out=out)
    assert "keine Kickbase-Auktion mit laufender Welle" not in text
    assert "Jev nahm alle heraus" in text


def test_the_summary_says_when_candidates_are_unchecked():
    meta = {"positionen": [],
            "kauf": [{"name": "Ok", "mv": 3_000_000, "max_bid": 3_150_000, "r3": 0.12,
                      "expires_at": (NOW + timedelta(hours=5)).isoformat()}],
            "jev_check": {"status": "fehler", "text": "Jev-Check nicht möglich (TimeoutError)"}}
    lines = summary_lines(meta)
    assert lines[0] == "• Jev-Check nicht möglich (TimeoutError) — Kandidaten ungeprüft"
    assert lines[1].startswith("• Kandidat: Ok")
    assert summary_lines({**meta, "jev_check": {"status": "ok", "text": ""}})[0].startswith(
        "• Kandidat: Ok")


async def test_without_candidates_jev_is_not_asked(db, monkeypatch):
    seen = _jev_world(monkeypatch, db)
    client = _ReadOnlyClient(squad=[], market=[])
    _world(monkeypatch, client)

    assert await tr.run_trade_radar(None, now=NOW) == "ok:0/0"
    assert "players" not in seen


def test_summary_names_how_many_candidates_jev_took_out():
    meta = {"positionen": [], "kauf": [],
            "jev_raus": [{"player_id": "hurt", "name": "Hurt", "title": _HURT.title}]}
    assert "• Jev nahm 1 Kandidaten raus (kicker meldet Ausfall) — Cockpit" in summary_lines(meta)


async def test_the_job_names_a_planned_trade_bid_shortly_before_its_auction_ends(db, monkeypatch):
    """Codex-Review 02.10.2026: Ein schon geplantes Handelsgebot bleibt Kandidat, auch wenn die
    Auktion in weniger als 30 min endet (sonst räumte der Autopilot es) — ein ungeplantes nicht."""
    soon = NOW + timedelta(minutes=20)
    client = _ReadOnlyClient(
        squad=[],
        market=[MarketPlayer(player=_player(pid, pid.title(), 3_990_000), price=3_990_000,
                             expires_at=soon) for pid in ("planned", "other")],
        fixtures=[Fixture(matchday=5, home_team_id="3", away_team_id="4", kickoff=DEADLINE)],
    )
    _world(monkeypatch, client)
    for pid in ("planned", "other"):
        _seed_history(db, pid, {TODAY - timedelta(d): round(3_990_000 / 1.05 ** d)
                                for d in range(25)})
    with db() as s:
        s.add(Intent(created_at=NOW - timedelta(hours=1), updated_at=NOW - timedelta(hours=1),
                     kind="bid", player_id="planned", player_name="Planned", amount=4_400_000,
                     sources={"trade": True, "league_id": "L1"}, listing_type="kickbase",
                     deadline=soon, execute_at=soon - timedelta(minutes=15), status="planned"))
        s.commit()

    await tr.run_trade_radar(None, now=NOW)

    with db() as s:
        report = s.scalars(select(Report).where(Report.source == tr.SOURCE)).one()
    assert [row["player_id"] for row in report.meta["kauf"]] == ["planned"]


def test_steady_mittel_risers_above_five_million_qualify_with_euro_gain():
    """Emil 03.11.2026: Ein Stammspieler über 5 M € (z. B. Leon Avdullahu 14,07 M €, +216k € in
    3 Tagen = +1,56 %, +73k € in 24h) qualifiziert sich in der Stufe 1,5–5 % als Welle; ein
    billiger Spieler unter 5 M € braucht weiter >= 5 %."""
    avdullahu = tr.Momentum(mv=14_070_663, r1=0.0052, r3=0.0156, r7=0.035)
    cheap_slow = tr.Momentum(mv=3_000_000, r1=0.02, r3=0.04, r7=0.08)
    assert tr._is_wave(avdullahu) is True
    assert tr.momentum_bucket(avdullahu.r3) == "1.5-5"
    assert tr._is_wave(cheap_slow) is False
