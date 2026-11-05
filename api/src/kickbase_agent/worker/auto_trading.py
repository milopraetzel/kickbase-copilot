"""Mini-Auto-Trading-Executor (Stufe 2) — bewusst DORMANT ausgeliefert.

Täglicher Lauf um 22:30 (nach dem 22:00-MW-Update und dem 22:15-Briefing):
baut den Plan FRISCH (Frische-Gate — es gibt keinen Weg, alte Vorschläge
auszuführen) und setzt die Trading-Empfehlungen in echte Writes um — aber nur,
wenn AGENT_AUTO_TRADING=1 den Pfad ärmt. Default ist 0: dann fährt der Job im
**Schatten-Modus** — KEIN Write, nur der Discord-Report, was er getan HÄTTE,
inklusive aller Gate-Ergebnisse. So sammelt Stufe 2 Betriebsbeweise, bevor
echtes Geld fließt.

Freischalt-Kriterien der Strategie (docs/STRATEGIE-SAISON-26-27.md, „Harte
Kriterien für Autopilot Stufe 2"; 1.–3. wörtlich):

    Stufe 2 (Trading autonom ≤ 1,5 M) wird erst gezündet, wenn ALLE erfüllt sind:
    1. **14 Tage fehlerfreie API-Writes** (keine failed-Einträge im Audit-Log).
    2. **Realisierter Trading-Gewinn > +500 k €** aus freigegebenen Empfehlungen.
    3. **Anpfiff-Wächter hat ≥ 1 Spieltag fehlerfrei überwacht** (T-120/T-60/T-35
       gefeuert, Konto beim Anpfiff ≥ 0).
    4. Emils explizites GO.

Punkt 4 meint das Scharfschalten DIESES Jobs (AGENT_AUTO_TRADING=1 in der .env
der Box — eiserne Regel 2), nicht einzelne Trades. Der Zusatz „Ab 5 M € und
Tier-1 bleibt IMMER Emil", der im Strategie-Dokument noch an Punkt 4 hängt, ist
seit Emils Vollmandat (25.08.26) überholt: Es gibt keine Betrags-Freigabe mehr
(Tabellenzeile „Vollmandat" im selben Dokument).

Sicherheits-Gates (hart im Code — ZUSÄTZLICH zu Kill-Switch, Live-Interlock,
Idempotenz und Preflight des ActionExecutors):
- Betrag je Spieler ≤ AGENT_TRADING_MAX_PER_PLAYER (Default 1,5 M).
- requires_approval-Vorschläge (Betrag > approval_threshold, Default 5 M) fasst
  dieser Executor nicht an: Mit der Default-Kappe von 1,5 M je Spieler wären sie
  ohnehin zu groß, und das Gate hält auch, wenn jemand die Kappe hochsetzt.
  Entschieden werden sie im Vorhaben-Pfad (worker/intents.py, Veto-Fenster ≥ 2 h)
  — seit Emils Vollmandat (25.08.26) braucht dafür kein Betrag eine Freigabe.
- Käufe nur für Spieler außerhalb des eigenen Kaders (Core-Schutz, siehe
  Kommentar in gate_buys — die Zonen-API kennt nur Kaderspieler).
- Verkäufe nur als Listing ("list", Computer-Angebot ~103 %) und nur für
  Spieler der Trading-Zone (trading/zones.py via load_trading_holdings) —
  Sofortverkäufe ("sell") und Core-Spieler laufen nie über diesen Executor
  (über sie entscheidet der Vorhaben-Pfad).
- Tages-Caps aus den Guardrails (daily_buy_cap/daily_sell_cap), gezählt über
  heutige executed-Aktionen mit rule_id-Präfix 'auto-trading' im Audit-Log.
- Incident-Stopp: existiert HEUTE irgendeine Aktion mit Status unknown/failed,
  pausiert sich der Auto-Trader komplett selbst (erst Reconcile, dann weiter).
- 200er-Sättigung: meldet der Plan trade_count_saturated, wird sofort blockiert.
- Scharfe Ausführung bricht beim ERSTEN nicht-executed-Ergebnis ab — kein
  blindes Weiterfeuern in einen unklaren Zustand.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from kickbase_agent.agent.executor import ActionExecutor
from kickbase_agent.cli.plan import Plan, build_plan
from kickbase_agent.config import Settings, get_settings
from kickbase_agent.db.models import TransferAction
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS, Guardrails
from kickbase_agent.decision.models import Proposal
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.notify.report_store import save_report
from kickbase_agent.trading.orchestrator import load_trading_holdings

#: rule_id-Präfix des Auto-Trading-Pfads: der Executor-Interlock
#: (AGENT_AUTO_TRADING) ärmt GENAU dieses Präfix, und das Tages-Cap zählt
#: heutige Ausführungen im Audit-Log darüber.
AUTO_TRADING_PREFIX = "auto-trading"
RULE_AUTO_BUY = "auto-trading-buy"
RULE_AUTO_LIST = "auto-trading-list"


def _fmt_m(value: int | None) -> str:
    return f"{(value or 0) / 1_000_000:.2f}M €"


def _name(p: Proposal) -> str:
    return p.player_name or p.action.player_id or "?"


def _as_utc(dt: datetime) -> datetime:
    """SQLite liefert naive Zeitstempel zurück — als UTC interpretieren."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


@dataclass(frozen=True)
class TodayCounts:
    """Heute bereits executed gelaufene Auto-Trading-Aktionen (aus dem Audit-Log).

    `lists` (31.08., L-077 vollendet): Listings zählen in der Intent-Pipeline
    gegen ihr EIGENES Kontingent (daily_list_cap), nicht mehr gegen den
    Verkaufs-Cap — konsistent zur einzigen Schreibstelle (ActionExecutor).
    Default 0 hält Alt-Aufrufer (Auto-Trading) unverändert.
    """

    buys: int = 0
    sells: int = 0
    lists: int = 0


@dataclass(frozen=True)
class GateVerdict:
    """Ein Kandidat mit Gate-Ergebnis — Grundlage für Auswahl UND Schatten-Report."""

    proposal: Proposal
    kind: str  # "Kauf" | "Listing"
    passed: bool
    reason: str  # "ok" oder das Gate, das gefiltert hat


def load_today_state(session: Session, now: datetime) -> tuple[TodayCounts, bool]:
    """Tages-Zähler + Incident-Blick aus transfer_actions (UTC-Tag von `now`).

    - Zähler: heutige Aktionen mit rule_id-Präfix 'auto-trading' und Status
      'executed' (Käufe = bid, Verkäufe = list/sell) — Grundlage der Tages-Caps.
      Der Tag ist der UTC-Tag, derselbe, mit dem der Executor seine
      Idempotenz-Schlüssel bildet.
    - Incident: existiert HEUTE IRGENDEINE Aktion (egal welche rule_id) mit
      Status 'unknown' oder 'failed', ist der Schreibpfad nicht vertrauenswürdig
      → Komplett-Stopp. Die Datumsprüfung läuft in Python, weil SQLite naive und
      aware Zeitstempel gemischt zurückgeben kann.
    """
    today = now.date()
    buys = sells = 0
    executed = session.query(TransferAction).filter(
        TransferAction.rule_id.like(f"{AUTO_TRADING_PREFIX}%"),
        TransferAction.status == "executed",
    )
    for row in executed:
        if _as_utc(row.created_at).date() != today:
            continue
        if row.action_type == "bid":
            buys += 1
        elif row.action_type in ("list", "sell"):
            sells += 1
    troubled = session.query(TransferAction).filter(
        TransferAction.status.in_(("unknown", "failed"))
    )
    has_incident = any(_as_utc(row.created_at).date() == today for row in troubled)
    return TodayCounts(buys=buys, sells=sells), has_incident


# ------------------------------------------------------------------ Gates


_INCIDENT_REASON = "Incident-Stopp: heute existiert eine unknown/failed-Aktion im Audit-Log"
_APPROVAL_REASON = "Groß-Vorhaben (> 5 M) — für den Stufe-2-Executor gesperrt"


def gate_buys(
    plan: Plan,
    settings: Settings,
    today_counts: TodayCounts,
    has_incident: bool,
    guardrails: Guardrails = DEFAULT_GUARDRAILS,
) -> list[GateVerdict]:
    """Kauf-Gates je plan.buys — reine Funktion, jedes Urteil mit Begründung."""
    if has_incident:
        return [GateVerdict(p, "Kauf", False, _INCIDENT_REASON) for p in plan.buys]
    max_amount = settings.agent_trading_max_per_player
    slots = max(0, guardrails.daily_buy_cap - today_counts.buys)
    squad_pids = {sp.player.id for sp in plan.squad}
    out: list[GateVerdict] = []
    taken = 0
    for p in plan.buys:
        reason: str | None = None
        if p.action.action_type != "bid":
            reason = f"kein Kaufgebot (action_type '{p.action.action_type}')"
        elif p.requires_approval:
            reason = _APPROVAL_REASON
        elif p.action.amount is None or p.action.amount > max_amount:
            reason = f"Gebot über dem Autonomie-Limit ({_fmt_m(max_amount)} je Spieler)"
        elif p.action.player_id in squad_pids:
            # Core-Schutz, konservative Näherung: Die Zonen-API (trading/
            # zones.py: classify_squad über load_trading_holdings) teilt nur den
            # EIGENEN Kader in Core/Trading — für Markt-Kandidaten existiert
            # keine Zonen-Zuordnung. Autonome Käufe sind deshalb nur für Spieler
            # erlaubt, die NICHT im aktuellen Kader stehen (build_plan filtert
            # eigene gelistete Spieler ohnehin aus dem Kauf-Pfad; dieses Gate
            # ist die zweite Verteidigungslinie gegen Regressionen).
            # TODO(Stufe 2+): sobald die API eine echte Zonen-Zuordnung für
            # ZUGÄNGE hergibt (z. B. "dieser Kauf belegt einen Trading-Slot"),
            # hier gegen sie prüfen — nichts erfinden, heute existiert sie nicht.
            reason = "Spieler steht im eigenen Kader (Core-Schutz)"
        elif taken >= slots:
            reason = (
                f"Tages-Cap Käufe erreicht ({today_counts.buys} heute + {taken} "
                f"geplant ≥ {guardrails.daily_buy_cap})"
            )
        if reason is None:
            out.append(GateVerdict(p, "Kauf", True, "ok"))
            taken += 1
        else:
            out.append(GateVerdict(p, "Kauf", False, reason))
    return out


def gate_sells(
    plan: Plan,
    settings: Settings,
    today_counts: TodayCounts,
    has_incident: bool,
    trading_ids: set[str],
    guardrails: Guardrails = DEFAULT_GUARDRAILS,
) -> list[GateVerdict]:
    """Verkaufs-Gates je plan.sells — nur Listings, nie Sofortverkauf.

    Fable 5.1 (02.09., Emil): „Core" ist kein Schutzschild mehr. Wenn die
    Entscheidungslogik einen ungeschützten Kaderspieler wegen fallendem MW listet,
    darf der Auto-Trading-Pfad dieses Listing ausführen — unabhängig davon, ob der
    Spieler ursprünglich als Trading-Kauf markiert war. Listings kosten keinen
    Spieler; der finale Abgang bleibt in Accept-/Sell-Pfaden an Deckung gekoppelt.
    """
    if has_incident:
        return [GateVerdict(p, "Listing", False, _INCIDENT_REASON) for p in plan.sells]
    max_amount = settings.agent_trading_max_per_player
    slots = max(0, guardrails.daily_sell_cap - today_counts.sells)
    out: list[GateVerdict] = []
    taken = 0
    for p in plan.sells:
        reason: str | None = None
        if p.action.action_type == "sell":
            reason = "Sofortverkauf ('sell') — Stufe 2 listet nur"
        elif p.action.action_type != "list":
            reason = f"kein Listing (action_type '{p.action.action_type}')"
        elif p.requires_approval and p.action.action_type != "list":
            reason = _APPROVAL_REASON
        elif (
            p.action.action_type != "list"
            and (p.action.amount is None or p.action.amount > max_amount)
        ):
            reason = f"Marktwert über dem Autonomie-Limit ({_fmt_m(max_amount)} je Spieler)"
        elif taken >= slots:
            reason = (
                f"Tages-Cap Verkäufe erreicht ({today_counts.sells} heute + {taken} "
                f"geplant ≥ {guardrails.daily_sell_cap})"
            )
        if reason is None:
            out.append(GateVerdict(p, "Listing", True, "ok"))
            taken += 1
        else:
            out.append(GateVerdict(p, "Listing", False, reason))
    return out


def select_auto_buys(
    plan: Plan,
    settings: Settings,
    today_counts: TodayCounts,
    has_incident: bool,
    guardrails: Guardrails = DEFAULT_GUARDRAILS,
) -> list[Proposal]:
    """Kaufvorschläge, die ALLE Autonomie-Gates bestehen (reine Funktion)."""
    return [
        v.proposal
        for v in gate_buys(plan, settings, today_counts, has_incident, guardrails)
        if v.passed
    ]


def select_auto_sells(
    plan: Plan,
    settings: Settings,
    today_counts: TodayCounts,
    has_incident: bool,
    trading_ids: set[str],
    guardrails: Guardrails = DEFAULT_GUARDRAILS,
) -> list[Proposal]:
    """Listing-Vorschläge, die ALLE Autonomie-Gates bestehen (reine Funktion)."""
    return [
        v.proposal
        for v in gate_sells(plan, settings, today_counts, has_incident, trading_ids, guardrails)
        if v.passed
    ]


# ------------------------------------------------------------------ Reports


def _rule_for(v: GateVerdict) -> str:
    return RULE_AUTO_BUY if v.kind == "Kauf" else RULE_AUTO_LIST


def format_shadow_report(
    verdicts: list[GateVerdict],
    counts: TodayCounts,
    guardrails: Guardrails = DEFAULT_GUARDRAILS,
) -> str | None:
    """Schatten-Report mit allen Gate-Ergebnissen — None, wenn nichts durchkam (leise)."""
    passed = [v for v in verdicts if v.passed]
    if not passed:
        return None
    lines = ["🤖 **Auto-Trading (Schatten)** — hätte ausgeführt:"]
    for v in passed:
        lines.append(
            f"• {v.kind} {_name(v.proposal)}: {_fmt_m(v.proposal.action.amount)} [{_rule_for(v)}]"
        )
    blocked = [v for v in verdicts if not v.passed]
    if blocked:
        lines.append("Gate-gefiltert (kein Stufe-2-Trade):")
        for v in blocked[:8]:
            lines.append(
                f"✗ {v.kind} {_name(v.proposal)} "
                f"({_fmt_m(v.proposal.action.amount)}): {v.reason}"
            )
    lines.append(
        f"Caps heute: Käufe {counts.buys}/{guardrails.daily_buy_cap} · "
        f"Verkäufe {counts.sells}/{guardrails.daily_sell_cap} · Incident: nein"
    )
    lines.append(
        "-# Schatten-Modus (AGENT_AUTO_TRADING=0): KEINE Writes — scharf erst nach "
        "den Stufe-2-Kriterien der Strategie + Emils GO."
    )
    return "\n".join(lines)


def format_live_report(
    results: list[tuple[GateVerdict, str]],
    skipped: list[GateVerdict],
    counts: TodayCounts,
    guardrails: Guardrails = DEFAULT_GUARDRAILS,
) -> str:
    """Abschluss-Report des scharfen Laufs — mit den ECHTEN Executor-Statūs."""
    ok = all(status == "executed" for _, status in results)
    if ok:
        lines = ["🤖 **Auto-Trading (LIVE)** — ausgeführt:"]
    else:
        bad_v, bad_status = next((v, s) for v, s in results if s != "executed")
        lines = [
            f"🚨 **Auto-Trading ABGEBROCHEN** — {bad_v.kind} {_name(bad_v.proposal)} "
            f"endete als '{bad_status}'; Restliste ({len(skipped)}) NICHT ausgeführt. "
            "Bitte Audit-Log prüfen (bei 'unknown': erst Reconcile, kein blinder Retry)."
        ]
    for v, status in results:
        mark = "✓" if status == "executed" else "✗"
        lines.append(
            f"{mark} {v.kind} {_name(v.proposal)}: {_fmt_m(v.proposal.action.amount)} "
            f"[{_rule_for(v)}] → {status}"
        )
    for v in skipped:
        lines.append(f"– {v.kind} {_name(v.proposal)}: übersprungen (Abbruch)")
    lines.append(
        f"Caps heute vor dem Lauf: Käufe {counts.buys}/{guardrails.daily_buy_cap} · "
        f"Verkäufe {counts.sells}/{guardrails.daily_sell_cap}"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ Runner


async def run_auto_trading(channel) -> str:
    """Täglicher Auto-Trading-Lauf — Schatten oder scharf, je nach AGENT_AUTO_TRADING.

    Rückgabe: 'blocked_saturated' | 'blocked_incident' | 'quiet' | 'shadow'
    | 'executed' | 'aborted'.
    """
    settings = get_settings()
    guardrails = DEFAULT_GUARDRAILS
    # Frische-Gate: der Plan entsteht UNMITTELBAR vor der Entscheidung neu
    # (Live-Kader, -Markt, -Budget, -Gebote, 200er-Zählung) — alte Vorschläge
    # können hier nie ausgeführt werden. Je Write prüft zusätzlich der
    # Executor-Preflight frische Marktdaten.
    plan = await build_plan()
    if plan.trade_count_saturated:
        await channel.send_info(
            "🚫 **Auto-Trading blockiert** — 200er-Transferzählung gesättigt "
            f"(≥ {plan.trades_used}/{plan.trade_limit}, Activity-Feed abgeschnitten): "
            "keine autonomen Trades, Notreserve schützen.",
            kind="auto_trading",
        )
        return "blocked_saturated"

    now = datetime.now(UTC)
    with SessionLocal() as session:
        counts, has_incident = load_today_state(session, now)
        trading_ids = load_trading_holdings(session, [sp.player.id for sp in plan.squad])

    if has_incident:
        await channel.send_info(
            "🚨 **Auto-Trading pausiert sich selbst** — im Audit-Log steht HEUTE "
            "mindestens eine Aktion mit Status unknown/failed. Erst den Zustand "
            "gegen Kickbase reconcilen, dann läuft der Auto-Trader wieder.",
            kind="auto_trading",
        )
        return "blocked_incident"

    verdicts = gate_buys(plan, settings, counts, has_incident, guardrails) + gate_sells(
        plan, settings, counts, has_incident, trading_ids, guardrails
    )
    todo = [v for v in verdicts if v.passed]
    if not todo:
        return "quiet"

    if not settings.agent_auto_trading:
        # Schatten-Modus (Default): KEIN Client, KEIN Executor, KEIN Write —
        # nur der Report, was gelaufen WÄRE, mit allen Gate-Ergebnissen.
        # Meldungs-Umbau (Emil, 31.08.): Der dormante Schatten-Report geht nur
        # noch ins Berichte-Archiv — Discord bleibt echten Alarmen (blockiert/
        # Incident) und dem scharfen Pfad vorbehalten.
        msg = format_shadow_report(verdicts, counts, guardrails)
        if msg is not None:
            save_report(
                kind="auto_trading",
                title="Auto-Trading-Schatten (Archiv)",
                body=msg,
                source="worker:auto_trading",
                meta={"delivered": False, "discord": "abbestellt 31.08."},
            )
        return "shadow"

    # --- Scharfer Pfad (AGENT_AUTO_TRADING=1) -------------------------------
    # Jede Aktion läuft durch den ActionExecutor (einzige Schreibstelle):
    # Kill-Switch, Live-Interlock (das Flag ärmt NUR rule_id 'auto-trading*'),
    # Idempotenz und der automatisch verdrahtete Preflight gelten weiter.
    client, auth = await make_kickbase_client()
    results: list[tuple[GateVerdict, str]] = []
    skipped: list[GateVerdict] = []
    with SessionLocal() as session:
        executor = ActionExecutor(client, session, user_id=auth.user_id)
        for i, v in enumerate(todo):
            if i > 0:
                # Rate-Limit-Schonung: Abstand zwischen echten Writes.
                await asyncio.sleep(settings.agent_write_spacing_seconds)
            action = replace(v.proposal.action, rule_id=_rule_for(v))
            result = await executor.execute(action, dry_run=False)
            results.append((v, result.status))
            if result.status != "executed":
                # Beim ERSTEN nicht-executed sofort abbrechen — kein blindes
                # Weiterfeuern in einen unklaren/kaputten Zustand.
                skipped = todo[i + 1 :]
                break

    await channel.send_info(
        format_live_report(results, skipped, counts, guardrails), kind="auto_trading"
    )
    return "executed" if all(s == "executed" for _, s in results) else "aborted"
