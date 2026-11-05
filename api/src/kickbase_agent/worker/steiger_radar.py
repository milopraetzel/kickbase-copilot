"""Steiger-Radar: rein lesender Tagesbericht mit 2–5 Kaufkandidaten für Marktwert-Handel.

Anlass (Emils Entscheidung 21.09.2026): In der Länderspielpause bis Fr 09.10.2026
will Emil täglich Kandidaten sehen und selbst in der App kaufen — je Spieler, auf
eigenen Auftrag (CLAUDE.md §5.12). Dieses Modul legt KEIN Vorhaben an und ruft
keine schreibende Client-Methode auf.

Datenbefund der Analyse vom 21.09.2026 (Grundlage der Regel, im Bericht genannt):
nur STEIGER haben einen kleinen, schnell abklingenden Vorteil — Marktwert ≥ +10 %
über drei Tage, Marktwert 1–10 M, fit. „Billig kaufen, was gefallen ist" verliert
(0,3–1,8 % Gewinner). Auktionen brauchen +4–10 % Aufschlag; ein Verkauf an Kickbase
geht sofort zum Marktwert. Verkaufsregel: am ersten Tag ohne Gewinn verkaufen; alle
Handelspositionen bis Mo 05.10.2026 verkauft (Glättung vor der Frist).

Aufbau: `momentum_candidates` und `format_radar` sind rein (kein I/O) und ohne
Fakes testbar; `run_steiger_radar` verdrahtet DB-Historie (`player_history`, nur
die letzten fünf Tage), Markt, Kader und Konto und speichert den Bericht.

Zustellung (Emil 23.09.2026: „Discord soll wieder senden. Aber nur einmal am Tag
eine Zusammenfassung"): Der Radar sendet NICHT mehr selbst. Er speichert den Bericht
um 08:10; die Tageszusammenfassung um 08:15 (worker/daily_summary.py) übernimmt die
Kandidatenzeilen aus `meta["kandidaten"]`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from kickbase_agent.db.models import PlayerHistory
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.notify.report_store import save_report
from kickbase_agent.scouts.ligainsider.teams import TEAMS

SOURCE = "worker:steiger_radar"
#: Seit 23.09.2026 `recherche` statt `morgenbericht`: Der Radar ist ein rein lesender
#: Recherchebericht und Quelle der Tageszusammenfassung, nicht selbst die Nachricht
#: des Tages. `morgenbericht` hätte die Art mit dem Kanal verwechselbar gemacht.
REPORT_KIND = "recherche"
BERLIN = ZoneInfo("Europe/Berlin")

#: Wie viele Tage Marktwert-Historie der Job aus der DB liest (3-Tage-Vergleich + Luft).
HISTORY_DAYS = 5
#: Höchstens so viele Detailabrufe (`get_player`) je Lauf — Abrufgrenzen der API.
MAX_DETAIL_LOOKUPS = 24
#: Höchstens so viele Kandidatenzeilen im Bericht.
MAX_LINES = 5
#: KB-Startelfstufe: 1 sicher … 5 ausgeschlossen. Bis 3 gilt als Rolle vorhanden.
MAX_LINEUP_PROB = 3
#: Discord-Grenze des Info-Kanals.
MAX_TEXT_LEN = 1900

RULE_LINE = (
    "Regel: Steiger ≥ +10 % / 3 Tage, 1–10 M, fit; Verkauf am ersten Tag ohne Gewinn; "
    "alles glatt bis Mo 05.10."
)
BASIS_LINE = (
    "Grundlage: Analyse 21.09.2026 — nur Steiger haben einen kleinen, schnell abklingenden "
    "Vorteil; „billig kaufen, was gefallen ist“ verliert (0,3–1,8 % Gewinner). "
    "Verkauf an Kickbase sofort zum Marktwert."
)
CLOSING_LINE = "Kein Kauf ohne deinen Auftrag je Spieler (CLAUDE.md §5.12). Der Radar liest nur."
AUCTION_HINT = "Auktion: +4–10 % Aufschlag üblich"
OWNER_HINT = "nur Angebot an Besitzer; nimmt erfahrungsgemäß niemand an"

_POS = {1: "TW", 2: "ABW", 3: "MF", 4: "ST"}
_WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


@dataclass(frozen=True)
class Candidate:
    """Ein Steiger aus der Marktwert-Historie: heute, vor `days` Tagen, Anstieg in %."""

    player_id: str
    mv_now: int
    mv_before: int
    rise_pct: float


@dataclass(frozen=True)
class ListingInfo:
    """Marktlisting des Kandidaten. `seller_id` leer = Kickbase-Listing (Auktion)."""

    price: int
    expires_at: datetime
    seller_id: str = ""

    @property
    def is_kickbase(self) -> bool:
        return not self.seller_id


@dataclass(frozen=True)
class RadarEntry:
    """Was der Radar über einen Kandidaten weiß (Name, Club, Rolle, Fitness, Listing).

    `fitness_status`/`lineup_prob` None = nicht geliefert; fehlend gilt nie als fit
    oder als Rolle (CLAUDE.md §6: fehlende Belege sperren neue Käufe).
    """

    name: str
    team: str
    position: int
    lineup_prob: int | None
    fitness_status: int | None
    listing: ListingInfo | None = None

    @property
    def eligible(self) -> bool:
        return (
            self.fitness_status == 0
            and self.lineup_prob is not None
            and self.lineup_prob <= MAX_LINEUP_PROB
        )


# --- reine Funktionen ---------------------------------------------------------


def _as_utc(stamp: datetime) -> datetime:
    """SQLite gibt DateTime naiv zurück; die Historie wird in UTC geschrieben."""
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def momentum_candidates(
    history_by_player: dict[str, list[tuple[datetime, int]]],
    now: datetime,
    *,
    min_rise: float = 0.10,
    days: int = 3,
    min_mv: int = 1_000_000,
    max_mv: int = 10_000_000,
) -> list[Candidate]:
    """Steiger finden: MW heute (jüngster Wert ≤ now) gegen den nächstliegenden
    Tageswert ≤ now − days. Fehlt einer der beiden, ist der Spieler kein Kandidat.

    Der Vergleich rechnet mit Brüchen: 3,3 M / 3,0 M ist in float knapp unter 1,1 —
    „genau +10 %“ muss aber drin sein.
    """
    cutoff = now - timedelta(days=days)
    threshold = Fraction(min_rise).limit_denominator(1_000_000)
    found: list[Candidate] = []
    for player_id, points in history_by_player.items():
        current: tuple[datetime, int] | None = None
        before: tuple[datetime, int] | None = None
        for stamp, value in points:
            at = _as_utc(stamp)
            if at <= now and (current is None or at > current[0]):
                current = (at, value)
            if at <= cutoff and (before is None or at > before[0]):
                before = (at, value)
        if current is None or before is None or before[1] <= 0:
            continue
        mv_now, mv_before = current[1], before[1]
        if not (min_mv <= mv_now <= max_mv):
            continue
        rise = Fraction(mv_now - mv_before, mv_before)
        if rise < threshold:
            continue
        found.append(
            Candidate(
                player_id=player_id,
                mv_now=mv_now,
                mv_before=mv_before,
                rise_pct=round(float(rise) * 100, 1),
            )
        )
    found.sort(key=lambda c: (-c.rise_pct, c.player_id))
    return found


def _eur(value: int) -> str:
    """7_410_000 → „7,41 M", 850_000 → „850 k", 120 → „120 €"."""
    sign = "-" if value < 0 else ""
    a = abs(int(value))
    if a >= 1_000_000:
        text = f"{a / 1e6:.2f} M"
    elif a >= 1_000:
        text = f"{a / 1e3:.0f} k"
    else:
        text = f"{a} €"
    return sign + text.replace(".", ",")


def _pct(value: float) -> str:
    return f"+{value:.1f} %".replace(".", ",")


def _date_de(stamp: datetime) -> str:
    local = stamp.astimezone(BERLIN)
    return f"{_WEEKDAYS[local.weekday()]} {local:%d.%m.%Y}"


def _time_de(stamp: datetime) -> str:
    local = stamp.astimezone(BERLIN)
    return f"{_WEEKDAYS[local.weekday()]} {local:%H:%M}"


def _access_text(listing: ListingInfo | None) -> str:
    if listing is not None and listing.is_kickbase:
        return (
            f"Auktion {_eur(listing.price)} bis {_time_de(listing.expires_at)} "
            f"({AUCTION_HINT})"
        )
    return OWNER_HINT


def select_lines(
    candidates: list[Candidate],
    *,
    market_by_id: dict[str, RadarEntry],
    own_ids: frozenset[str] | set[str],
    limit: int = MAX_LINES,
) -> list[tuple[Candidate, RadarEntry]]:
    """Kandidaten in Radar-Reihenfolge, ohne eigene, unfitte oder rollenlose Spieler.

    Ein Kandidat ohne Eintrag ist ungeprüft und erscheint nicht — er wird nie erfunden.
    """
    chosen: list[tuple[Candidate, RadarEntry]] = []
    for cand in candidates:
        if cand.player_id in own_ids:
            continue
        entry = market_by_id.get(cand.player_id)
        if entry is None or not entry.eligible:
            continue
        chosen.append((cand, entry))
        if len(chosen) >= limit:
            break
    return chosen


def format_radar(
    candidates: list[Candidate],
    *,
    market_by_id: dict[str, RadarEntry],
    budget: int,
    own_ids: frozenset[str] | set[str],
    now: datetime,
    unchecked: int = 0,
) -> str:
    """Discord-Text ≤ 1900 Zeichen: Kopf, Regel, Grundlage, ≤ 5 Kandidaten, Schluss.

    `unchecked` = Steiger, die der Job wegen der Abrufgrenze nicht prüfen konnte;
    sie werden gezählt, nicht erraten. Ohne Kandidaten: ein ehrlicher Ein-Zeiler.
    """
    head = f"Steiger-Radar {_date_de(now)} · Konto {_eur(budget)}"
    lines = select_lines(candidates, market_by_id=market_by_id, own_ids=own_ids)
    if not lines:
        text = (
            f"{head} · Kein Kandidat: kein fitter Spieler (KB-Stufe ≤ {MAX_LINEUP_PROB}) "
            f"zwischen 1 und 10 M ist in 3 Tagen um ≥ 10 % gestiegen"
        )
        if unchecked:
            text += f"; {unchecked} weitere Steiger ungeprüft (Abrufgrenze {MAX_DETAIL_LOOKUPS})"
        return text + "."
    out = [head, RULE_LINE, BASIS_LINE, ""]
    for index, (cand, entry) in enumerate(lines, start=1):
        out.append(
            f"{index}. {entry.name} ({entry.team}, {_POS.get(entry.position, '?')}) · "
            f"MW {_eur(cand.mv_now)} · {_pct(cand.rise_pct)} / 3 Tage · "
            f"KB-Stufe {entry.lineup_prob} · {_access_text(entry.listing)}"
        )
    if unchecked:
        out.append(
            f"{unchecked} weitere Steiger ungeprüft (Abrufgrenze {MAX_DETAIL_LOOKUPS} je Lauf)."
        )
    out.extend(["", CLOSING_LINE])
    text = "\n".join(out)
    if len(text) > MAX_TEXT_LEN:
        # Vor dem Kürzen: Schlusszeile bleibt immer stehen.
        keep = MAX_TEXT_LEN - len(CLOSING_LINE) - 3
        text = text[:keep].rstrip() + "…\n" + CLOSING_LINE
    return text


# --- Verdrahtung ---------------------------------------------------------------


def _team_name(team_id: str, fallback: str = "") -> str:
    team = TEAMS.get(str(team_id))
    if team is not None:
        return team.kb_name
    return fallback or str(team_id) or "?"


def load_history(session: Any, since: datetime) -> dict[str, list[tuple[datetime, int]]]:
    """Marktwert-Historie aller Spieler ab `since` (rein lesend)."""
    rows = session.execute(
        select(PlayerHistory.player_id, PlayerHistory.recorded_at, PlayerHistory.market_value)
        .where(PlayerHistory.recorded_at >= since.replace(tzinfo=None))
        .order_by(PlayerHistory.player_id, PlayerHistory.recorded_at)
    ).all()
    history: dict[str, list[tuple[datetime, int]]] = {}
    for player_id, recorded_at, market_value in rows:
        history.setdefault(str(player_id), []).append((_as_utc(recorded_at), int(market_value)))
    return history


def _entry_from_market(listing: Any) -> RadarEntry:
    player = listing.player
    return RadarEntry(
        name=f"{player.first_name} {player.last_name}".strip(),
        team=_team_name(player.team_id),
        position=int(player.position),
        lineup_prob=player.lineup_prob,
        fitness_status=player.fitness_status,
        listing=ListingInfo(
            price=int(listing.price),
            expires_at=_as_utc(listing.expires_at),
            seller_id=str(listing.seller_id or ""),
        ),
    )


def _entry_from_detail(detail: Any) -> RadarEntry:
    return RadarEntry(
        name=f"{detail.first_name} {detail.last_name}".strip(),
        # `get_player` liefert ein `Player` ohne team_name (Beweisregel 3, 22.09.2026);
        # nur ein PlayerDetail hätte das Feld.
        team=_team_name(detail.team_id, getattr(detail, "team_name", None)),
        position=int(detail.position),
        lineup_prob=detail.lineup_prob,
        fitness_status=detail.fitness_status,
        listing=None,
    )


def _meta(lines: list[tuple[Candidate, RadarEntry]], unchecked: int) -> dict[str, Any]:
    kandidaten = []
    for cand, entry in lines:
        row: dict[str, Any] = {**asdict(cand), "name": entry.name, "team": entry.team,
                               "position": entry.position, "lineup_prob": entry.lineup_prob,
                               "fitness_status": entry.fitness_status, "listing": None}
        if entry.listing is not None:
            row["listing"] = {
                "price": entry.listing.price,
                "expires_at": entry.listing.expires_at.isoformat(),
                "seller_id": entry.listing.seller_id,
                "kickbase": entry.listing.is_kickbase,
            }
        kandidaten.append(row)
    return {
        "kandidaten": kandidaten,
        "ungeprueft": unchecked,
        "regel": RULE_LINE,
        "grundlage": "Analyse 21.09.2026",
        "verkaufsfrist": "2026-10-05",
    }


async def run_steiger_radar(channel, *, now: datetime | None = None) -> str:
    """Ein Radar-Lauf: Historie lesen, Steiger prüfen, Bericht speichern.

    Rückgabe `ok:<n>` mit n Kandidatenzeilen im Bericht, `no_league` ohne Liga.
    Kickbase wird ausschließlich GELESEN (get_squad, get_market, get_budget,
    höchstens MAX_DETAIL_LOOKUPS × get_player). `channel` bleibt Teil der Signatur
    (Job-Verdrahtung), wird aber nicht mehr benutzt: gesendet wird nur die
    Tageszusammenfassung. Scheitert das Speichern, fliegt ein Fehler — sonst wäre der
    Radar still verloren.
    """
    del channel  # bewusst ungenutzt, siehe Docstring
    stamp = now or datetime.now(UTC)
    client, auth = await make_kickbase_client()
    lg = auth.league_id
    if lg is None:
        return "no_league"

    with SessionLocal() as session:
        history = load_history(session, since=stamp - timedelta(days=HISTORY_DAYS))
        candidates = momentum_candidates(history, stamp)
        own_ids = frozenset(str(sp.player.id) for sp in await client.get_squad(lg))
        market = {str(m.player.id): m for m in await client.get_market(lg)}
        budget = int(await client.get_budget(lg))

        entries: dict[str, RadarEntry] = {}
        lookups = 0
        unchecked = 0
        shown = 0
        for cand in candidates:
            if cand.player_id in own_ids:
                continue
            if shown >= MAX_LINES:
                break
            if cand.player_id in market:
                entries[cand.player_id] = _entry_from_market(market[cand.player_id])
            elif lookups < MAX_DETAIL_LOOKUPS:
                lookups += 1
                entries[cand.player_id] = _entry_from_detail(
                    await client.get_player(lg, cand.player_id)
                )
            else:
                unchecked += 1
                continue
            if entries[cand.player_id].eligible:
                shown += 1

        # Name, Club und Rolle stammen aus Markt oder Detail — die players-Tabelle wird
        # über die Historie (player_id) nur als Kandidatenmenge berührt.
        lines = select_lines(candidates, market_by_id=entries, own_ids=own_ids)
        text = format_radar(
            candidates,
            market_by_id=entries,
            budget=budget,
            own_ids=own_ids,
            now=stamp,
            unchecked=unchecked,
        )
        title = f"Steiger-Radar {stamp.astimezone(BERLIN):%d.%m.%Y}"
        saved = save_report(
            REPORT_KIND, title, text, SOURCE, meta=_meta(lines, unchecked), session=session,
            created_at=stamp,
        )
        if saved is None:
            raise RuntimeError("Steiger-Radar: Bericht nicht gespeichert.")
    return f"ok:{len(lines)}"
