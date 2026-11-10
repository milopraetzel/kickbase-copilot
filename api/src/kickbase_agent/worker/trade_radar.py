"""Handels-Radar: steigende Spieler kaufen, mit Gewinn verkaufen — der Radar liest nur.

Emil 01.10.2026: „gerade ist ja Spielerpause. Eine Pause die man nutzen könnte um Spieler zu
kaufen die steigen. Und damit Geld zu machen. Auch wenn man ins Minus geht.“ Nach der
Recherche: „ja bau den Handels-Radar“. Spec `docs/superpowers/specs/2026-10-01-handels-radar.md`.
Seit dem Handels-Autopiloten (Emil 01.10.2026: „alles soll automatisch geschehen ich will nicht
dass ich bieten muss“, Spec `docs/superpowers/specs/2026-10-01-handels-autopilot.md`) ist der
Radar die Analyse: Der Autopilot (`worker/trade_autopilot.py`) legt aus seinen Kandidaten
Gebots-Vorhaben an und verkauft die Handelsspieler selbst.

Befund 01.10.2026 (Box-Daten, Länderspielpause): Wer in drei Tagen um mindestens 5 % stieg,
stieg im Median weiter, billige Spieler deutlich stärker als mittlere (Zahlen in L-117). Die
Liga zahlt für Steiger aber im Median 17,5 % Aufschlag, und nach drei Tagen lagen solche Käufe
noch im Minus. Ein Sofortverkauf an Kickbase bringt genau den Marktwert. Daraus die Regel:

* **Kaufen** nur Kickbase-Auktionen (Manager-Listings verkaufen nicht), MW 0,5–15 M, fit und
  KB-Stufe 1–3, Anstieg ≥ `MIN_RISE_3D` in drei Tagen und letzter Tagesschritt > 0 (die Welle
  läuft noch).
* **Höchstgebot** aus den eigenen Daten der letzten `STATS_LOOKBACK_DAYS` Tage: MW × (1 +
  min(30 %, `PREMIUM_SHARE` × Median-Anstieg der Steigungsstufe über die Haltezeit)), ein Wert
  je Spieler; unter `MIN_EVIDENCE` Spielern die Klasse, darunter nur `FALLBACK_PREMIUM`; trägt
  die Welle nicht (Median ≤ 0), kein Kauf. Die Haltezeit endet am Verkaufstermin
  (`trading.trade_book.sale_deadline`: T−96 h vor der Spieltags-Frist, solange die Elf offene
  Plätze hat, sonst T−50 h): Es zählen nur die Updates bis dahin, höchstens sieben; ohne ein
  einziges kein Kauf (Review 01.10.: sonst hieße ein Kauf am Mittwoch +30 % für ein Update).
* **Verkaufen** nach dem ersten fallenden Update (der Preis steht bis 22:00 fest) und alle
  Handelsspieler am Verkaufstermin — sie stehen nie in der Elf (Emil 01.10.2026: „sie bleiben
  Bank“), ein Verkauf kostet also keinen Platz.
* **Rahmen:** höchstens `EXPOSURE_CAP` (20 M, Emil 01.10.2026) in Kaufpreisen offener
  Handelsspieler und Handelsgeboten.

Handelsspieler: aus dem Handelsbuch (`trading/trade_book.py`) — Kaderspieler, deren jüngstes
echtes Gebot der Autopilot abgab.

Jev-Pluspunkt: Steht ein Kandidat im heutigen News-Radar als Rückkehrer (Jev „rueckkehr“
≥ 0,95), sagt die Zeile das und er rückt nach vorn — Rückkehrer ziehen im Marktwert an.

Jev-Check (Emil 01.10.2026: „ja, bau den Jev-Check danach ein“): Jeder Kandidat durchläuft
dieselbe Rollenfrage wie die Kaufprüfung der Engine (`intel/role_judge.py`, kicker-Anreißer
der letzten 96 h nach dem letzten Spiel des Vereins): Ein klares „fehlt“ (≥ 0,95) fürs nächste
Bundesligaspiel nimmt ihn heraus, der Artikel steht im Bericht. Anders als die Engine (nur
gespeicherte Anreißer) liest der Radar auch frische — wie der News-Radar. Ohne Schlüssel oder bei
Ausfall bleiben die Kandidaten, als ungeprüft benannt (Bericht oben und Tageszusammenfassung); in
einer Ausfall-Pause gelten die Urteile, die Jev schon gab. Der Radar fragt ohne Ausfall-Pause,
damit sein Ausfall nie die Kaufsperre der Engine abschaltet; jede Frage steht im Jev-Protokoll.

Rein lesend: Kickbase nur get_*; in die DB schreibt der Radar nur seinen Bericht und die
Jev-Protokollzeilen seiner Fragen. Läufe 08:12 und 22:35 (nach dem Marktwert-Update 22:00 und
dem Snapshot 22:05). Zustellung über die Tageszusammenfassung 08:15 (`summary_lines`), kein
eigener Discord-Versand.
"""

from __future__ import annotations

import asyncio
import logging
import math
import statistics
import time as _time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from kickbase_agent.config import get_settings
from kickbase_agent.db.models import Intent, Report
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.intel import role_judge
from kickbase_agent.intel.role_judge import RoleVerdict, judge_roles, role_note
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.notify.report_store import save_report
from kickbase_agent.scouts.ligainsider.teams import TEAMS
from kickbase_agent.trading.trade_book import (
    TRADE_CAP_EUR,
    TradePosition,
    live_swap_cover,
    load_trade_book,
    regular_fill,
    sale_deadline,
)
from kickbase_agent.worker.kickoff_guard import matchday_deadline
from kickbase_agent.worker.news_radar import gather_articles
from kickbase_agent.worker.steiger_radar import load_history

log = logging.getLogger(__name__)

SOURCE = "worker:trade_radar"
REPORT_KIND = "recherche"
NEWS_SOURCE = "worker:news_radar"
BERLIN = ZoneInfo("Europe/Berlin")

#: Mindestanstieg in drei Tagen (Befund 01.10.: ab hier trug die Welle).
MIN_RISE_3D = 0.05
#: In der mittel-Klasse (>= 5 M €) steigen Top-Stammspieler absolut stark (+150k bis +450k € in
#: 3 Tagen), prozentual aber oft 1,5–5 % (z. B. +216k € auf 14 M € = +1,56 %). Deshalb gilt
#: ab 5 M € zusätzlich eine Mindest-Euro-Steigung (Emil 03.11.2026).
MIN_RISE_3D_MITTEL = 0.015
MIN_GAIN_3D_MITTEL_EUR = 150_000
MIN_GAIN_1D_MITTEL_EUR = 50_000
MIN_MV = 500_000
#: Obergrenze der mittel-Klasse im Kurzfrist-Bank-Flipper (20.10.2026 / 06.11.2026: 18,5 M €,
#: damit reine 3-Tage-Bank-Flips kein 30–40-M-Klumpenrisiko tragen und den Dispo für echte
#: Startelf-Upgrades im Planer frei halten).
MAX_MV = 18_500_000
#: Grenze billig/mittel: darunter stiegen Steiger um ein Vielfaches stärker.
CHEAP_LIMIT = 5_000_000
#: Höchstaufschlag je Klasse und der Anteil des erwarteten Anstiegs, den wir bieten. 30 % =
#: Kappen-Stufe „Handel“ der Schreibstelle (130 % MW, nur Kickbase-Auktionen; Emil 01.10.).
PREMIUM_CAP = {"billig": 0.30, "mittel": 0.30}
PREMIUM_SHARE = 0.5
#: Steigungsstufen des 3-Tage-Anstiegs (Emil 01.10.: „ein Spieler der 12% steigt muss noch
#: höher angesetzt werden“) — Box-Daten: mittel ≥ 15 % stieg in sieben Tagen +20–23 %, 5–10 %
#: nur +6–8 %. Die Erwartung kommt aus der eigenen Stufe, sonst aus der Klasse.
BUCKET_LABEL = {
    "1.5-5": "1,5–5 %",
    "5-10": "5–10 %",
    "10-15": "10–15 %",
    ">=15": "≥ 15 %",
}
#: Die Schreibstelle nimmt Handelsgebote nur auf fitte Spieler mit KB-Stufe 1–3 an.
TRADE_LINEUP_PROBS = frozenset({1, 2, 3})
#: Unter so vielen SPIELERN der Klasse gilt nur der vorsichtige Aufschlag (Review 01.10.:
#: überlappende Signal-Tage eines Spielers sind kein zusätzlicher Beleg).
MIN_EVIDENCE = 15
FALLBACK_PREMIUM = 0.05
STATS_LOOKBACK_DAYS = 14
FORWARD_DAYS = 7
#: Handelsrahmen: Kaufpreise offener Handelsspieler plus Handelsgebote (Emil 01.10.2026:
#: „Ja, bis 20 M“ ins Minus; vorher 10 M, als Emil noch selbst in der App kaufte).
EXPOSURE_CAP = TRADE_CAP_EUR
#: Verlauf je Kickbase-Auktion von Kickbase — höchstens so viele Abrufe und so viele Sekunden
#: je Lauf (am 01.10. standen 20 Kickbase-Auktionen am Markt; die Zusammenfassung kommt 08:15).
#: Danach gilt die DB, und der Bericht sagt, wie viele Verläufe von dort stammen.
MAX_HISTORY_LOOKUPS = 25
HISTORY_BUDGET_S = 45.0
#: Wie lange die Historie zurückreicht (Statistik + 3 + 7 Tage + Luft).
HISTORY_DAYS = STATS_LOOKBACK_DAYS + FORWARD_DAYS + 5
UPDATE_TIME = time(22, 0)
#: Kürzer laufende Auktionen nennt der Radar nicht mehr (Zusammenfassung 08:15).
MIN_AUCTION_LEFT = timedelta(minutes=30)
#: So steht der Jev-Check im Jev-Protokoll (Zweck der Rollenfrage).
JEV_PURPOSE = "handel"
#: Obergrenze für den ganzen Jev-Schritt (Vereinsnamen, kicker, Jev mit eigener 10-s-Grenze).
JEV_STEP_TIMEOUT_S = 40.0
_JEV_SUFFIX = {"aus": " — Kandidaten ungeprüft", "fehler": " — Kandidaten ungeprüft",
               "teilweise": " — Kandidaten nur teilweise geprüft", "pause": ""}

MAX_BUY_LINES = 6
SUMMARY_MAX_BUY = 2
SUMMARY_MAX_POS = 3
MAX_TEXT_LEN = 1900

RULE_LINE = (
    f"Regel: nur Kickbase-Auktionen, fit und KB-Stufe 1–3, ≥ +{MIN_RISE_3D * 100:.0f} % in "
    "3 Tagen und letzter Tag steigend; Höchstgebot = halber erwarteter Anstieg der "
    "Steigungsstufe bis zum Verkauf (höchstens +30 %); verkaufen nach dem ersten fallenden "
    "Update, alle spätestens am Verkaufstermin (T−96 h bei offener Elf, sonst T−50 h)."
)
CLOSING_LINE = (
    "Der Handels-Autopilot bietet und verkauft selbst — als Vorhaben mit Veto-Fenster in der "
    "Zentrale; Handelsspieler bleiben auf der Bank."
)

_POS = {1: "TW", 2: "ABW", 3: "MF", 4: "ST"}
_WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
_SIGNAL_RANK = {"verkaufen (Frist)": 0, "verkaufen": 1, "halten": 2}
_SIGNAL_HEAD = {"verkaufen (Frist)": "Verkaufen (Frist)", "verkaufen": "Verkaufen",
                "halten": "Halten"}

#: Uhr für das Zeitbudget (Tests setzen sie).
_clock = _time.monotonic


@dataclass(frozen=True)
class Momentum:
    """Marktwert am Bezugstag und Anstieg gegen 1, 3 und 7 Tage davor (None = unbekannt)."""

    mv: int
    r1: float | None
    r3: float | None
    r7: float | None


@dataclass(frozen=True)
class ClassStats:
    """Median-Anstieg nach einem Steiger-Signal — ein Wert je Spieler — und wie viele Spieler.

    `median_f7`/`n` gelten für sieben Tage, `by_days` für 1 … 7 Tage (Tage → (Median, n))."""

    median_f7: float | None
    n: int
    by_days: Mapping[int, tuple[float | None, int]] = field(default_factory=dict)

    def at(self, days: int) -> tuple[float | None, int]:
        if days >= FORWARD_DAYS:
            return self.median_f7, self.n
        return self.by_days.get(days, (None, 0))


@dataclass(frozen=True)
class Listing:
    player_id: str
    name: str
    team: str
    position: int
    mv: int
    price: int
    expires_at: datetime
    seller_id: str
    lineup_prob: int | None
    fitness_status: int | None = None


@dataclass(frozen=True)
class BuyRow:
    player_id: str
    name: str
    team: str
    position: int
    mv: int
    r1: float | None
    r3: float | None
    r7: float | None
    price_class: str
    premium: float
    max_bid: int
    expected: float | None
    evidence_n: int
    expires_at: datetime
    jev_return: bool
    lineup_prob: int | None
    horizon_days: int = FORWARD_DAYS
    forced_sale: bool = False
    sell_by: datetime | None = None
    bucket: str = ""
    basis: str = ""


@dataclass(frozen=True)
class OwnPlayer:
    """Eigener Spieler; `last_step` = Marktwert-Änderung beim letzten Update (None =
    unbekannt). Nicht Kickbases Trend-Wert `mvt`: Scheller trug am 01.10.2026 `mvt` 2 bei
    +101.503 € „seit gestern“ — der Wert ist nicht die Richtung des letzten Updates."""

    player_id: str
    name: str
    mv: int
    last_step: int | None
    in_lineup: bool = False


@dataclass(frozen=True)
class PositionRow:
    player_id: str
    name: str
    buy_price: int
    bought_at: datetime
    mv: int
    pnl: int
    signal: str
    last_step: int | None
    in_lineup: bool = False


# ------------------------------------------------------------------ reine Funktionen


def _utc(stamp: datetime) -> datetime:
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def daily_values(points: Iterable[tuple[datetime, int]]) -> dict[date, int]:
    """Ein Wert je UTC-Tag — der jüngste des Tages."""
    latest: dict[date, tuple[datetime, int]] = {}
    for stamp, value in points:
        at = _utc(stamp)
        day = at.date()
        if day not in latest or at >= latest[day][0]:
            latest[day] = (at, int(value))
    return {day: value for day, (_, value) in latest.items()}


def momentum_at(values: Mapping[date, int], day: date) -> Momentum | None:
    """Marktwert an `day` gegen 1, 3 und 7 Tage davor; ohne Wert an `day` None."""
    now = values.get(day)
    if not now:
        return None

    def rise(back: int) -> float | None:
        before = values.get(day - timedelta(days=back))
        return now / before - 1 if before else None

    return Momentum(mv=now, r1=rise(1), r3=rise(3), r7=rise(7))


def price_class(mv: int) -> str | None:
    if mv < MIN_MV or mv > MAX_MV:
        return None
    return "billig" if mv < CHEAP_LIMIT else "mittel"


def _is_wave(m: Momentum | None) -> bool:
    if m is None or m.r3 is None or m.r1 is None or m.r1 <= 0:
        return False
    if m.r3 >= MIN_RISE_3D:
        return True
    return (
        m.mv >= CHEAP_LIMIT
        and m.r3 >= MIN_RISE_3D_MITTEL
        and m.mv * m.r3 >= MIN_GAIN_3D_MITTEL_EUR
        and m.mv * m.r1 >= MIN_GAIN_1D_MITTEL_EUR
    )


def momentum_bucket(r3: float) -> str:
    """Steigungsstufe des 3-Tage-Anstiegs (die Welle beginnt bei `MIN_RISE_3D` bzw. `1,5 %`)."""
    if r3 < MIN_RISE_3D:
        return "1.5-5"
    return "5-10" if r3 < 0.10 else "10-15" if r3 < 0.15 else ">=15"


def _rise_stats(
    history: Mapping[str, Mapping[date, int]], today: date, lookback: int, key: Any,
) -> dict[Any, ClassStats]:
    """Median-Anstieg 1 … 7 Tage nach einem Steiger-Signal je Gruppe (`key(Klasse, Momentum)`):
    je Spieler EIN Wert (Median seiner Signal-Tage), dann der Median über die Spieler; n =
    Spieler. Nur Startpunkte, deren Wert nach der Haltezeit am Stichtag schon bekannt ist."""
    per: dict[Any, dict[int, list[float]]] = {}
    for values in history.values():
        own: dict[Any, dict[int, list[float]]] = {}
        for back in range(1, lookback + 1):
            start = today - timedelta(days=back)
            m = momentum_at(values, start)
            cls = price_class(m.mv) if m else None
            if not cls or not _is_wave(m):
                continue
            group = key(cls, m)
            for h in range(1, min(back, FORWARD_DAYS) + 1):
                later = values.get(start + timedelta(days=h))
                if later:
                    own.setdefault(group, {}).setdefault(h, []).append(later / m.mv - 1)
        for group, by_h in own.items():
            for h, rises in by_h.items():
                per.setdefault(group, {}).setdefault(h, []).append(statistics.median(rises))
    out: dict[Any, ClassStats] = {}
    for group, by_h in per.items():
        by_days = {h: (statistics.median(xs) if xs else None, len(xs))
                   for h, xs in ((h, by_h.get(h, [])) for h in range(1, FORWARD_DAYS + 1))}
        median_f7, n = by_days[FORWARD_DAYS]
        out[group] = ClassStats(median_f7, n, by_days)
    return out


def class_stats(
    history: Mapping[str, Mapping[date, int]], today: date, *, lookback: int = STATS_LOOKBACK_DAYS
) -> dict[str, ClassStats]:
    """Je Preisklasse (billig/mittel): Median-Anstieg nach einem Steiger-Signal (gleiche
    Auswahl wie die Kandidaten), ein Wert je Spieler; beide Klassen sind immer da."""
    found = _rise_stats(
        history,
        today,
        lookback,
        lambda cls, m: cls if m.r3 is not None and m.r3 >= MIN_RISE_3D else f"{cls}_1.5-5",
    )
    return {cls: found.get(cls, ClassStats(None, 0, {h: (None, 0)
                                                     for h in range(1, FORWARD_DAYS + 1)}))
            for cls in ("billig", "mittel")}


def bucket_stats(
    history: Mapping[str, Mapping[date, int]], today: date, *, lookback: int = STATS_LOOKBACK_DAYS
) -> dict[tuple[str, str], ClassStats]:
    """Wie `class_stats`, aber je (Klasse, Steigungsstufe des Startpunkts)."""
    return _rise_stats(history, today, lookback, lambda cls, m: (cls, momentum_bucket(m.r3)))


def _basis(
    cls: str, bucket: str, days: int, stats: Mapping[str, ClassStats],
    buckets: Mapping[tuple[str, str], ClassStats] | None,
) -> tuple[ClassStats | None, str]:
    """Erwartung aus der eigenen Steigungsstufe, wenn sie genug Spieler hat, sonst die Klasse."""
    own = (buckets or {}).get((cls, bucket))
    if own is not None and own.at(days)[1] >= MIN_EVIDENCE:
        return own, f"Stufe {BUCKET_LABEL[bucket]}"
    return stats.get(cls), "Klasse"


def max_premium(
    stats: ClassStats | None, cls: str, days: int = FORWARD_DAYS
) -> tuple[float | None, str]:
    """(Aufschlag, Grundlage) für `days` Tage Haltezeit. None = kein Kauf: Die Welle trug in
    dieser Klasse über diese Zeit nicht."""
    median, n = stats.at(days) if stats is not None else (None, 0)
    if n < MIN_EVIDENCE or median is None:
        return FALLBACK_PREMIUM, "zu wenig Daten"
    if median <= 0:
        return None, "Welle trägt nicht"
    return min(PREMIUM_CAP[cls], PREMIUM_SHARE * median), "eigene Daten"


def _ceil_thousand(value: float) -> int:
    return int(math.ceil(round(value, 6) / 1000) * 1000)


def updates_between(start: datetime, end: datetime) -> int:
    """Zahl der Marktwert-Updates (22:00 Berlin) nach `start` und vor `end`."""
    begin, stop = _utc(start).astimezone(BERLIN), _utc(end).astimezone(BERLIN)
    count, day = 0, begin.date()
    while (update := datetime.combine(day, UPDATE_TIME, tzinfo=BERLIN)) <= stop:
        count += update > begin
        day += timedelta(days=1)
    return count


def buy_candidates(
    listings: Iterable[Listing],
    momentum: Mapping[str, Momentum],
    stats: Mapping[str, ClassStats],
    *,
    own_ids: set[str] | frozenset[str],
    jev_returns: set[str] | frozenset[str],
    sale_by: datetime | None = None,
    now: datetime | None = None,
    buckets: Mapping[tuple[str, str], ClassStats] | None = None,
    planned: set[str] | frozenset[str] = frozenset(),
) -> tuple[list[BuyRow], dict[str, int]]:
    """Kaufbare Steiger mit Höchstgebot — Jev-Rückkehrer zuerst, dann nach 3-Tage-Anstieg.

    Die Haltezeit endet am Verkaufstermin `sale_by` (`trade_book.sale_deadline`): Es zählt der
    Median über die Updates zwischen Auktionsende und Termin, höchstens `FORWARD_DAYS`; ohne
    ein einziges solches Update kein Kandidat (`skipped["keine_zeit"]`). Ohne Termin (keine
    Frist bekannt) gelten sieben Tage. Mit `now` fallen Auktionen weg, die in weniger als
    `MIN_AUCTION_LEFT` enden — außer mit geplantem Handelsgebot (`planned`): Das führt der
    Executor gleich aus, und nur als Kandidat durchläuft es weiter alle Prüfungen (Jev,
    Fitness, Rahmen, Deckung) statt geräumt oder ungeprüft gehalten zu werden (Codex-Review
    02.10.2026). Die Erwartung kommt aus der Steigungsstufe (`buckets`), sonst aus der Klasse;
    nur fitte Spieler mit KB-Stufe 1–3 (sonst lehnt die Schreibstelle ab)."""
    rows: list[BuyRow] = []
    skipped: dict[str, int] = {}
    for listing in listings:
        if listing.seller_id or listing.player_id in own_ids:
            continue
        if (now is not None and listing.player_id not in planned
                and _utc(listing.expires_at) < _utc(now) + MIN_AUCTION_LEFT):
            continue
        if listing.fitness_status != 0 or listing.lineup_prob not in TRADE_LINEUP_PROBS:
            continue
        m = momentum.get(listing.player_id)
        cls = price_class(listing.mv)
        if not cls or not _is_wave(m):
            continue
        horizon = FORWARD_DAYS
        if sale_by is not None:
            horizon = min(updates_between(listing.expires_at, sale_by), FORWARD_DAYS)
            if horizon < 1:
                skipped["keine_zeit"] = skipped.get("keine_zeit", 0) + 1
                continue
        bucket = momentum_bucket(m.r3)
        chosen, basis = _basis(cls, bucket, horizon, stats, buckets)
        premium, why = max_premium(chosen, cls, horizon)
        if premium is None:
            continue
        expected, evidence = chosen.at(horizon) if chosen else (None, 0)
        if why == "zu wenig Daten":
            basis = "zu wenig Daten"
        rows.append(BuyRow(
            player_id=listing.player_id, name=listing.name, team=listing.team,
            position=listing.position, mv=listing.mv, r1=m.r1, r3=m.r3, r7=m.r7,
            price_class=cls, premium=premium, max_bid=_ceil_thousand(listing.mv * (1 + premium)),
            expected=expected, evidence_n=evidence, expires_at=listing.expires_at,
            jev_return=listing.player_id in jev_returns, lineup_prob=listing.lineup_prob,
            horizon_days=horizon, forced_sale=horizon < FORWARD_DAYS, sell_by=sale_by,
            bucket=bucket, basis=basis,
        ))
    rows.sort(key=lambda r: (not r.jev_return, -(r.r3 or 0), r.player_id))
    return rows, skipped


def positions(
    squad: Iterable[OwnPlayer],
    book: Mapping[str, TradePosition],
    now: datetime,
    sale_by: datetime | None,
) -> list[PositionRow]:
    """Handelsspieler mit Signal: ab dem Tag des Verkaufstermins `sale_by` alle „verkaufen
    (Frist)“, sonst „verkaufen“ nach einem fallenden Update, sonst „halten“."""
    due = sale_by is not None and (
        _utc(now).astimezone(BERLIN).date() >= _utc(sale_by).astimezone(BERLIN).date()
    )
    rows: list[PositionRow] = []
    for player in squad:
        trade = book.get(player.player_id)
        if trade is None:
            continue
        falling = player.last_step is not None and player.last_step < 0
        signal = "verkaufen (Frist)" if due else "verkaufen" if falling else "halten"
        rows.append(PositionRow(player_id=player.player_id, name=player.name,
                                buy_price=trade.buy_price, bought_at=trade.bought_at,
                                mv=player.mv, pnl=player.mv - trade.buy_price, signal=signal,
                                last_step=player.last_step, in_lineup=player.in_lineup))
    rows.sort(key=lambda r: (_SIGNAL_RANK.get(r.signal, 3), r.pnl))
    return rows


def account_summary(
    *,
    budget: int,
    rows: Iterable[PositionRow],
    open_bids: int = 0,
    trade_bids: int = 0,
    swap_cover: int = 0,
) -> dict[str, Any]:
    """Konto nach Verkauf aller Handelsspieler zum Marktwert — offene eigene Gebote
    eingerechnet, als gewännen sie alle (gekoppelte `swap_sell`-Erlöse gegengerechnet,
    Konto ≥ 0 beim Anpfiff). Handelsrahmen = Kaufpreise der Handelsspieler + Handelsgebote."""
    rows = list(rows)
    exposure = sum(r.buy_price for r in rows) + trade_bids
    after = budget - open_bids + swap_cover + sum(r.mv for r in rows)
    return {"budget": budget, "open_bids": open_bids, "trade_bids": trade_bids,
            "swap_cover": swap_cover, "after_sale": after, "exposure": exposure,
            "room": EXPOSURE_CAP - exposure, "cap": EXPOSURE_CAP, "covered": after >= 0}


# ------------------------------------------------------------------ Texte


def _eur(value: int) -> str:
    sign = "-" if value < 0 else ""
    a = abs(int(value))
    text = f"{a / 1e6:.2f} M" if a >= 1_000_000 else f"{a / 1e3:.0f} k" if a >= 1_000 else f"{a} €"
    return sign + text.replace(".", ",")


def _signed_eur(value: int) -> str:
    return ("+" if value >= 0 else "") + _eur(value)


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value * 100:+.0f} %".replace(".", ",")


def _when(stamp: datetime) -> str:
    local = _utc(stamp).astimezone(BERLIN)
    return f"{_WEEKDAYS[local.weekday()]} {local:%d.%m. %H:%M}"


def _clock_time(stamp: datetime) -> str:
    local = _utc(stamp).astimezone(BERLIN)
    return f"{_WEEKDAYS[local.weekday()]} {local:%H:%M}"


def _updates(n: int) -> str:
    return f"{n} Update" if n == 1 else f"{n} Updates"


def _buy_line(row: BuyRow) -> str:
    days = f"{row.horizon_days} Tagen" if row.horizon_days != 1 else "1 Tag"
    expect = (f"Median {_pct(row.expected)} in {days} ({row.basis}, n = {row.evidence_n} "
              "Spieler)" if row.evidence_n >= MIN_EVIDENCE else "zu wenig Daten für eine Erwartung")
    sale = (f" · Verkauf spätestens {_when(row.sell_by)} ({_updates(row.horizon_days)})"
            if row.forced_sale and row.sell_by else "")
    jev = " · Rückkehr laut kicker (Jev)" if row.jev_return else ""
    return (f"• Kandidat: {row.name} ({row.team}, {_POS.get(row.position, '?')}) · MW "
            f"{_eur(row.mv)} · {_pct(row.r3)} in 3 Tagen · höchstens {_eur(row.max_bid)} "
            f"(+{row.premium * 100:.0f} %) · {expect} · Auktion bis {_when(row.expires_at)}"
            f"{sale}{jev}")


def _hold_reason(row: PositionRow) -> str:
    if row.last_step is None:
        return "letztes Update unbekannt"
    if row.last_step > 0:
        return "MW stieg beim letzten Update"
    return "MW unverändert beim letzten Update"


def _frist_reason(row: PositionRow, sale_by: datetime | None,
                  deadline: datetime | None, now: datetime) -> str:
    if sale_by is not None and _utc(now) <= _utc(sale_by):
        when = f"spätestens {_when(sale_by)}"
    else:
        when = f"sofort — Anpfiff-Frist {_when(deadline)}" if deadline else "sofort"
    hole = " · steht in der Elf: Ersatz aufstellen, sonst −100 Punkte" if row.in_lineup else ""
    return f"Verkaufstermin vor der Spieltags-Frist · {when}{hole}"


def _position_line(row: PositionRow, sale_by: datetime | None,
                   deadline: datetime | None, now: datetime) -> str:
    why = {"verkaufen": "MW fiel beim letzten Update — der Autopilot verkauft vor 22:00",
           "verkaufen (Frist)": _frist_reason(row, sale_by, deadline, now),
           "halten": _hold_reason(row)}.get(row.signal, "")
    return (f"• {_SIGNAL_HEAD.get(row.signal, row.signal)}: {row.name} · gekauft "
            f"{_eur(row.buy_price)} · MW {_eur(row.mv)} · {_signed_eur(row.pnl)} · {why}")


def _account_line(acc: Mapping[str, Any], deadline: datetime | None) -> str:
    frist = f" bis {_when(deadline)}" if deadline else ""
    status = "gedeckt" if acc["covered"] else "NICHT gedeckt — Handelsspieler reichen nicht"
    bids = (f" · offene Gebote {_eur(acc['open_bids'])} (eingerechnet)"
            if acc.get("open_bids") else "")
    return (f"Konto {_eur(acc['budget'])}{bids} · nach Verkauf aller Handelsspieler "
            f"{_eur(acc['after_sale'])} ({status}){frist} · Handelsrahmen "
            f"{_eur(acc['exposure'])} von {_eur(acc['cap'])}")


def format_trade_radar(
    buys: list[BuyRow],
    rows: list[PositionRow],
    acc: Mapping[str, Any],
    *,
    deadline: datetime | None,
    stats: Mapping[str, ClassStats],
    now: datetime,
    notes: Iterable[str] = (),
    jev_out: Iterable[Mapping[str, Any]] = (),
    jev_check: Mapping[str, Any] | None = None,
    sale_by: datetime | None = None,
) -> str:
    """Bericht ≤ 1900 Zeichen: Kopf, Konto, Jev-Prüfstatus und was Jev herausnahm (oben —
    die Kürzung trifft nur das Ende), Handelsspieler, Kandidaten, Hinweise, Regel.
    `sale_by` = Verkaufstermin aller Handelsspieler (`trade_book.sale_deadline`)."""
    local = _utc(now).astimezone(BERLIN)
    out = [f"Handels-Radar {_WEEKDAYS[local.weekday()]} {local:%d.%m.%Y}",
           _account_line(acc, deadline)]
    status = jev_status_line(jev_check)
    if status:
        out.append(status)
    removed = list(jev_out)
    if removed:
        out += ["", "Ohne Kauf — kicker meldet Ausfall (Jev):"]
        out += [f"• {row['name']} — {row['note']}" for row in removed]
    out.append("")
    if sale_by is not None:
        out.append(f"Verkaufstermin aller Handelsspieler: spätestens {_when(sale_by)}")
    out += ([_position_line(r, sale_by, deadline, now) for r in rows]
            or ["• keine Handelsspieler"])
    out.append("")
    if buys:
        out += [_buy_line(b) for b in buys[:MAX_BUY_LINES]]
    elif removed:
        out.append("• kein Kandidat übrig — Jev nahm alle heraus (siehe oben)")
    else:
        out.append(f"• kein Kandidat: keine Kickbase-Auktion mit laufender Welle "
                   f"(≥ +{MIN_RISE_3D * 100:.0f} % / 3 Tage)")
    if len(buys) > MAX_BUY_LINES:
        out.append(f"• … {len(buys) - MAX_BUY_LINES} weitere Kandidaten, hier nicht angezeigt")
    basis = " · ".join(
        f"{cls}: {_pct(s.median_f7)} in 7 Tagen (n = {s.n} Spieler)" for cls, s in stats.items()
    )
    out += ["", *notes, RULE_LINE, f"Grundlage (letzte {STATS_LOOKBACK_DAYS} Tage): {basis}",
            CLOSING_LINE]
    text = "\n".join(out)
    if len(text) > MAX_TEXT_LEN:
        keep = MAX_TEXT_LEN - len(CLOSING_LINE) - 3
        text = text[:keep].rstrip() + "…\n" + CLOSING_LINE
    return text


def jev_status_line(check: Mapping[str, Any] | None) -> str | None:
    """Prüfstatus des Jev-Checks als Zeile — None, wenn Jev alle Kandidaten prüfen konnte."""
    if not isinstance(check, Mapping) or check.get("status") in (None, "ok"):
        return None
    return f"{check.get('text') or 'Jev-Check unklar'}{_JEV_SUFFIX.get(check['status'], '')}"


def _summary_position(row: Mapping[str, Any]) -> str:
    head = _SIGNAL_HEAD[row["signal"]]
    hole = " · in der Elf: Ersatz aufstellen" if row.get("in_lineup") else ""
    return f"• {head}: {row['name']} · MW {_eur(row['mv'])} · {_signed_eur(row['pnl'])}{hole}"


def _summary_buy(row: Mapping[str, Any]) -> str:
    until = _clock_time(datetime.fromisoformat(row["expires_at"]))
    jev = " · Rückkehr laut kicker" if row.get("jev_return") else ""
    return (f"• Kandidat: {row['name']} · MW {_eur(row['mv'])} · {_pct(row['r3'])} in 3 Tagen · "
            f"höchstens {_eur(row['max_bid'])} · Auktion bis {until}{jev}")


def summary_lines(meta: Mapping[str, Any] | None) -> list[str]:
    """Zeilen für die Tageszusammenfassung: Kontozeile (bei Handelsspielern oder ohne Deckung),
    dann was zu tun ist — Verkaufen (Frist) vor Verkaufen, höchstens `SUMMARY_MAX_POS`, Halten
    nur gezählt —, dann höchstens `SUMMARY_MAX_BUY` Kandidaten; der Rest als Zahl."""
    meta = meta or {}
    raw_pos = meta.get("positionen") or []
    raw_buy = meta.get("kauf") or []
    held: list[Mapping[str, Any]] = []
    for row in raw_pos:
        try:
            if isinstance(row, Mapping) and row["signal"] in _SIGNAL_HEAD:
                _summary_position(row)
                held.append(row)
        except (KeyError, TypeError, ValueError):
            continue
    buys: list[str] = []
    for row in raw_buy:
        try:
            if isinstance(row, Mapping) and row.get("name"):
                buys.append(_summary_buy(row))
        except (KeyError, TypeError, ValueError):
            continue
    if (raw_pos or raw_buy) and not held and not buys:
        return ["• Handels-Radar-Bericht nicht lesbar — Cockpit prüfen"]
    lines: list[str] = []
    acc = meta.get("konto")
    if isinstance(acc, Mapping) and (held or acc.get("covered") is False):
        try:
            status = "gedeckt" if acc["covered"] else "NICHT gedeckt"
            frist = meta.get("frist")
            until = f" bis {_when(datetime.fromisoformat(frist))}" if frist else ""
            lines.append(f"• Konto nach Verkauf aller Handelsspieler "
                         f"{_eur(acc['after_sale'])} — {status}{until}")
        except (KeyError, TypeError, ValueError):
            pass
    act = sorted((r for r in held if r["signal"] != "halten"),
                 key=lambda r: (_SIGNAL_RANK[r["signal"]], r.get("pnl", 0)))
    lines += [_summary_position(r) for r in act[:SUMMARY_MAX_POS]]
    if len(act) > SUMMARY_MAX_POS:
        rest = len(act) - SUMMARY_MAX_POS
        lines.append(f"• … {rest} weitere{'r' if rest == 1 else ''} Handelsspieler im Cockpit")
    holding = len(held) - len(act)
    if holding:
        lines.append(f"• {holding} Handelsspieler halten (Details im Cockpit)")
    status = jev_status_line(meta.get("jev_check")) if buys else None
    if status:
        lines.append(f"• {status}")
    lines += buys[:SUMMARY_MAX_BUY]
    if len(buys) > SUMMARY_MAX_BUY:
        lines.append(f"• … {len(buys) - SUMMARY_MAX_BUY} weitere Kandidaten im Cockpit")
    removed = [r for r in (meta.get("jev_raus") or []) if isinstance(r, Mapping)]
    if removed:
        lines.append(f"• Jev nahm {len(removed)} Kandidaten raus (kicker meldet Ausfall) — Cockpit")
    return lines or ["• kein Kandidat, keine Handelsspieler"]


# ------------------------------------------------------------------ Verdrahtung


def _team(team_id: str) -> str:
    team = TEAMS.get(str(team_id))
    return team.kb_name if team else str(team_id or "?")


def _jev_returns(session: Any, now: datetime) -> set[str] | None:
    """Rückkehrer aus dem heutigen News-Radar (Berliner Tag); None = noch kein Bericht."""
    start = datetime.combine(_utc(now).astimezone(BERLIN).date(), time(0), tzinfo=BERLIN)
    report = session.scalars(
        select(Report).where(Report.source == NEWS_SOURCE,
                             Report.created_at >= start.astimezone(UTC).replace(tzinfo=None))
        .order_by(Report.created_at.desc()).limit(1)
    ).first()
    if report is None:
        return None
    rows = (report.meta or {}).get("kauf") or []
    return {str(r.get("player_id")) for r in rows if isinstance(r, dict) and r.get("player_id")}


def _reference_day(values: Mapping[date, int], now: datetime) -> date | None:
    """Jüngster Tag ≤ heute — höchstens einen Tag alt, sonst ist die Historie veraltet."""
    today = _utc(now).date()
    days = [d for d in values if d <= today]
    if not days:
        return None
    ref = max(days)
    return ref if ref >= today - timedelta(days=1) else None


def _last_step(player: Any, values: Mapping[date, int] | None, now: datetime) -> int | None:
    """Änderung beim letzten Update: Kickbases „seit gestern“ (`tfhmvt`), wenn beobachtet,
    sonst der letzte Tagesschritt der eigenen Historie; sonst unbekannt."""
    if player.mv_change_24h_observed:
        return int(player.mv_change_24h)
    ref = _reference_day(values or {}, now)
    if ref is None:
        return None
    before = (values or {}).get(ref - timedelta(days=1))
    return int(values[ref] - before) if before else None


def _jev_available() -> bool:
    """Ist ein TypeSafe-Schlüssel geladen? Ohne ihn fragt kein Judge (Tests setzen das)."""
    return bool(get_settings().typesafe_api_key)


async def _jev_check(
    client: Any, players: list[Any], fixtures: list[Any], now: datetime
) -> tuple[dict[str, RoleVerdict], dict[str, Any]]:
    """Rollenfrage der Engine für die Kandidaten: klares „fehlt“ ≥ `role_judge.MIN_P_ROLE`
    fürs nächste Bundesligaspiel. Rückgabe (Urteile je Spieler-ID, Prüfstatus
    {"status": ok | aus | pause | fehler | teilweise, "text": …}).

    In einer Ausfall-Pause fragt `judge_roles` nicht neu, liefert aber die Urteile, die Jev
    schon gab — genau die, mit denen die Engine gerade sperrt (Review 01.10.: früher ließ der
    Radar sie in der Pause weg). Der ganze Schritt hat `JEV_STEP_TIMEOUT_S`."""
    if not _jev_available():
        return {}, {"status": "aus", "text": "Jev-Check aus: kein TypeSafe-Schlüssel geladen"}
    paused = role_judge.is_paused()
    try:
        async with asyncio.timeout(JEV_STEP_TIMEOUT_S):
            team_names = await client.get_team_names()
            articles, rss_failed = await gather_articles(now)
            verdicts = await judge_roles(players, articles, team_names, now=now,
                                         fixtures=fixtures, pause_on_failure=False,
                                         purpose=JEV_PURPOSE)
        unjudged = role_judge.unjudged(players, articles, now=now, fixtures=fixtures)
    except TimeoutError:
        log.warning("Handels-Radar: Jev-Check nach %g s abgebrochen", JEV_STEP_TIMEOUT_S)
        return {}, {"status": "fehler",
                    "text": f"Jev-Check zu langsam (über {JEV_STEP_TIMEOUT_S:g} s)"}
    except Exception as exc:  # noqa: BLE001 — ohne Urteil bleibt der Kandidat, benannt
        log.warning("Handels-Radar: Jev-Check nicht möglich (%s)", type(exc).__name__,
                    exc_info=True)
        return {}, {"status": "fehler", "text": f"Jev-Check nicht möglich ({type(exc).__name__})"}
    if paused:
        return verdicts, {"status": "pause", "text": "Jev pausiert nach einem Ausfall — nur "
                                                     "bereits beurteilte Meldungen geprüft"}
    gaps = []
    if rss_failed:
        gaps.append("kicker nicht erreichbar, nur gespeicherte Anreißer")
    if unjudged:
        gaps.append(f"{unjudged} Meldungen ohne Urteil")
    if gaps:
        return verdicts, {"status": "teilweise",
                          "text": "Jev-Check unvollständig — " + ", ".join(gaps)}
    return verdicts, {"status": "ok", "text": ""}


def _meta(buys, rows, acc, deadline, stats, notes, jev_out=(), jev_check=None,
          sale_by=None) -> dict[str, Any]:
    def plain(row) -> dict[str, Any]:
        data = asdict(row)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = _utc(value).isoformat()
        return data

    return {
        "kauf": [plain(b) for b in buys],
        "positionen": [plain(r) for r in rows],
        "konto": dict(acc),
        "frist": _utc(deadline).isoformat() if deadline else None,
        "verkauf_bis": _utc(sale_by).isoformat() if sale_by else None,
        "grundlage": {cls: {"median_f7": s.median_f7, "n": s.n,
                            "by_days": {str(h): list(v) for h, v in s.by_days.items()}}
                      for cls, s in stats.items()},
        "hinweise": list(notes),
        "jev_raus": list(jev_out),
        "jev_check": dict(jev_check or {"status": "ok", "text": ""}),
        "regel": RULE_LINE,
    }


async def run_trade_radar(channel, *, now: datetime | None = None) -> str:
    """Ein Radar-Lauf: Markt, Kader, Konto, Gebote, Handelsbuch und Termine lesen, Bericht
    speichern.

    Rückgabe `ok:<Kandidaten>/<Handelsspieler>`, `no_league` ohne Liga. Kickbase wird nur
    gelesen (get_squad, get_market, get_budget, get_own_bids, get_matchdays, get_team_names,
    höchstens `MAX_HISTORY_LOOKUPS` × get_player_mv_history im Zeitbudget `HISTORY_BUDGET_S`);
    Jev beurteilt die Kandidaten (Jev-Protokoll). `channel` bleibt Teil der Job-Signatur;
    gesendet wird nur die Tageszusammenfassung. Scheitert das Speichern, fliegt ein Fehler."""
    del channel  # bewusst ungenutzt, siehe Docstring
    stamp = now or datetime.now(UTC)
    client, auth = await make_kickbase_client()
    lg = auth.league_id
    if lg is None:
        return "no_league"
    user_id = str(auth.user_id or "")

    squad = await client.get_squad(lg)
    market = await client.get_market(lg)
    budget = int(await client.get_budget(lg))
    own_bids = [b for b in await client.get_own_bids(lg, user_id) if isinstance(b, Mapping)]
    fixtures = await client.get_matchdays()
    found = matchday_deadline(list(fixtures), stamp)
    deadline = found[1] if found else None
    notes: list[str] = []
    live_bids = {str(b["player_id"]): int(b.get("amount") or 0)
                 for b in own_bids if b.get("player_id")}
    squad_ids = {str(sp.player.id) for sp in squad}

    squad_mv = {str(sp.player.id): int(sp.player.market_value) for sp in squad}

    with SessionLocal() as session:
        history = {pid: daily_values(points) for pid, points in
                   load_history(session, since=stamp - timedelta(days=HISTORY_DAYS)).items()}
        book = load_trade_book(session, now=stamp, squad_ids=squad_ids, live_bids=live_bids,
                               league_id=lg)
        open_sells = list(session.scalars(select(Intent).where(
            Intent.kind == "sell", Intent.status.in_(("planned", "executing"))
        )))
        swap_cover_eur, _ = live_swap_cover(
            open_sells, squad_mv=squad_mv, live_bids=live_bids, now=stamp
        )

    # Kandidat ist weder ein eigener Spieler noch einer mit laufendem Gebot (Engine-Ziel oder
    # schon platziertes Handelsgebot).
    own_ids = squad_ids | set(live_bids)
    listings = []
    for mp in market:
        player = mp.player
        listings.append(Listing(
            player_id=str(player.id), name=f"{player.first_name} {player.last_name}".strip(),
            team=_team(player.team_id), position=int(player.position),
            mv=int(player.market_value), price=int(mp.price), expires_at=_utc(mp.expires_at),
            seller_id=str(mp.seller_id or ""), lineup_prob=player.lineup_prob,
            fitness_status=player.fitness_status,
        ))

    # Verlauf je Kickbase-Auktion der Preisspanne von Kickbase (maßgeblich, begrenzt nach Zahl
    # und Zeit); die DB nur als Rückfall.
    live: dict[str, dict[date, int]] = {}
    started = _clock()
    for listing in listings:
        if listing.seller_id or listing.player_id in own_ids or not price_class(listing.mv):
            continue
        if len(live) >= MAX_HISTORY_LOOKUPS or _clock() - started > HISTORY_BUDGET_S:
            break
        try:
            snaps = await client.get_player_mv_history(lg, listing.player_id, 92)
        except Exception as exc:  # noqa: BLE001 — ein Spieler ohne Verlauf kippt den Lauf nie
            log.warning("Handels-Radar: Verlauf %s nicht lesbar (%s)", listing.player_id,
                        type(exc).__name__)
            snaps = []
        live[listing.player_id] = daily_values((s.date, s.value) for s in snaps)

    momentum: dict[str, Momentum] = {}
    sources = {"kickbase": 0, "db": 0, "keine": 0}
    for listing in listings:
        in_range = (not listing.seller_id and listing.player_id not in own_ids
                    and price_class(listing.mv))
        values = live.get(listing.player_id) or history.get(listing.player_id) or {}
        if in_range:
            sources["kickbase" if live.get(listing.player_id) else
                    "db" if history.get(listing.player_id) else "keine"] += 1
        ref = _reference_day(values, stamp)
        m = momentum_at(values, ref) if ref else None
        if m is not None:
            momentum[listing.player_id] = m
    notes.insert(0, f"Verlauf: {sources['kickbase']} von Kickbase, {sources['db']} aus der DB"
                    + (f", {sources['keine']} ohne Verlauf" if sources["keine"] else ""))

    # Erst jetzt den News-Radar lesen (Review 01.10.: er startet 08:11 mit Jitter und speichert
    # erst am Ende) — fehlt er noch, sagt der Bericht es statt still ohne Jev zu rechnen.
    with SessionLocal() as session:
        jev_returns = _jev_returns(session, stamp)
    if jev_returns is None:
        notes.append("Jev: News-Radar von heute fehlt noch — Rückkehr-Hinweise fehlen.")

    open_bids = sum(live_bids.values())
    sale_by = sale_deadline(deadline, regular_fill(
        int(sp.player.position) for sp in squad if str(sp.player.id) not in book.positions
    ))

    db_days = [d for values in history.values() for d in values if d <= _utc(stamp).date()]
    stats = class_stats(history, max(db_days)) if db_days else {}
    buckets = bucket_stats(history, max(db_days)) if db_days else {}
    own = [OwnPlayer(player_id=str(sp.player.id),
                     name=f"{sp.player.first_name} {sp.player.last_name}".strip(),
                     mv=int(sp.player.market_value),
                     last_step=_last_step(sp.player, history.get(str(sp.player.id)), stamp),
                     in_lineup=sp.lineup_order is not None)
           for sp in squad]
    rows = positions(own, book.positions, stamp, sale_by)
    acc = account_summary(budget=budget, rows=rows, open_bids=open_bids,
                          trade_bids=sum(book.bids.values()), swap_cover=swap_cover_eur)
    found_buys, skipped = buy_candidates(listings, momentum, stats, own_ids=own_ids,
                                         jev_returns=jev_returns or set(), sale_by=sale_by,
                                         now=stamp, buckets=buckets,
                                         planned=frozenset(book.pending))
    # Ein schon geplantes Handelsvorhaben steckt bereits im Rahmen — nicht doppelt zählen.
    buys = [b for b in found_buys if b.max_bid <= acc["room"] + book.pending.get(b.player_id, 0)]
    if len(buys) < len(found_buys):
        notes.append(f"{len(found_buys) - len(buys)} Kandidaten über dem Handelsrahmen "
                     f"(frei {_eur(max(acc['room'], 0))}) ausgelassen.")
    # Jev-Check (erst nach dem Handelsrahmen — gefragt wird nur, wer übrig ist): dieselbe
    # Rollenfrage wie die Kaufprüfung der Engine; ein klares „fehlt“ nimmt den Kandidaten
    # heraus, der Artikel steht im Bericht.
    jev_out: list[dict[str, Any]] = []
    jev_check: dict[str, Any] = {"status": "ok", "text": ""}
    if buys:
        by_id = {str(mp.player.id): mp.player for mp in market}
        verdicts, jev_check = await _jev_check(
            client, [by_id[b.player_id] for b in buys], list(fixtures), stamp
        )
        names = {b.player_id: b.name for b in buys}
        jev_out = [{"player_id": pid, "name": names[pid], "note": role_note(v),
                    "title": v.title, "url": v.url, "probability": v.probability,
                    "published_at": _utc(v.published_at).isoformat()}
                   for pid, v in verdicts.items() if pid in names]
        buys = [b for b in buys if b.player_id not in verdicts]
    if skipped.get("keine_zeit"):
        notes.append(f"{skipped['keine_zeit']} Kandidaten ohne Update vor dem Verkaufstermin "
                     "ausgelassen.")
    text = format_trade_radar(buys, rows, acc, deadline=deadline, stats=stats, now=stamp,
                              notes=notes, jev_out=jev_out, jev_check=jev_check,
                              sale_by=sale_by)

    with SessionLocal() as session:
        saved = save_report(
            REPORT_KIND, f"Handels-Radar {_utc(stamp).astimezone(BERLIN):%d.%m.%Y}", text,
            SOURCE, meta=_meta(buys, rows, acc, deadline, stats, notes, jev_out, jev_check,
                               sale_by),
            session=session,
            created_at=stamp,
        )
    if saved is None:
        raise RuntimeError("Handels-Radar: Bericht nicht gespeichert.")
    return f"ok:{len(buys)}/{len(rows)}"
