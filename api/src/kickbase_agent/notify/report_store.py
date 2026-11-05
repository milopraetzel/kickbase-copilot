"""Berichte-Persistenz (Steuerzentrale, Zone „Berichte"): alles, was nach Discord geht,
bleibt als Volltext in `reports` nachlesbar (Emils Wunsch 23.08.).

Drei Schreibstellen rufen `save_report`: der DiscordChannel (Worker-Posts, source
`worker:<kind>`), `kba post-discord` (`cli:post-discord`) und das MCP-Tool
`discord_post` (`mcp:discord_post`). Die Speicherung darf den VERSAND nie stören:
jeder DB-Fehler wird nur geloggt (`None` statt Exception) — ein fehlendes
Alembic-Upgrade kostet höchstens den Bericht, nie die Discord-Nachricht.

`infer_kind`/`title_from` sind reine Text-Heuristiken für Posts ohne explizite
Angabe (z. B. Claude im Chat über MCP); die Worker-Jobs geben ihren kind selbst mit.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kickbase_agent.db.models import Report
from kickbase_agent.db.session import SessionLocal

_log = logging.getLogger(__name__)

#: Erlaubte kind-Werte (Spalte `reports.kind`, siehe db/models.py). Unbekanntes → sonstiges.
REPORT_KINDS: tuple[str, ...] = (
    "briefing",
    "blocker",
    "recherche",
    "schatten",
    "auto_trading",
    "planner",
    "executor",
    "retro",
    "bid_watch",
    "engine_decision",
    "engine_state",
    "model_evaluation",
    "deploy",
    # Emil 22.09.2026: täglicher Morgenbericht. Seit 23.09.2026 nur noch Archivart
    # (Altbestand, externe Routinen) — der Digest lässt sie NICHT mehr durch; den Kanal
    # erreicht am Tag allein die Tageszusammenfassung (DIGEST_DELIVERY_SLOTS).
    "morgenbericht",
    "info",
    "sonstiges",
)
DEFAULT_KIND = "info"
FALLBACK_KIND = "sonstiges"


def discord_delivery_allowed(kind: str, digest_only: bool) -> bool:
    """Im Digest-Modus kommen über `send_info`/`discord_post` nur Blocker in den Kanal."""
    return not digest_only or kind in DIGEST_PASSTHROUGH_KINDS


#: Berichtsarten, die der Digest-Modus zustellt statt nur zu archivieren. Emil
#: 23.09.2026 („Discord soll wieder senden. Aber nur einmal am Tag eine
#: Zusammenfassung"): `morgenbericht` ist nicht mehr dabei. Worker-Blocker stellen
#: nur noch die versiegelte Anpfiff-Kaskade und der Ergebnis-Wächter binnen 24 h vor
#: der Frist zu (tests/worker/test_daily_summary.py belegt die Aufruferliste).
DIGEST_PASSTHROUGH_KINDS: frozenset[str] = frozenset({"blocker"})

#: Briefing-Slots, die der Digest-Modus zustellt: nur der Morgen (08:15) — dort steht
#: die Tageszusammenfassung. Das Abend-Briefing (22:15) wird nur archiviert.
DIGEST_DELIVERY_SLOTS: frozenset[str] = frozenset({"morning"})


_BERLIN = ZoneInfo("Europe/Berlin")
_BRIEFING_TIMES = {"morning": (8, 15), "evening": (22, 15)}


def briefing_slot_key(slot: str | None, now: datetime | None = None) -> str | None:
    """Lieferschlüssel nur innerhalb fünf Minuten nach dem festen Berliner Slot."""
    if slot not in _BRIEFING_TIMES:
        return None
    current = (now or datetime.now(UTC)).astimezone(_BERLIN)
    hour, minute = _BRIEFING_TIMES[slot]
    start = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if not timedelta(0) <= current - start <= timedelta(minutes=5):
        return None
    return f"{current.date().isoformat()}:{slot}"


def claim_briefing_slot(
    *, slot_key: str, title: str, body: str, footer: str
) -> int | None:
    """Slot vor Discord atomar claimen; None heißt immer fail-closed/nicht senden."""
    row = Report(
        created_at=datetime.now(UTC),
        kind="briefing",
        title=(title_from(title) or "Briefing")[:200],
        body=(body or "").strip(),
        source="worker:briefing",
        meta={
            "delivered": False,
            "chars": len((body or "").strip()),
            "embed": True,
            "footer": footer,
            "delivery_slot": slot_key,
            "delivery_status": "claimed",
        },
        delivery_slot=slot_key,
    )
    try:
        with SessionLocal() as session:
            session.add(row)
            session.commit()
            return int(row.id)
    except IntegrityError:
        return None
    except Exception:  # noqa: BLE001 — Claim-Fehler darf nie einen Post zulassen
        _log.warning(
            "Briefing-Slot konnte nicht geclaimt werden; Zustellung gesperrt.",
            exc_info=True,
        )
        return None


def finish_briefing_claim(report_id: int, *, delivered: bool) -> None:
    """Claim nach dem Discord-Versuch aktualisieren; Unique-Slot bleibt dauerhaft bestehen."""
    try:
        with SessionLocal() as session:
            row = session.get(Report, report_id)
            if row is None:
                return
            meta = dict(row.meta or {})
            meta["delivered"] = delivered
            meta["delivery_status"] = "delivered" if delivered else "failed"
            row.meta = meta
            session.commit()
    except Exception:  # noqa: BLE001 — Claim bleibt auch bei Updatefehler at-most-once
        _log.warning("Briefing-Claim konnte nicht abgeschlossen werden.", exc_info=True)

#: Kürzere Texte ohne expliziten kind gelten als Kurz-Info (Prämie, Puls-Test, …) und
#: werden NICHT gespeichert; Jobs mit explizitem kind speichern unabhängig von der Länge.
MIN_REPORT_CHARS = 120
#: Maximale Titellänge (Spalte ist 200, die Anzeige braucht weniger).
TITLE_MAX_CHARS = 120

#: „⚠️ Briefing-Fehler: ValueError: …" (worker/__main__._safe) — reine Fehlermeldungen.
_ERROR_NOTICE_RE = re.compile(r"^[^\n]{0,40}-Fehler:\s")
_MARKDOWN_RE = re.compile(r"\*\*|__|[*`]")
_LEADING_JUNK = " \t#-–—:·•|>"
_TRAILING_JUNK = " \t-–—:·•|"
_WS_RE = re.compile(r"\s+")
#: Marker je Berichtsart (Kleinschreibung, Reihenfolge = Priorität; siehe infer_kind).
_KIND_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("briefing", ("briefing",)),
    ("recherche", ("recherche", "lage & gebote", "lage und gebote", "teil 1", "teil 2")),
    ("auto_trading", ("auto-trading", "auto trading", "autotrading")),
    ("schatten", ("schatten",)),
    ("bid_watch", ("gebots-eskalation", "schnäppchen", "sniper", "gebots-wächter",
                   "gebotswächter")),
    ("executor", ("executor",)),
    ("planner", ("planner",)),
    ("retro", ("retro",)),
)
#: Unicode-Kategorien, die aus Titeln fliegen: Symbole (Emoji), Modifier, Format-/
#: Kombinationszeichen (Variation Selector, ZWJ), private/unbelegte Codepoints.
_STRIP_CATEGORIES = frozenset({"So", "Sk", "Cf", "Mn", "Me", "Cn", "Co"})


# ------------------------------------------------------------------ Heuristiken


def first_line(text: str) -> str:
    """Erste nicht-leere Zeile (gestrippt) — leer, wenn der Text leer ist."""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line:
            return line
    return ""


def normalize_kind(kind: str | None, default: str = DEFAULT_KIND) -> str:
    """Unbekannte/leere kinds landen nie in der DB: leer → default, fremd → sonstiges."""
    value = (kind or "").strip().lower()
    if not value:
        return default
    return value if value in REPORT_KINDS else FALLBACK_KIND


def infer_kind(text: str, default: str = DEFAULT_KIND) -> str:
    """Berichtsart aus der ERSTEN Zeile raten (Marker der bekannten Jobs).

    Reihenfolge ist Absicht: „Auto-Trading (Schatten)" ist ein Auto-Trading-Report,
    „Schatten-Scan" ein Schatten-Report; „ausgeführt" zählt nur im Steuerzentrale-
    Kontext als Executor (T-15-Notbremse/Gebots-Storno bleiben info).
    """
    line = first_line(text).lower()
    if not line:
        return default
    for kind, markers in _KIND_MARKERS:
        if any(m in line for m in markers):
            return kind
    if "ausgeführt" in line and any(m in line for m in ("steuerzentrale", "vorhaben")):
        return "executor"
    if "vorhaben" in line:
        return "planner"
    return default


def _plain_first_line(text: str) -> str:
    """Erste Zeile ohne Markdown-Sternchen/Backticks, Emoji/Symbole und Rand-Zeichen."""
    line = _MARKDOWN_RE.sub("", first_line(text))
    line = "".join(ch for ch in line if unicodedata.category(ch) not in _STRIP_CATEGORIES)
    return _WS_RE.sub(" ", line).strip().lstrip(_LEADING_JUNK).rstrip(_TRAILING_JUNK)


def title_from(text: str, max_chars: int = TITLE_MAX_CHARS) -> str:
    """Erste nicht-leere Zeile ohne Markdown-Sternchen/Emoji, ≤ max_chars (mit „…")."""
    line = _plain_first_line(text)
    if len(line) > max_chars:
        line = line[: max_chars - 1].rstrip() + "…"
    return line


def is_error_notice(text: str) -> bool:
    """„⚠️ Scan-Fehler: TimeoutError: …" — Laufzeitfehler-Meldungen sind keine Berichte."""
    return bool(_ERROR_NOTICE_RE.match(_plain_first_line(text)))


def worth_storing(text: str) -> bool:
    """Ohne expliziten kind: nur substanzielle Texte, keine Kurz-Infos/Fehlermeldungen."""
    body = (text or "").strip()
    return len(body) >= MIN_REPORT_CHARS and not is_error_notice(body)


# ------------------------------------------------------------------ Persistenz


def save_report(
    kind: str | None,
    title: str | None,
    body: str,
    source: str,
    meta: dict[str, Any] | None = None,
    session: Session | None = None,
    created_at: datetime | None = None,
) -> int | None:
    """Bericht speichern; gibt die neue id zurück — oder None, wenn die DB nicht mitspielt.

    Ohne `session` öffnet die Funktion eine eigene SessionLocal-Session; mit
    `session` wird DORT committed (bei Fehler rollback). Fehler werden nur geloggt,
    damit die Persistenz nie den Discord-Versand stört. `created_at` (Standard: jetzt)
    trägt die Beobachtungszeit eines Jobs, der mit eigenem `now` rechnet — die
    Tageszusammenfassung wählt ihre Quellen nach genau diesem Zeitpunkt.
    """
    body = (body or "").strip()
    kind_norm = normalize_kind(kind)
    clean_title = (title or "").strip() or title_from(body) or kind_norm
    row = Report(
        created_at=created_at or datetime.now(UTC),
        kind=kind_norm,
        title=clean_title[:200],
        body=body,
        source=(source or "")[:64],
        meta=meta,
    )
    try:
        if session is not None:
            return _insert(session, row)
        with SessionLocal() as own:
            return _insert(own, row)
    except Exception:  # noqa: BLE001 — Persistenz darf den Versand nie stören
        _log.warning(
            "Bericht (%s, %s) konnte nicht gespeichert werden.", kind_norm, source, exc_info=True
        )
        return None


def _insert(session: Session, row: Report) -> int:
    try:
        session.add(row)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return int(row.id)


#: Flüchtige Bericht-Arten (Meldungs-Umbau, Emil 31.08.): Lagemeldungen, deren
#: Wert nach Wochen null ist. Substanz (briefing/recherche/retro/sonstiges)
#: bleibt unbegrenzt; Audit (`transfer_actions`) ist hiervon NIE betroffen.
EPHEMERAL_KINDS: tuple[str, ...] = (
    "info", "bid_watch", "planner", "executor", "schatten", "auto_trading",
)

#: Aufbewahrung flüchtiger Berichte in Tagen.
REPORT_RETENTION_DAYS = 90


def purge_old_reports(
    now: datetime | None = None,
    *,
    days: int = REPORT_RETENTION_DAYS,
    session: Session | None = None,
) -> int:
    """Flüchtige Berichte älter als `days` löschen — Rückgabe: gelöschte Zeilen.

    Regelbasiert und eng begrenzt (nur EPHEMERAL_KINDS): das Archiv bleibt für
    Substanz-Berichte vollständig, und das Audit-Log liegt in einer anderen
    Tabelle. Fehler werden nur geloggt (0 zurück) — Aufräumen darf nie einen
    Job reißen.
    """
    cutoff = (now or datetime.now(UTC)).replace(tzinfo=None) - timedelta(days=days)
    try:
        own = session is None
        s = SessionLocal() if own else session
        try:
            deleted = (
                s.query(Report)
                .filter(Report.kind.in_(EPHEMERAL_KINDS), Report.created_at < cutoff)
                .delete(synchronize_session=False)
            )
            s.commit()
            return int(deleted)
        finally:
            if own:
                s.close()
    except Exception:  # noqa: BLE001 — Aufräumen ist nie einen Jobabbruch wert
        _log.warning("Berichte-Aufräumen übersprungen.", exc_info=True)
        return 0
