"""Trade-Budget: interne Liga-Regel — max. 200 Transfers pro Halbserie.

Nicht in den App-Einstellungen sichtbar (menschliche Abmachung der Liga BASE),
darum hier hart verankert. Gezählt wird jeder ABGESCHLOSSENE Transfer (Kauf mit
Zuschlag oder Verkauf) über den Activity-Feed (t=15, byr/slr == unser Liga-Name).

Grenzwerte: ab 85 % Warnung im Briefing, ab (Limit − Notreserve) schlägt die
Engine KEINE Trades mehr vor — die letzten 5 bleiben für Notbremse/Verletzungen.

Feed-Fenster (Review-Fund): Der Feed-Endpoint kennt nur den verifizierten
`max`-Parameter (endpoints/Client, live geprobt); ein Offset-/`start`-Parameter
ist nirgends belegt und wird ohne Live-Probe NICHT erfunden. Kommt der Feed
VOLL zurück (== FEED_MAX_ITEMS), sind ältere Einträge unsichtbar — `used` ist
dann nur eine UNTERGRENZE (`count_saturated=True`). Konservative Konsequenz:
die Sperre gilt als aktiv (die 195er-Grenze ist nicht mehr verifizierbar) und
jede Anzeige muss „≥ N" + Warnung zeigen, nie ein stilles „3/200". Dauerlösung
laut Roadmap: persistenter Zähler, sobald die Halbserie >100 Feed-Items hat.
Kappt der Server künftig UNTER FEED_MAX_ITEMS, wäre Saturation so nicht
erkennbar — vor jeder Fenster-Änderung live proben.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

EMERGENCY_RESERVE = 5
WARN_FRACTION = 0.85
# Größtes Feed-Fenster, das der Endpoint nachweislich bedient (bisheriger
# Abrufwert; deckte die Halbserie bislang vollständig ab).
# Live geprobt 21.08.26: Der activitiesFeed liefert bei max=1000 den KOMPLETTEN
# Feed (aktuell 576 Items, stabile Item-IDs `i`) — die alte 100er-Grenze war
# NUR unser Parameter und ließ die Saturations-Sperre falsch greifen (rote
# Ampel, Engine-Pause). 1000 deckt die Liga bis ~Oktober; wächst der Feed
# darüber, greift der Fail-Safe wieder → dann persistenter Zähler (Dedupe
# über Item-ID `i` ist vorbereitet, Backlog).
FEED_MAX_ITEMS = 1000


@dataclass(frozen=True)
class TradeUsage:
    """Ergebnis der Feed-Zählung.

    `used` ist bei `count_saturated=True` nur eine Untergrenze: der Feed kam
    voll zurück, ältere eigene Transfers können fehlen.
    """

    used: int
    my_name: str
    count_saturated: bool = False
    # Aufteilung (Emils Fund 25.08.: „10 Trades" verriet nicht, dass sechs davon
    # Verkäufe waren). Die Liga-Regel zählt beides — aber wer die Zahl liest,
    # muss sehen, wofür sie verbraucht wurde.
    buys: int = 0
    sells: int = 0

    def __iter__(self) -> Iterator[object]:
        # Kompatibilität für Alt-Aufrufer (`used, name = await get_trade_usage(...)`,
        # z. B. cli/research.py) — neue Aufrufer nutzen die Attribute + Flag.
        yield self.used
        yield self.my_name


def count_my_trades(feed: list[dict], my_name: str) -> int:
    """Abgeschlossene eigene Transfers im Feed (Kauf-Zuschläge + Verkäufe)."""
    buys, sells = split_my_trades(feed, my_name)
    return buys + sells


def split_my_trades(feed: list[dict], my_name: str) -> tuple[int, int]:
    """(Käufe, Verkäufe) — getrennt, weil die reine Summe irreführt.

    Beide zählen gegen die 200er-Liga-Regel; für die Beurteilung („waren wir
    aktiv oder haben wir nur aufgeräumt?") ist die Aufteilung entscheidend.
    """
    buys = sells = 0
    for it in feed:
        if it.get("t") != 15:
            continue
        d = it.get("data", {})
        if d.get("byr") == my_name:
            buys += 1
        elif d.get("slr") == my_name:
            sells += 1
    return buys, sells


def trades_blocked(used: int, limit: int, count_saturated: bool = False) -> bool:
    """True = Engine darf keine neuen Trades mehr vorschlagen (Notreserve schützen).

    Saturierter Feed → Sperre aktiv: `used` ist nur eine Untergrenze, das
    Erreichen von (Limit − Notreserve) lässt sich nicht mehr ausschließen.
    Fail-safe statt stiller Fehlzählung — manuelle/Notbremsen-Trades bleiben
    möglich, die Dauerlösung ist der persistente Zähler (siehe Modul-Docstring).
    """
    if count_saturated:
        return True
    return used >= limit - EMERGENCY_RESERVE


def budget_line(used: int, limit: int, count_saturated: bool = False) -> str:
    if count_saturated:
        # NIE ein stilles "N/limit" bei abgeschnittenem Feed: Untergrenze
        # kennzeichnen + rote Ampel (Sperre aktiv, s. trades_blocked).
        return (
            f"🔴 Trade-Budget: ≥{used}/{limit} (Halbserie) — Feed voll, "
            f"Zählung ist Untergrenze; Engine pausiert bis persistenter Zähler."
        )
    pct = used / limit if limit else 0.0
    flag = "🔴" if trades_blocked(used, limit) else ("🟠" if pct >= WARN_FRACTION else "🟢")
    return f"{flag} Trade-Budget: {used}/{limit} (Halbserie)"


async def get_trade_usage(client, league_id: str, user_id: str) -> TradeUsage:
    """Feed-Zählung der eigenen Transfers dieser Halbserie (+ Liga-Name).

    Holt das größte verifizierte Feed-Fenster (`max=FEED_MAX_ITEMS`). Kommt der
    Feed voll zurück, ist er abgeschnitten → `count_saturated=True` und `used`
    nur Untergrenze (Details im Modul-Docstring).
    """
    ranking = await client.get_ranking(league_id)
    me = next((r.name for r in ranking if r.user_id == user_id), "")
    feed = await client.get_activity_feed(league_id, max_items=FEED_MAX_ITEMS)
    buys, sells = split_my_trades(feed, me)
    return TradeUsage(
        used=buys + sells,
        my_name=me,
        count_saturated=len(feed) >= FEED_MAX_ITEMS,
        buys=buys,
        sells=sells,
    )
