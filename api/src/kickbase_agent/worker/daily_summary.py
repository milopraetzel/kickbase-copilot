"""Tageszusammenfassung: die EINE Discord-Nachricht des Tages (Emil, 23.09.2026).

Emils Auftrag wörtlich: „Discord soll wieder senden. Aber nur einmal am Tag eine
Zusammenfassung". Das Morgen-Briefing (08:15, atomarer Slot-Claim in
`DiscordChannel.send_briefing`) wird deshalb zur Tageszusammenfassung. Sie enthält
über dem gewohnten Briefing:

- Wächter-Befunde der letzten 24 h (Berichte `blocker` mit Quelle
  `outcome_guard.FINDINGS_SOURCE`; je Zustand nur der jüngste),
- offene Vorhaben, deren Veto-Frist (`execute_at`) in den nächsten 24 h endet,
- die Kandidaten des heutigen Steiger-Radars (08:10, Quelle `worker:steiger_radar`),
- Kaufsignale und Kader-Warnungen des heutigen News-Radars (08:11, Quelle
  `worker:news_radar`; Jev liest kicker, Emil 30.09.2026).

Die Beschreibung bleibt ≤ `SUMMARY_LIMIT` Zeichen. Gekürzt wird nur an ganzen Zeilen
bzw. ganzen Code-Blöcken, dann folgt `MORE_MARKER`; die ungekürzte Fassung liegt als
Bericht im Cockpit. Das Abend-Briefing (22:15) wird unverändert gebaut, im Digest-Modus
aber nur archiviert (`report_store.DIGEST_DELIVERY_SLOTS`).

Aufbau: `compose_daily_summary`/`fit_description` sind rein; `load_summary_sources`
liest die DB; `publish_briefing` verdrahtet beides mit dem Kanal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from kickbase_agent.db.models import Intent, Report
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.notify.report_store import first_line, save_report
from kickbase_agent.worker.briefing import Briefing, format_briefing
from kickbase_agent.worker.news_radar import NEWS_EMPTY, summary_lines
from kickbase_agent.worker.news_radar import SOURCE as NEWS_SOURCE
from kickbase_agent.worker.outcome_guard import FINDINGS_SOURCE
from kickbase_agent.worker.steiger_radar import SOURCE as RADAR_SOURCE
from kickbase_agent.worker.trade_radar import SOURCE as TRADE_SOURCE
from kickbase_agent.worker.trade_radar import summary_lines as trade_lines

_log = logging.getLogger(__name__)
_BERLIN = ZoneInfo("Europe/Berlin")

#: Obergrenze der Embed-Beschreibung (Emil: ≤ 1900 Zeichen, kürzer ist besser).
SUMMARY_LIMIT = 1900
#: Schluss einer gekürzten Zusammenfassung.
MORE_MARKER = "… (mehr im Cockpit)"
#: Rückblick für Wächter-Befunde und Vorschau für Veto-Fristen.
FINDINGS_WINDOW = timedelta(hours=24)
INTENT_WINDOW = timedelta(hours=24)
MAX_FINDINGS = 5
MAX_INTENTS = 6
#: Längste Zeile eines Befunds in der Zusammenfassung (erste Zeile der Karte).
MAX_FINDING_CHARS = 260

_WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
_POS = {1: "TW", 2: "ABW", 3: "MF", 4: "ST"}
_INTENT_LABEL = {"bid": "Gebot", "list": "Listing", "sell": "Verkauf", "lineup": "Aufstellung"}
#: SLOs, deren Befunde je Spieler getrennt bleiben (sonst je SLO der jüngste).
_PER_PLAYER_SLOS = frozenset({"statuswechsel", "dispo_glaettung"})


@dataclass(frozen=True)
class SummarySources:
    """Fertige Zeilen je Abschnitt. `radar`/`news`/`trade` None = heute kein Bericht."""

    findings: tuple[str, ...]
    intents: tuple[str, ...]
    radar: tuple[str, ...] | None
    news: tuple[str, ...] | None = None
    trade: tuple[str, ...] | None = None


# --- reine Funktionen ---------------------------------------------------------------


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _naive_utc(moment: datetime) -> datetime:
    """SQLite speichert ohne Zeitzone (UTC-Wandzeit) — Vergleichswerte ebenso."""
    return _aware(moment).astimezone(UTC).replace(tzinfo=None)


def _time_de(moment: datetime) -> str:
    local = _aware(moment).astimezone(_BERLIN)
    return f"{_WEEKDAYS[local.weekday()]} {local:%H:%M}"


def _eur(value: int) -> str:
    """7_410_000 → „7,41 M", 850_000 → „850 k"."""
    sign = "-" if value < 0 else ""
    a = abs(int(value))
    text = f"{a / 1e6:.2f} M" if a >= 1_000_000 else f"{a / 1e3:.0f} k" if a >= 1_000 else f"{a} €"
    return sign + text.replace(".", ",")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _units(text: str) -> list[str]:
    """Text in unteilbare Einheiten: einzelne Zeilen, Code-Blöcke als Ganzes."""
    units: list[str] = []
    block: list[str] | None = None
    for line in text.split("\n"):
        if block is not None:
            block.append(line)
            if line.strip().startswith("```"):
                units.append("\n".join(block))
                block = None
            continue
        if line.strip().startswith("```") and line.strip().count("```") == 1:
            block = [line]
            continue
        units.append(line)
    if block is not None:  # nie geschlossener Block: lieber weglassen als kaputt senden
        _log.warning("Tageszusammenfassung: offener Code-Block verworfen.")
    return units


def _dangling(unit: str) -> bool:
    """Leerzeile oder Abschnittsüberschrift ohne Inhalt dahinter."""
    stripped = unit.strip()
    return not stripped or (stripped.startswith("__") and stripped.endswith("__"))


def fit_description(units: list[str], limit: int = SUMMARY_LIMIT) -> str:
    """Einheiten der Reihe nach aufnehmen, solange sie passen; sonst sauber kürzen.

    Gekürzt wird nie mitten in einer Einheit; eine hängende Überschrift fliegt mit
    raus, dann folgt `MORE_MARKER`. Ergebnis ist immer ≤ `limit`.
    """
    taken: list[str] = []
    length = 0
    for unit in units:
        extra = len(unit) + (1 if taken else 0)
        if length + extra > limit:
            break
        taken.append(unit)
        length += extra
    else:
        return "\n".join(taken)
    room = limit - len(MORE_MARKER) - 1
    while taken and (len("\n".join(taken)) > room or _dangling(taken[-1])):
        taken.pop()
    return "\n".join([*taken, MORE_MARKER])


def compose_daily_summary(briefing_body: str, sources: SummarySources) -> str:
    """Tageszusammenfassung: Kopfzeile, Befunde, Veto-Fristen, Radar, dann das Briefing.

    Reihenfolge = Dringlichkeit; gekürzt wird von hinten (das ausführliche Briefing
    steht ungekürzt im Cockpit).
    """
    lines = briefing_body.split("\n")
    head, rest = lines[0], lines[1:]
    units: list[str] = [head, "", "__🚨 Wächter (24 h)__"]
    units += list(sources.findings) or ["• keine offenen Befunde"]
    units += ["", "__⏳ Veto-Fristen (24 h)__"]
    units += list(sources.intents) or ["• keine"]
    # Handels-Radar (Emil 01.10.2026): Verkaufssignale sind heute vor 22:00 fällig — deshalb
    # direkt nach den Veto-Fristen und vor den lesenden Radaren. Bieten und verkaufen tut seit
    # dem 01.10.2026 der Handels-Autopilot; seine Vorhaben stehen bei den Veto-Fristen.
    units += ["", "__💹 Handels-Radar__"]
    if sources.trade is None:
        units.append("• heute kein Handels-Radar-Bericht (Job prüfen)")
    else:
        units += list(sources.trade)
        if any(line.startswith(("• Kandidat:", "• Verkaufen")) for line in sources.trade):
            units.append("-# Der Handels-Autopilot bietet und verkauft selbst — seine Vorhaben "
                         "stehen oben bei den Veto-Fristen.")
    units += ["", "__📈 Steiger-Radar__"]
    if sources.radar is None:
        units.append("• heute kein Radar-Bericht (Job prüfen)")
    else:
        units += list(sources.radar) or ["• kein Kandidat"]
        if sources.radar:
            units.append("-# Kauf nur auf deinen Auftrag je Spieler (CLAUDE.md §5.12).")
    units += ["", "__📰 News-Radar__"]
    if sources.news is None:
        units.append("• heute kein News-Radar-Bericht (Job prüfen)")
    else:
        units += list(sources.news) or [NEWS_EMPTY]
        if any(line.startswith("• Kauf?") for line in sources.news):
            units.append("-# Kauf nur auf deinen Auftrag je Spieler (CLAUDE.md §5.12).")
    if rest and rest[0].strip():
        units.append("")
    units += _units("\n".join(rest))
    return fit_description(units)


def _finding_group(meta: dict[str, Any]) -> str:
    key = str(meta.get("key") or "")
    slo = str(meta.get("slo") or key.split("|")[0])
    parts = key.split("|")
    if slo in _PER_PLAYER_SLOS and len(parts) > 3:
        return f"{slo}|{parts[3].split(':')[0]}"
    return slo


def _finding_line(row: Report) -> str:
    meta = row.meta or {}
    suffix = " (sofort gemeldet)" if meta.get("sofort") else ""
    head = _clip(first_line(row.body), MAX_FINDING_CHARS)
    return f"• {_time_de(row.created_at)} {head}{suffix}"


def _intent_line(row: Intent) -> str:
    label = _INTENT_LABEL.get(row.kind, row.kind)
    name = row.player_name or row.player_id
    amount = f" · {_eur(row.amount)}" if row.amount else ""
    return f"• {label} {name}{amount} · Veto bis {_time_de(row.execute_at)}"


def _radar_line(row: dict[str, Any]) -> str:
    pos = _POS.get(int(row.get("position") or 0), "?")
    rise = f"+{float(row.get('rise_pct') or 0):.1f} %".replace(".", ",")
    listing = row.get("listing")
    if listing and listing.get("kickbase"):
        ends = datetime.fromisoformat(str(listing["expires_at"]))
        access = f"Auktion {_eur(int(listing['price']))} bis {_time_de(ends)}"
    else:
        access = "nur Angebot an Besitzer"
    return (
        f"• {row.get('name')} ({row.get('team')}, {pos}) · MW {_eur(int(row.get('mv_now') or 0))}"
        f" · {rise} / 3 T · {access}"
    )


# --- DB ------------------------------------------------------------------------------


def load_summary_sources(session, now: datetime) -> SummarySources:
    """Zeilen für die Zusammenfassung aus `reports` und `intents` (rein lesend)."""
    stamp = _aware(now)
    rows = session.scalars(
        select(Report)
        .where(
            Report.source == FINDINGS_SOURCE,
            Report.created_at >= _naive_utc(stamp - FINDINGS_WINDOW),
            Report.created_at <= _naive_utc(stamp),
        )
        .order_by(Report.id.desc())
    ).all()
    latest: dict[str, Report] = {}
    for row in rows:  # jüngste zuerst
        latest.setdefault(_finding_group(row.meta or {}), row)
    ordered = sorted(latest.values(), key=lambda r: r.id)
    findings = [_finding_line(r) for r in ordered[:MAX_FINDINGS]]
    if len(ordered) > MAX_FINDINGS:
        findings.append(f"• … {len(ordered) - MAX_FINDINGS} weitere im Cockpit")

    intents = session.scalars(
        select(Intent)
        .where(
            Intent.status == "planned",
            Intent.execute_at > _naive_utc(stamp),
            Intent.execute_at <= _naive_utc(stamp + INTENT_WINDOW),
        )
        .order_by(Intent.execute_at.asc(), Intent.id.asc())
    ).all()
    intent_lines = [_intent_line(r) for r in intents[:MAX_INTENTS]]
    if len(intents) > MAX_INTENTS:
        intent_lines.append(f"• … {len(intents) - MAX_INTENTS} weitere im Cockpit")

    local_midnight = stamp.astimezone(_BERLIN).replace(hour=0, minute=0, second=0, microsecond=0)
    radar_row = session.scalars(
        select(Report)
        .where(
            Report.source == RADAR_SOURCE,
            Report.created_at >= _naive_utc(local_midnight),
            Report.created_at <= _naive_utc(stamp),
        )
        .order_by(Report.id.desc())
        .limit(1)
    ).first()
    radar: tuple[str, ...] | None = None
    if radar_row is not None:
        radar = tuple(_radar_line(c) for c in (radar_row.meta or {}).get("kandidaten") or [])
    news_row = session.scalars(
        select(Report)
        .where(
            Report.source == NEWS_SOURCE,
            Report.created_at >= _naive_utc(local_midnight),
            Report.created_at <= _naive_utc(stamp),
        )
        .order_by(Report.id.desc())
        .limit(1)
    ).first()
    news = summary_lines(news_row.meta or {}) if news_row is not None else None
    trade_row = session.scalars(
        select(Report)
        .where(
            Report.source == TRADE_SOURCE,
            Report.created_at >= _naive_utc(local_midnight),
            Report.created_at <= _naive_utc(stamp),
        )
        .order_by(Report.id.desc())
        .limit(1)
    ).first()
    trade = tuple(trade_lines(trade_row.meta or {})) if trade_row is not None else None
    return SummarySources(tuple(findings), tuple(intent_lines), radar, news, trade)


# --- Verdrahtung -------------------------------------------------------------------


_LABEL = {"morning": "Tageszusammenfassung", "evening": "Abendbericht"}


async def publish_briefing(
    channel, briefing: Briefing, *, period: str | None, now: datetime
) -> None:
    """Briefing über den Kanal geben — morgens als Tageszusammenfassung.

    Der Kanal entscheidet über die Zustellung (Digest: nur der Morgen-Slot, einmal).
    Fällt das Lesen der Quellen aus, geht die Zusammenfassung trotzdem raus und sagt
    es — die eine Nachricht des Tages darf nicht an einer Nebenquelle scheitern.
    """
    full = format_briefing(briefing)
    stamp = _aware(now).astimezone(_BERLIN).strftime("%d.%m.%Y · %H:%M")
    title = f"📊 {briefing.league_name} · {_LABEL.get(period or '', 'Tagesbriefing')}"
    body = full
    if period == "morning":
        try:
            with SessionLocal() as session:
                sources = load_summary_sources(session, now)
        except Exception:  # noqa: BLE001 — Nebenquelle, die Nachricht geht trotzdem raus
            _log.warning("Tageszusammenfassung: Quellen nicht lesbar.", exc_info=True)
            hint = ("• Quellen nicht lesbar — Cockpit prüfen",)
            sources = SummarySources(hint, hint, hint, hint, hint)
        body = compose_daily_summary(full, sources)
        if body.endswith(MORE_MARKER):
            save_report(
                "briefing",
                f"{briefing.league_name} · Morgenbericht (Vollfassung)",
                full,
                "worker:briefing",
                meta={"delivered": False, "vollfassung": True, "chars": len(full)},
            )
    await channel.send_briefing(
        title,
        body,
        footer=f"Kickbase-Agent · {stamp}",
        kind="briefing",
        delivery_slot=period,
        now=now,
    )
