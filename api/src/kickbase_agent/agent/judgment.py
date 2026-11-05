"""Urteil im Einzelfall — der Code trägt den Fall zusammen, entschieden wird woanders.

Der naheliegende Fehler wäre eine Tabelle: „hohe Erwartung verloren ⇒ Aufschlag
+0,01". Das wäre dieselbe Hardcodierung wie vorher, nur eine Ebene tiefer versteckt
— und sie wäre falsch, weil dieselbe Beobachtung drei verschiedene Ursachen haben
kann: unsere Formel, der Markt, oder ein Mechanismus der API, den wir nicht kannten.

Deshalb baut dieses Modul eine **Fallakte** (Erwartung, Wirklichkeit, vergleichbare
Altfälle, einschlägige Learnings, aktuelle Stellschrauben) und legt sie dem Agenten
vor — über denselben Weg wie die tägliche Recherche (`claude -p`, Abo-Token). Die
Fallakte enthält KEINEN Vorschlag. Sie ist Material, keine Meinung.

Zurück kommt genau eine von drei Antworten:

* **einzelfall** — nur protokollieren. Der Normalausgang, und mit Absicht auch der
  Ausgang jedes Zweifels: unlesbare Antwort, fehlende Recherche, unbekannte
  Stellschraube, zu dünne Beweislage.
* **parameter** — eine Zahl in `knowledge/tuning.json` ändern.
* **code** — eine Zeile Code ändern.

Zwei mechanische Riegel stehen vor den teuren Ausgängen (beide gelten für Parameter
UND Code):

1. **Zwei-Treffer-Regel.** Es braucht mindestens einen zweiten Fall desselben
   Fingerabdrucks in den letzten 21 Tagen. Der Fingerabdruck wird gerechnet, nicht
   gewählt — sonst könnte man ihn passend machen.
2. **Mechanismus-Ausnahme.** Ein einzelner Fall genügt nur, wenn die Recherche einen
   *Mechanismus* belegt statt einer *Tendenz*. Mechanismus = eine überprüfbare
   Aussage darüber, WIE das System funktioniert („`ofc` zeigt nur aktuelle Bieter");
   die gilt immer. Tendenz = eine Aussage über Häufigkeit oder Stimmung („die Preise
   waren diese Woche hoch"); die braucht den zweiten Fall.

Ist die CLI nicht erreichbar, wird der Fehler weitergereicht — nie geraten. Der
Scheduler merkt offene Aktionsfälle für den nächsten Lauf vor; Job-Fälle haben
bislang keinen eigenen dauerhaften Urteilsspeicher.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import TransferAction
from kickbase_agent.intel.researcher import ClaudeAuthError, ClaudeCliError
from kickbase_agent.knowledge.tuning import DEFAULTS, load_tuning
from kickbase_agent.worker.surprise_job import Surprise, surprise_thresholds

log = logging.getLogger(__name__)

__all__ = [
    "ClaudeAuthError", "ClaudeCliError", "Verdict", "build_context", "enforce",
    "format_prompt", "judge_surprise", "parse_verdict",
]

EINZELFALL, PARAMETER, CODE = "einzelfall", "parameter", "code"
VERDICTS: tuple[str, ...] = (EINZELFALL, PARAMETER, CODE)

#: Fenster für vergleichbare Altfälle. Drei Wochen: lang genug für ein Muster bei
#: ~10 Trades/Woche, kurz genug, dass eine alte Marktlage nicht mitentscheidet.
COMPARISON_WINDOW_DAYS = 21

#: Ab so vielen gleichartigen Fällen (inklusive dem aktuellen) darf geändert werden.
MIN_SIMILAR_CASES = 2

#: Ein „Mechanismus" muss eine Aussage sein, kein Stichwort. Wer die Ausnahme zieht,
#: soll erklären müssen, wie das System funktioniert — in einem ganzen Satz.
MIN_MECHANISM_CHARS = 40

SYSTEM_PROMPT = (
    "Du beurteilst EINEN konkreten Vorfall im Kickbase-Agenten (Fantasy-Bundesliga). "
    "Der Agent hat etwas erwartet, die Wirklichkeit war anders. Deine Aufgabe: "
    "erklaeren, was passiert ist — und nur dann eine Aenderung vorschlagen, wenn sie "
    "belegt ist.\n"
    "REGELN:\n"
    "- Der Normalausgang ist 'einzelfall'. Ein Ausreisser verstellt nichts.\n"
    "- 'parameter' oder 'code' nur, wenn ES MINDESTENS EINEN ZWEITEN gleichartigen "
    "Fall gibt (steht im Dossier als gleichartige_faelle) ODER du einen MECHANISMUS "
    "belegen kannst: eine ueberpruefbare Aussage darueber, WIE das System "
    "funktioniert, die immer gilt — nicht eine Tendenz ('war diese Woche teuer').\n"
    "- Recherche ist Pflicht: fuelle 'recherche' mit Quelle, Datum und Aussage, oder "
    "begruende in 'markt_unauffaellig', warum es nichts zu finden gab.\n"
    "- 'stellschraube' muss ein Schluessel aus dem Block stellschrauben sein. "
    "Erfinde keinen Namen. Aendere nie mehr als eine Zahl.\n"
    "- Du fuehrst nichts aus. Du beurteilst.\n"
    "Antworte AUSSCHLIESSLICH als JSON-Objekt mit den Feldern: ausgang "
    "('einzelfall'|'parameter'|'code'), erklaerung (1-3 Saetze), mechanismus "
    "(Satz oder null), recherche (Liste aus {quelle,datum,aussage}), "
    "markt_unauffaellig (Satz oder null), stellschraube (Name oder null), "
    "neuer_wert (Zahl oder null), beleg (String), learning (Satz oder leer). "
    "Keine Markdown-Formatierung, nur das JSON."
)


# ----------------------------------------------------------------- Urteil


@dataclass
class Verdict:
    ausgang: str
    erklaerung: str = ""
    mechanismus: str | None = None
    recherche: list[dict[str, Any]] = field(default_factory=list)
    markt_unauffaellig: str = ""
    stellschraube: str | None = None
    neuer_wert: float | None = None
    beleg: str = ""
    learning: str = ""
    #: Grund, falls ein teurer Ausgang auf 'einzelfall' zurückgestuft wurde.
    herabgestuft: str = ""
    #: True = ein einzelner Fall genügte, weil ein Mechanismus ihn trug.
    einzelfall_ausnahme: bool = False

    def as_line(self) -> str:
        text = f"• **{self.ausgang}** — {self.erklaerung.strip()}"
        if self.herabgestuft:
            text += f" (herabgestuft: {self.herabgestuft})"
        if self.ausgang == PARAMETER and self.stellschraube:
            text += f" [{self.stellschraube} → {self.neuer_wert}]"
        if self.einzelfall_ausnahme:
            text += " [Mechanismus-Ausnahme]"
        return text

    def as_dict(self) -> dict[str, Any]:
        return {
            "urteil": self.ausgang, "erklaerung": self.erklaerung,
            "mechanismus": self.mechanismus, "recherche": self.recherche,
            "markt_unauffaellig": self.markt_unauffaellig,
            "stellschraube": self.stellschraube, "neuer_wert": self.neuer_wert,
            "beleg": self.beleg, "learning": self.learning,
            "herabgestuft": self.herabgestuft,
            "einzelfall_ausnahme": self.einzelfall_ausnahme,
        }


def _strip_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    return text.strip()


def parse_verdict(raw: str) -> Verdict:
    """Antwort streng lesen. Jede Unklarheit endet als 'einzelfall'."""
    try:
        data = json.loads(_strip_fence(raw))
    except (ValueError, TypeError):
        return Verdict(EINZELFALL, herabgestuft="Antwort war kein gueltiges JSON")
    if not isinstance(data, dict):
        return Verdict(EINZELFALL, herabgestuft="Antwort war kein Objekt")

    ausgang = str(data.get("ausgang") or "").strip().lower()
    verdict = Verdict(
        ausgang=ausgang if ausgang in VERDICTS else EINZELFALL,
        erklaerung=str(data.get("erklaerung") or "").strip(),
        mechanismus=(str(data["mechanismus"]).strip()
                     if data.get("mechanismus") else None),
        recherche=[r for r in (data.get("recherche") or []) if isinstance(r, dict)],
        markt_unauffaellig=str(data.get("markt_unauffaellig") or "").strip(),
        stellschraube=(str(data["stellschraube"]).strip()
                       if data.get("stellschraube") else None),
        beleg=str(data.get("beleg") or "").strip(),
        learning=str(data.get("learning") or "").strip(),
    )
    value = data.get("neuer_wert")
    if isinstance(value, int | float) and not isinstance(value, bool):
        verdict.neuer_wert = float(value)
    if ausgang not in VERDICTS:
        verdict.herabgestuft = f"unbekannter Ausgang {ausgang!r}"
    return verdict


def _downgrade(verdict: Verdict, reason: str) -> Verdict:
    verdict.ausgang = EINZELFALL
    verdict.herabgestuft = reason
    verdict.einzelfall_ausnahme = False
    return verdict


def enforce(verdict: Verdict, *, similar_count: int) -> Verdict:
    """Die Riegel vor den teuren Ausgängen. Nichts hiervon steht zur Verhandlung.

    `similar_count` zählt gleichartige Fälle INKLUSIVE des aktuellen — gerechnet
    aus dem Fingerabdruck, nicht aus der Antwort.
    """
    if verdict.ausgang == EINZELFALL:
        return verdict

    if not verdict.recherche and not verdict.markt_unauffaellig:
        return _downgrade(verdict, "keine Recherche belegt (weder Fund noch Begruendung)")

    mechanism = (verdict.mechanismus or "").strip()
    has_mechanism = len(mechanism) >= MIN_MECHANISM_CHARS
    if similar_count < MIN_SIMILAR_CASES and not has_mechanism:
        return _downgrade(
            verdict,
            f"nur {similar_count} Fall dieser Art und kein belegter Mechanismus — "
            "es fehlt ein zweiter Fall",
        )
    verdict.einzelfall_ausnahme = similar_count < MIN_SIMILAR_CASES and has_mechanism

    if verdict.ausgang == PARAMETER:
        if verdict.stellschraube not in DEFAULTS:
            return _downgrade(
                verdict, f"Stellschraube {verdict.stellschraube!r} ist unbekannt"
            )
        if verdict.neuer_wert is None:
            return _downgrade(verdict, "kein neuer Wert genannt")
    return verdict


# ----------------------------------------------------------------- Fallakte


def _as_utc(stamp: datetime) -> datetime:
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def winner_from_feed(feed: list[dict] | None, player_id: str) -> dict[str, Any]:
    """Wer hat den Spieler bekommen, zu welchem Preis? (Activity-Feed t=15)

    Fehlt der Feed oder der Eintrag, bleibt der Block leer — eine erfundene Zahl
    wäre schlimmer als eine fehlende.
    """
    for item in feed or []:
        if item.get("t") != 15:
            continue
        data = item.get("data") or {}
        if str(data.get("pi") or "") != str(player_id):
            continue
        out: dict[str, Any] = {}
        if data.get("byr"):
            out["kaeufer"] = str(data["byr"])
        if data.get("trp") is not None:
            out["preis"] = int(data["trp"])
        if out:
            return out
    return {}


def similar_cases(
    session: Session, surprise: Surprise, *, now: datetime,
    days: int = COMPARISON_WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """Altfälle mit demselben Fingerabdruck im Vergleichsfenster (ohne den aktuellen)."""
    if surprise.art != "gebot":
        return []
    high, low = surprise_thresholds()
    since = _as_utc(now) - timedelta(days=days)
    rows = session.execute(
        select(TransferAction).where(
            TransferAction.action_type == "bid",
            TransferAction.status == "executed",
        ).order_by(TransferAction.id.desc())
    ).scalars().all()

    out: list[dict[str, Any]] = []
    for row in rows:
        if f"action:{row.id}" == surprise.ref:
            continue
        data = dict(row.payload or {})
        exp, outcome = data.get("expectation"), data.get("outcome")
        if not exp or not outcome or exp.get("p") is None:
            continue
        if str(exp.get("kind") or "?") != surprise.kind:
            continue
        stamp = _as_utc(row.created_at)
        if stamp < since:
            continue
        p, result = float(exp.get("p") or 0), str(outcome.get("result") or "")
        richtung = (
            "zu_optimistisch" if result == "lost" and p >= high
            else "zu_vorsichtig" if result == "won" and p <= low
            else ""
        )
        if richtung != surprise.richtung:
            continue
        out.append({
            "ref": f"action:{row.id}", "player_id": row.player_id,
            "p": p, "ergebnis": result, "betrag": row.amount,
            "wann": stamp.date().isoformat(),
        })
    return out


def _relevant_learnings(surprise: Surprise, limit: int = 4) -> list[dict[str, Any]]:
    """Learnings, die zum Fall passen — bevorzugt solche mit einer `Regel:`-Zeile."""
    try:
        from kickbase_agent.knowledge.learnings import load_learnings

        entries = load_learnings()
    except Exception:  # noqa: BLE001 — fehlendes Wissen macht das Dossier nicht ungueltig
        return []
    needles = {surprise.kind.lower(), surprise.art.lower()}
    hits = [
        {"date": e.get("date"), "title": e.get("title"),
         "regel": e.get("rule"), "kurz": e.get("summary")}
        for e in entries
        if any(n and n in (str(e.get("text") or "") + str(e.get("title") or "")).lower()
               for n in needles)
    ]
    hits.sort(key=lambda e: (0 if e.get("regel") else 1, str(e.get("date") or "")),
              reverse=False)
    return hits[:limit]


def build_context(
    session: Session,
    surprise: Surprise,
    *,
    now: datetime,
    feed: list[dict] | None = None,
) -> dict[str, Any]:
    """Die Fallakte. Vollständig aus vorhandenen Daten — und ohne Vorschlag."""
    evidence = dict(surprise.evidence or {})
    expectation = dict(evidence.get("expectation") or {})
    reality = dict(evidence.get("outcome") or {})
    if surprise.art == "gebot":
        reality.update(winner_from_feed(feed, str(evidence.get("player_id") or "")))

    cases = similar_cases(session, surprise, now=now)
    return {
        "ueberraschung": surprise.as_dict(),
        "erwartung": expectation,
        "wirklichkeit": reality,
        "vergleichsfaelle": cases,
        "gleichartige_faelle": len(cases) + 1,
        "vergleichsfenster_tage": COMPARISON_WINDOW_DAYS,
        "wissen": _relevant_learnings(surprise),
        "stellschrauben": load_tuning(),
    }


def format_prompt(context: dict[str, Any]) -> str:
    """Die Fallakte als Text plus genau eine Frage."""
    return (
        "FALL:\n"
        + json.dumps(context, ensure_ascii=False, indent=2, default=str)
        + "\n\nFRAGE: Was erklaert diesen Fall — und folgt daraus eine Aenderung?\n"
        "Beachte: 'parameter'/'code' brauchen entweder gleichartige_faelle >= 2 "
        "oder einen belegten Mechanismus. Die Recherche ist Pflicht "
        "(recherche[] oder markt_unauffaellig).\n"
        "Antworte nur mit dem JSON-Objekt."
    )


# ----------------------------------------------------------------- Aufruf


async def ask_claude(prompt: str, *, timeout: float = 180.0) -> str:
    """Frage an die lokale `claude`-CLI (Abo-Auth) — wie die Morgen-Recherche.

    ANTHROPIC_API_KEY wird entfernt (er wuerde die Abo-Auth ueberschreiben);
    ein gesetzter `claude_code_oauth_token` wird injiziert.
    """
    from kickbase_agent.config import get_settings

    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    token = get_settings().claude_code_oauth_token
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt, "--system-prompt", SYSTEM_PROMPT,
            "--output-format", "text",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
    except FileNotFoundError as exc:
        raise ClaudeCliError(
            "claude-CLI ist im Worker nicht installiert; Urteilsdienst nicht verfügbar"
        ) from exc
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ClaudeCliError("claude-CLI Timeout") from exc

    out, err = out_b.decode().strip(), err_b.decode().strip()
    combined = f"{out}\n{err}".lower()
    if "authenticate" in combined or "oauth" in combined or "logged in" in combined:
        raise ClaudeAuthError(err or out or "nicht angemeldet")
    if proc.returncode != 0 or not out:
        raise ClaudeCliError(err or f"exit {proc.returncode}")
    return out


def _record_on_audit_row(session: Session, surprise: Surprise, verdict: Verdict,
                         now: datetime) -> None:
    """Urteil am Audit-Eintrag vermerken (Muster: `payload["outcome"]`).

    Das ist zugleich die Dublettensperre: Ein beurteilter Fall wird nicht wieder
    vorgelegt. JSON-Spalte NIE in-place mutieren — neu zuweisen (Lehre 25.08.).
    """
    if not surprise.ref.startswith("action:"):
        return
    row = session.get(TransferAction, int(surprise.ref.split(":", 1)[1]))
    if row is None:
        return
    data = dict(row.payload or {})
    data.pop("surprise_pending", None)
    data["surprise"] = {
        **verdict.as_dict(),
        "fingerprint": surprise.fingerprint,
        "at": _as_utc(now).isoformat(),
    }
    row.payload = data
    session.commit()


async def judge_surprise(
    session: Session,
    surprise: Surprise,
    *,
    now: datetime,
    feed: list[dict] | None = None,
    ask=None,
) -> Verdict:
    """Fall zusammentragen, vorlegen, Antwort erzwingen — und protokollieren.

    Wirft `ClaudeCliError`/`ClaudeAuthError` weiter, wenn die CLI nicht erreichbar
    ist: Es wird kein Urteil am Audit-Eintrag vermerkt. Der aufrufende Job hält
    offene Aktionsfälle für einen neuen Versuch fest. Geraten wird nie.
    """
    context = build_context(session, surprise, now=now, feed=feed)
    raw = await (ask or ask_claude)(format_prompt(context))
    verdict = enforce(parse_verdict(raw), similar_count=int(context["gleichartige_faelle"]))
    log.info(
        "URTEIL %s -> %s (%s gleichartige Faelle)",
        surprise.ref, verdict.ausgang, context["gleichartige_faelle"],
    )
    _record_on_audit_row(session, surprise, verdict, now)
    return verdict
