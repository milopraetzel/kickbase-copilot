"""News-Radar: rein lesender Morgenbericht mit Kader-Warnungen und Kaufsignalen aus kicker.

Schritt 4 des Jev-Umbaus (Emil 30.09.2026: „bau Schritt 4 mit den News"). Läuft 08:11 und
speichert einen Bericht `recherche` (Quelle `SOURCE`); die Tageszusammenfassung 08:15
(`worker/daily_summary.py`) übernimmt davon höchstens `SUMMARY_MAX_SQUAD` +
`SUMMARY_MAX_BUY` kurze Zeilen (`summary_lines`), der Bericht im Cockpit zeigt alle. Der
Radar legt KEIN Vorhaben an und ruft keine schreibende Client-Methode auf — gekauft wird nur
auf Emils namentlichen Auftrag je Spieler (CLAUDE.md §5.12), wie beim Steiger-Radar.

- **Kader-Warnungen** (zuerst, sie betreffen die eigene Elf): eigene Spieler mit klarem
  „fehlt" fürs nächste Bundesligaspiel (`intel/role_judge.py`, dieselbe Frage und Schwelle
  wie die Kaufsperre der Engine, 96 h nach dem letzten Spiel des Vereins).
- **Kaufsignale:** Kickbase-Listings (keine eigenen Spieler, keine Manager-Angebote) mit
  klarer Rückkehr nach Verletzung oder Sperre (`intel/news_signal.py`, ab 0,95, 48 h).
  Meldet die Rollenfrage für denselben Spieler „fehlt" im nächsten Spiel, steht das dabei.

Nebenwirkungsfrei (Nach-Review 30.09.2026): Frische Anreißer werden nur im Speicher
beurteilt, nicht gespeichert — sonst sähe der News-Bericht 10:33 die Meldungen der Nacht
nicht mehr als neu, und die Engine plante wegen des Radars neu. Seine Rollenfragen laufen
ohne Ausfall-Pause, damit ein Fehler hier die Kaufsperre der Engine nicht abschaltet.
Kein Schlüssel, pausiertes Jev, kicker-Ausfall, keine Anreißer und unbeurteilte Meldungen
stehen als Hinweis im Bericht, statt eine ruhige Lage vorzutäuschen.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from kickbase_agent.config import get_settings
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.intel import news_signal, role_judge
from kickbase_agent.intel.news_signal import SIGNAL_WINDOW, ReturnSignal, judge_returns
from kickbase_agent.intel.news_text import as_utc, club_name
from kickbase_agent.intel.role_judge import NEWS_WINDOW, RoleVerdict, judge_roles
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.kickbase.models import Fixture, MarketPlayer, Player
from kickbase_agent.news.kicker_rss import KickerRSSClient
from kickbase_agent.news.models import NewsArticle
from kickbase_agent.news.store import load_recent_articles
from kickbase_agent.notify.report_store import save_report

log = logging.getLogger(__name__)

SOURCE = "worker:news_radar"
REPORT_KIND = "recherche"
BERLIN = ZoneInfo("Europe/Berlin")
#: Zeilen je Abschnitt in der Tageszusammenfassung (1900 Zeichen für alles).
SUMMARY_MAX_SQUAD = 2
SUMMARY_MAX_BUY = 2
#: Titel im Bericht bzw. in der Zusammenfassung; der volle Titel steht in `meta`.
REPORT_TITLE = 80
SUMMARY_TITLE = 40
#: Leerer Radar ohne Hinweis: nichts gefunden, und Jev hat alles gelesen.
NEWS_EMPTY = "• keine Rückkehr am Markt, keine Kader-Warnung"

BASIS_LINE = (
    "Grundlage: Jev liest kicker-Anreißer. Kader-Warnung nur bei klarem „fehlt“ fürs "
    "nächste Bundesligaspiel (ab 95 %), Kaufsignal nur bei klarer Rückkehr nach Verletzung "
    "oder Sperre (ab 95 %, letzte 48 h; ein jüngerer wahrscheinlicher Ausfall hebt auf). "
    "Messung 29./30.09.2026."
)
CLOSING_LINE = "Kein Kauf ohne deinen Auftrag je Spieler (CLAUDE.md §5.12). Der Radar liest nur."

_POS = {1: "TW", 2: "ABW", 3: "MF", 4: "ST"}
_WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


# --- reine Funktionen ---------------------------------------------------------------


def _eur(value: int) -> str:
    """7_410_000 → „7,41 M", 850_000 → „850 k"."""
    a = abs(int(value))
    text = f"{a / 1e6:.2f} M" if a >= 1_000_000 else f"{a / 1e3:.0f} k"
    return ("-" if value < 0 else "") + text.replace(".", ",")


def _time_de(stamp: datetime) -> str:
    local = stamp.astimezone(BERLIN)
    return f"{_WEEKDAYS[local.weekday()]} {local:%H:%M}"


def _clip(title: str, limit: int) -> str:
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def _article(title: str, url: str, published_at: datetime, probability: float, source: str):
    return {
        "title": title,
        "url": url,
        "published_at": as_utc(published_at).isoformat(),
        "probability": round(probability, 3),
        "source": source,
    }


def buy_row(signal: ReturnSignal, listing: MarketPlayer, team_names: dict[str, str]) -> dict:
    """Meta-Zeile eines Kaufsignals: Spieler, Geld, Listing und Artikel."""
    player = listing.player
    return {
        "player_id": player.id,
        "name": f"{player.first_name} {player.last_name}".strip(),
        "last_name": player.last_name,
        "team": club_name(player.team_id, team_names),
        "position": int(player.position),
        "mv": int(player.market_value),
        "lineup_prob": player.lineup_prob,
        "listing": {
            "price": int(listing.price),
            "expires_at": as_utc(listing.expires_at).isoformat(),
        },
        "naechstes_spiel": None,
        **_article(signal.title, signal.url, signal.published_at, signal.probability,
                   signal.source),
    }


def squad_row(verdict: RoleVerdict, player: Player, team_names: dict[str, str]) -> dict:
    """Meta-Zeile einer Kader-Warnung: Spieler und Artikel."""
    return {
        "player_id": player.id,
        "name": f"{player.first_name} {player.last_name}".strip(),
        "last_name": player.last_name,
        "team": club_name(player.team_id, team_names),
        "position": int(player.position),
        **_article(verdict.title, verdict.url, verdict.published_at, verdict.probability,
                   verdict.source),
    }


def _source_text(row: dict[str, Any], title_len: int) -> str:
    when = datetime.fromisoformat(str(row["published_at"])).astimezone(BERLIN)
    return (
        f"„{_clip(str(row['title']), title_len)}“ ({row.get('source', 'kicker')} "
        f"{when:%d.%m.}, {round(float(row['probability']) * 100)} %)"
    )


def buy_line(row: dict[str, Any], *, title_len: int = REPORT_TITLE) -> str:
    """Eine Zeile Kaufsignal — für Bericht und (gekürzt) Tageszusammenfassung."""
    pos = _POS.get(int(row.get("position") or 0), "?")
    ends = datetime.fromisoformat(str(row["listing"]["expires_at"]))
    stage = row.get("lineup_prob")
    line = (
        f"• Kauf? {row['last_name']} ({row['team']}, {pos}) · MW {_eur(int(row['mv']))} · "
        f"KB {stage if stage is not None else '?'} · Auktion bis {_time_de(ends)} · "
        f"Rückkehr: {_source_text(row, title_len)}"
    )
    if row.get("naechstes_spiel") == "fehlt":
        line += " · nächstes Spiel: fehlt noch"
    return line


def squad_line(row: dict[str, Any], *, title_len: int = REPORT_TITLE) -> str:
    """Eine Zeile Kader-Warnung — für Bericht und (gekürzt) Tageszusammenfassung."""
    pos = _POS.get(int(row.get("position") or 0), "?")
    return (
        f"• Kader: {row['last_name']} ({row['team']}, {pos}) fehlt: "
        f"{_source_text(row, title_len)}"
    )


def _newest_first(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r["published_at"], r["probability"]), reverse=True)


def summary_lines(meta: dict[str, Any]) -> tuple[str, ...]:
    """Kurze Zeilen für die Tageszusammenfassung: Hinweis, Kader zuerst, dann Kauf.

    Eine unlesbare Zeile wird benannt, nie die ganze Quelle; ohne Zeilen und ohne Hinweis
    steht `NEWS_EMPTY`, mit Hinweis nur der Hinweis (keine behauptete ruhige Lage)."""
    unreadable = "• (eine Zeile nicht lesbar — Cockpit prüfen)"
    if not isinstance(meta, dict):
        return ("• (News-Radar nicht lesbar — Cockpit prüfen)",)
    lines = [f"• Hinweis: {meta['hinweis']}"] if meta.get("hinweis") else []
    rest = 0
    for key, build, limit in (("kader", squad_line, SUMMARY_MAX_SQUAD),
                              ("kauf", buy_line, SUMMARY_MAX_BUY)):
        rows = meta.get(key)
        if rows is not None and not isinstance(rows, list):
            lines.append(unreadable)
            continue
        for index, row in enumerate(rows or []):
            if index >= limit:
                rest += 1
                continue
            try:
                lines.append(build(row, title_len=SUMMARY_TITLE))
            except Exception:  # noqa: BLE001 — eine kaputte Zeile, nie die ganze Nachricht
                lines.append(unreadable)
    if rest:
        lines.append(f"• … {rest} weitere im Cockpit")
    if not lines:
        lines.append(NEWS_EMPTY)
    return tuple(lines)


def format_news_radar(
    buy: list[dict], squad: list[dict], *, now: datetime, hint: str | None, articles: int
) -> str:
    """Berichtstext für das Cockpit: Kopf, Grundlage, alle Zeilen, Schluss."""
    out = [f"News-Radar {now.astimezone(BERLIN):%d.%m.%Y} · {articles} kicker-Anreißer geprüft"]
    if hint:
        out.append(f"Hinweis: {hint}")
    out += [BASIS_LINE, "", "Kader-Warnungen (fehlt im nächsten Bundesligaspiel):"]
    out += [squad_line(r) for r in squad] or ["• keine"]
    out += ["", "Kaufsignale (Rückkehr am Transfermarkt):"]
    out += [buy_line(r) for r in buy] or ["• keine"]
    out += ["", CLOSING_LINE]
    return "\n".join(out)


def jev_paused() -> bool:
    """Wartet einer der beiden Judges nach einem Ausfall?"""
    return news_signal.is_paused() or role_judge.is_paused()


def count_unjudged(
    market: list[Player], squad: list[Player], returners: list[Player],
    articles: list[NewsArticle], *, now: datetime, fixtures: list[Fixture],
) -> int:
    """Paare ohne gültiges Urteil (Ausfall, Obergrenze, ungültige Antwort): Rückkehr-Frage
    für den Markt, Rollenfrage für den Kader und für die Rückkehrer („nächstes Spiel")."""
    return (
        news_signal.unjudged(market, articles, now=now, fixtures=fixtures)
        + role_judge.unjudged([*squad, *returners], articles, now=now, fixtures=fixtures)
    )


def _hint(*, rss_failed: bool, articles: int, unjudged: int) -> str | None:
    if not get_settings().typesafe_api_key:
        return "Kein TypeSafe-Schlüssel geladen — Jev liest nicht, dieser Radar ist leer."
    notes = []
    if jev_paused():
        notes.append("Jev nicht erreichbar — Signale unvollständig")
    if rss_failed:
        notes.append("kicker nicht erreichbar — nur gespeicherte Anreißer")
    if not articles:
        notes.append("keine kicker-Anreißer der letzten 96 h")
    if unjudged:
        notes.append(f"{unjudged} Meldungen nicht beurteilt (Ausfall, Obergrenze oder "
                     "ungültige Antwort)")
    return " · ".join(notes) or None


# --- Verdrahtung --------------------------------------------------------------------


async def gather_articles(stamp: datetime) -> tuple[list[NewsArticle], bool]:
    """Gespeicherte kicker-Anreißer der Fenster plus frische (nur im Speicher, nie gespeichert)
    — (Artikel, kicker nicht erreichbar). Auch der Handels-Radar fragt Jev damit."""
    rss_failed = False
    try:
        fresh = await KickerRSSClient().fetch()
    except Exception as exc:  # noqa: BLE001 — ohne frische Anreißer gilt der Bestand
        log.warning("News-Radar: kicker nicht erreichbar (%s), Bestand genügt",
                    type(exc).__name__)
        rss_failed, fresh = True, []
    since = stamp - max(NEWS_WINDOW, SIGNAL_WINDOW)
    with SessionLocal() as session:
        stored = load_recent_articles(session, since)
    known = {a.content_hash() for a in stored}
    articles = [
        *stored,
        *(a for a in fresh if a.content_hash() not in known and as_utc(a.published_at) >= since),
    ]
    return articles, rss_failed


async def run_news_radar(channel, *, now: datetime | None = None) -> str:
    """Ein Radar-Lauf: Anreißer lesen, Jev fragen, Bericht speichern.

    Rückgabe `ok:<Kaufsignale>/<Kader-Warnungen>`, `no_league` ohne Liga. Kickbase wird
    ausschließlich GELESEN (get_squad, get_market, get_team_names, get_matchdays); in die
    DB schreibt der Radar nur seinen Bericht und das Jev-Protokoll seiner Fragen. `channel`
    bleibt Teil der Job-Signatur, gesendet wird nur die Tageszusammenfassung. Scheitert das
    Speichern, fliegt ein Fehler.
    """
    del channel  # bewusst ungenutzt, siehe Docstring
    stamp = now or datetime.now(UTC)
    client, auth = await make_kickbase_client()
    lg = auth.league_id
    if lg is None:
        return "no_league"

    squad = [sp.player for sp in await client.get_squad(lg)]
    market = await client.get_market(lg)
    team_names = await client.get_team_names()
    fixtures = await client.get_matchdays()
    articles, rss_failed = await gather_articles(stamp)

    own_ids = {p.id for p in squad}
    listings = {
        mp.player.id: mp for mp in market if not mp.seller_id and mp.player.id not in own_ids
    }
    absences = await judge_roles(squad, articles, team_names, now=stamp, fixtures=fixtures,
                                 pause_on_failure=False, purpose="kader")
    returns = await judge_returns(
        [mp.player for mp in listings.values()], articles, team_names, now=stamp,
        fixtures=fixtures,
    )
    still_out: dict[str, RoleVerdict] = {}
    if returns:
        still_out = await judge_roles(
            [listings[pid].player for pid in returns if pid in listings], articles, team_names,
            now=stamp, fixtures=fixtures, pause_on_failure=False, purpose="naechstes_spiel",
        )
    by_id = {p.id: p for p in squad}
    warnings = _newest_first(
        [squad_row(v, by_id[pid], team_names) for pid, v in absences.items() if pid in by_id]
    )
    buy = []
    for pid, signal in returns.items():
        if pid in listings:
            row = buy_row(signal, listings[pid], team_names)
            row["naechstes_spiel"] = "fehlt" if pid in still_out else None
            buy.append(row)
    buy = _newest_first(buy)
    unjudged = count_unjudged(
        [mp.player for mp in listings.values()], squad,
        [listings[pid].player for pid in returns if pid in listings], articles,
        now=stamp, fixtures=fixtures,
    )
    hint = _hint(rss_failed=rss_failed, articles=len(articles), unjudged=unjudged)
    text = format_news_radar(buy, warnings, now=stamp, hint=hint, articles=len(articles))
    with SessionLocal() as session:
        saved = save_report(
            REPORT_KIND,
            f"News-Radar {stamp.astimezone(BERLIN):%d.%m.%Y}",
            text,
            SOURCE,
            meta={"kauf": buy, "kader": warnings, "hinweis": hint, "artikel": len(articles)},
            session=session,
            created_at=stamp,
        )
    if saved is None:
        raise RuntimeError("News-Radar: Bericht nicht gespeichert.")
    return f"ok:{len(buy)}/{len(warnings)}"
