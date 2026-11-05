"""Erwartung je Gebot — und die Prüfung, ob sie stimmt (Emils Auftrag 25.08.).

Anlass, in Emils Worten: „Wenn du da die ganze Zeit auf Spieler wettest, wo es sehr
unwahrscheinlich ist, sie zu bekommen, dann bindest du Geld." Vier von fünf
Manager-Deals scheiterten — aufgefallen ist das erst, als er fragte, weil jede
Einzelentscheidung für sich vernünftig aussah und niemand die Summe zog.

Deshalb trägt ab jetzt **jedes Gebot eine überprüfbare Erwartung**: eine
Zuschlag-Wahrscheinlichkeit mit Begründung, gespeichert im Audit-Eintrag
(`transfer_actions.payload["expectation"]`). Der stündliche Abgleich schreibt das
Ergebnis dazu (`payload["outcome"]`), und die Kalibrierung hält beides gegeneinander.

**Die Schätzformel ist selbst die Hypothese.** Ihre Zahlen stammen aus der Empirie
(siehe LEARNINGS 25.08.: Kickbase-Listings 4/5 gewonnen, Manager-Deals 1/5), und die
Kalibrierung prüft sie fortlaufend. Weicht sie systematisch ab, meldet sich der Agent
selbst — das ist der Unterschied zwischen Notizen sammeln und lernen.

Bewusst grob: Wahrscheinlichkeiten in Stufen, keine Nachkommastellen-Akrobatik. Bei
zweistelligen Fallzahlen wäre jede feinere Kurve gelogen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import TransferAction

#: Unter so vielen abgeschlossenen Geboten wird nichts gemeldet — vier Fälle sind
#: keine Statistik, und eine Zahl mit n=4 klingt sicherer, als sie ist.
MIN_SAMPLE = 5

#: Ab dieser Abweichung zwischen Erwartung und Realität meldet sich der Agent.
CALIBRATION_TOLERANCE = 0.15

KICKBASE, MANAGER = "kickbase", "manager"

# --- Stellschrauben (autonomer Lernzyklus, Stufe D) ------------------------
# Die Zahlen dieser Formel sind ihre Hypothese — also gehören sie nicht als
# Literale in den Code, sondern in eine versionierte Datei, die man mit Beleg
# ändern kann (knowledge/tuning.json). Die Konstanten hier bleiben der
# NOTFALL-RÜCKFALL: fehlt die Datei oder ist sie kaputt, rechnet die Formel
# weiter mit genau den Werten, mit denen sie geschrieben wurde.
_FALLBACK: dict[str, float] = {
    "expectation.manager_underpay_p": 0.02,
    "expectation.manager_thin_p": 0.15,
    "expectation.manager_premium_p": 0.55,
    "expectation.manager_thin_ratio": 1.15,
    "expectation.kickbase_underpay_p": 0.30,
    "expectation.kickbase_underpay_ratio": 0.90,
    "expectation.kickbase_thin_p": 0.45,
    "expectation.kickbase_over_p": 0.60,
    "expectation.kickbase_over_ratio": 1.20,
    "expectation.kickbase_far_over_p": 0.80,
    "expectation.kickbase_far_over_ratio": 1.35,
    "expectation.min_sample": MIN_SAMPLE,
    "expectation.calibration_tolerance": CALIBRATION_TOLERANCE,
}


def _t(name: str) -> float:
    """Ein Stellschrauben-Wert; Rückfall auf die Code-Konstante."""
    from kickbase_agent.knowledge.tuning import tuning_value

    return float(tuning_value(name, _FALLBACK[name]))


@dataclass(frozen=True)
class Expectation:
    """Was wir uns von einem Gebot versprechen — prüfbar formuliert."""

    p: float          # Zuschlag-Wahrscheinlichkeit 0..1
    why: str          # ein Satz: worauf die Schätzung beruht
    kind: str         # kickbase | manager (getrennt kalibriert)
    ratio: float = 0  # Gebot / Marktwert, für die spätere Auswertung

    def as_dict(self) -> dict[str, Any]:
        return {"p": round(float(self.p), 2), "why": self.why,
                "kind": self.kind, "ratio": round(float(self.ratio), 3)}

    @property
    def label(self) -> str:
        return f"Zuschlag-Chance {int(round(self.p * 100))} % — {self.why}"


def estimate_bid_success(
    *,
    kind: str,
    amount: int,
    market_value: int,
    listing_price: int | None = None,
) -> Expectation:
    """Zuschlag-Wahrscheinlichkeit aus den Faktoren, die wir wirklich kennen.

    Fremde Gebote gehören NICHT dazu — weder ihre Höhe noch ihre Anzahl. Das
    Feld `ofc` sah lange nach einem Bieterzähler aus; die Stichprobe vom
    26.08.2026 zeigt, dass es ausschließlich unsere eigenen Gebote zählt
    (49 von 51 Listings standen auf 0, die zwei mit 1 waren genau die, auf die
    wir geboten hatten). Ein Kickbase-Gebot ist blind: Es entscheidet allein
    die eigene Gebotshöhe. Siehe L-079.
    """
    mv = max(1, int(market_value or 0))
    ratio = float(amount) / mv

    if kind == MANAGER:
        # Der Verkäufer vergleicht immer mit dem Marktwert, den Kickbase ihm
        # garantiert — darunter hat er keinen Grund anzunehmen (Bilanz 25.08.:
        # Ulrich 2x, Gyamerah, Otávio alle abgelehnt), und die API blockt es sogar.
        if ratio < 1.0:
            return Expectation(
                _t("expectation.manager_underpay_p"),
                "unter Marktwert — Kickbase blockt (err 5080)", MANAGER, ratio,
            )
        if ratio < _t("expectation.manager_thin_ratio"):
            return Expectation(
                _t("expectation.manager_thin_p"),
                "nur knapp über Marktwert — der Verkäufer bekommt dasselbe von Kickbase",
                MANAGER, ratio,
            )
        return Expectation(
            _t("expectation.manager_premium_p"),
            f"echter Aufschlag ({int(round((ratio - 1) * 100))} % über Marktwert)",
            MANAGER, ratio,
        )

    # Kickbase-Listing: beim Ablauf gewinnt schlicht das höchste Gebot.
    if ratio < _t("expectation.kickbase_underpay_ratio"):
        return Expectation(
            _t("expectation.kickbase_underpay_p"),
            "Auto-Zuschlag, aber deutlich unter Marktwert (Friedrich-Fall 22.08.)",
            KICKBASE, ratio,
        )
    # Ljubičić-Lehre (26.08.), nachgemessen am selben Abend: Wir boten 108 % und
    # galten „laut API als einziger Bieter" — beim Ablauf schnappte Lukas M.
    # ihn mit 157 % weg. Der Grund ist simpler als gedacht: `ofc` zählt gar keine
    # fremden Gebote, sondern nur unsere eigenen. Wir waren nie informiert, wir
    # waren blind. Deshalb entscheidet hier ausschließlich die Gebotshöhe.
    if ratio >= _t("expectation.kickbase_far_over_ratio"):
        return Expectation(
            _t("expectation.kickbase_far_over_p"),
            "Auto-Zuschlag, Gebot deutlich über Marktwert", KICKBASE, ratio,
        )
    if ratio >= _t("expectation.kickbase_over_ratio"):
        return Expectation(
            _t("expectation.kickbase_over_p"),
            "Auto-Zuschlag, Gebot spürbar über Marktwert", KICKBASE, ratio,
        )
    return Expectation(
        _t("expectation.kickbase_thin_p"),
        "Auto-Zuschlag, aber nur knapp über Marktwert — Späteinsteiger schlagen das",
        KICKBASE, ratio,
    )


# ------------------------------------------------------------------ Auswertung


@dataclass(frozen=True)
class Settled:
    action_id: int
    player_id: str
    result: str        # won | lost
    p: float | None


def _payload(row: TransferAction) -> dict[str, Any]:
    return dict(row.payload or {})


def settle_bid_expectations(
    session: Session,
    *,
    squad_pids: set[str],
    open_bid_pids: set[str],
    now: datetime,
    league_id: str | None = None,
    receipts: list[dict] | None = None,
    my_name: str = "",
) -> list[Settled]:
    """Belege abgleichen; verschwunden ist ausdrücklich keine Niederlage.

    Nur ein passender Transfer nach dem Gebot belegt den Zuschlag. Ein späterer
    Verkauf löscht diesen Beleg nicht. Frühere Gebotsversionen derselben Auktion
    werden nicht als zusätzliche Wettbewerbsbeispiele gezählt.
    """
    def utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    query = select(TransferAction).where(
        TransferAction.action_type.in_(("bid", "cancel_bid")),
        TransferAction.status == "executed",
    )
    if league_id is not None:
        query = query.where(TransferAction.league_id == league_id)
    all_rows = session.execute(query).scalars().all()
    rows = [r for r in all_rows if r.action_type == "bid"]
    rows.sort(key=lambda r: (utc(r.created_at), r.id), reverse=True)
    used_receipts: set[tuple[str, str]] = set()
    settled: list[Settled] = []
    changed = False
    for row in rows:
        data = _payload(row)
        exp, previous = data.get("expectation"), data.get("outcome") or {}
        if not exp:
            continue
        pid = str(row.player_id or "")
        if previous.get("evidence_version") == 2 and (
            previous.get("training_eligible") is True
            or previous.get("result") in {"cancelled", "superseded"}
        ):
            if previous.get("event_id"):
                used_receipts.add((row.league_id, str(previous["event_id"])))
            continue
        if pid in open_bid_pids:
            continue
        created = utc(row.created_at)
        outcome: dict = {"result": "unresolved", "evidence_version": 2,
                         "training_eligible": False, "source": "missing_receipt"}
        cancellations = [r for r in all_rows if r.action_type == "cancel_bid"
                         and r.league_id == row.league_id and r.player_id == pid
                         and created <= utc(r.created_at) <= now]
        matches = []
        for event in receipts or []:
            event_data = event.get("data") or {}
            if event.get("t") != 15 or str(event_data.get("pi")) != pid:
                continue
            try:
                stamp = datetime.fromisoformat(event["dt"])
            except (ValueError, TypeError, KeyError):
                continue
            if (stamp.tzinfo is None or not created <= stamp <= now
                    or stamp - created > timedelta(hours=48)
                    or not event.get("i") or not event_data.get("byr")):
                continue
            # Ein anderer Handelstyp beweist keinen Ausgang dieser Auktion.
            if bool(event_data.get("slr")) != (exp.get("kind") == MANAGER):
                continue
            matches.append((stamp, event))
        matches.sort(key=lambda item: item[0])
        if matches and my_name:
            stamp, event = matches[0]
            event_data = event["data"]
            event_id = str(event["i"])
            cancelled = any(utc(r.created_at) <= stamp for r in cancellations)
            if (row.league_id, event_id) in used_receipts:
                outcome.update(result="superseded", source="newer_bid_version")
            elif cancelled:
                outcome.update(result="cancelled", source="executed_cancellation")
            else:
                won = str(event_data["byr"]).strip().casefold() == my_name.strip().casefold()
                paid = event_data.get("trp")
                # Ein abweichender eigener Preis oder ein billigerer fremder Kauf
                # kann ein anderes Gebot bzw. eine technische Ablehnung bedeuten.
                confirmed = type(paid) is int and row.amount is not None and (
                    (won and paid == row.amount) or (not won and paid >= row.amount)
                )
                if confirmed:
                    outcome.update(
                        result="won" if won else "lost", event_id=event_id,
                        source="confirmed_receipt", settled_at=stamp.isoformat(),
                        winner_amount=paid, training_eligible=True,
                    )
                    used_receipts.add((row.league_id, event_id))
        elif cancellations:
            outcome.update(result="cancelled", source="executed_cancellation")
        elif pid in squad_pids or previous.get("source") == "observed_ownership":
            # Besitz belegt Erwerb, aber ohne Beleg noch nicht welches Gebot gewann.
            outcome.update(result="won", source="observed_ownership")
        if (previous.get("result") == outcome["result"]
                and previous.get("source") == outcome["source"]):
            continue
        outcome["at"] = now.isoformat()
        data["outcome"] = outcome
        row.payload = data
        changed = True
        if outcome["result"] in {"won", "lost"} and previous.get("result") != outcome["result"]:
            probability = exp.get("p")
            settled.append(Settled(row.id, pid, outcome["result"], probability))
    if changed:
        session.commit()
    return settled


# ------------------------------------------------------------------ Kalibrierung


@dataclass
class Group:
    kind: str
    total: int = 0
    won: int = 0
    expected: float = 0.0

    @property
    def expected_rate(self) -> float:
        return self.expected / self.total if self.total else 0.0

    @property
    def actual_rate(self) -> float:
        return self.won / self.total if self.total else 0.0


@dataclass
class CalibrationReport:
    total: int = 0
    won: int = 0
    expected: float = 0.0
    #: Aufgeloeste Gebote, deren Erwartung NACHGETRAGEN wurde — gezaehlt, aber
    #: nicht verrechnet. Sie erscheinen in der Meldung, damit erklaerbar bleibt,
    #: warum die Stichprobe kleiner ist als die Zahl der Gebote.
    backfilled: int = 0
    groups: list[Group] = field(default_factory=list)

    @property
    def expected_rate(self) -> float:
        return self.expected / self.total if self.total else 0.0

    @property
    def actual_rate(self) -> float:
        return self.won / self.total if self.total else 0.0

    @property
    def gap(self) -> float:
        return self.actual_rate - self.expected_rate


def calibration(session: Session, *, now: datetime, days: int = 30) -> CalibrationReport:
    """Erwartung gegen Realität — gesamt und getrennt nach Listing-Art.

    Die Trennung ist wesentlich: Kickbase- und Manager-Gebote sind zwei völlig
    verschiedene Wetten; zusammengerührt ergäben sie eine Zahl ohne Aussage.

    NACHGETRAGENE Erwartungen (``expectation.backfilled``) bleiben draußen
    (28.08.2026). Sie wurden nicht zur Tatzeit geschrieben, sondern später
    rekonstruiert — teils als der Ausgang schon feststand. Eine im Nachhinein
    gesetzte Zahl ist keine Vorhersage, und eine Kalibrierung, die sie mitzählt,
    misst nicht die Formel, sondern das eigene Gedächtnis. Konkret betraf das
    vier von sieben Fällen, alle mit p = 0.9 und der Begründung „bisher einziger
    Bieter" — also genau der Annahme, die sich als haltlos erwiesen hat (L-079).
    Sie werden gezählt und gemeldet, damit ihr Fehlen erklärbar bleibt.
    """
    since = now - timedelta(days=days)
    rows = session.execute(
        select(TransferAction).where(
            TransferAction.action_type == "bid",
            TransferAction.created_at >= since,
        )
    ).scalars().all()

    report = CalibrationReport()
    by_kind: dict[str, Group] = {}
    for row in rows:
        data = _payload(row)
        exp, out = data.get("expectation"), data.get("outcome")
        if not exp or not out:
            continue
        if exp.get("backfilled"):
            report.backfilled += 1
            continue
        if (out.get("result") not in {"won", "lost"}
                or out.get("training_eligible") is not True
                or not isinstance(exp.get("p"), (int, float))
                or isinstance(exp.get("p"), bool) or not 0 <= exp["p"] <= 1):
            continue
        p = float(exp["p"])
        won = out.get("result") == "won"
        kind = str(exp.get("kind") or "?")
        group = by_kind.setdefault(kind, Group(kind))
        for target in (report, group):
            target.total += 1
            target.expected += p
            if won:
                target.won += 1
    report.groups = sorted(by_kind.values(), key=lambda g: -g.total)
    return report


def format_calibration(report: CalibrationReport) -> str | None:
    """Discord-Text — nur wenn die Stichprobe trägt UND etwas nicht stimmt.

    Schweigen ist hier das richtige Verhalten: Eine Meldung „Erwartung passt"
    wäre bei n=6 genauso wenig belastbar wie eine Warnung.
    """
    if (report.total < _t("expectation.min_sample")
            or abs(report.gap) <= _t("expectation.calibration_tolerance")):
        return None
    zu = "zu optimistisch" if report.gap < 0 else "zu vorsichtig"
    lines = [
        f"🎯 **Meine Gebots-Erwartung war {zu}** "
        f"({report.total} abgeschlossene Gebote): erwartet "
        f"{report.expected_rate:.0%} Zuschläge, tatsächlich {report.actual_rate:.0%}.",
    ]
    for g in report.groups:
        if g.total < 2:
            continue
        lines.append(
            f"• {g.kind}: {g.won}/{g.total} gewonnen (erwartet {g.expected_rate:.0%})"
        )
    if report.backfilled:
        lines.append(
            f"• {report.backfilled} Gebot"
            f"{'e' if report.backfilled != 1 else ''} mit nachgetragener Erwartung "
            "bleiben draußen — eine im Nachhinein gesetzte Zahl ist keine Vorhersage."
        )
    lines.append(
        "-# Konsequenz: Schätzformel in `trading/expectation.py` prüfen und die "
        "Beschaffungs-Doktrin nachziehen — nicht einfach weiterbieten."
    )
    return "\n".join(lines)


def expectation_for_market_row(amount: int, row: Any) -> Expectation:
    """Bequemer Weg vom Markt-Item zur Erwartung (Verkäufer ⇒ Listing-Art)."""
    seller = (getattr(row, "seller_name", None) or (row.get("u") or {}).get("n")
              if isinstance(row, dict) else getattr(row, "seller_name", None))
    kind = MANAGER if seller else KICKBASE
    if isinstance(row, dict):
        mv, prc = int(row.get("mv") or 0), int(row.get("prc") or 0)
    else:
        mv = int(getattr(row.player, "market_value", 0))
        prc = int(getattr(row, "price", 0))
    return estimate_bid_success(kind=kind, amount=amount, market_value=mv, listing_price=prc)


def now_utc() -> datetime:
    return datetime.now(UTC)
