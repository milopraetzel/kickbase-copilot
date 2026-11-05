"""Schatten-Decision-Scan (22:20) — was der Automat heute getan HAETTE.

Baut den Tagesplan (build_plan) und postet je Kandidat den Gate-Status
(Stufe-2-autonom vs. Vorhaben-Pfad) nach Discord. Seit 23.08. (Review-Fund P0)
staged dieser Lauf NICHTS mehr: fruehere 'proposed'-Zeilen blockierten das
Intent desselben Spielers als 'duplicate'. Vorhaben legt ausschliesslich der
Intent-Planner (:25) an; die Schatten-Bilanz liest die Evaluation aus den Intents.
"""

from __future__ import annotations

from kickbase_agent.cli.plan import Plan, build_plan
from kickbase_agent.config import get_settings
from kickbase_agent.decision.models import Proposal
from kickbase_agent.notify.discord_channel import DiscordChannel
from kickbase_agent.notify.report_store import save_report


def _m(value: int | None) -> str:
    """Millionen deutsch & kompakt: 6.130.000 → '6,13M', 1.500.000 → '1,5M'."""
    text = f"{(value or 0) / 1_000_000:.2f}".rstrip("0").rstrip(".")
    return f"{text.replace('.', ',')}M"


def _gate(p: Proposal, trading_max: int, saturated: bool) -> tuple[bool, str]:
    """Ehrliche Zonen-Rechnung: ✅ nur wenn ALLE Gates offen sind.

    auto ⇔ amount ≤ AGENT_TRADING_MAX_PER_PLAYER UND kein Groß-Vorhaben
    (> approval_threshold, Default 5 M) UND Trade-Ampel nicht saturiert — sonst
    hätte der Stufe-2-Executor den Vorschlag liegen lassen. Liegen lassen heißt
    seit dem Vollmandat (25.08.26) keine Freigabe: entschieden wird er dann im
    Vorhaben-Pfad (stündlicher Intent-Planner) mit Veto-Fenster.
    """
    amount = p.action.amount or 0
    blockers: list[str] = []
    if saturated:
        blockers.append("Trade-Ampel saturiert")
    if amount > trading_max:
        blockers.append(f"{_m(amount)} > {_m(trading_max)}")
    if p.requires_approval:
        blockers.append("Groß-Vorhaben (> 5 M)")
    head = f"✅ auto: Trading ≤{_m(trading_max)}"
    if blockers:
        return False, f"{head} — NEIN, {' · '.join(blockers)} ⇒ 📋 Vorhaben-Pfad"
    return True, f"{head} — JA, {_m(amount)} ≤ {_m(trading_max)} ⇒ ✅ wäre autonom gelaufen"


def _line(p: Proposal, trading_max: int, saturated: bool) -> tuple[bool, str]:
    auto, gate = _gate(p, trading_max, saturated)
    name = p.player_name or p.action.player_id or "?"
    way = "" if p.action.action_type == "bid" else f" ({p.action.action_type})"
    return auto, f"• {name}{way}: {_m(p.action.amount)} · Score {p.score:.2f} ({gate})"


def format_shadow_report(
    plan: Plan, staged: list[tuple[Proposal, int]], trading_max: int
) -> str:
    """Reine Formatierung: Kandidaten mit Gate-Status + ehrliche Staging-Bilanz."""
    saturated = plan.trade_count_saturated
    lines = ["🤖 **Schatten-Scan** — was der Automat (Phase 2) heute getan HÄTTE"]
    auto_n = intent_n = 0
    for title, proposals in (("KÄUFE", plan.buys), ("VERKÄUFE", plan.sells)):
        if not proposals:
            continue
        lines.append(f"{title} ({len(proposals)}):")
        for p in proposals:
            auto, text = _line(p, trading_max, saturated)
            auto_n += auto
            intent_n += not auto
            lines.append(text)
    total = len(plan.buys) + len(plan.sells)
    lines.append(
        f"Bilanz: {auto_n}× ✅ wäre Stufe-2-autonom · {intent_n}× 📋 über den "
        f"Vorhaben-Pfad — {total} Kandidaten, nichts gestaged."
    )
    lines.append(
        "Schatten: nichts wurde ausgeführt und nichts vorgemerkt — Vorhaben legt der "
        "Intent-Planner (:25) an; dieser Lauf blockiert keine Aktion."
    )
    return "\n".join(lines)


async def run_decision_scan(channel: DiscordChannel) -> str:
    """Täglicher Schatten-Lauf: Plan bauen, Schatten-Report posten — sonst nichts.

    Kein Staging, keine Writes (siehe Modul-Docstring, Review-Fund P0): Der Lauf
    liest nur den Plan und postet je Kandidat den Gate-Status. Ohne Kandidaten:
    'quiet', kein Post.
    """
    plan = await build_plan()
    if not plan.buys and not plan.sells:
        return "quiet"
    # Review-Fund 23.08. (P0): Der Schatten-Lauf hat bisher echte 'proposed'-Zeilen
    # gestaged; die blockierten 5 Minuten spaeter das Intent desselben Spielers als
    # 'duplicate' (Idempotenz-Key ohne rule_id). Ein passiver Schattenmodus darf
    # KEINE blockierende Action-Zeile erzeugen -> nur noch Report. Die
    # Schatten-Bilanz liest die Evaluation aus den Intents (vetoed/expired/failed).
    trading_max = get_settings().agent_trading_max_per_player
    # Meldungs-Umbau (Emil, 31.08.): Der Schatten-Report ist ein Generalproben-
    # Artefakt aus der Zeit VOR den scharfen Vorhaben — die echte Pipeline
    # zeigt sich seit dem 31.08. als Veto-Karten. Der Report wandert nur noch
    # ins Berichte-Archiv (Cockpit), nicht mehr nach Discord.
    save_report(
        kind="schatten",
        title="Schatten-Scan (Archiv)",
        body=format_shadow_report(plan, [], trading_max),
        source="worker:schatten",
        meta={"delivered": False, "discord": "abbestellt 31.08."},
    )
    return "archived"
