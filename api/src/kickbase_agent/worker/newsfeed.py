"""News-Update für Discord: erst die Lage (News + Spieler + Liga), dann Schlussfolgerungen.

Emils Format-Wunsch: ZWEI Nachrichten — ① „was ist passiert" (kicker-News,
Spieler-Status via LigaInsider, Liga-Feed mit Rivalen-Transfers), ② „was heißt das
für uns + wie reagieren wir". Die Schlussfolgerungen sind deterministisch (laufen
24/7 ohne API-Guthaben); die tiefe KI-Analyse bleibt der Morgen-Recherche.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from kickbase_agent.db.session import SessionLocal
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.news.kicker_rss import KickerRSSClient
from kickbase_agent.news.models import NewsArticle
from kickbase_agent.news.store import persist_articles
from kickbase_agent.notify.discord_channel import DiscordChannel
from kickbase_agent.scouts.ligainsider.models import InjuryEntry
from kickbase_agent.scouts.ligainsider.scraper import fetch_snapshot
from kickbase_agent.scouts.ligainsider.teams import TEAMS


def _m(v: int | float) -> str:
    return f"{v / 1_000_000:.1f}M"


@dataclass
class NewsBundle:
    transfers: list[dict] = field(default_factory=list)   # Feed t=15 (byr/slr, pn, trp)
    market_events: list[dict] = field(default_factory=list)  # Feed t=3 (ln, mv)
    injuries: list[InjuryEntry] = field(default_factory=list)
    news: list[NewsArticle] = field(default_factory=list)
    squad_names: list[str] = field(default_factory=list)
    bid_names: list[str] = field(default_factory=list)     # Namen unserer offenen Gebote
    watch_names: list[str] = field(default_factory=list)   # Strategie-Watchlist


def split_feed(feed: list[dict], since_hours: int = 26) -> tuple[list[dict], list[dict]]:
    """Feed in Transfers (t=15) und Markt-Events (t=3) der letzten N Stunden teilen."""
    cutoff = datetime.now(UTC) - timedelta(hours=since_hours)
    transfers, events = [], []
    for it in feed:
        try:
            when = datetime.fromisoformat(str(it.get("dt", "")).replace("Z", "+00:00"))
        except ValueError:
            when = datetime.now(UTC)
        if when < cutoff:
            continue
        if it.get("t") == 15:
            transfers.append(it)
        elif it.get("t") == 3:
            events.append(it)
    return transfers, events


def _transfer_line(it: dict) -> str:
    d = it.get("data", {})
    price = _m(int(d.get("trp", 0)))
    who = d.get("byr") or d.get("slr") or "?"
    verb = "kauft" if d.get("byr") else "verkauft"
    return f"• **{who}** {verb} {d.get('pn', '?')} für {price}"


def format_update(b: NewsBundle) -> str:
    """Nachricht ①: die Lage — Liga, Spieler, Presse."""
    lines = [f"📰 **Update · {datetime.now().strftime('%d.%m. %H:%M')}**"]
    if b.transfers:
        lines.append("__🏟 Liga (Rivalen-Transfers)__")
        lines += [_transfer_line(t) for t in b.transfers[:6]]
    if b.market_events:
        names = ", ".join(
            f"{e.get('data', {}).get('ln', '?')} ({_m(int(e.get('data', {}).get('mv', 0)))})"
            for e in b.market_events[:6]
        )
        lines.append(f"__🛒 Markt-Events__: {names}")
    if b.injuries:
        inj = " · ".join(
            f"{i.player_name} ({TEAMS[i.team_tid].kb_name if i.team_tid in TEAMS else i.team_tid})"
            for i in b.injuries[:6]
        )
        lines.append(f"__🩺 Neue/aktive Ausfälle__: {inj}")
    if b.news:
        lines.append("__🗞 Presse (neu)__")
        lines += [f"• {a.title[:90]}" for a in b.news[:6]]
    if len(lines) == 1:
        lines.append("-# ruhige Lage — nichts Neues seit dem letzten Update")
    return "\n".join(lines)[:1900]


def build_conclusions(b: NewsBundle) -> str:
    """Nachricht ②: Schlussfolgerungen + wie wir reagieren (deterministisch)."""
    out: list[str] = ["🧠 **Schlussfolgerungen & Reaktion**"]

    # 1) Rivale schnappt sich eines unserer Gebots-Ziele?
    taken = [t for t in b.transfers
             if t.get("data", {}).get("byr")
             and any(n and n in str(t.get("data", {}).get("pn", "")) for n in b.bid_names)]
    for t in taken:
        d = t["data"]
        out.append(
            f"⚠️ **{d.get('byr')}** hat unser Ziel **{d.get('pn')}** "
            f"({_m(int(d.get('trp', 0)))}) gekauft → Gebot hinfällig, "
            "Ersatzkandidat kommt in die nächste Recherche."
        )

    # 2) Große Rivalen-Käufe binden deren Budget → weniger Gebots-Konkurrenz für uns.
    big = [t for t in b.transfers
           if t.get("data", {}).get("byr") and int(t.get("data", {}).get("trp", 0)) >= 15_000_000
           and t not in taken]
    for t in big[:3]:
        d = t["data"]
        out.append(f"💰 **{d.get('byr')}** bindet {_m(int(d.get('trp', 0)))} in {d.get('pn')} "
                   f"→ weniger Konkurrenz bei den 22-Uhr-Geboten.")

    # 3) Ausfälle, die UNSEREN Kader treffen — mit Profi-Regel je nach Schwere. Das
    # TypeSafe-Urteil der Engine hat Vorrang vor der Wortsuche (Review 29.09.2026).
    from kickbase_agent.scouts.ligainsider.severity import sell_advice
    from kickbase_agent.scouts.ligainsider.severity_judge import cached_severity
    hit = [i for i in b.injuries
           if any(n and n in i.player_name for n in b.squad_names)]
    for i in hit:
        out.append(
            f"🔴 Kader-Alarm: **{i.player_name}** ({i.reason}) — fliegt automatisch "
            f"aus der Aufstellung. {sell_advice(i.reason, cached_severity(i.reason))}"
        )

    # 4) Presse, die Kader/Gebote/Watchlist berührt.
    watch = [n for n in (b.squad_names + b.bid_names + b.watch_names) if n]
    touched = [a for a in b.news if any(n in a.title for n in watch)]
    for a in touched[:3]:
        out.append(f'🗞 Relevant: „{a.title[:80]}" — beobachten wir (Watchlist/Kader betroffen).')

    if len(out) == 1:
        out.append("✅ Nichts davon ändert den Plan.")
    out.append(
        "▶️ **Reaktion**: Gebote laufen ("
        + (", ".join(n for n in b.bid_names if n) or "keine")
        + ") · Auto-Aufstellung Fr 19:45 · Welle 2 nach Spieltag 1 · Purge-Regel aktiv."
    )
    return "\n".join(out)[:1900]


async def run_news_update(channel: DiscordChannel) -> None:
    client, auth = await make_kickbase_client()
    lg = auth.league_id
    if lg is None:
        return

    feed = await client.get_activity_feed(lg, max_items=30)
    transfers, events = split_feed(feed)

    squad = await client.get_squad(lg)
    squad_names = [sp.player.last_name or "" for sp in squad]

    # Offene/heutige Gebote aus dem Audit-Log → Namen via Markt auflösen.
    market = await client.get_market(lg)
    name_by_id = {mp.player.id: (mp.player.last_name or "") for mp in market}
    from sqlalchemy import select

    from kickbase_agent.db.models import TransferAction
    with SessionLocal() as session:
        rows = session.execute(
            select(TransferAction.player_id).where(
                TransferAction.action_type == "bid",
                TransferAction.status == "executed",
                TransferAction.created_at >= datetime.now(UTC) - timedelta(days=4),
            )
        ).all()
        bid_names = [name_by_id.get(r[0], "") for r in rows if r[0]]

        # kicker: nur NEUE Artikel (Hash-Dedup über den News-Store).
        try:
            articles = await KickerRSSClient().fetch()
        except Exception:  # noqa: BLE001
            articles = []
        from kickbase_agent.db.models import NewsItem
        known = {row[0] for row in session.query(NewsItem.content_hash).all()}
        fresh = [a for a in articles if a.content_hash() not in known]
        persist_articles(session, articles)

    snap = await fetch_snapshot()

    bundle = NewsBundle(
        transfers=transfers,
        market_events=events,
        injuries=snap.injuries[:12],
        news=fresh,
        squad_names=squad_names,
        bid_names=[n for n in bid_names if n],
        watch_names=["Kimmich", "Musiala", "Bellingham", "Džeko", "Schnellbacher"],
    )
    # Review-Fix: event-getrieben statt gesprächig — ohne echten Inhalt kein Post.
    has_content = bool(
        bundle.transfers or bundle.market_events or bundle.news
        or any(any(n and n in i.player_name for n in bundle.squad_names)
               for i in bundle.injuries)
    )
    if not has_content:
        return
    # Meldungs-Umbau (Emil, 31.08.): EIN Post statt zwei — Lage und
    # Schlussfolgerungen gehören zusammen; das 1900er-Limit clippt notfalls.
    combined = f"{format_update(bundle)}\n\n{build_conclusions(bundle)}"
    await channel.send_info(combined if len(combined) <= 1900
                            else combined[:1898].rstrip() + " …")
