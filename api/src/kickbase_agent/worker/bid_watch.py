"""Gebots-Wächter: Eskalations-Erinnerung für offene Gebote kurz vor Listing-Ablauf.

Läuft ein Listing in die heiße Phase (< bid_ladder_raise_hours Restzeit),
erinnert dieser Wächter höchstens EINMAL an das individuell geprüfte Zielgebot.
Eine gültige gespeicherte Kaufprüfung begrenzt jedes Ziel; ohne Prüfung gibt
es keine Erhöhungsempfehlung.

Er führt sie NIE selbst aus. Über die Erhöhung entscheidet seit dem Vollmandat
(25.08.26) der Agent selbst, eine Freigabe braucht es dafür nicht mehr. Ob ein
erneutes place_bid ein bestehendes Gebot überschreibt oder erst ein cancel nötig
ist, ist aber ungeprüft (und Storno verbrennt den Offer-Slot, Otávio-Lehre) —
deshalb bleibt dieser Job eine reine Erinnerung: keine Writes, kein
Executor-Aufruf; die Erhöhung selbst läuft über den normalen Gebots-Pfad.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from kickbase_agent.db.models import TransferAction
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS, Guardrails
from kickbase_agent.decision.purchase_review import is_engine_review, purchase_review_veto
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.notify.discord_channel import DiscordChannel


def _fmt_m(value: int) -> str:
    # 2 Nachkommastellen wie in der Kauf-rationale: Gebot und Ziel liegen oft
    # so nah beieinander, dass 1 Nachkommastelle beide gleich zeigen würde.
    return f"{value / 1_000_000:.2f}M €"


def _fmt_rest(seconds: int) -> str:
    h, m = divmod(max(0, seconds) // 60, 60)
    return f"{h}h {m:02d}m"


def _key(bid: dict) -> tuple[str, str]:
    """Merker-Schlüssel je (Spieler, eigenes Gebot) — uoid identifiziert das
    Gebot auf genau diesem Listing."""
    return (str(bid.get("player_id")), str(bid.get("offer_id", "")))


# Erinnerungs-Merker: bewusst NUR im Prozess-Speicher (keine DB-Migration).
# Ein Neustart vergisst ihn — akzeptiert, weil das Erinnerungs-Fenster (siehe
# find_escalations) beim stündlichen Lauf ohnehin nur 1–2 Feuerungen je Listing
# zulässt; der Merker drückt es auf genau 1 pro Prozesslauf. Persistenz wäre
# für maximal einen Doppel-Ping nach Neustart Overkill.
_reminded: set[tuple[str, str]] = set()


def find_escalations(
    bids: list[dict],
    mv_by_pid: dict[str, int],
    guardrails: Guardrails = DEFAULT_GUARDRAILS,
    reminded: frozenset[tuple[str, str]] | set[tuple[str, str]] = frozenset(),
    *,
    purchase_reviews: dict[str, dict] | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Gebote, die JETZT eine Eskalations-Erinnerung verdienen (reine Funktion).

    Fenster statt bloßer Schwelle: erinnert nur bei Restzeit zwischen
    raise_hours−1h und raise_hours. So trifft der stündliche Wächter jedes
    Listing höchstens 1–2×, selbst wenn der In-Memory-Merker durch einen
    Neustart verloren ging. Mutiert `reminded` nicht — den Merker setzt der
    Aufrufer erst NACH erfolgreichem Post (kein verlorener Ping bei
    Discord-Fehlern).
    """
    upper = guardrails.bid_ladder_raise_hours * 3600
    lower = max(0.0, upper - 3600)
    stamp = now or datetime.now(UTC)
    out: list[dict] = []
    for b in bids:
        exs = int(b.get("expires_in_s", 0) or 0)
        if not lower < exs < upper:
            continue  # zu früh (Staffel hält noch) oder schon abgelaufen
        mv = mv_by_pid.get(str(b.get("player_id")))
        if not mv:
            continue  # Spieler nicht (mehr) im Markt → kein Ziel bestimmbar
        pid = str(b.get("player_id"))
        review = (purchase_reviews or {}).get(pid)
        if purchase_review_veto(
            review, player_id=pid, amount=b.get("amount"), now=stamp,
        ) is not None:
            continue
        assert review is not None
        engine = is_engine_review(review)
        if engine:
            target = next(s["amount"] for s in review["bid_scenarios"] if s["selected"])
        else:
            target = review.get("target_bid_eur", review["max_bid_eur"])
        if type(target) is not int or target <= 0:
            continue
        premium = (guardrails.max_bid_premium_need if review.get("squad_need")
                   else guardrails.max_bid_premium)
        target = min(target, review["max_bid_eur"], int(mv * premium))
        if engine and purchase_review_veto(
            review, player_id=pid, amount=target, now=stamp,
        ) is not None:
            continue  # Ein gekapptes Zwischengebot ist noch kein geprüftes Preisszenario.
        if int(b.get("amount", 0)) >= target:
            continue  # schon auf/über Ziel-Level — nichts zu erinnern
        if _key(b) in reminded:
            continue  # pro (Spieler, Listing) nur EINE Erinnerung
        out.append({**b, "target": target, "max_bid_eur": review["max_bid_eur"],
                    "review_reason": review["reasons"][0]})
    return out


def format_escalations(entries: list[dict]) -> str | None:
    """Discord-Karte — oder None, wenn nichts zu erinnern ist (leise)."""
    if not entries:
        return None
    lines = ["⏫ **Gebots-Eskalation empfohlen** — Listing läuft bald ab"]
    for e in entries:
        lines.append(
            f"• {e['name']}: Gebot {_fmt_m(int(e['amount']))} · Restzeit "
            f"{_fmt_rest(int(e['expires_in_s']))} · Ziel {_fmt_m(int(e['target']))} "
            f"· individuelles Limit {_fmt_m(int(e['max_bid_eur']))}. "
            f"{e['review_reason']}"
        )
    # Textpräzision (Review-Fund): »mach den Trade« ist ein Befehl an Claude im
    # CHAT — der Discord-Bot hier versteht keine Chat-Nachrichten (kein
    # on_message-Handler), er reagiert nur auf Buttons.
    lines.append(
        "-# Nur eine Erinnerung: Dieser Wächter erhöht nie selbst (Nachbieten auf ein "
        "bestehendes Gebot ist ungeprüft) — die Erhöhung entscheidet der Agent, eine "
        "Freigabe braucht es seit dem Vollmandat nicht. Anstoßen: sag Claude im Chat "
        "»mach den Trade« (der Discord-Bot liest keine Nachrichten)"
    )
    return "\n".join(lines)


# ------------------------------------------------- Schnäppchen-Radar (Emil-Idee)

#: Listing-Preis ≤ 85 % des Marktwerts gilt als Schnäppchen — NUR bei
#: Kickbase-Listings (ohne Konkurrenz gehen sie zum Listing-Preis weg, siehe
#: Veerman-Fall des Mitspielers). Manager-Listings sind seit dem err-5080-Beweis
#: (24.08.) raus: Kickbase verlangt dort ≥ 100 % MW, der „Discount" existiert nicht.
BARGAIN_MAX_RATIO = 0.85
#: Kleinstwerte rauschen nur (Discount in € bedeutungslos).
BARGAIN_MIN_MV = 1_000_000

_bargain_seen: set[tuple[str, int]] = set()


def find_bargains(
    market: list,
    own_bid_pids: set[str],
    squad_pids: set[str],
    seen: frozenset[tuple[str, int]] | set[tuple[str, int]] = frozenset(),
    guardrails: Guardrails = DEFAULT_GUARDRAILS,
) -> list[dict]:
    """KICKBASE-Listings deutlich unter Marktwert (reine Funktion über MarketPlayer).

    Eigene Gebote und eigene (gelistete) Kader-Spieler sind ausgeschlossen;
    `seen` verhindert Wiederholungs-Pings je (Spieler, Preis). Der Vorschlag
    ist bewusst das MINIMAL-Gebot (Listing-Preis + Psycho-Offset) — und nur
    als ERST-Gebot, denn ein Storno verbrennt den Offer-Slot (LEARNINGS).
    Manager-Listings fliegen raus: Kickbase verlangt dort ≥ 100 % MW (err 5080
    „UnderpayNotAllowed", 24.08.), und Sub-MW-Listings inaktiver Manager sind
    überwiegend Zombies — ein Minimal-Gebot am Listing-Preis wäre die Falle,
    die uns 5 Fehlversuche gekostet hat.
    """
    out: list[dict] = []
    for mp in market:
        price = int(getattr(mp, "price", 0) or 0)
        mv = int(mp.player.market_value or 0)
        pid = str(mp.player.id)
        if getattr(mp, "seller_id", None):
            continue  # Manager-Listing: kein Underpay möglich → kein Schnäppchen
        if price <= 0 or mv < BARGAIN_MIN_MV:
            continue
        if price > mv * BARGAIN_MAX_RATIO:
            continue
        if pid in own_bid_pids or pid in squad_pids:
            continue
        if (pid, price) in seen:
            continue
        out.append({
            "player_id": pid,
            "name": f"{mp.player.first_name} {mp.player.last_name}".strip(),
            "price": price,
            "mv": mv,
            "discount_pct": round((1 - price / mv) * 100),
            "suggested": price + guardrails.bid_psych_offset,
        })
    out.sort(key=lambda b: -b["discount_pct"])
    return out[:5]


def format_bargains(entries: list[dict]) -> str | None:
    """Discord-Karte fürs Schnäppchen-Radar — None wenn leer (leise)."""
    if not entries:
        return None
    lines = ["💎 **Schnäppchen-Radar** — Kickbase-Listings deutlich unter Marktwert"]
    for b in entries:
        lines.append(
            f"• {b['name']}: Listing {_fmt_m(b['price'])} bei MW {_fmt_m(b['mv'])} "
            f"(−{b['discount_pct']} %) → Minimal-Gebot {_fmt_m(b['suggested'])}"
        )
    lines.append(
        "-# Nur ERST-Gebote — ein Storno verbrennt den Offer-Slot. Zum Bieten "
        "sag Claude im Chat: »mach den Trade«"
    )
    return "\n".join(lines)


#: Sniper (Veerman-Fall): Kickbase-Listings, auf die bis kurz vor Ablauf
#: niemand geboten hat, gehen ans EINZIGE Gebot — egal wie niedrig (unser
#: Friedrich-Gebot 1,22M wurde unter Listing-Preis 1,46M akzeptiert).
SNIPE_WINDOW_S = 2 * 3600
SNIPE_MIN_MV = 3_000_000

_snipe_seen: set[str] = set()


def find_snipes(
    market: list,
    own_bid_pids: set[str],
    squad_pids: set[str],
    seen: frozenset[str] | set[str] = frozenset(),
) -> list[dict]:
    """Bald ablaufende Kickbase-Listings ohne eigenes Gebot (reine Funktion).

    Fremde Gebote sind UNSICHTBAR — der Sniper ist eine Wette auf leere
    Konkurrenz: kostet nichts, wenn überboten; gewinnt zum Minimal-Preis,
    wenn nicht. Nur relevante Marktwerte (Rauschen raus); die Score-Prüfung
    macht Emil/Claude vor dem Gebot (Cockpit → Markt).
    """
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    out: list[dict] = []
    for mp in market:
        pid = str(mp.player.id)
        mv = int(mp.player.market_value or 0)
        expires_at = getattr(mp, "expires_at", None)
        if expires_at is None or mv < SNIPE_MIN_MV:
            continue
        rest = (expires_at - now).total_seconds()
        if not 0 < rest < SNIPE_WINDOW_S:
            continue  # Kickbase-Listings haben exs→expires_at; Manager-Listings fallen raus
        if pid in own_bid_pids or pid in squad_pids or pid in seen:
            continue
        out.append({
            "player_id": pid,
            "name": f"{mp.player.first_name} {mp.player.last_name}".strip(),
            "mv": mv,
            "price": int(getattr(mp, "price", 0) or 0),
            "rest_s": int(rest),
        })
    out.sort(key=lambda s: s["rest_s"])
    return out[:5]


def format_snipes(entries: list[dict]) -> str | None:
    if not entries:
        return None
    lines = ["🎯 **Sniper-Fenster** — Kickbase-Listings laufen bald ab (Konkurrenz unbekannt)"]
    for s in entries:
        lines.append(
            f"• {s['name']}: MW {_fmt_m(s['mv'])} · Listing {_fmt_m(s['price'])} · "
            f"läuft in {_fmt_rest(s['rest_s'])} ab"
        )
    # Paket 1 (P0-6): kein „~90 % MW"-Versprechen mehr — ligaweit gingen 0 von 316
    # Käufen unter Marktwert weg, Auktionen verlangen einen Aufschlag.
    lines.append(
        "-# Zuschläge liegen in dieser Liga am oder über dem Marktwert zum "
        "Transferzeitpunkt; ein Gebot darunter verbrennt nur den Offer-Slot."
    )
    return "\n".join(lines)


async def run_bid_watch(channel: DiscordChannel) -> str:
    """Eigene Gebote prüfen: Eskalations-Erinnerung kurz vor Listing-Ablauf.

    Meldungs-Umbau (Emil, 31.08.): Schnäppchen-Radar und Sniper-Fenster posten
    NICHT mehr — sie waren mit 81 bid_watch-Posts/Woche der größte
    Lärmproduzent (das In-Memory-Dedupe hielt weder Neustarts noch die tägliche
    22-Uhr-MW-Änderung aus) und sind seit den Veto-Karten redundant: Was die
    Engine kaufen will, legt der Planner als Karte mit Buttons an; den Rest
    zeigt der Markt-Tab des Cockpits. Die Eskalations-Erinnerung bleibt — sie
    betrifft laufendes Geld mit echtem Zeitfenster (die +10/+20-%-Buttons der
    Karte sind der zugehörige Hebel).
    """
    client, auth = await make_kickbase_client()
    lg = auth.league_id
    if lg is None:
        return "no_league"
    bids = await client.get_own_bids(lg, auth.user_id)
    posted = False

    if bids:
        market = await client.get_market(lg)
        mv_by_pid = {mp.player.id: mp.player.market_value for mp in market}
        reviews = _load_purchase_reviews(lg, {str(b["player_id"]) for b in bids})
        escalations = find_escalations(
            bids, mv_by_pid, reminded=_reminded, purchase_reviews=reviews,
        )
        msg = format_escalations(escalations)
        if msg is not None:
            await channel.send_info(msg, kind="bid_watch")
            _reminded.update(_key(e) for e in escalations)  # erst nach Post
            posted = True

    return "posted" if posted else "quiet"


def _load_purchase_reviews(league_id: str, player_ids: set[str]) -> dict[str, dict]:
    """Nur tatsächlich abgegebene eigene Gebote tragen ein prüfbares Preislimit."""
    reviews: dict[str, dict] = {}
    with SessionLocal() as session:
        rows = session.scalars(
            select(TransferAction).where(
                TransferAction.league_id == league_id,
                TransferAction.player_id.in_(player_ids),
                TransferAction.action_type == "bid",
                TransferAction.status == "executed",
                TransferAction.dry_run.is_(False),
            ).order_by(TransferAction.created_at.desc(), TransferAction.id.desc())
        )
        seen: set[str] = set()
        for row in rows:
            pid = str(row.player_id)
            if pid in seen:
                continue
            seen.add(pid)
            payload = row.payload if isinstance(row.payload, dict) else {}
            review = payload.get("purchase_review")
            if isinstance(review, dict):
                reviews[pid] = {**review, "squad_need": bool(payload.get("squad_need"))}
    return reviews
