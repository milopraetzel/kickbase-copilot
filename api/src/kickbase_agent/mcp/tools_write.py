"""Write-Tools — AUSSCHLIESSLICH über den ActionExecutor (eiserne Regel 1).

Der MCP setzt KEINE globalen Flags und ruft NIE den Client direkt für Writes:
- `confirm=False` → `dry_run=True` am Executor (Vormerkung `skipped_dry_run`, Audit-Zeile).
- `confirm=True`  → `dry_run=False`: live NUR, wenn die Umgebung geärmt ist
  (AGENT_DRY_RUN=0 + AGENT_ALLOW_LIVE=1 bzw. Test-Liga) — sonst meldet der
  Executor ehrlich `skipped_dry_run`/`refused`, nie still „executed".
- Ab 5 M € (Tier-1-Spieler liegen per Marktwert immer darüber) und bei jedem
  Sofortverkauf ist für live zusätzlich `emil_go` (Emils Wortlaut + Zeit) Pflicht;
  ohne → `refused`, der Executor wird gar nicht erst gerufen.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from sqlalchemy import select

from kickbase_agent.agent.executor import ActionExecutor, PlannedAction
from kickbase_agent.cli.learn import remote_target
from kickbase_agent.config import get_settings
from kickbase_agent.db.models import TransferAction
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.mcp._util import (
    ToolError,
    audit_row,
    fmt_money,
    money_fields,
    settings_flags,
    tool_guard,
)
from kickbase_agent.mcp.tools_read import _ctx
from kickbase_agent.worker.bonus_job import run_bonus

#: Ab diesem Gebot (und bei Tier-1) bleibt die Freigabe IMMER bei Emil (Strategie/Spec).
EMIL_GO_THRESHOLD_EUR = 5_000_000
RULE_BID, RULE_LIST, RULE_SELL = "mcp-bid", "mcp-list", "mcp-sell"
RULE_DELIST = "mcp-delist"


def _remote() -> tuple[str, tuple[str, str] | None] | None:
    """Zwei-DB-Fix (24.08.): Ziel-API auf der Box, wenn KBA_REMOTE_API_URL gesetzt ist.

    Der lokale MCP führt Live-Writes dann NICHT mehr selbst aus (lokale DB = falsches
    Audit, Planner-Dedupe blind), sondern legt ein manuelles VORHABEN über die Box-API
    an — ausgeführt vom einzigen Kickbase-Schreiber, dem Box-Worker (Tick */5), Audit
    in der Box-DB. KBA_REMOTE_API_AUTH="user:pass" (Traefik-Basic-Auth)."""
    return remote_target()


async def _remote_intent(
    kind: str,
    player_id: str,
    amount: int,
    rationale: str,
    *,
    emil_go: str = "",
    listing_type: str = "kickbase",
    player_name: str = "",
    delay_s: int = 0,
    squad_need: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    remote = _remote()
    assert remote is not None
    base, auth = remote
    payload = {
        "kind": kind,
        "player_id": str(player_id),
        "amount": int(amount),
        "rationale": rationale,
        "player_name": player_name,
        "listing_type": listing_type,
        "delay_s": max(0, int(delay_s)),
        "emil_go": emil_go,
        # Kader-Bedarf (26.08.): muss mit auf die Box, sonst gilt dort wieder
        # die Standard-Kappe (110 % MW) und ein 120–130-%-Gebot endet refused.
        "squad_need": bool(squad_need),
        "via": "mcp",
    }
    headers = {"X-Requested-With": "kba"}
    # Schreib-Token (Codex-Folge-Review 24.08., P1): mitsenden, wenn lokal
    # konfiguriert — die Box verlangt ihn, sobald IHR Setting gesetzt ist.
    token = (os.environ.get("KBA_API_WRITE_TOKEN")
             or get_settings().kba_api_write_token or "").strip()
    if token:
        headers["X-KBA-Write-Token"] = token  # CSRF-Schutz der Box-API
    # Die Box berechnet für neue Gebote einen frischen Mannschaftsplan (bis 150 s).
    async with httpx.AsyncClient(auth=auth, timeout=180, headers=headers) as http:
        resp = await http.post(f"{base}/intents", json=payload)
    if resp.status_code == 409:
        detail = (resp.json() or {}).get("detail", {})
        if not isinstance(detail, dict) or not isinstance(detail.get("intent"), dict):
            return {
                "status": "refused",
                "player_id": str(player_id),
                "reason": detail.get("error", "Mannschaftsprüfung lehnt den Kauf ab.")
                if isinstance(detail, dict) else str(detail),
                "purchase_plan": detail if isinstance(detail, dict) else None,
                "remote": True,
                **extra,
            }
        return {
            "status": "duplicate_intent",
            "player_id": str(player_id),
            "message": "Offenes Vorhaben existiert bereits (Dedupe) — nichts angelegt.",
            "intent": detail.get("intent") if isinstance(detail, dict) else None,
            "remote": True,
            **extra,
        }
    resp.raise_for_status()
    intent = (resp.json() or {}).get("intent")
    return {
        "status": "intent_planned",
        "player_id": str(player_id),
        "message": (
            "Vorhaben auf der Box angelegt — Ausführung durch den Intent-Executor "
            "binnen ≤5 min (bzw. nach delay_s), Audit in der Box-DB (Zwei-DB-Fix)."
        ),
        "intent": intent,
        "remote": True,
        **extra,
    }


class _CollectChannel:
    """Null-Channel für run_bonus: sammelt die Meldungen statt nach Discord zu posten."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_info(self, text: str) -> None:
        self.messages.append(text)

    async def send(self, text: str) -> None:
        self.messages.append(text)


def _latest_action(action_type: str, player_id: str | None = None) -> dict[str, Any] | None:
    stmt = select(TransferAction).where(TransferAction.action_type == action_type)
    if player_id is not None:
        stmt = stmt.where(TransferAction.player_id == str(player_id))
    stmt = stmt.order_by(TransferAction.created_at.desc(), TransferAction.id.desc()).limit(1)
    with SessionLocal() as session:
        row = session.execute(stmt).scalars().first()
        return None if row is None else audit_row(row)


def _refused_go(kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": "refused",
        "reason": f"{kind} — Emils GO (Wortlaut + Zeit) ist für live Pflicht.",
        "hint": (
            'emil_go="Emil 23.08. 10:42: mach den Trade" mitgeben — '
            "oder confirm=False für die Dry-Run-Vorschau."
        ),
        "executor_called": False,
        **extra,
    }


def _with_go(rationale: str, emil_go: str) -> str:
    text = rationale.strip()
    return f"{text} | Emil-GO: {emil_go.strip()}" if emil_go.strip() else text


async def _execute(action: PlannedAction, *, confirm: bool, **extra: Any) -> dict[str, Any]:
    """Die EINZIGE Schreibstelle: ActionExecutor.execute(…, dry_run=not confirm)."""
    client, league_id, user_id = await _ctx()
    action.league_id = league_id
    with SessionLocal() as session:
        result = await ActionExecutor(
            client, session, user_id=user_id
        ).execute(action, dry_run=not confirm)
        row = session.get(TransferAction, result.action_id) if result.action_id else None
        audit = audit_row(row) if row is not None else None
    raw = result.raw_response if isinstance(result.raw_response, dict) else None
    return {
        "status": result.status,
        "action_id": result.action_id,
        "message": result.message,
        "live_requested": confirm,
        "executor_called": True,
        "flags": settings_flags(),
        "audit": audit,
        "raw_response": raw,
        **extra,
    }


# ------------------------------------------------------------------ collect_bonus


@tool_guard
async def collect_bonus() -> dict[str, Any]:
    """Tägliche Prämie einsammeln — via worker.bonus_job.run_bonus (Executor misst das
    Konto-Delta: executed = wirklich gutgeschrieben, noop = 0 €/schon per App kassiert).
    Scharf nur mit AGENT_AUTO_BONUS=1 und AGENT_DRY_RUN=0, sonst ehrlich skipped_dry_run."""
    # Zwei-DB-Fix gilt auch hier (Codex-Folge-Review 24.08., P1): im Remote-Modus
    # schreibt der Mac NIE selbst — die Box sammelt die Prämie in ihren Slots
    # (08:00/13:03/18:03); ein lokaler Live-Call wäre ein zweiter Schreiber mit
    # für Box-Planner/-Audit unsichtbarer Buchung.
    if _remote() is not None:
        return {
            "status": "refused_remote",
            "reason": (
                "KBA_REMOTE_API_URL ist gesetzt — die Prämie sammelt der Box-Worker "
                "selbst (Slots 08:00/13:03/18:03); lokal kein Live-Write "
                "(Ein-Schreiber-Prinzip)."
            ),
            "hint": "Box-Audit prüfen: audit_log bzw. Zentrale → Protokoll.",
        }
    channel = _CollectChannel()
    status = await run_bonus(channel, quiet_duplicate=False, warn_on_noop=True, primary=True)
    return {
        "status": status,
        "messages": channel.messages,
        "audit": _latest_action("bonus"),
        "flags": settings_flags(),
    }


# ------------------------------------------------------------------ place_bid


@tool_guard
async def place_bid(
    player_id: str,
    amount_eur: int,
    rationale: str,
    confirm: bool = False,
    emil_go: str = "",
    squad_need: bool = False,
) -> dict[str, Any]:
    """Gebot platzieren (rule_id mcp-bid) — NUR über den ActionExecutor. confirm=False →
    Dry-Run-Vormerkung (skipped_dry_run, Audit-Zeile); confirm=True → live, sofern die Umgebung
    geärmt ist (AGENT_DRY_RUN=0 + AGENT_ALLOW_LIVE=1), sonst ehrlich refused/skipped_dry_run.
    Ab 5 M € (Tier-1 immer) ist für live `emil_go` (Emils Wortlaut + Zeit) Pflicht.
    Manager-Listings (Verkäufer im Markt): Kickbase lehnt Gebote unter dem aktuellen MW mit
    err 5080 UnderpayNotAllowed ab — der Executor-Preflight verweigert sie vorab; Anker ist
    MW + 13.337. Nur Kickbase-Listings erlauben Underpay.
    `squad_need=True` markiert ein Gebot als KADER-BEDARF (ohne diesen Spieler steht die
    Formation nicht): dann gilt die Overpay-Kappe 135 % MW statt 110 % — der Weg für die
    Regel „für wirklich gebrauchte Starter >= 120 % MW bieten" (Ljubičić 26.08.). Nur mit
    echter Begründung setzen; ohne Marker bleibt es bei 110 %."""
    pid = str(player_id).strip()
    amount = int(amount_eur)
    if not pid:
        raise ToolError("player_id fehlt.", hint="Spieler-ID aus market()/squad() nehmen.")
    if amount <= 0:
        raise ToolError("amount_eur muss > 0 sein.", hint="Gebot in Euro, z. B. 5_120_000.")
    if not rationale.strip():
        raise ToolError("rationale ist Pflicht (Audit-Begründung).", hint="Warum dieses Gebot?")
    needs_go = amount >= EMIL_GO_THRESHOLD_EUR
    if confirm and needs_go and not emil_go.strip():
        return _refused_go(
            f"Gebot {fmt_money(amount)} ≥ {fmt_money(EMIL_GO_THRESHOLD_EUR)}",
            player_id=pid,
            **money_fields("amount", amount),
        )
    if _remote() is not None:
        if not confirm:
            return {
                "status": "preview_only",
                "message": "Remote-Modus: confirm=False legt KEIN Vorhaben an (keine Audit-Zeile).",
                "player_id": pid,
                **money_fields("amount", amount),
                "emil_go_required": needs_go,
                "remote": True,
            }
        return await _remote_intent(
            "bid", pid, amount, _with_go(rationale, emil_go), emil_go=emil_go,
            squad_need=squad_need,
            **money_fields("amount", amount), emil_go_required=needs_go,
        )
    action = PlannedAction(
        action_type="bid",
        league_id="",
        player_id=pid,
        amount=amount,
        rule_id=RULE_BID,
        rationale=_with_go(rationale, emil_go),
        squad_need=bool(squad_need),
        emil_go=emil_go.strip(),
    )
    return await _execute(
        action,
        confirm=confirm,
        player_id=pid,
        **money_fields("amount", amount),
        emil_go_required=needs_go,
    )


# ------------------------------------------------------------------ list_player


@tool_guard
async def list_player(
    player_id: str, price_eur: int, rationale: str, confirm: bool = False,
    emil_go: str = "",
) -> dict[str, Any]:
    """Eigenen Spieler zum Preis listen (rule_id mcp-list, ~103 % MW für Computer-Angebote) —
    NUR über den ActionExecutor. confirm=False → Dry-Run-Vormerkung; confirm=True → live,
    sofern geärmt (AGENT_DRY_RUN=0 + AGENT_ALLOW_LIVE=1), sonst ehrlich refused/skipped_dry_run.
    Ab 5 M € ist für live `emil_go` (Emils Wortlaut + Zeit) Pflicht — dieselbe Schwelle wie
    bei place_bid. Ohne das Feld lehnte die Box-API (POST /api/intents) jede Listung ab 5 M
    mit HTTP 400 ab, obwohl das Werkzeug den Wortlaut gar nicht übergeben konnte: eine
    teure Listung war über MCP schlicht unmöglich (gefunden 16.09.2026 beim Versuch, einen
    unter Marktwert stehenden Kaderspieler vor einem Zufallsverkauf zu schützen)."""
    pid = str(player_id).strip()
    price = int(price_eur)
    if not pid:
        raise ToolError("player_id fehlt.", hint="Spieler-ID aus squad() nehmen.")
    if price <= 0:
        raise ToolError("price_eur muss > 0 sein.", hint="Listenpreis in Euro.")
    if not rationale.strip():
        raise ToolError("rationale ist Pflicht (Audit-Begründung).", hint="Warum listen?")
    needs_go = price >= EMIL_GO_THRESHOLD_EUR
    if confirm and needs_go and not emil_go.strip():
        return _refused_go(
            f"Listung {fmt_money(price)} ≥ {fmt_money(EMIL_GO_THRESHOLD_EUR)}",
            player_id=pid,
            **money_fields("price", price),
        )
    if _remote() is not None:
        if not confirm:
            return {
                "status": "preview_only",
                "message": "Remote-Modus: confirm=False legt KEIN Vorhaben an (keine Audit-Zeile).",
                "player_id": pid,
                **money_fields("price", price),
                "emil_go_required": needs_go,
                "remote": True,
            }
        return await _remote_intent(
            "list", pid, price, _with_go(rationale, emil_go), emil_go=emil_go,
            **money_fields("price", price), emil_go_required=needs_go,
        )
    action = PlannedAction(
        action_type="list",
        league_id="",
        player_id=pid,
        amount=price,
        rule_id=RULE_LIST,
        rationale=_with_go(rationale, emil_go),
    )
    return await _execute(
        action, confirm=confirm, player_id=pid,
        **money_fields("price", price), emil_go_required=needs_go,
    )


# ------------------------------------------------------------------ sell_player


@tool_guard
async def sell_player(
    player_id: str, rationale: str, confirm: bool = False, emil_go: str = ""
) -> dict[str, Any]:
    """Sofortverkauf an Kickbase (rule_id mcp-sell; nur Langzeit-Verletzte — sonst listen) —
    NUR über den ActionExecutor. Für live IMMER `emil_go` Pflicht; confirm=False → Dry-Run-
    Vormerkung. Betrag = aktueller Marktwert aus dem Kader (Preflight-Drift-Check)."""
    pid = str(player_id).strip()
    if not pid:
        raise ToolError("player_id fehlt.", hint="Spieler-ID aus squad() nehmen.")
    if not rationale.strip():
        raise ToolError("rationale ist Pflicht (Audit-Begründung).", hint="Warum verkaufen?")
    if confirm and not emil_go.strip():
        return _refused_go("Sofortverkauf", player_id=pid)
    client, league_id, _user_id = await _ctx()
    member = next(
        (sp for sp in await client.get_squad(league_id) if str(sp.player.id) == pid), None
    )
    if member is None:
        raise ToolError(
            f"Spieler {pid} ist nicht im eigenen Kader.", hint="squad() zeigt die IDs."
        )
    value = int(member.player.market_value)
    if _remote() is not None:
        if not confirm:
            return {
                "status": "preview_only",
                "message": "Remote-Modus: confirm=False legt KEIN Vorhaben an (keine Audit-Zeile).",
                "player_id": pid,
                **money_fields("market_value", value),
                "remote": True,
            }
        return await _remote_intent(
            "sell", pid, value, _with_go(rationale, emil_go), emil_go=emil_go,
            player_name=f"{member.player.first_name} {member.player.last_name}".strip(),
            **money_fields("market_value", value),
        )
    action = PlannedAction(
        action_type="sell",
        league_id=league_id,
        player_id=pid,
        amount=value,
        rule_id=RULE_SELL,
        rationale=_with_go(rationale, emil_go),
    )
    return await _execute(
        action,
        confirm=confirm,
        player_id=pid,
        player_name=f"{member.player.first_name} {member.player.last_name}".strip(),
        **money_fields("market_value", value),
    )


# ------------------------------------------------------------------ delist_player


@tool_guard
async def delist_player(
    player_id: str, rationale: str, confirm: bool = False, emil_go: str = ""
) -> dict[str, Any]:
    """Eigenes Listing zurückziehen (Delist) über ein Vorhaben auf der Box — kein direkter
    Kickbase-Write. Nur auf Emils namentlichen Auftrag für genau diesen Spieler
    (CLAUDE.md §5.12). confirm=False → nur Vorschau bzw. lokale Dry-Run-Vormerkung (rule_id
    mcp-delist), kein Netzaufruf an die Box. Kein emil_go-Zwang: ein Delist baut Risiko ab
    (wie cancel_bid); der Wortlaut wird, wenn übergeben, ins Audit mitgeführt. Der Spieler
    muss im eigenen Kader stehen; ob er gelistet ist, entscheidet Kickbase beim Write."""
    pid = str(player_id).strip()
    if not pid:
        raise ToolError("player_id fehlt.", hint="Spieler-ID aus squad() nehmen.")
    if not rationale.strip():
        raise ToolError("rationale ist Pflicht (Audit-Begründung).", hint="Warum zurückziehen?")
    client, league_id, _user_id = await _ctx()
    member = next(
        (sp for sp in await client.get_squad(league_id) if str(sp.player.id) == pid), None
    )
    if member is None:
        raise ToolError(
            f"Spieler {pid} ist nicht im eigenen Kader.", hint="squad() zeigt die IDs."
        )
    player_name = f"{member.player.first_name} {member.player.last_name}".strip()
    if _remote() is not None:
        if not confirm:
            return {
                "status": "preview_only",
                "message": "Remote-Modus: confirm=False legt KEIN Vorhaben an (keine Audit-Zeile).",
                "player_id": pid,
                "player_name": player_name,
                "remote": True,
            }
        return await _remote_intent(
            "delist", pid, 0, _with_go(rationale, emil_go), emil_go=emil_go,
            player_name=player_name,
        )
    action = PlannedAction(
        action_type="delist",
        league_id=league_id,
        player_id=pid,
        amount=None,
        rule_id=RULE_DELIST,
        rationale=_with_go(rationale, emil_go),
        emil_go=emil_go.strip(),
    )
    return await _execute(action, confirm=confirm, player_id=pid, player_name=player_name)
