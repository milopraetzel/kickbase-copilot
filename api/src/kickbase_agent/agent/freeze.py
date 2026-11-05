"""Selbst-Einfrieren: der Agent legt sich still, bevor ein schlechter Tag teuer wird.

Autonomie ohne Notbremse ist kein Fortschritt, sondern eine Wette. Drei Lagen sind
so eindeutig, dass Weitermachen falsch ist — und in allen dreien merkt es der Agent
selbst, bevor ein Mensch hinschaut:

1. **Tagesverlust über der Schwelle** — realisiert aus dem Audit-Log: jeder heute
   verkaufte Spieler gegen den Preis, den der Agent selbst dafür gezahlt hat.
   Spieler ohne eigenen Einstandspreis zählen NICHT mit (eine geschätzte Basis wäre
   eine erfundene Zahl).
2. **Zwei oder mehr unklare Writes an einem Tag** (`unknown`/`failed`) — einmal ist
   ein Zwischenfall, zweimal ist ein Muster: Entweder die API antwortet anders als
   gedacht, oder wir schreiben Unsinn. Beides klärt man nicht durch mehr Writes.
3. **Fehlgeschlagener Selbst-Deploy** — wer sich gerade selbst kaputt gemacht hat,
   soll nicht mit Geld weiterarbeiten (gesetzt von agent/self_change.py).

Wirkung: Der ActionExecutor verweigert Geld-Writes mit klarer Begründung — an
derselben Stelle wie die bestehenden Interlocks, KEINE zweite Schreibstelle.
Ausgenommen bleiben Aktionen, die Risiko ABBAUEN (Storno, Auslisten), Writes ohne
Geld (Aufstellung, Prämie) und die Anpfiff-Notbremse (`kickoff-*`) — Konto ≥ 0 beim
Anpfiff schlägt jede Selbstsicherung, sonst kostet die Sicherung einen Spieltag.

Entsperrt wird ausschließlich von Hand (`kba unfreeze --by emil`). Ein Agent, der
sich selbst wieder scharf schaltet, hat keine Notbremse, sondern eine Verzögerung.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import AgentFreeze, TransferAction

SOURCE_LOSS = "verlust"
SOURCE_WRITES = "fehl-writes"
SOURCE_DEPLOY = "deploy"
SOURCE_MANUAL = "manuell"
FREEZE_SOURCES: tuple[str, ...] = (SOURCE_LOSS, SOURCE_WRITES, SOURCE_DEPLOY, SOURCE_MANUAL)

#: Realisierter Tagesverlust, ab dem eingefroren wird (Default; die Schwelle liegt
#: als `freeze.daily_loss_limit` in knowledge/tuning.json und ist damit belegpflichtig
#: änderbar). Bis 02.09. 1 M € — dann fror sich der Agent nach zwei von Emil
#: mandatierten Verlierer-Verkäufen (−3,2 M realisiert) selbst ein: Die Momentum-
#: Doktrin realisiert Verluste BEWUSST, ein Doktrin-Tag muss durch die Schwelle
#: passen. 10 M ≈ Tages-Cap (5 Verkäufe) × ~2 M Drift — der Notausgang gegen einen
#: Panik-Ausverkauf bleibt (Decision-Record 02.09., Anker 37be3e3).
DAILY_LOSS_LIMIT = 10_000_000

#: Unklare Writes (`unknown`/`failed`) an einem Tag, ab denen eingefroren wird.
#: `refused` zählt NICHT — das ist die Sicherung bei der Arbeit, kein Störfall.
UNCLEAR_WRITE_LIMIT = 2
_UNCLEAR_STATUSES = ("unknown", "failed")

#: Aktionstypen, die der Einfrier-Zustand sperrt: alles, was Geld bindet oder
#: den Kader verkleinert. Bewusst NICHT dabei: cancel_bid/delist (bauen Risiko ab),
#: lineup/bonus (kein Geld).
FROZEN_ACTION_TYPES: tuple[str, ...] = ("bid", "sell", "list", "accept")

#: Die Anpfiff-Notbremse ist von jeder Selbstsicherung ausgenommen (wie schon bei
#: den Tages-Caps im Executor): sie rettet den Spieltag, sie riskiert ihn nicht.
_EXEMPT_RULE_PREFIX = "kickoff-"


def _as_utc(stamp: datetime) -> datetime:
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def _mio(value: int) -> str:
    return f"{value / 1_000_000:.1f} M €".replace(".", ",")


# ----------------------------------------------------------------- Zustand


def active_freeze(session: Session) -> AgentFreeze | None:
    """Der aktive Einfrier-Zustand (jüngster ohne Freigabe) oder None."""
    return session.execute(
        select(AgentFreeze)
        .where(AgentFreeze.released_at.is_(None))
        .order_by(AgentFreeze.id.desc())
        .limit(1)
    ).scalars().first()


def is_frozen(session: Session) -> bool:
    return active_freeze(session) is not None


def freeze(
    session: Session,
    *,
    reason: str,
    source: str,
    now: datetime,
    evidence: dict[str, Any] | None = None,
) -> AgentFreeze:
    """Einfrieren — idempotent: ein bestehender Zustand behält seinen ERSTEN Grund.

    Der erste Grund ist der wahre; was danach schiefgeht, ist meist Folge. Wer die
    Ursache sucht, will nicht die letzte Fehlermeldung lesen.
    """
    existing = active_freeze(session)
    if existing is not None:
        return existing
    row = AgentFreeze(
        readonly_since=_as_utc(now),
        reason=reason,
        source=source if source in FREEZE_SOURCES else SOURCE_MANUAL,
        evidence=evidence or {},
    )
    session.add(row)
    session.commit()
    return row


def release(
    session: Session, *, by: str, now: datetime, note: str = ""
) -> AgentFreeze | None:
    """Entsperren (nur von Hand). None = es war gar nichts eingefroren."""
    row = active_freeze(session)
    if row is None:
        return None
    row.released_at = _as_utc(now)
    row.released_by = by
    row.note = note or None
    session.commit()
    return row


def freeze_veto(
    session: Session, action_type: str, rule_id: str | None
) -> str | None:
    """Interlock-Antwort für den ActionExecutor; None = kein Einwand.

    Bewusst als reine Abfrage: Die Entscheidung fällt hier, geschrieben wird
    weiterhin ausschließlich in agent/executor.py.
    """
    if action_type not in FROZEN_ACTION_TYPES:
        return None
    if (rule_id or "").startswith(_EXEMPT_RULE_PREFIX):
        return None
    row = active_freeze(session)
    if row is None:
        return None
    since = _as_utc(row.readonly_since)
    return (
        f"Selbst-Einfrieren aktiv seit {since:%d.%m. %H:%M} UTC ({row.source}): "
        f"{row.reason} — Geld-Writes gesperrt, Entsperren nur von Hand "
        "(`kba unfreeze --by emil`)."
    )


# ----------------------------------------------------------------- Auslöser


@dataclass
class RealizedPnL:
    """Realisierter Tages-Saldo aus dem Audit-Log."""

    total: int = 0
    #: Verkäufe ohne eigenen Einstandspreis — bewusst nicht bewertet.
    unknown_basis: int = 0
    trades: list[dict[str, Any]] = field(default_factory=list)

    @property
    def loss(self) -> int:
        """Verlust als positive Zahl (0, wenn der Tag im Plus war)."""
        return -self.total if self.total < 0 else 0


def realized_pnl(session: Session, day: date) -> RealizedPnL:
    """Was der Agent heute WIRKLICH realisiert hat.

    Für jeden heute ausgeführten Verkauf (`sell`/`accept`) wird der letzte eigene
    ausgeführte Kauf desselben Spielers als Einstandspreis genommen. Ohne so einen
    Kauf gibt es keine ehrliche Basis — der Fall wird gezählt, nicht geschätzt.
    """
    rows = session.execute(
        select(TransferAction).where(
            TransferAction.status == "executed",
            TransferAction.dry_run.is_(False),
        ).order_by(TransferAction.id)
    ).scalars().all()

    purchases: dict[str, int] = {}
    result = RealizedPnL()
    for row in rows:
        pid = str(row.player_id or "")
        stamp = _as_utc(row.created_at)
        if row.action_type == "bid" and pid:
            purchases[pid] = int(row.amount or 0)
            continue
        if row.action_type not in ("sell", "accept") or not pid:
            continue
        if stamp.date() != day:
            continue
        basis = purchases.get(pid)
        if basis is None:
            result.unknown_basis += 1
            continue
        gain = int(row.amount or 0) - basis
        result.total += gain
        result.trades.append(
            {"player_id": pid, "kauf": basis, "verkauf": int(row.amount or 0), "saldo": gain}
        )
    return result


def unclear_writes(session: Session, day: date) -> list[TransferAction]:
    """Echte Writes des Tages, die ungeklärt oder fehlgeschlagen endeten."""
    rows = session.execute(
        select(TransferAction).where(
            TransferAction.status.in_(_UNCLEAR_STATUSES),
            TransferAction.dry_run.is_(False),
        )
    ).scalars().all()
    return [r for r in rows if _as_utc(r.created_at).date() == day]


@dataclass(frozen=True)
class FreezeTrigger:
    source: str
    reason: str
    evidence: dict[str, Any]


#: Code-Defaults der beiden Schwellen. Sie liegen zusätzlich in
#: knowledge/tuning.json: die Datei gewinnt, wenn sie lesbar ist — fehlt oder
#: bricht sie, gelten diese Werte (nie ein Absturz, nie ein stilles 0).
_FALLBACKS: dict[str, int] = {
    "freeze.daily_loss_limit": DAILY_LOSS_LIMIT,
    "freeze.unclear_write_limit": UNCLEAR_WRITE_LIMIT,
}


def _limit(name: str) -> int:
    """Schwelle aus der versionierten Tuning-Datei, Code-Default als Fallback."""
    from kickbase_agent.knowledge.tuning import tuning_value

    return int(tuning_value(name, _FALLBACKS[name]))


def evaluate(
    session: Session,
    *,
    now: datetime,
    loss_limit: int | None = None,
    unclear_limit: int | None = None,
) -> FreezeTrigger | None:
    """Prüft die Auslöser für HEUTE; None = alles normal, nichts zu tun.

    Reihenfolge: Geld vor Technik. Ein Verlusttag ist der teurere Befund.
    """
    day = _as_utc(now).date()
    limit = int(loss_limit if loss_limit is not None else _limit("freeze.daily_loss_limit"))
    pnl = realized_pnl(session, day)
    if pnl.loss >= limit:
        return FreezeTrigger(
            SOURCE_LOSS,
            f"Realisierter Tagesverlust {_mio(pnl.loss)} (Schwelle {_mio(limit)})",
            {"loss": pnl.loss, "limit": limit, "trades": pnl.trades},
        )

    max_unclear = int(
        unclear_limit if unclear_limit is not None else _limit("freeze.unclear_write_limit")
    )
    rows = unclear_writes(session, day)
    if len(rows) >= max_unclear:
        detail = ", ".join(f"#{r.id} {r.action_type}/{r.status}" for r in rows[:6])
        return FreezeTrigger(
            SOURCE_WRITES,
            f"{len(rows)} unklare Writes heute ({detail})",
            {"count": len(rows), "limit": max_unclear,
             "actions": [r.id for r in rows]},
        )
    return None


def evaluate_and_freeze(
    session: Session,
    *,
    now: datetime,
    loss_limit: int | None = None,
    unclear_limit: int | None = None,
) -> AgentFreeze | None:
    """Auslöser prüfen und ggf. einfrieren. None = nichts passiert (Normalfall).

    Gibt auch dann None zurück, wenn bereits eingefroren war — der Aufrufer soll
    nur bei einem NEUEN Einfrieren alarmieren, nicht stündlich dieselbe Meldung.
    """
    if is_frozen(session):
        return None
    trigger = evaluate(
        session, now=now, loss_limit=loss_limit, unclear_limit=unclear_limit
    )
    if trigger is None:
        return None
    return freeze(
        session,
        reason=trigger.reason,
        source=trigger.source,
        now=now,
        evidence=trigger.evidence,
    )


# ----------------------------------------------------------------- Meldung


def format_alarm(row: AgentFreeze) -> str:
    """Discord-Alarm: was passiert ist, was jetzt gilt, wie man es aufhebt."""
    since = _as_utc(row.readonly_since)
    return (
        "🧊 **Ich habe mich selbst eingefroren.**\n"
        f"Grund ({row.source}): {row.reason}\n"
        f"Seit {since:%d.%m.%Y %H:%M} UTC führe ich **keine Geld-Writes** mehr aus "
        "(Gebote, Verkäufe, Listings, Angebots-Annahmen). Aufstellung, Prämie, "
        "Gebots-Storno und die Anpfiff-Notbremse laufen weiter.\n"
        "-# Entsperren nur von Hand: `kba unfreeze --by emil --note \"…\"` — "
        "vorher bitte nachsehen, ob die Ursache behoben ist."
    )


def format_status(row: AgentFreeze | None) -> str:
    """Klartext-Status für die CLI."""
    if row is None:
        return "Nicht eingefroren — Geld-Writes sind (soweit geärmt) erlaubt."
    since = _as_utc(row.readonly_since)
    return (
        f"EINGEFROREN seit {since:%d.%m.%Y %H:%M} UTC ({row.source}): {row.reason}\n"
        "Geld-Writes gesperrt. Entsperren: kba unfreeze --by emil"
    )


#: Wer den Freeze automatisch löst (steht so im Protokoll — nie „emil").
AUTO_RELEASER = "auto (Tagesende, Auslöser nicht mehr aktiv)"


def release_if_stale(session: Session, *, now: datetime) -> AgentFreeze | None:
    """Selbst-Verfall: einen Freeze aus einem VERGANGENEN Tag freigeben.

    Emils Vollfreigabe (02.09.2026, „wirklich volle Freigabe"): Nichts soll auf
    einen Menschen warten. Ein Endpunkt, mit dem der handelnde Agent seine
    eigene Notbremse abschaltet, wäre aber keine Sicherung mehr — deshalb
    dieser Weg: Alle Auslöser sind TAGES-Größen (realisierter Tagesverlust,
    unklare Writes des Tages). Also ist auch die Bremse tagesgebunden.

    Freigegeben wird nur, wenn BEIDES gilt:
    1. Der Freeze stammt aus einem früheren UTC-Tag (heute eingefroren heißt
       heute gesperrt — der laufende Tag wird nie selbst entschärft), und
    2. `evaluate` findet für HEUTE keinen Auslöser mehr. Läuft die Schieflage
       weiter, bleibt der Freeze also bestehen, Tag für Tag.

    Der Deploy-Auslöser (SOURCE_DEPLOY) ist ausgenommen: ein kaputter
    Selbstumbau heilt nicht durch Zeitablauf, den räumt ein Mensch weg.
    Rückgabe: die freigegebene Zeile oder None (nichts zu tun).
    """
    row = active_freeze(session)
    if row is None or row.source == SOURCE_DEPLOY:
        return None
    stamp = _as_utc(now)
    if _as_utc(row.readonly_since).date() >= stamp.date():
        return None  # heute eingefroren — heute gesperrt
    if evaluate(session, now=stamp) is not None:
        return None  # die Lage von heute rechtfertigt den Freeze weiterhin
    return release(
        session,
        by=AUTO_RELEASER,
        now=stamp,
        note=(
            f"Auslöser vom {_as_utc(row.readonly_since):%d.%m.} ({row.source}: {row.reason}) "
            "gilt heute nicht mehr — automatisch freigegeben."
        ),
    )
