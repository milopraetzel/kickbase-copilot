"""Steuerzentrale-API: Vorhaben (Intents) + KI-Protokoll.

Spec: docs/superpowers/specs/2026-08-23-steuerzentrale.md (§2 Datenmodell, §4 API).

- GET  /api/intents?status=planned|all&limit=50
- POST /api/intents/{id}/veto         {"note": str|null}
- POST /api/intents/{id}/adjust       {"amount": int} ODER {"pct": number}
- POST /api/intents/{id}/execute-now
- GET  /api/protocol?limit=100

Eiserne Regel: diese Endpunkte ändern ausschließlich Zeilen in `intents` — nie
Kickbase, nie `transfer_actions`. Jeder Kickbase-Write läuft weiter über den
Intent-Executor-Job -> ActionExecutor. Die Zustandswechsel (Veto/Anpassen/
Sofort ausführen) leben seit 31.08. im gemeinsamen Service
`kickbase_agent.intents.actions` (Spec 2026-08-31-discord-veto-buttons §4.2):
atomare, bedingte UPDATEs (`WHERE status = 'planned'`), damit ein gleichzeitig
zugreifender Executor-Tick nie ein bereits ausgeführtes Vorhaben „übersprungen"
sieht — die Route übersetzt die typisierten Fehler in ihre heutigen
404/409/400-Texte, die Discord-Buttons dieselben Fehler in Kurzmeldungen.
Keine Secrets im JSON (kein raw_response/payload/idempotency_key/channel_msg_id,
kein discord_msg_id/discord_card_synced_at).
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.cli.plan import build_plan
from kickbase_agent.db.models import Intent, Player, Report, TransferAction
from kickbase_agent.decision.guardrails import PROTECTED_ENV_VAR, protected_player_ids
from kickbase_agent.decision.purchase_review import (
    is_engine_review,
    purchase_review_veto,
    reviewed_bid_amount,
)
from kickbase_agent.intents import actions
from kickbase_agent.intents.actions import INTENT_STATUS_DE as _INTENT_STATUS_DE
from kickbase_agent.mcp._util import fmt_money
from kickbase_agent.scouts.ligainsider.teams import TEAMS
from kickbase_agent.server.deps import db_session
from kickbase_agent.server.schemas import IntentAdjustIn, IntentCreateIn, IntentVetoIn

router = APIRouter()

#: Ab diesem Betrag gilt ein Vorhaben als „groß" (Spec §1: Hervorhebung der Karte).
BIG_AMOUNT = 5_000_000

#: Absender, die Vorhaben anlegen dürfen (Paket 1, P0-8). Stolperdraht, kein Riegel.
INTENT_VIA_WHITELIST = frozenset({"web", "mcp", "claude-session", "owner"})

INTENT_STATUSES = (
    "planned", "executing", "executed", "vetoed", "expired", "failed", "superseded",
)
StatusFilter = Literal[
    "planned", "all", "executing", "executed", "vetoed", "expired", "failed", "superseded",
]

_POS = {1: "TW", 2: "ABW", 3: "MF", 4: "ST"}

_ACTION_STATUS_DE = {
    "executed": "ausgeführt",
    "failed": "fehlgeschlagen",
    "refused": "verweigert",
    "rejected": "abgelehnt",
    "expired": "abgelaufen",
    "skipped_dry_run": "Schatten (nicht ausgeführt)",
    "noop": "ohne Wirkung",
    "pending": "läuft",
    "proposed": "wartet auf Freigabe",
    "unknown": "unklar (Timeout)",
}

EXECUTE_NOW_NOTE = "wird beim nächsten Executor-Tick (≤ 5 min) ausgeführt"


# ------------------------------------------------------------------ Hilfsfunktionen


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(dt: datetime) -> datetime:
    """SQLite liefert naive Zeitstempel zurück — als UTC interpretieren."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else _as_utc(dt).isoformat()


def _seconds_left(target: datetime | None, now: datetime) -> int | None:
    if target is None:
        return None
    return max(0, int((_as_utc(target) - now).total_seconds()))


def _club(team_id: str | None) -> str:
    team = TEAMS.get(str(team_id or ""))
    return team.kb_name if team else str(team_id or "")


def _intent_json(intent: Intent, now: datetime) -> dict[str, Any]:
    """Alle Modellfelder (Zeiten ISO/UTC) + abgeleitete Anzeigefelder."""
    amount = int(intent.amount or 0)
    return {
        "id": intent.id,
        "created_at": _iso(intent.created_at),
        "updated_at": _iso(intent.updated_at),
        "kind": intent.kind,
        "player_id": intent.player_id,
        "player_name": intent.player_name,
        "team_id": intent.team_id,
        "position": intent.position,
        "amount": amount,
        "rationale": intent.rationale,
        "sources": intent.sources,
        "listing_type": intent.listing_type,
        "deadline": _iso(intent.deadline),
        "execute_at": _iso(intent.execute_at),
        "status": intent.status,
        "emil_note": intent.emil_note,
        "adjusted_from": intent.adjusted_from,
        "executed_action_id": intent.executed_action_id,
        "result": intent.result,
        # Veto-Vormerkung (Review 31.08.): gesetzt, wenn Emils Veto den Versuch
        # im executing-Fenster traf — die Zentrale zeigt dann „Veto vorgemerkt".
        "veto_requested_at": _iso(intent.veto_requested_at),
        # abgeleitet
        "seconds_left": _seconds_left(intent.execute_at, now),
        "deadline_seconds_left": _seconds_left(intent.deadline, now),
        "amount_fmt": fmt_money(amount),
        "big": amount >= BIG_AMOUNT,
        "club": _club(intent.team_id),
        "pos": _POS.get(int(intent.position or 0), ""),
        "img": None,
    }


def _get_intent(session: Session, intent_id: int) -> Intent:
    intent = session.get(Intent, intent_id)
    if intent is None:
        raise HTTPException(status_code=404, detail=f"Vorhaben {intent_id} nicht gefunden")
    return intent


def _http_error(exc: actions.IntentActionError) -> HTTPException:
    """Service-Fehler → HEUTIGE Statuscodes/Texte (Verhaltensneutralität der Extraktion)."""
    if isinstance(exc, actions.IntentNotFound):
        return HTTPException(status_code=404, detail=exc.detail)
    if isinstance(exc, actions.IntentNotPlanned):
        return HTTPException(status_code=409, detail=exc.detail)
    if isinstance(exc, actions.IntentAdjustError):
        return HTTPException(status_code=409 if exc.conflict else 400, detail=exc.detail)
    return HTTPException(status_code=400, detail=exc.detail)


# ------------------------------------------------------------------ Intents


async def _current_purchase_plan():
    """Frische Mannschaftsentscheidung; bei Datenfehlern keine manuelle Ersatzfreigabe."""
    try:
        return await asyncio.wait_for(build_plan(), timeout=150)
    except Exception as exc:  # noqa: BLE001 — jeder Planfehler muss den Kauf schließen
        raise HTTPException(
            status_code=503,
            detail="Mannschaftsprüfung derzeit nicht verfügbar; kein Kaufauftrag angelegt.",
        ) from exc


def _purchase_plan_json(plan) -> dict[str, Any]:
    selected = []
    for pid, review in (plan.purchase_reviews or {}).items():
        amount = reviewed_bid_amount(review)
        if not is_engine_review(review) or purchase_review_veto(
            review, player_id=pid, amount=amount, now=_now(),
        ) is not None:
            continue
        selected.append({
            "player_id": pid, "amount_eur": amount, "max_bid_eur": review["max_bid_eur"],
            "listing_type": review.get("listing_type"), "reasons": review["reasons"],
            "valid_until": review["valid_until"],
        })
    return {
        "league_id": plan.league_id, "plan_id": plan.team_plan.get("plan_id"),
        "observed_at": plan.team_plan.get("observed_at"), "selected": selected,
        "planning_notes": plan.curation_notes,
        "note": "Nur Vorschläge; jeder Kauf wird beim Anlegen und vor Ausführung geprüft.",
    }


@router.get("/purchase-plan")
async def purchase_plan() -> dict[str, Any]:
    """Handhabbare Engine-Auswahl für Web-App, Hermes und andere Operatoren."""
    return _purchase_plan_json(await _current_purchase_plan())


def _operator_bid_review(intent: Intent) -> None:
    if intent.kind != "bid" or not (intent.sources or {}).get("manual"):
        return
    review = (intent.sources or {}).get("purchase_review")
    if not is_engine_review(review) or purchase_review_veto(
        review, player_id=intent.player_id, amount=intent.amount, now=_now(),
    ) is not None:
        raise HTTPException(
            status_code=409,
            detail="Kauf braucht einen gültigen Mannschaftsplan. Kaufvorschläge neu abrufen; "
            "bestehende Kickbase-Gebote werden dadurch nicht zurückgezogen.",
        )


@router.get("/intents")
def list_intents(
    status: StatusFilter = Query("planned"),
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(db_session),
) -> dict[str, Any]:
    """Vorhaben: geplante zuerst (nach execute_at), dann der Rest (updated_at absteigend)."""
    now = _now()
    rows: list[Intent] = []
    if status in ("planned", "all"):
        rows.extend(
            session.execute(
                select(Intent)
                .where(Intent.status == "planned")
                .order_by(Intent.execute_at.asc(), Intent.id.asc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
    if status == "all":
        rows.extend(
            session.execute(
                select(Intent)
                .where(Intent.status != "planned")
                .order_by(Intent.updated_at.desc(), Intent.id.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
    elif status != "planned":
        rows.extend(
            session.execute(
                select(Intent)
                .where(Intent.status == status)
                .order_by(Intent.updated_at.desc(), Intent.id.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
    rows = rows[:limit]
    return {
        "now": now.isoformat(),
        "count": len(rows),
        "intents": [_intent_json(i, now) for i in rows],
    }


@router.get("/intents/{intent_id}")
def get_intent(intent_id: int, session: Session = Depends(db_session)) -> dict[str, Any]:
    now = _now()
    return {"intent": _intent_json(_get_intent(session, intent_id), now)}


@router.post("/intents")
async def create_manual_intent(
    payload: IntentCreateIn,
    session: Session = Depends(db_session),
) -> dict[str, Any]:
    """Manuelles Vorhaben anlegen (Zwei-DB-Fix 24.08.): ausgeführt wird AUSSCHLIESSLICH
    vom Box-Worker (Intent-Executor) — hier passiert kein Kickbase-Write. `sources.manual`
    schützt das Vorhaben vor dem Planner-Supersede; Dedupe wie beim Planner: höchstens
    EIN offenes Vorhaben je (kind, player_id) → 409 mit dem bestehenden Vorhaben."""
    kind = payload.kind.strip()
    if kind not in ("bid", "list", "sell", "cancel_bid", "delist"):
        raise HTTPException(
            status_code=400, detail="kind muss bid|list|sell|cancel_bid|delist sein",
        )
    # Absender-Stolperdraht (Paket 1, P0-8): `via` ist Selbstauskunft, kein Riegel —
    # alle Schreiber teilen denselben Zugang. Er lässt alte Fremdskripte (Hermes
    # sendet fest via=hermes) geschlossen scheitern. Leer bleibt vorerst "mcp" und
    # wird über `via_declared` zählbar. Veto/adjust/execute-now bleiben offen.
    via_declared = "via" in payload.model_fields_set and bool(payload.via.strip())
    via = payload.via.strip().lower() if via_declared else "mcp"
    if via not in INTENT_VIA_WHITELIST:
        raise HTTPException(
            status_code=403,
            detail=(
                f"via={via!r} darf keine Vorhaben anlegen (erlaubt: "
                f"{', '.join(sorted(INTENT_VIA_WHITELIST))}). Ein Schreiber — siehe "
                "docs/STUDIE-TURNAROUND-2026-09-19.md."
            ),
        )
    if payload.after_squad_size is not None and kind != "sell":
        raise HTTPException(status_code=400, detail="after_squad_size gilt nur für sell")
    # Gebots-Rückzug (02.09.): hängende Manager-Angebote binden das Konto (pbaa)
    # und sperren im T-120-Fenster jedes neue Gebot — der Rückzug ist der einzige
    # Weg, das zu lösen. Offer-ID Pflicht (Executor-Dispatch braucht sie).
    if kind == "cancel_bid" and not payload.offer_id.strip():
        raise HTTPException(status_code=400, detail="cancel_bid braucht offer_id (own_bids)")
    pid = str(payload.player_id).strip()
    if not pid:
        raise HTTPException(status_code=400, detail="player_id fehlt")
    # Verfassung (26.08.): Ein Abgabe-Vorhaben für einen geschützten Spieler
    # wird gar nicht erst angelegt. Der Executor verweigert es ohnehin — aber
    # ein Vorhaben „Olise verkaufen", das in Emils Zentrale steht und dann
    # scheitert, ist eine Lüge auf dem Bildschirm.
    if kind in ("sell", "list") and pid in protected_player_ids():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Geschützter Spieler {pid}: {kind} wird nicht angelegt — "
                f"Verfassung (protected_player_ids). Änderbar nur über "
                f"{PROTECTED_ENV_VAR} in der .env (Emils Hoheit)."
            ),
        )
    if not payload.rationale.strip():
        raise HTTPException(status_code=400, detail="rationale ist Pflicht (Audit-Begründung)")
    amount = int(payload.amount or 0)
    if kind in ("bid", "list") and amount <= 0:
        raise HTTPException(status_code=400, detail=f"{kind}: amount > 0 ist Pflicht")
    # Delist (Emil 21.09.2026, gebaut 22.09.): eigenes Listing zurückziehen. Wie
    # der Gebots-Rückzug baut es Risiko ab — kein emil_go-Zwang, kein Engine-
    # Plan, keine Verfassungssperre (Executor: `delist` ist kein Abgang). Ein
    # Betrag hat hier keine Bedeutung; ein mitgesendeter wäre ein Irrtum des
    # Aufrufers und darf nicht als Preis im Vorhaben/Audit stehen.
    if kind == "delist" and amount != 0:
        raise HTTPException(status_code=400, detail="delist: amount muss 0 sein (kein Preis)")
    # Gate-Parität mit dem MCP (Codex-Folge-Review 24.08., P1): ab 5 M und bei
    # jedem Sofortverkauf ist Emils GO (Wortlaut + Zeit) Pflicht — auch remote.
    if (amount >= BIG_AMOUNT or kind == "sell") and not payload.emil_go.strip():
        raise HTTPException(
            status_code=400,
            detail="emil_go ist Pflicht (>= 5 M bzw. Sofortverkauf) — Wortlaut + Zeit",
        )
    now = _now()
    existing = (
        session.execute(
            select(Intent).where(
                Intent.kind == kind,
                Intent.player_id == pid,
                # `executing` zählt mit (Codex-Folge-Review 24.08., P2): ein
                # Vorhaben mitten im Write ist kein freier Slot.
                Intent.status.in_(("planned", "executing")),
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Offenes Vorhaben existiert bereits",
                "intent": _intent_json(existing, now),
            },
        )
    checked_review = None
    checked_plan = None
    checked_team_id = None
    if kind == "bid":
        checked_plan = await _current_purchase_plan()
        checked_review = deepcopy((checked_plan.purchase_reviews or {}).get(pid))
        problem = purchase_review_veto(checked_review, player_id=pid, amount=amount, now=_now())
        if not is_engine_review(checked_review) or problem is not None:
            raise HTTPException(status_code=409, detail={
                "error": problem or "Spieler ist im aktuellen Mannschaftsplan nicht ausgewählt.",
                **_purchase_plan_json(checked_plan),
            })
        assert isinstance(checked_review, dict)
        snapshot = checked_review["team_plan"].get("forecast_inputs", {}).get(pid, {})
        checked_team_id = (snapshot.get("player") or {}).get("team_id")
        if (checked_review.get("listing_type") not in ("kickbase", "manager")
                or type(checked_review.get("position")) is not int
                or checked_review["position"] not in (1, 2, 3, 4)
                or not isinstance(checked_team_id, str) or not checked_team_id.strip()):
            raise HTTPException(
                status_code=503, detail="Mannschaftsplan ohne gültige Spielerdaten.",
            )
        # Der Planbau liest Daten und kann dauern. Ein paralleler Worker darf nicht
        # zwischen erster Dedupe-Prüfung und Insert einen zweiten Auftrag erzeugen.
        session.expire_all()
        if session.scalar(select(Intent).where(
            Intent.kind == kind, Intent.player_id == pid,
            Intent.status.in_(("planned", "executing")),
        )) is not None:
            raise HTTPException(status_code=409, detail="Offenes Vorhaben inzwischen vorhanden.")
        now = _now()
    sources: dict[str, Any] = {"manual": True, "via": via, "via_declared": via_declared}
    if checked_review is not None:
        assert checked_plan is not None
        sources.update(purchase_review=checked_review, league_id=checked_plan.league_id)
    if payload.emil_go.strip():
        sources["emil_go"] = payload.emil_go.strip()
    # Kader-Bedarf (26.08.): nur für Gebote sinnvoll — er hebt allein die
    # Overpay-Kappe (110 % → 135 % MW), und die gilt nur beim Kaufen.
    if checked_review is not None:
        sources["squad_need"] = checked_review["purpose"] == "lineup_completion"
    if kind == "cancel_bid":
        sources["offer_id"] = payload.offer_id.strip()
    # Tausch-Bedingung (02.09.): nur für Sofortverkäufe, und nur befristet — ein
    # unbefristetes „warte auf Ersatz" würde der Executor ehrlich verwerfen.
    deadline_s = max(0, int(payload.deadline_s or 0))
    if payload.after_squad_size is not None:
        if kind != "sell":
            raise HTTPException(status_code=400, detail="after_squad_size gilt nur für sell")
        if int(payload.after_squad_size) < 1:
            raise HTTPException(status_code=400, detail="after_squad_size muss >= 1 sein")
        if deadline_s <= 0:
            raise HTTPException(
                status_code=400,
                detail="after_squad_size braucht deadline_s > 0 (befristetes Warten)",
            )
        sources["after_squad_size"] = int(payload.after_squad_size)
    deadline = now + timedelta(seconds=deadline_s) if deadline_s > 0 else None
    execute_at = now + timedelta(seconds=max(0, int(payload.delay_s or 0)))
    if checked_review is not None:
        review_end = datetime.fromisoformat(checked_review["valid_until"])
        deadline = min(deadline, review_end) if deadline else review_end
        if execute_at >= deadline:
            raise HTTPException(
                status_code=409, detail="Ausführungszeit liegt nach der Kaufprüfung.",
            )
    intent = Intent(
        created_at=now,
        updated_at=now,
        kind=kind,
        player_id=pid,
        player_name=payload.player_name.strip(),
        team_id=checked_team_id if checked_team_id is not None else payload.team_id.strip(),
        position=checked_review["position"] if checked_review else int(payload.position or 0),
        amount=amount,
        rationale=payload.rationale.strip(),
        sources=sources,
        listing_type=checked_review["listing_type"] if checked_review else (
            payload.listing_type if payload.listing_type in ("kickbase", "manager") else "kickbase"
        ),
        deadline=deadline,
        execute_at=execute_at,
        status="planned",
    )
    session.add(intent)
    session.commit()
    return {"intent": _intent_json(intent, now)}


@router.post("/intents/{intent_id}/veto")
def veto_intent(
    intent_id: int,
    body: IntentVetoIn | None = None,
    session: Session = Depends(db_session),
) -> dict[str, Any]:
    """Veto: planned -> vetoed (+ optionale Notiz). Der Executor fasst es nie mehr an.

    Trifft das Veto ein `executing`-Fenster, wird es VORGEMERKT (Review 31.08.):
    Status bleibt executing, `veto_requested_at` ist gesetzt, und der Executor
    löst die Vormerkung beim Scheitern des Versuchs als vetoed ein — die
    Antwort trägt dann eine erklärende `note`.
    """
    try:
        # `_now` als Callable: der Service löst die Zeit erst NACH dem Lesen des
        # Ist-Standes auf (Race-Semantik der bedingten UPDATEs bleibt identisch).
        intent = actions.veto_intent(
            session,
            intent_id,
            note=body.note if body is not None else None,
            actor=actions.ACTOR_WEB,
            now=_now,
        )
    except actions.IntentActionError as exc:
        raise _http_error(exc) from exc
    payload: dict[str, Any] = {"intent": _intent_json(intent, _now())}
    if intent.status == "executing":
        payload["note"] = (
            "Versuch läuft gerade — Veto vorgemerkt: greift, falls der Versuch "
            "scheitert; ging der Kauf schon durch, ist es zu spät."
        )
    return payload


@router.post("/intents/{intent_id}/adjust")
def adjust_intent(
    intent_id: int,
    body: IntentAdjustIn,
    session: Session = Depends(db_session),
) -> dict[str, Any]:
    """Betrag ändern (absolut oder ±%). `adjusted_from` hält den Erst-Betrag fest
    (nur beim ERSTEN Anpassen gesetzt) — der Planner überschreibt ihn dann nicht mehr."""
    _operator_bid_review(_get_intent(session, intent_id))
    try:
        intent = actions.adjust_intent(
            session,
            intent_id,
            pct=body.pct,
            amount=body.amount,
            actor=actions.ACTOR_WEB,
            now=_now,
        )
    except actions.IntentActionError as exc:
        raise _http_error(exc) from exc
    return {"intent": _intent_json(intent, _now())}


@router.post("/intents/{intent_id}/execute-now")
def execute_intent_now(
    intent_id: int,
    session: Session = Depends(db_session),
) -> dict[str, Any]:
    """`execute_at = jetzt` — der Executor-Job nimmt das Vorhaben beim nächsten Tick.
    Es wird hier NICHTS ausgeführt (kein Kickbase-Write aus der API)."""
    _operator_bid_review(_get_intent(session, intent_id))
    try:
        intent = actions.execute_intent_now(
            session, intent_id, actor=actions.ACTOR_WEB, now=_now
        )
    except actions.IntentActionError as exc:
        raise _http_error(exc) from exc
    return {"intent": _intent_json(intent, _now()), "note": EXECUTE_NOW_NOTE}


# ------------------------------------------------------------------ Protokoll


def _intent_title(intent: Intent) -> str:
    name = intent.player_name or f"Spieler {intent.player_id}"
    money = fmt_money(int(intent.amount or 0))
    if intent.kind == "bid":
        base = f"Gebot {money} auf {name}"
    elif intent.kind == "list":
        base = f"Listing {name} für {money}"
    elif intent.kind == "cancel_bid":
        base = f"Gebot zurückziehen: {name}".strip()
    elif intent.kind == "delist":
        base = f"Listing zurückziehen: {name}".strip()
    elif intent.kind == "sell":
        base = f"Sofortverkauf {name}"
    elif intent.kind == "lineup":
        base = "Aufstellung"
    else:
        base = f"{intent.kind} {name}".strip()
    return f"{base} — {_INTENT_STATUS_DE.get(intent.status, intent.status)}"


def _action_title(action: TransferAction, name: str) -> str:
    money = fmt_money(action.amount) if action.amount is not None else ""
    t = action.action_type
    if t == "bid":
        base = f"Gebot {money} auf {name}"
    elif t == "cancel_bid":
        base = f"Gebot zurückgezogen: {name}"
    elif t == "accept":
        base = f"Angebot angenommen: {name}"
    elif t == "decline":
        base = f"Angebot abgelehnt: {name}"
    elif t == "list":
        base = f"Listing {name} für {money}" if money else f"Listing {name}"
    elif t == "delist":
        base = f"Listing zurückgezogen: {name}"
    elif t == "sell":
        base = f"Sofortverkauf {name}"
    elif t == "lineup":
        base = "Aufstellung gesetzt"
    elif t == "bonus":
        base = "Prämie kassiert"
    else:
        base = f"{t} {name}".strip()
    status_de = _ACTION_STATUS_DE.get(action.status, action.status)
    return f"{base} — {status_de}"


def _emil_text(intent: Intent) -> str | None:
    parts: list[str] = []
    if intent.adjusted_from is not None:
        parts.append(
            f"Betrag angepasst: {fmt_money(intent.adjusted_from)} → "
            f"{fmt_money(int(intent.amount or 0))}"
        )
    if intent.emil_note:
        parts.append(f"Notiz: {intent.emil_note}")
    return " · ".join(parts) or None


def _player_names(
    session: Session, actions: list[TransferAction], intents: list[Intent]
) -> dict[str, str]:
    """player_id -> Anzeigename: Intents (jüngster zuerst) vor `players`-Tabelle."""
    names: dict[str, str] = {}
    ids = {a.player_id for a in actions if a.player_id}
    if ids:
        for pid, pname in session.execute(
            select(Player.id, Player.name).where(Player.id.in_(sorted(ids)))
        ).all():
            names[str(pid)] = pname
    for intent in reversed(intents):  # älteste zuerst, jüngste überschreiben
        if intent.player_name:
            names[intent.player_id] = intent.player_name
    return names


def _action_entry(action: TransferAction, name: str) -> dict[str, Any]:
    ts = action.resolved_at or action.created_at
    return {
        "kind": "action",
        "id": action.id,
        "ts": _iso(ts),
        "title": _action_title(action, name),
        "why": action.rationale or None,
        "sources": None,
        "status": action.status,
        "emil": None,
        "action_type": action.action_type,
        "player_id": action.player_id,
        "player_name": name if action.player_id else None,
        "amount": action.amount,
        "amount_fmt": fmt_money(action.amount) if action.amount is not None else None,
        "rule_id": action.rule_id,
        "dry_run": bool(action.dry_run),
        "created_at": _iso(action.created_at),
        "resolved_at": _iso(action.resolved_at),
    }


def _rule_id_from_sources(intent: Intent) -> str | None:
    sources = intent.sources if isinstance(intent.sources, dict) else {}
    value = sources.get("rule_id")
    return str(value) if value else None


def _intent_entry(intent: Intent, now: datetime, linked: TransferAction | None) -> dict[str, Any]:
    return {
        "kind": "intent",
        "id": intent.id,
        "ts": _iso(intent.updated_at or intent.created_at),
        "title": _intent_title(intent),
        "why": intent.rationale or None,
        "sources": intent.sources,
        "status": intent.status,
        "emil": _emil_text(intent),
        "intent_kind": intent.kind,
        "player_id": intent.player_id,
        "player_name": intent.player_name or None,
        "amount": int(intent.amount or 0),
        "amount_fmt": fmt_money(int(intent.amount or 0)),
        "adjusted_from": intent.adjusted_from,
        "emil_note": intent.emil_note,
        "result": intent.result,
        "execute_at": _iso(intent.execute_at),
        "deadline": _iso(intent.deadline),
        "seconds_left": _seconds_left(intent.execute_at, now),
        "executed_action_id": intent.executed_action_id,
        # aus der verknüpften transfer_actions-Zeile (wenn vorhanden)
        "action_status": linked.status if linked is not None else None,
        "rule_id": linked.rule_id if linked is not None else _rule_id_from_sources(intent),
        "dry_run": bool(linked.dry_run) if linked is not None else None,
        "club": _club(intent.team_id),
        "pos": _POS.get(int(intent.position or 0), ""),
    }


#: Berichtsarten, die als Zusammenfassungs-Einträge (kind `summary`) ins Protokoll gehen.
_SUMMARY_REPORT_KINDS = ("briefing", "recherche", "schatten")


def _summary_entries(session: Session, limit: int) -> list[dict[str, Any]]:
    """Berichte (briefing/recherche/schatten) als Protokoll-Einträge kind `summary`."""
    from kickbase_agent.server.routes.reports import preview_of

    rows = (
        session.execute(
            select(Report)
            .where(Report.kind.in_(_SUMMARY_REPORT_KINDS))
            .order_by(Report.id.desc())
            .limit(min(limit, 50))
        )
        .scalars()
        .all()
    )
    return [
        {
            "kind": "summary",
            "id": r.id,
            "ts": _iso(r.created_at),
            "title": r.title or r.kind,
            "why": preview_of(r.body or ""),
            "sources": {"report_id": r.id, "kind": r.kind},
            "status": "bericht",
            "emil": None,
            "report_kind": r.kind,
        }
        for r in rows
    ]


def _extra_entries(session: Session, limit: int) -> list[dict[str, Any]]:
    """Zusatzquellen des Protokolls — jede darf fehlen, keine darf es blockieren:

    - KI-Entscheidungen + Lektionen (`decision`/`lesson`, knowledge.decision_entries),
    - Git-Commits als Systemänderungen (`system`, knowledge.commits.system_entries),
    - Liga-Ereignisse aus dem Activity-Feed (`transfer`/`league`,
      intel.transfers.league_events; Feed aus dem 60-s-Cache von routes/live —
      NIE live in diesem Request geholt; leerer Cache ohne Kickbase-Zugang ⇒ []),
    - Berichte als Zusammenfassungen (`summary`, s. _summary_entries).
    """
    extra: list[dict[str, Any]] = []
    try:
        from kickbase_agent.knowledge import decision_entries

        extra.extend(decision_entries())
    except Exception:  # noqa: BLE001 — Wissens-Schicht darf das Protokoll nie blockieren
        pass
    try:
        from kickbase_agent.knowledge.commits import system_entries

        extra.extend(system_entries())
    except Exception:  # noqa: BLE001 — Commits sind Zusatzinfo
        pass
    try:
        from kickbase_agent.intel.transfers import league_events
        from kickbase_agent.server.routes.live import cached_feed

        extra.extend(league_events(cached_feed()))
    except Exception:  # noqa: BLE001 — Feed-Cache/Parsing darf nie crashen
        pass
    try:
        extra.extend(_summary_entries(session, limit))
    except Exception:  # noqa: BLE001 — z. B. reports-Tabelle ohne Migration
        pass
    return extra


@router.get("/protocol")
def get_protocol(
    limit: int = Query(100, ge=1, le=1000),
    session: Session = Depends(db_session),
) -> dict[str, Any]:
    """KI-Protokoll: transfer_actions + Intents aller Status, neueste zuerst.

    Eine Aktion, die ein Vorhaben per `executed_action_id` referenziert, erscheint
    NICHT doppelt: ihr Status/rule_id/dry_run wandern in den Intent-Eintrag
    (`action_status`, `rule_id`, `dry_run`). Schatten-Einträge („hätte getan")
    sind Aktionen mit `dry_run: true` / Status `skipped_dry_run`.

    Eintragsarten (`kind`), alle mit den Basisfeldern
    {kind, ts, title, why, sources, status, emil}:

    - `action` / `intent`  — wie bisher (DB, s. o.)
    - `decision` / `lesson` — KI-Entscheidungen + Lektionen (Wissens-Schicht)
    - `system`   — Git-Commit als Systemänderung: title „System: <subject>",
                   why = Commit-Body (≤ 400), sources {"commit": sha7}, status `deployt`
    - `transfer` — EIGENES Liga-Ereignis aus dem Feed: „Zuschlag: …"/„Verkauft: …"/
                   „Prämie kassiert (+X)", status `zuschlag`/`verkauft`/`praemie`,
                   sources {price/bonus, …, buyer/seller}; zusätzlich player_name,
                   amount, amount_fmt
    - `league`   — fremder Transfer: „Liga: <Manager> kauft/verkauft …", status `liga`
    - `summary`  — Bericht (briefing/recherche/schatten): title = Bericht-Titel,
                   why = Vorschau, sources {"report_id", "kind"}, status `bericht`
    """
    now = _now()
    actions = list(
        session.execute(select(TransferAction).order_by(TransferAction.id.desc()).limit(limit))
        .scalars()
        .all()
    )
    intents = list(
        session.execute(
            select(Intent).order_by(Intent.updated_at.desc(), Intent.id.desc()).limit(limit)
        )
        .scalars()
        .all()
    )

    linked_ids = {i.executed_action_id for i in intents if i.executed_action_id is not None}
    linked: dict[int, TransferAction] = {a.id: a for a in actions if a.id in linked_ids}
    missing = linked_ids - set(linked)
    if missing:
        for a in (
            session.execute(select(TransferAction).where(TransferAction.id.in_(sorted(missing))))
            .scalars()
            .all()
        ):
            linked[a.id] = a

    names = _player_names(session, actions, intents)
    stamped: list[tuple[datetime, int, dict[str, Any]]] = []
    for a in actions:
        if a.id in linked_ids:
            continue  # steckt im Intent-Eintrag
        pname = names.get(a.player_id or "", f"Spieler {a.player_id}" if a.player_id else "")
        ts = _as_utc(a.resolved_at or a.created_at)
        stamped.append((ts, a.id, _action_entry(a, pname)))
    for i in intents:
        ts = _as_utc(i.updated_at or i.created_at)
        stamped.append((ts, i.id, _intent_entry(i, now, linked.get(i.executed_action_id or -1))))

    # Zusatzquellen (Entscheidungen, Commits, Liga-Feed, Berichte) einmischen —
    # negative IDs nur als Tiebreak; Einträge ohne parsebares ts fliegen still raus.
    for j, x_entry in enumerate(_extra_entries(session, limit)):
        try:
            ts = datetime.fromisoformat(x_entry["ts"]).astimezone(UTC)
        except (KeyError, TypeError, ValueError):
            continue
        stamped.append((ts, -1 - j, x_entry))
    stamped.sort(key=lambda t: (t[0], t[1]), reverse=True)
    entries = [e for _, _, e in stamped[:limit]]
    return {"now": now.isoformat(), "count": len(entries), "entries": entries}
