"""Steuerzentrale — Vorhaben (Intents) mit Veto-Fenster: Planner + Executor.

Spec: docs/superpowers/specs/2026-08-23-steuerzentrale.md (§1 Prinzip, §2 Daten-
modell, §3 Worker-Jobs). Emils Richtung (23.08.): „Die KI soll nicht mehr warten
… möglichst transparent zeigen, was sie vorhat, und da noch Entscheidungsfreiraum
haben — der ist immer optional. Das soll komplett autonom passieren."

Ein **Vorhaben** (db.models.Intent) ist eine von der Engine beschlossene Aktion
(Gebot `bid`, Listing `list`, Sofortverkauf `sell`), die zu `execute_at` autonom
ausgeführt wird — es sei denn, Emil greift vorher über die Web-App ein (Veto,
Betrag anpassen, sofort ausführen). Zwei Jobs (worker/__main__.py):

- **Intent-Planner** (stündlich :25, damit auch 22:25 nach dem Abend-Scan):
  `build_plan` liefert die Vorschläge der Decision-Engine (dieselben Guardrails
  wie Scan/Briefing); der rohe Markt liefert je Listing Countdown (`exs`, nur
  Kickbase-Listings) bzw. Verkäufer (`u`, Manager-Listings). Daraus entstehen
  Vorhaben nach den Regeln aus §1:
    · Kickbase-Listing: deadline = jetzt + exs, execute_at = deadline − 15 min
      (Auto-Zuschlag ans höchste Gebot beim Ablauf — spät bieten verrät weniger).
    · Manager-Listing: kein Ablauf, execute_at = jetzt + 2 h (Default-Fenster).
    · Sniper: < 30 min Restzeit → execute_at = jetzt (sofort fällig).
    · Beträge ≥ 5 M: Mindest-Veto-Fenster 2 h (außer Sniper), aber nie später
      als deadline − 15 min.
    · list/sell ohne Listing-Bezug: execute_at = jetzt + 2 h.
  Idempotenz: höchstens EIN `planned`-Vorhaben je (kind, player_id). Ein neuer
  Lauf aktualisiert Betrag/Begründung/Quellen; `execute_at` wandert dabei nur
  NACH VORN (Deadline rückt näher, Sniper, Emils »Jetzt ausführen« bleibt) —
  nie nach hinten, sonst würde ein Manager-Listing stündlich vertagt. Emils
  Anpassung (`adjusted_from` gesetzt) bleibt unangetastet. Vorschläge, die
  nicht mehr kommen, setzen das offene Vorhaben auf `superseded`.
  Der Planner schreibt KEINE Kickbase-Writes.
- **Intent-Executor** (alle 5 min): fällige Vorhaben (`planned`, execute_at ≤
  jetzt) laufen durch den ActionExecutor (einzige Schreibstelle: Kill-Switch,
  Live-Interlock, Idempotenz, Preflight) mit rule_id `intent-<kind>`. Ergebnis
  ehrlich ins Vorhaben: `executed` oder `failed` mit `result` =
  „<Executor-Status>: <Grund>" (auch bei skipped_dry_run/refused, damit die
  Karte zeigt, WARUM nichts passiert ist). Deadline überschritten → `expired`.
  Tages-Caps der Guardrails (5 Käufe/5 Verkäufe) gelten über heutige executed
  `intent-*`-Zeilen im Audit-Log. Vor dem Write wird das Vorhaben atomar geclaimt
  (planned → `executing`, UPDATE … WHERE status='planned'): ein Veto/Adjust der
  API, das zwischen Laden und Write eintrifft, wird nie überschrieben; nach dem
  Claim wird die Zeile frisch gelesen (Adjust-Betrag zählt). `executing` ist nur
  ein Sekunden-Zwischenstatus — verwaiste Reste (Absturz) räumt der nächste Lauf
  als failed auf.
  Neuanlage-Sperren des Planners: heute executed/failed (kein Doppel-Vorhaben,
  keine Fehlschlag-Schleife) und Emils Veto (24 h bzw. bis zum Listing-Ablauf).

Arming: `ActionExecutor._live_allowed` ärmt bid/sell/list mit rule_id-Präfix
`intent-` über AGENT_AUTO_INTENTS=1 (Default 0 → Vorhaben sichtbar, Executor
meldet refused). Kein Kickbase-Write außerhalb des ActionExecutors.

Discord-Veto-Karten (Spec 2026-08-31-discord-veto-buttons): Der Planner postet
je NEUEM Vorhaben eine Karte mit Buttons (post_intent_cards); der Executor-Tick
zieht veraltete Karten nach (sync_intent_cards, L-088-Kehrer — auch Web-Vetos
und Endzustände); Button-Klicks laufen über handle_intent_action in den
gemeinsamen Aktions-Service (intents/actions.py) — sie mutieren NUR
`intents`-Zeilen, nie Kickbase.
"""

from __future__ import annotations

import asyncio
import logging
import re
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from kickbase_agent.agent import freeze as fz
from kickbase_agent.agent.executor import ActionExecutor, ActionResult, PlannedAction
from kickbase_agent.agent.rules import load_agent_rules
from kickbase_agent.cli.plan import Plan, build_plan
from kickbase_agent.config import get_settings
from kickbase_agent.db.models import Intent, Report, TransferAction
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.decision.guardrails import (
    DEFAULT_GUARDRAILS,
    Guardrails,
    protected_player_ids,
)
from kickbase_agent.decision.kickoff_freeze import (
    EXPOSURE_GUARD_HOURS,
    KICKOFF_MIN_BALANCE,
    bid_freeze_reason,
)
from kickbase_agent.decision.lineup import MIN_SQUAD_SIZE
from kickbase_agent.decision.models import Proposal
from kickbase_agent.decision.policies import (
    ACCEPT_WINDFALL_FLOOR,
    FailedListing,
    IncomingOffer,
    OwnListing,
    buy_blocker_reason,
    offers_below_asking,
    propose_accepts,
    propose_declines,
    propose_delists,
    propose_failed_listing_sells,
)
from kickbase_agent.decision.purchase_review import (
    is_engine_review,
    purchase_review_veto,
    reviewed_bid_amount,
    team_selection_veto,
)
from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.intents import actions as intent_actions
from kickbase_agent.kickbase import endpoints
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.kickbase.models import ZOMBIE_LISTING_AGE, Player
from kickbase_agent.notify.intent_cards import (
    IntentActionOutcome,
    build_intent_view,
    format_intent_embed,
)
from kickbase_agent.notify.report_store import save_report
from kickbase_agent.trading.trade_book import is_trade
from kickbase_agent.worker.auto_trading import TodayCounts

#: rule_id-Präfix des Intent-Pfads: der Executor-Interlock (AGENT_AUTO_INTENTS)
#: ärmt GENAU dieses Präfix; die Tages-Caps zählen heutige executed-Zeilen darüber.
INTENT_PREFIX = "intent-"

#: Default-Veto-Fenster (Manager-Listings, list/sell) und Mindestfenster ≥ 5 M.
VETO_WINDOW = timedelta(hours=2)
#: Accept-Vorhaben (Angebote auf EIGENE Listings, 24.08.): kürzeres Fenster,
#: weil die Offer-Gültigkeit unbekannt ist — ≥ 5 M gilt trotzdem das
#: 2-h-Mindestfenster (Emils Regel schlägt Eile). Decision-Record in
#: knowledge/parameter_history.json.
ACCEPT_VETO_WINDOW = timedelta(minutes=45)
#: Windfall-Übergebots-Annahme (≥ 115 % MW auf explizite Bankspieler, 20.10.2026):
#: 20 min Veto-Fenster, damit der Gewinn eingesackt wird, bevor der Mitspieler storniert.
WINDFALL_ACCEPT_VETO_WINDOW = timedelta(minutes=20)
#: Kickbase-Listings: Ausführung so spät wie sicher möglich vor dem Ablauf.
EXECUTE_BEFORE_DEADLINE = timedelta(minutes=15)
#: Sniper-Fenster: unter dieser Restzeit wird sofort ausgeführt.
SNIPER_WINDOW = timedelta(minutes=30)
#: „groß": ab hier gilt das Mindest-Veto-Fenster (und die Karte hebt hervor).
BIG_AMOUNT = DEFAULT_GUARDRAILS.approval_threshold

# Statuswerte (db.models.Intent.status) — hier zentral, damit API/UI sie importieren.
STATUS_PLANNED = "planned"
#: Zwischenstatus NUR während des Executor-Writes: atomar von `planned` geclaimt
#: (UPDATE … WHERE status='planned'), danach immer executed/failed — nie hängend
#: (recover_stale_executing räumt Reste eines Prozess-Absturzes als failed auf).
STATUS_EXECUTING = "executing"
STATUS_EXECUTED = "executed"
STATUS_VETOED = "vetoed"
STATUS_EXPIRED = "expired"
STATUS_FAILED = "failed"
STATUS_SUPERSEDED = "superseded"

# result-Wortlaute (Intent.result), ebenfalls zentral.
RESULT_EXECUTED = "executed"
RESULT_NOT_ON_MARKET = "nicht mehr am Markt"
RESULT_NOT_IN_PLAN = "nicht mehr im Plan"
#: Geplantes Handelsgebot, das einem Kauf der Engine weicht (Befund 03.10.2026, Emils GO).
RESULT_TRADE_YIELDS = "Handels-Autopilot: Die Engine plant einen Kauf — Elf zuerst"
RESULT_NOT_CURATED = "nicht mehr in der geprüften Kaufauswahl oder über individuellem Preislimit"
RESULT_OFFER_GONE = "Angebot nicht mehr vorhanden (angenommen/abgelaufen/zurückgezogen)"
RESULT_EXPIRED = "Deadline überschritten, nicht ausgeführt"
#: Dispo-Tauschverkauf, dessen gekoppelte Auktion ohne Zuschlag endete (zweites Review
#: 23.09.2026): Das Paar ist vorbei; ein späterer Kauf des Ziels glättet nichts.
RESULT_SWAP_LOST = "Tausch-Auktion ohne Zuschlag beendet — gekoppelter Verkauf entfällt"
RESULT_KILL_SWITCH = "skipped_dry_run: Kill-Switch AGENT_DRY_RUN=1 — keine echte Kickbase-Aktion"
RESULT_STALE_EXECUTING = (
    "failed: Ausführung abgebrochen (Prozess-Neustart während des Writes?) — "
    "Audit-Log/Reconcile prüfen"
)
#: Nach so langer Zeit in `executing` gilt ein Vorhaben als verwaist (ein Lauf
#: braucht Sekunden; die Sperre lässt nur einen Lauf gleichzeitig zu).
EXECUTING_GRACE = timedelta(minutes=10)

BERLIN = ZoneInfo("Europe/Berlin")

# Planner und Executor laufen im selben Event-Loop (APScheduler) — die Sperre
# serialisiert den DB-Abgleich des :25-Planners und den :25-Executor-Lauf
# (zusätzlich zu den konditionalen UPDATEs und blocked_keys: Gürtel + Hosenträger).
_intent_lock = asyncio.Lock()


def _as_utc(dt: datetime) -> datetime:
    """SQLite liefert naive Zeitstempel zurück — als UTC interpretieren."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _fmt_m(value: int | None) -> str:
    """Millionen deutsch: 6.800.000 → '6,80 M'."""
    return f"{(value or 0) / 1_000_000:.2f}".replace(".", ",") + " M"


def _fmt_clock(dt: datetime | None) -> str:
    if dt is None:
        return "–"
    return _as_utc(dt).astimezone(BERLIN).strftime("%d.%m. %H:%M")


# ------------------------------------------------------------------ Markt


@dataclass(frozen=True)
class MarketRow:
    """Ein Markt-Listing aus dem rohen Payload — nur die Felder, die der Planner braucht.

    `expires_in_s` = `exs` (live geprobt 20.08.: nur Kickbase-Listings tragen
    einen Countdown; Manager-Listings haben exs 0/fehlend und ein `u`-Objekt mit
    Verkäufer `u.i`/`u.n`). `our_bid`/`is_ours`: eigenes Gebot (`ofs[].u`) bzw.
    eigenes Listing — beides schließt ein neues Vorhaben aus.
    """

    player_id: str
    price: int
    expires_in_s: int | None = None
    seller_id: str = ""
    seller_name: str = ""
    player_name: str = ""
    team_id: str = ""
    position: int = 0
    kb_prob: int | None = None
    our_bid: int | None = None
    is_ours: bool = False

    @property
    def is_manager(self) -> bool:
        return bool(self.seller_id or self.seller_name)


def market_rows_from_raw(raw: dict[str, Any], user_id: str = "") -> list[MarketRow]:
    """Roh-Markt (`client._get(endpoints.market(lid))`) → MarketRow je Listing.

    Liest dieselben Felder wie cli/cockpit/data._market_rows und mcp/tools_read.market
    (i, prc, exs, u.i/u.n, ofs[].u/uop); Spielerfelder über Player.from_api.
    """
    rows: list[MarketRow] = []
    for it in raw.get("it", []) or []:
        seller = it.get("u") or {}
        seller_id = str(seller.get("i") or "")
        seller_name = str(seller.get("n") or "").strip()
        exs = it.get("exs")
        our_bid = None
        if user_id:
            for offer in it.get("ofs", []) or []:
                if str(offer.get("u")) == str(user_id):
                    our_bid = int(offer.get("uop") or 0)
                    break
        name, team_id, position, kb_prob = "", str(it.get("tid", "") or ""), 0, None
        try:
            p = Player.from_api(it)
            name = f"{p.first_name} {p.last_name}".strip()
            team_id, position, kb_prob = p.team_id, int(p.position), p.lineup_prob
        except (KeyError, ValueError, TypeError):
            name = f"{it.get('fn', '')} {it.get('n', '')}".strip()
        rows.append(
            MarketRow(
                player_id=str(it.get("i")),
                price=int(it.get("prc", 0) or 0),
                expires_in_s=int(exs) if exs is not None else None,
                seller_id=seller_id,
                seller_name=seller_name,
                player_name=name,
                team_id=team_id,
                position=position,
                kb_prob=kb_prob,
                our_bid=our_bid,
                is_ours=bool(user_id) and seller_id == str(user_id),
            )
        )
    return rows


def incoming_offers_from_raw(raw: dict[str, Any], user_id: str) -> list[IncomingOffer]:
    """Eingehende Angebote auf EIGENE Listings aus dem rohen Markt-Payload.

    Eigene Listings tragen `u.i == user_id`; ihre `ofs[]`-Einträge sind die
    Angebote der anderen (Bieter `u`, Betrag `uop`, Offer-ID `uoid`) — exakt
    die Feldnamen, die get_own_bids für den Spiegelfall (unser Gebot auf
    fremdes Listing) live verifiziert hat (20.08.). Manager-Angebote sind
    damit gedeckt; ob Kickbase-COMPUTER-Angebote (~103 %) ebenfalls hier
    erscheinen, verifiziert der Abend-Check 24.08. (`kba probe`) — die Quelle
    ist bewusst in DIESER Funktion isoliert, damit ein anderer Fundort nur
    eine Stelle ändert. Einträge ohne `uoid` sind nicht annehmbar → skip.
    """
    offers: list[IncomingOffer] = []
    for it in raw.get("it", []) or []:
        if str((it.get("u") or {}).get("i") or "") != str(user_id):
            continue  # fremdes Listing — dort sind ofs ggf. UNSERE Gebote
        name = f"{it.get('fn', '')} {it.get('n', '')}".strip()
        for offer in it.get("ofs", []) or []:
            oid = str(offer.get("uoid", "") or "")
            amount = int(offer.get("uop", 0) or 0)
            if not oid or amount <= 0:
                continue
            offers.append(
                IncomingOffer(
                    player_id=str(it.get("i")),
                    player_name=name,
                    team_id=str(it.get("tid", "") or ""),
                    position=int(it.get("pos", 0) or 0),
                    market_value=int(it.get("mv", 0) or 0),
                    amount=amount,
                    offer_id=oid,
                    bidder_id=str(offer.get("u", "") or ""),
                    # Unser eigener Listungspreis (`prc`, dasselbe Feld wie in
                    # market_rows_from_raw) — Emils Preis, nicht der Marktwert.
                    listing_price=int(it.get("prc", 0) or 0),
                )
            )
    return offers


def accept_drafts(
    proposals: list[Proposal],
    offers_by_pid: dict[str, IncomingOffer],
    now: datetime,
    *,
    bench_ids: set[str] | frozenset[str] | None = None,
) -> list[IntentDraft]:
    """Accept-Proposals (decision.policies.propose_accepts) → Vorhaben-Entwürfe.

    Fenster: ACCEPT_VETO_WINDOW (45 min); ≥ 5 M gilt Emils 2-h-Mindestfenster
    (Ausnahme seit 20.10.: Windfall-Übergebote ≥ 115 % MW auf explizite
    Bankspieler bekommen WINDFALL_ACCEPT_VETO_WINDOW = 20 min, damit der
    Mitspieler nicht vor der Annahme zurückzieht).
    deadline None — die Offer-Gültigkeit liefert die API nicht; verschwindet
    das Angebot, superseded es der nächste Planner-Lauf (RESULT_OFFER_GONE).
    Die `offer_id` wandert in sources und von dort in die PlannedAction.
    """
    drafts: list[IntentDraft] = []
    for p in proposals:
        if p.action.action_type != "accept" or not p.action.player_id:
            continue
        pid = str(p.action.player_id)
        offer = offers_by_pid.get(pid)
        amount = int(p.action.amount or 0)
        is_bench_windfall = (
            bench_ids is not None
            and pid in bench_ids
            and offer is not None
            and offer.market_value > 0
            and amount >= offer.market_value * ACCEPT_WINDFALL_FLOOR
        )
        if is_bench_windfall:
            window = WINDFALL_ACCEPT_VETO_WINDOW
        else:
            window = VETO_WINDOW if amount >= BIG_AMOUNT else ACCEPT_VETO_WINDOW
        sources: dict[str, Any] = {
            "score": round(float(p.score), 4),
            "rule_id": p.rule_id,
            "requires_approval": bool(p.requires_approval),
            "offer_id": p.action.offer_id or "",
            **(
                {"market_value": offer.market_value, "bidder_id": offer.bidder_id}
                if offer is not None
                else {}
            ),
        }
        if offer is not None and offer.meets_asking_price:
            # Preis erreicht (16.09.): Der Listungspreis ist Emils Entscheidung
            # — der Executor führt das Vorhaben als `intent-accept-manual` aus
            # (action_for liest `asking_price`), damit weder der Starter-Schutz
            # autonomer Pfade noch der T-120-Floor greift: Olise zu 100 M soll
            # auch dann gehen, wenn er aufgestellt ist. Das 2-h-Veto-Fenster ab
            # 5 M bleibt. BEWUSST KEIN `manual`-Marker (Review 17.09.): manuelle
            # Zeilen fasst der Planner nie an — ein zurückgezogenes oder erhöhtes
            # Angebot würde dann als failed enden und die Annahme für den Tag
            # sperren. So bleibt die Zeile Planner-Eigentum (supersede/update).
            sources["asking_price"] = int(offer.listing_price)
            sources["emil_go"] = (
                f"Listungspreis {_fmt_m(offer.listing_price)} von Emil am Listing gesetzt; "
                f"Angebot {_fmt_m(amount)} erreicht ihn (Offer {offer.offer_id})."
            )
        drafts.append(
            IntentDraft(
                kind="accept",
                player_id=pid,
                player_name=p.player_name or pid,
                team_id=offer.team_id if offer else "",
                position=offer.position if offer else 0,
                amount=amount,
                rationale=p.rationale,
                sources=sources,
                listing_type="offer",
                deadline=None,
                execute_at=now + window,
            )
        )
    return drafts


def decline_drafts(
    proposals: list[Proposal],
    offers_by_pid: dict[str, IncomingOffer],
    now: datetime,
) -> list[IntentDraft]:
    """Decline-Proposals (decision.policies.propose_declines) → Vorhaben-Entwürfe.

    Fenster: ACCEPT_VETO_WINDOW (45 min), damit Emil bei Bedarf eingreifen kann,
    bevor das Lowball-Angebot aktiv abgelehnt wird.
    """
    drafts: list[IntentDraft] = []
    for p in proposals:
        if p.action.action_type != "decline" or not p.action.player_id:
            continue
        pid = str(p.action.player_id)
        offer = offers_by_pid.get(pid)
        amount = int(p.action.amount or 0)
        sources: dict[str, Any] = {
            "score": round(float(p.score), 4),
            "rule_id": p.rule_id,
            "requires_approval": False,
            "offer_id": p.action.offer_id or "",
            **(
                {"market_value": offer.market_value, "bidder_id": offer.bidder_id}
                if offer is not None
                else {}
            ),
        }
        drafts.append(
            IntentDraft(
                kind="decline",
                player_id=pid,
                player_name=p.player_name or pid,
                team_id=offer.team_id if offer else "",
                position=offer.position if offer else 0,
                amount=amount,
                rationale=p.rationale,
                sources=sources,
                listing_type="offer",
                deadline=None,
                execute_at=now + ACCEPT_VETO_WINDOW,
            )
        )
    return drafts


def own_listings_from_raw(raw: dict[str, Any], user_id: str) -> list[OwnListing]:
    """Eigene aktive Manager-Listings (`u.i == user_id`) aus dem rohen Markt-Payload."""
    if not user_id:
        return []
    out: list[OwnListing] = []
    for it in raw.get("it", []) or []:
        if str((it.get("u") or {}).get("i") or "") != str(user_id):
            continue
        if int(it.get("exs", 0) or 0) > 0:
            continue
        out.append(
            OwnListing(
                player_id=str(it.get("i")),
                player_name=f"{it.get('fn', '')} {it.get('n', '')}".strip(),
                team_id=str(it.get("tid", "") or ""),
                position=int(it.get("pos", 0) or 0),
                market_value=int(it.get("mv", 0) or 0),
                listing_price=int(it.get("prc", 0) or 0),
            )
        )
    return out


def delist_drafts(
    proposals: list[Proposal],
    own_by_pid: dict[str, OwnListing],
    now: datetime,
) -> list[IntentDraft]:
    """Delist-Proposals (decision.policies.propose_delists) → Vorhaben-Entwürfe."""
    drafts: list[IntentDraft] = []
    for p in proposals:
        if p.action.action_type != "delist" or not p.action.player_id:
            continue
        pid = str(p.action.player_id)
        info = own_by_pid.get(pid)
        drafts.append(
            IntentDraft(
                kind="delist",
                player_id=pid,
                player_name=p.player_name or pid,
                team_id=info.team_id if info else "",
                position=info.position if info else 0,
                amount=0,
                rationale=p.rationale,
                sources={
                    "score": round(float(p.score), 4),
                    "rule_id": p.rule_id,
                    "requires_approval": False,
                    **(
                        {
                            "listing_price": info.listing_price,
                            "market_value": info.market_value,
                        }
                        if info is not None
                        else {}
                    ),
                },
                listing_type="manager",
                deadline=None,
                execute_at=now + ACCEPT_VETO_WINDOW,
            )
        )
    return drafts


def accept_squad_floor(now: datetime, deadline: datetime | None) -> int:
    """Kader-Floor für Annahmen: 11 im T-120-Fenster, sonst 0 (nur Positions-Minima).

    Dieselbe Zweiteilung wie der Executor-Preflight (L-095, 09.09.): Innerhalb
    T-120 kostet jeder Leerplatz 100 Punkte, davor ist der Kader ein Portfolio,
    das nachgefüllt wird. Vorher verlangte der Planner IMMER ≥ 12 Spieler vor
    einer Annahme — bei einem 11er-Kader konnte damit kein einziges Angebot je
    angenommen werden, auch nicht eines zu Emils Preis (16.09.).
    """
    if deadline is None:
        return 0
    inside = _as_utc(deadline) - now <= timedelta(hours=EXPOSURE_GUARD_HOURS)
    return MIN_SQUAD_SIZE if inside else 0


#: Berichtsquelle der Meldungen „Angebot unter Preis" — Dedupe-Schlüssel ist
#: die Offer-ID in `meta`; eine zweite Meldung gibt es nur für ein neues Angebot.
BELOW_ASKING_SOURCE = "worker:intent_planner:below_asking"


def record_below_asking(
    offers: list[IncomingOffer], session: Session | None = None
) -> list[IncomingOffer]:
    """Noch nicht gemeldete Angebote unter Preis als Bericht speichern und zurückgeben.

    Ein Angebot gilt als gemeldet, wenn ein Bericht mit derselben Offer-ID UND
    demselben Betrag existiert (ein nachgebessertes Angebot desselben Bieters
    hat eine neue Kombination und wird erneut gemeldet). Persistenzfehler
    werden geloggt, nie geworfen — dann wird schlimmstenfalls doppelt gemeldet.
    """
    if not offers:
        return []
    own = session is None
    sess = session or SessionLocal()
    try:
        seen: set[tuple[str, int]] = set()
        for row in sess.scalars(select(Report).where(Report.source == BELOW_ASKING_SOURCE)):
            meta = row.meta or {}
            seen.add((str(meta.get("offer_id", "")), int(meta.get("amount", 0) or 0)))
        fresh: list[IncomingOffer] = []
        for o in offers:
            if (o.offer_id, o.amount) in seen:
                continue
            fresh.append(o)
            save_report(
                "planner",
                f"Angebot unter Preis: {o.player_name or o.player_id} {_fmt_m(o.amount)}",
                format_below_asking_notice([o]),
                BELOW_ASKING_SOURCE,
                meta={
                    "offer_id": o.offer_id,
                    "player_id": o.player_id,
                    "amount": int(o.amount),
                    "asking_price": int(o.listing_price),
                    "market_value": int(o.market_value),
                    "bidder_id": o.bidder_id,
                },
                session=sess,
            )
        return fresh
    except Exception:  # noqa: BLE001 — Meldung darf den Planner nie brechen
        _log.warning("Angebote unter Preis konnten nicht verbucht werden.", exc_info=True)
        return list(offers)
    finally:
        if own:
            sess.close()


def format_below_asking_notice(offers: list[IncomingOffer]) -> str:
    """Meldung für Angebote unter Emils Listungspreis (nie autonom angenommen)."""
    lines = ["**Angebote unter Preis** — nicht angenommen, Entscheidung liegt bei Emil:"]
    for o in offers:
        pct = (o.amount / o.market_value * 100) if o.market_value > 0 else 0.0
        lines.append(
            f"• {o.player_name or o.player_id}: {_fmt_m(o.amount)} geboten "
            f"({pct:.0f}% MW), Preis {_fmt_m(o.listing_price)} — Offer {o.offer_id}"
        )
    lines.append(
        "-# Annehmen: Preis am Listing senken oder in der Zentrale manuell annehmen."
    )
    return _clip("\n".join(lines))


def failed_listings_from_raw(
    raw: dict[str, Any], user_id: str, now: datetime
) -> list[FailedListing]:
    """Gescheiterte EIGENE Listings aus dem rohen Markt-Payload (Doktrin-Satz 3).

    Ein Listing gilt als gescheitert, wenn (L-081, Empirie 31.08.: vier
    Listings, sechs Tage, null Angebote):
    - Verkäufer `u.i` = wir (eigenes Manager-Listing),
    - KEIN Countdown läuft (`exs` 0/fehlend — Kickbase-Listings rollen von
      allein aus und sind nie unser Problem),
    - `ofs[]` leer ist (jedes vorhandene Angebot gehört dem Accept-Pfad —
      derselbe Lauf erzeugt dann statt des Verkaufs ein Annahme-Vorhaben),
    - der Listing-Zeitpunkt `dt` älter als ZOMBIE_LISTING_AGE (48 h) ist.
    Ohne lesbares `dt` kein Alters-Urteil → konservativ überspringen. Die
    Quelle ist bewusst in DIESER Funktion isoliert (Muster
    incoming_offers_from_raw): ändert sich der Fundort, ändert sich eine Stelle.
    """
    failed: list[FailedListing] = []
    for it in raw.get("it", []) or []:
        if str((it.get("u") or {}).get("i") or "") != str(user_id):
            continue
        if int(it.get("exs", 0) or 0) > 0:
            continue
        if it.get("ofs", []) or []:
            continue
        dt_raw = it.get("dt")
        if not isinstance(dt_raw, str):
            continue
        try:
            listed_at = datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        age = now - listed_at
        if age <= ZOMBIE_LISTING_AGE:
            continue
        mv = int(it.get("mv", 0) or 0)
        prc = int(it.get("prc", 0) or 0)
        if mv > 0 and prc > int(mv * 1.05):
            # Schaufenster-/Anker-Listing (z. B. 125 % MW oder Emils Wunschpreis):
            # dient nur für Mitspieler-Übergebote und darf nach 48 h nie per
            # Sofortverkauf an den Computer verramscht werden.
            continue
        failed.append(
            FailedListing(
                player_id=str(it.get("i")),
                player_name=f"{it.get('fn', '')} {it.get('n', '')}".strip(),
                team_id=str(it.get("tid", "") or ""),
                position=int(it.get("pos", 0) or 0),
                market_value=mv,
                listed_at=listed_at,
                age_hours=age.total_seconds() / 3600,
            )
        )
    return failed


def failed_listing_drafts(
    proposals: list[Proposal],
    failed_by_pid: dict[str, FailedListing],
    now: datetime,
) -> list[IntentDraft]:
    """Sofortverkaufs-Proposals gescheiterter Listings → Vorhaben-Entwürfe.

    Fenster wie der Accept-Pfad: 45 min Veto-Karte, ≥ 5 M Emils 2-h-Fenster.
    deadline None — das Listing läuft nicht ab, es steht nur tot herum;
    verschwindet der Spieler aus Kader oder Markt, superseded der nächste
    Planner-Lauf (Entwurf entsteht dann nicht mehr).
    """
    drafts: list[IntentDraft] = []
    for p in proposals:
        if p.action.action_type != "sell" or p.rule_id != "listing_failed":
            continue
        if not p.action.player_id:
            continue
        pid = str(p.action.player_id)
        info = failed_by_pid.get(pid)
        amount = int(p.action.amount or 0)
        window = VETO_WINDOW if amount >= BIG_AMOUNT else ACCEPT_VETO_WINDOW
        drafts.append(
            IntentDraft(
                kind="sell",
                player_id=pid,
                player_name=p.player_name or pid,
                team_id=info.team_id if info else "",
                position=info.position if info else 0,
                amount=amount,
                rationale=p.rationale,
                sources={
                    "score": round(float(p.score), 4),
                    "rule_id": p.rule_id,
                    "requires_approval": bool(p.requires_approval),
                    **(
                        {
                            "listing_age_h": round(info.age_hours, 1),
                            "listed_at": info.listed_at.isoformat(),
                        }
                        if info is not None
                        else {}
                    ),
                },
                listing_type="manager",
                deadline=None,
                execute_at=now + window,
            )
        )
    return drafts


# ------------------------------------------------------------------ Planner


@dataclass(frozen=True)
class Schedule:
    listing_type: str  # kickbase | manager
    deadline: datetime | None
    execute_at: datetime
    sniper: bool = False


def schedule_for(now: datetime, amount: int, row: MarketRow | None) -> Schedule:
    """Regeln §1 als reine Funktion über (jetzt, Betrag, Listing).

    - Kickbase-Listing (exs > 0): deadline = jetzt + exs, execute_at = deadline − 15 min.
    - Sniper: Restzeit < 30 min → execute_at = jetzt.
    - ≥ 5 M: execute_at = max(execute_at, jetzt + 2 h), aber nie nach deadline − 15 min
      (Sniper gewinnt nur unter 30 min).
    - Manager-Listing (Verkäufer-Objekt, kein Countdown) und Listings ohne exs:
      deadline None, execute_at = jetzt + 2 h.
    - Kein Listing-Bezug (list/sell): deadline None, execute_at = jetzt + 2 h.
    """
    rules = load_agent_rules()
    custom_veto = (
        timedelta(minutes=max(5, int(rules.veto_minutes)))
        if rules.is_custom_active()
        else VETO_WINDOW
    )
    default_at = now + custom_veto
    if row is None:
        return Schedule("kickbase", None, default_at)
    if row.is_manager or not row.expires_in_s or row.expires_in_s <= 0:
        return Schedule("manager" if row.is_manager else "kickbase", None, default_at)
    deadline = now + timedelta(seconds=row.expires_in_s)
    latest = deadline - EXECUTE_BEFORE_DEADLINE
    if deadline - now < SNIPER_WINDOW:
        return Schedule("kickbase", deadline, now, sniper=True)
    execute_at = latest
    if amount >= BIG_AMOUNT:
        execute_at = min(max(execute_at, default_at), latest)
    return Schedule("kickbase", deadline, execute_at)


@dataclass(frozen=True)
class IntentDraft:
    """Ein geplantes Vorhaben VOR dem DB-Abgleich (reines Ergebnis von plan_intents)."""

    kind: str  # bid | list | sell
    player_id: str
    player_name: str
    team_id: str
    position: int
    amount: int
    rationale: str
    sources: dict[str, Any]
    listing_type: str
    deadline: datetime | None
    execute_at: datetime
    sniper: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.player_id)


def _kind_label(kind: str) -> str:
    return {
        "bid": "Gebot", "list": "Listing", "sell": "Sofortverkauf", "accept": "Annahme",
        "decline": "Ablehnung", "cancel_bid": "Gebots-Rückzug", "delist": "Listing-Rückzug",
    }.get(kind, kind)


def _draft_from(
    p: Proposal,
    kind: str,
    now: datetime,
    row: MarketRow | None,
    *,
    name: str,
    team_id: str,
    position: int,
    extra_sources: dict[str, Any],
) -> IntentDraft:
    amount = int(p.action.amount or 0)
    sched = schedule_for(now, amount, row)
    sources: dict[str, Any] = {
        "score": round(float(p.score), 4),
        "rule_id": p.rule_id,
        "requires_approval": bool(p.requires_approval),
        "league_id": p.action.league_id,
    }
    if kind == "bid" and getattr(p.action, "purchase_review", None) is not None:
        sources["purchase_review"] = deepcopy(p.action.purchase_review)
    sources.update({k: v for k, v in extra_sources.items() if v is not None})
    if row is not None:
        sources["listing_price"] = row.price
        if row.is_manager and row.seller_name:
            sources["seller"] = row.seller_name
    if sched.sniper:
        sources["sniper"] = True
    return IntentDraft(
        kind=kind,
        player_id=str(p.action.player_id or ""),
        player_name=name,
        team_id=team_id,
        position=position,
        amount=amount,
        rationale=p.rationale,
        sources=sources,
        listing_type=sched.listing_type,
        deadline=sched.deadline,
        execute_at=sched.execute_at,
        sniper=sched.sniper,
    )


def _dispo_sale(review: Any) -> dict | None:
    """Gekoppelter Sofortverkauf eines Tauschs auf Planungs-Dispo (sonst None)."""
    sale = review.get("swap_sale") if isinstance(review, dict) else None
    return sale if isinstance(sale, dict) and isinstance(sale.get("player_id"), str) else None


def _backed_swap_pairs(session: Session) -> set[tuple[str, str]]:
    """(Verkaufsspieler, Ziel) aller Dispo-Tauschverkäufe, die noch warten oder liefen.

    Nur ein wartender (`planned`/`executing`) oder bereits ausgeführter Verkauf deckt
    einen Tausch (Review 23.09.2026, Befund 4). Übersprungen, gescheitert, abgelaufen
    oder abgeräumt deckt er nichts mehr — auch nicht für andere Käufe desselben Plans.
    """
    rows = session.scalars(
        select(Intent).where(
            Intent.kind == "sell",
            Intent.status.in_((STATUS_PLANNED, STATUS_EXECUTING, STATUS_EXECUTED)),
        )
    )
    return {
        (str(row.player_id), str((row.sources or {}).get("swap_for")))
        for row in rows
        if (row.sources or {}).get("swap_dispo") is True
    }


def _backed_swap_review(review: Any, backed: set[tuple[str, str]]) -> Any:
    """Kaufbeleg ohne Tausch-Anrechnungen, deren gekoppelter Verkauf nicht mehr wartet.

    Unverändert (dasselbe Objekt), wenn jede Tauschzeile gedeckt ist. Sonst eine Kopie
    ohne die ungedeckten Zeilen: Der Kaufbeleg rechnet dann wieder nur echtes Geld —
    im Planner, im Intent-Executor und im Preflight des ActionExecutors.
    """
    plan = review.get("team_plan") if isinstance(review, dict) else None
    rows = plan.get("swap_sales") if isinstance(plan, dict) else None
    if not isinstance(rows, list):
        return review
    kept = [
        row for row in rows
        if not isinstance(row, dict)
        or (row.get("player_id"), row.get("for_player_id")) in backed
    ]
    if len(kept) == len(rows):
        return review
    stripped = deepcopy(review)
    stripped["team_plan"]["swap_sales"] = kept
    return stripped


def plan_intents(plan: Plan, market_rows: list[MarketRow], now: datetime) -> list[IntentDraft]:
    """Plan-Vorschläge → Vorhaben-Entwürfe (reine Funktion, keine DB, keine API).

    buys → `bid` (nur mit Markt-Listing; eigenes offenes Gebot ⇒ kein Vorhaben),
    sells → `list`/`sell` gemäß Proposal.action.action_type (eigenes Listing am
    Markt ⇒ kein neues Listing-Vorhaben). Quellen: Score, Elf-Score/LI-Status
    aus plan.market_candidates (Kaufpfad), KB-Startelf-Stufe aus Markt/Kader.
    """
    rows = {r.player_id: r for r in market_rows}
    cands = {c.player_id: c for c in plan.market_candidates}
    squad = {sp.player.id: sp.player for sp in plan.squad}
    drafts: list[IntentDraft] = []

    recovery = (getattr(plan, "team_plan", None) or {}).get("funding_recovery") or {}
    for item in recovery.get("actions", []):
        proof = item.get("funding_review")
        pid = str(item.get("player_id") or "")
        row = rows.get(pid)
        if not isinstance(proof, dict) or row is None or row.our_bid != item.get("amount_eur"):
            continue
        try:
            expiry = datetime.fromisoformat(proof["abandon_until"])
            valid_until = datetime.fromisoformat(proof["valid_until"])
        except (KeyError, TypeError, ValueError):
            continue
        execute_at = now + VETO_WINDOW
        if (expiry.tzinfo is None or valid_until.tzinfo is None
                or now >= min(valid_until, expiry - EXECUTE_BEFORE_DEADLINE)):
            continue
        drafts.append(IntentDraft(
            kind="cancel_bid", player_id=pid, player_name=row.player_name or pid,
            team_id=row.team_id, position=row.position, amount=item["amount_eur"],
            rationale="Ungedecktes Gebot vollständig aufgeben; bis Auktionsende kein neues Gebot.",
            sources={"league_id": plan.league_id, "offer_id": item["offer_id"],
                     "funding_review": deepcopy(proof)},
            listing_type="kickbase", deadline=expiry - EXECUTE_BEFORE_DEADLINE,
            execute_at=execute_at,
        ))

    for p in plan.buys:
        if p.action.action_type != "bid" or not p.action.player_id:
            continue
        pid = str(p.action.player_id)
        row = rows.get(pid)
        if row is None or row.our_bid is not None or row.is_ours:
            continue  # weg vom Markt / eigenes Gebot / eigenes Listing
        cand = cands.get(pid)
        li_status = None
        if cand is not None and cand.injury_severity:
            li_status = f"out ({cand.injury_severity}): {cand.injury_reason}".strip()
        drafts.append(
            _draft_from(
                p, "bid", now, row,
                name=p.player_name or row.player_name or (cand.name if cand else pid),
                team_id=row.team_id or (cand.team_id if cand else ""),
                position=row.position or (cand.position if cand else 0),
                extra_sources={
                    "elf_score": round(cand.lineup_score, 4) if cand else None,
                    "li_status": li_status,
                    "kb_prob": row.kb_prob,
                    # Kader-Bedarf (26.08.): muss den Umweg über die
                    # Intent-Zeile überleben, sonst fällt das Gebot beim
                    # Executor zurück auf die Standard-Kappe (110 % statt 135 %).
                    # None wird von _draft_from herausgefiltert.
                    "squad_need": True if p.action.squad_need else None,
                },
            )
        )

    latest = (
        _as_utc(plan.matchday_deadline_at) - EXECUTE_BEFORE_DEADLINE
        if plan.matchday_deadline_at is not None
        else None
    )
    for p in plan.sells:
        kind = p.action.action_type
        if kind not in ("list", "sell") or not p.action.player_id:
            continue
        pid = str(p.action.player_id)
        row = rows.get(pid)
        if kind == "list" and row is not None and row.is_ours:
            continue  # steht schon als eigenes Listing am Markt
        player = squad.get(pid)
        draft = _draft_from(
            p, kind, now, None,
            name=p.player_name
            or (f"{player.first_name} {player.last_name}".strip() if player else pid),
            team_id=player.team_id if player else "",
            position=int(player.position) if player else 0,
            extra_sources={"kb_prob": player.lineup_prob if player else None},
        )
        if p.rule_id == "sell_deficit_cover":
            if latest is not None and latest <= now:
                continue
            veto = (
                DISPO_SMOOTHING_VETO
                if draft.amount < BIG_AMOUNT
                else max(DISPO_SMOOTHING_VETO, VETO_WINDOW)
            )
            if latest is not None:
                draft = replace(draft, deadline=latest, execute_at=min(now + veto, latest))
            else:
                draft = replace(draft, execute_at=now + veto)
        drafts.append(draft)
    # Tausch-Paare (02.09.): Gebot auf den Ersatz + Sofortverkauf des Verlierers,
    # der auf genau seinen Ersatz UND einen zusätzlichen Kaderplatz wartet.
    # Nach dem ersten Abgang darf das zweite Paar denselben Größen-Floor nutzen;
    # swap_for verhindert, dass ein fremder Zuschlag den Verkauf auslöst.
    # Scheitert das Gebot, wird das Paar im nächsten Lauf nicht mehr vorgeschlagen
    # und der wartende Verkauf ordentlich superseded.
    squad_size = len(plan.squad)
    for sw in plan.swaps:
        pid_in = str(sw.bid.action.player_id or "")
        row = rows.get(pid_in)
        if latest is None or row is None or row.our_bid is not None or row.is_ours:
            continue
        cand = cands.get(pid_in)
        bid_draft = _draft_from(
            sw.bid, "bid", now, row,
            name=sw.bid.player_name or row.player_name or pid_in,
            team_id=row.team_id or (cand.team_id if cand else ""),
            position=row.position or (cand.position if cand else 0),
            extra_sources={
                "swap_out": sw.faller.player_id,
                "elf_score": round(cand.lineup_score, 4) if cand else None,
                "kb_prob": row.kb_prob,
            },
        )
        if bid_draft.deadline is None and latest is not None:
            bid_latest = (
                _as_utc(plan.matchday_deadline_at)
                - timedelta(hours=EXPOSURE_GUARD_HOURS)
                - timedelta(minutes=5)
                if plan.matchday_deadline_at is not None
                else latest
            )
            exec_at = (
                min(bid_draft.execute_at, max(now, bid_latest))
                if bid_latest <= bid_draft.execute_at
                else bid_draft.execute_at
            )
            bid_draft = replace(bid_draft, deadline=latest, execute_at=min(exec_at, latest))
        drafts.append(bid_draft)
        f = sw.faller
        need = squad_size + 1
        trend_reason = next(
            (s.reason for s in f.signals if s.name == "trend"), "Marktwert fällt."
        )
        drafts.append(
            IntentDraft(
                kind="sell",
                player_id=f.player_id,
                player_name=f.name,
                team_id=f.team_id,
                position=int(f.position),
                amount=int(sw.sell_amount),
                rationale=(
                    f"{f.name}: Tausch-Verkauf — {trend_reason} Geht, sobald "
                    f"{sw.bid.player_name or pid_in} im Kader steht (Kader ≥ {need}); "
                    f"Sofortverkauf zum aktuellen Marktwert."
                ),
                sources={
                    "league_id": plan.league_id,
                    "score": round(float(f.total_score), 4),
                    "rule_id": "swap_sell",
                    "requires_approval": int(sw.sell_amount) > BIG_AMOUNT,
                    "after_squad_size": need,
                    "swap_for": pid_in,
                    # Planungs-Dispo (Emil 23.09.2026): Dieser Verkauf finanziert sein
                    # Gebot mit — ohne ihn gibt es kein Gebot (upsert_intents,
                    # execute_intent), und der nächste Plan rechnet ihn an.
                    **(
                        {"swap_dispo": True}
                        if _dispo_sale(sw.bid.action.purchase_review) is not None
                        else {}
                    ),
                },
                listing_type="manager",
                deadline=latest,
                execute_at=now,
            )
        )
    return drafts


#: Veto-Fenster einer Dispo-Glättung unter 5 M: Emil kann den Sofortverkauf überspringen.
#: Ab 5 M gilt wie in `schedule_for` mindestens `VETO_WINDOW` (2 h, CLAUDE.md §3).
DISPO_SMOOTHING_VETO = timedelta(minutes=30)
#: Wie weit zurück der Planner nach einem gescheiterten Dispo-Verkauf sucht.
DISPO_SMOOTHING_LOOKBACK = timedelta(days=14)


def _is_dispo_sale_row(row: Intent) -> bool:
    sources = row.sources or {}
    return row.kind == "sell" and not sources.get("manual") and (
        sources.get("swap_dispo") is True or sources.get("rule_id") == DISPO_SMOOTHING_RULE
    )


def latest_dispo_sales(session: Session, league_id: str, now: datetime) -> dict[str, Intent]:
    """Je Verkaufsspieler das jüngste Dispo-Verkaufsvorhaben (Tausch oder Glättung)."""
    latest: dict[str, Intent] = {}
    rows = session.scalars(
        select(Intent)
        .where(Intent.kind == "sell", Intent.updated_at >= now - DISPO_SMOOTHING_LOOKBACK)
        .order_by(Intent.updated_at.desc(), Intent.id.desc())
    )
    for row in rows:
        saved_league = (row.sources or {}).get("league_id")
        if not _is_dispo_sale_row(row) or (
            saved_league is not None and str(saved_league) != str(league_id)
        ):
            continue
        latest.setdefault(str(row.player_id), row)
    return latest


def dispo_smoothing_pair(
    session: Session, row: Intent, *, squad_pids: set[str], now: datetime,
) -> Intent | None:
    """Das gekoppelte Gebot, wenn genau dieses Paar eine Glättung trägt (sonst None).

    Zweites Review 23.09.2026, Auflage 1 — alle vier Bedingungen, sonst keine Glättung:
    (a) das verknüpfte Dispo-Gebot ist ausgeführt; (b) das Ziel kam aus genau dieser
    Auktion in den Kader: Zuschlag vermerkt (`record_dispo_win`) oder jetzt noch in der
    Karenz nach dem Listingende — ein späterer manueller Kauf nach verlorener Auktion
    zählt nicht; (c) kein Verkauf dieses Paars wurde von Emil übersprungen — ein Veto
    sperrt das Paar dauerhaft, nicht nur 24 h; (d) der gekoppelte Tauschverkauf wurde
    nicht abgeräumt (verlorene Auktion, Plan-Ende): nur gescheitert oder abgelaufen.
    """
    target = (row.sources or {}).get("swap_for")
    pid = str(row.player_id)
    if not isinstance(target, str) or pid not in squad_pids or target not in squad_pids:
        return None
    linked = _linked_swap_bid(session, row, target)
    if linked is None or linked.status != STATUS_EXECUTED or linked.deadline is None:
        return None  # (a)
    if not (linked.sources or {}).get(SWAP_WON_KEY) and not _in_win_window(linked, now):
        return None  # (b)
    pair = [
        r for r in session.scalars(
            select(Intent).where(Intent.kind == "sell", Intent.player_id == pid)
        )
        if _is_dispo_sale_row(r) and (r.sources or {}).get("swap_for") == target
    ]
    roots = [
        r for r in pair
        if (r.sources or {}).get("swap_dispo") is True
        and _as_utc(r.created_at) <= _as_utc(linked.created_at)
    ]
    if not roots:
        return None
    root = max(roots, key=lambda r: (_as_utc(r.created_at), r.id))
    since = (_as_utc(root.created_at), root.id)
    if any(
        r.status == STATUS_VETOED for r in pair if (_as_utc(r.created_at), r.id) >= since
    ):
        return None  # (c)
    if root.status not in (STATUS_FAILED, STATUS_EXPIRED):
        return None  # (d)
    return linked


def dispo_smoothing_drafts(session: Session, plan: Plan, now: datetime) -> list[IntentDraft]:
    """Dispo-Glättung (Review 23.09.2026, Befund 2b): Sofortverkauf nach gescheitertem Tausch.

    Nur wenn das echte Konto negativ ist, der jüngste Dispo-Verkauf des Spielers nicht
    mehr offen ist und `dispo_smoothing_pair` das konkrete Paar trägt (ausgeführtes
    Gebot, Zuschlag aus genau dieser Auktion, kein Veto Emils, Verkauf nicht wegen
    verlorener Auktion abgeräumt): dann wieder DERSELBE Spieler, nie ein anderer und nie
    ein geschützter, zum frischen Marktwert. Veto-Fenster wie `schedule_for`: 30 min,
    ab 5 M mindestens 2 h, nie nach Frist − 15 min. Eine offene Glättung wird jeden Lauf
    neu bestätigt (sonst räumte der Abgleich sie ab). Tageskontingent und Sperren prüft
    `upsert_intents` wie bei jedem Verkauf — dann meldet der Ergebnis-Wächter
    „Dispo-Glättung gescheitert".
    """
    budget = getattr(plan, "budget", None)
    deadline = plan.matchday_deadline_at
    if type(budget) is not int or budget >= 0 or deadline is None:
        return []
    latest = _as_utc(deadline) - EXECUTE_BEFORE_DEADLINE
    if latest <= now:
        return []
    squad = {str(sp.player.id): sp.player for sp in plan.squad}
    protected = protected_player_ids() | DEFAULT_GUARDRAILS.protected_player_ids
    drafts = []
    for pid, row in latest_dispo_sales(session, plan.league_id, now).items():
        sources = row.sources or {}
        target = sources.get("swap_for")
        smoothing = sources.get("rule_id") == DISPO_SMOOTHING_RULE
        if (
            not isinstance(target, str) or pid not in squad or target not in squad
            or pid in protected
            or row.status in (STATUS_EXECUTING, STATUS_EXECUTED)
            or (row.status == STATUS_PLANNED and not smoothing)  # Tausch-Verkauf wartet
            or dispo_smoothing_pair(session, row, squad_pids=set(squad), now=now) is None
        ):
            continue
        player = squad[pid]
        value = int(getattr(player, "market_value", 0) or 0)
        if value <= 0:
            continue
        veto = DISPO_SMOOTHING_VETO
        if value >= BIG_AMOUNT:
            veto = max(veto, VETO_WINDOW)  # Auflage 2: ab 5 M mindestens 2 h
        name = f"{getattr(player, 'first_name', '')} {getattr(player, 'last_name', '')}"
        drafts.append(
            IntentDraft(
                kind="sell",
                player_id=pid,
                player_name=name.strip() or row.player_name or pid,
                team_id=str(getattr(player, "team_id", "") or row.team_id or ""),
                position=int(getattr(player, "position", 0) or row.position or 0),
                amount=value,
                rationale=(
                    f"Dispo-Glättung: Tausch für {target} ist zugeschlagen, das Konto steht "
                    f"bei {budget:,} €, der gekoppelte Verkauf kam nicht zustande "
                    f"({row.result or row.status}). Derselbe Spieler geht per Sofortverkauf "
                    "zum aktuellen Marktwert.".replace(",", ".")
                ),
                sources={
                    "league_id": plan.league_id,
                    "rule_id": DISPO_SMOOTHING_RULE,
                    "dispo_smoothing": True,
                    "swap_for": target,
                    # Ziel steht schon im Kader: Bedingung erfüllt, der Starter-Schutz
                    # autonomer Pfade gilt für diesen Tausch-Abgang nicht (wie beim Tausch).
                    "after_squad_size": len(plan.squad),
                    "requires_approval": value > BIG_AMOUNT,
                    "smooths_intent_id": row.id,
                },
                listing_type="manager",
                deadline=latest,
                execute_at=min(now + veto, latest),
            )
        )
    return drafts


def _smoothing_block_lifted(session: Session, draft: IntentDraft, now: datetime) -> bool:
    """Heute gescheiterter Tausch-Verkauf sperrt die Glättung desselben Spielers nicht.

    Alles andere sperrt weiter: Emils Veto (auch auf eine frühere Glättung), ein heute
    schon gescheiterter Glättungsversuch (höchstens einer je Tag) oder ein laufender/
    ausgeführter Verkauf.
    """
    if draft.sources.get("rule_id") != DISPO_SMOOTHING_RULE:
        return False
    rows = session.scalars(
        select(Intent).where(
            Intent.kind == draft.kind,
            Intent.player_id == draft.player_id,
            Intent.status.in_(
                (STATUS_EXECUTED, STATUS_EXECUTING, STATUS_FAILED, STATUS_VETOED)
            ),
        )
    )
    for row in rows:
        if row.status == STATUS_VETOED:
            live_deadline = row.deadline is not None and _as_utc(row.deadline) > now
            if _as_utc(row.updated_at) >= now - VETO_BLOCK or live_deadline:
                return False
            continue
        if _as_utc(row.updated_at).date() != now.date():
            continue
        sources = row.sources or {}
        if row.status != STATUS_FAILED or sources.get("swap_dispo") is not True:
            return False
    return True


@dataclass
class PlannerStats:
    created: list[Intent] = field(default_factory=list)
    updated: list[Intent] = field(default_factory=list)
    unchanged: list[Intent] = field(default_factory=list)
    superseded: list[Intent] = field(default_factory=list)
    skipped_cap: list[IntentDraft] = field(default_factory=list)
    skipped_blocked: list[IntentDraft] = field(default_factory=list)
    #: Gewählte Engine-Gebote, die der Abgleich verwarf: (Spieler, Grund). Am 02.10.2026
    #: verschwand so das Obermair-Gebot ohne jede Spur (Befund 03.10.2026).
    dropped: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        text = (
            f"{len(self.created)} neu, {len(self.updated)} aktualisiert, "
            f"{len(self.superseded)} superseded"
        )
        if self.skipped_cap:
            text += f", {len(self.skipped_cap)} über Tages-Cap"
        if self.dropped:
            text += f", {len(self.dropped)} verworfen"
        return text

    def drop(self, player_id: str, reason: str) -> None:
        """Verworfenes Engine-Gebot merken und ins Log schreiben (nie mehr stumm)."""
        self.dropped.append((player_id, reason))
        _log.warning("Planner: Engine-Gebot %s verworfen — %s", player_id, reason)


def open_intents(session: Session) -> list[Intent]:
    """Alle offenen (`planned`) Vorhaben."""
    return list(
        session.execute(select(Intent).where(Intent.status == STATUS_PLANNED)).scalars().all()
    )


#: Veto-Sperre: ein von Emil übersprungenes Vorhaben wird für dieselbe
#: (kind, player_id) so lange nicht neu angelegt — sonst käme die Karte stündlich wieder.
VETO_BLOCK = timedelta(hours=24)
#: Sekunden-Drift der aus `exs` neu gerechneten Deadline zählt nicht als „näher gerückt".
_DRIFT_TOLERANCE = timedelta(seconds=60)


def blocked_keys(session: Session, now: datetime) -> set[tuple[str, str]]:
    """(kind, player_id), für die der Planner KEIN neues Vorhaben anlegen darf.

    - heute (UTC) `executed`/`executing`/`failed`: kein Doppel-Vorhaben direkt nach
      einem Write bzw. keine Fehlschlag-Schleife (der Executor-Idempotenz-Schlüssel
      gilt je UTC-Tag; ein neuer Tag darf neu versuchen).
    - `vetoed` innerhalb der letzten 24 h oder mit noch laufender Deadline: Emils
      »Überspringen« gilt für das Listing bzw. den Tag, nicht nur bis zum nächsten Lauf.
    """
    today = now.date()
    out: set[tuple[str, str]] = set()
    rows = session.execute(
        select(Intent).where(
            Intent.status.in_(
                (STATUS_EXECUTED, STATUS_EXECUTING, STATUS_FAILED, STATUS_VETOED)
            )
        )
    ).scalars()
    for r in rows:
        stamp = _as_utc(r.updated_at)
        if r.status == STATUS_VETOED:
            sources = r.sources or {}
            live_deadline = (
                r.deadline is not None
                and _as_utc(r.deadline) > now
                and sources.get("rule_id") != "swap_sell"
            )
            if stamp >= now - VETO_BLOCK or live_deadline:
                out.add((r.kind, r.player_id))
        elif stamp.date() == today:
            out.add((r.kind, r.player_id))
    return out


def blocked_bid_target_ids(session: Session, now: datetime) -> frozenset[str]:
    """Kaufziele, die der Mannschaftsplan heute nicht einplanen darf (Paket 1, P0-1).

    Bewusst ENGER als `blocked_keys`: nur `vetoed` (24 h bzw. bis zur Deadline) und
    heute `failed`. Heute `executed`/`executing` fehlt absichtlich — ein stehendes
    Gebot ist Wirklichkeit und bleibt im Plan als gebundene Option; schlösse man es
    aus, plante der Optimierer für denselben Platz einen Doppelkauf. Für NEUE
    Vorhaben sperrt `blocked_keys` diese Ziele weiterhin.

    Anlass (ABSCHLUSS-2026-09-18 §3.1): Pieringer und Al Dakhil wurden trotz Sperre
    erneut eingeplant — der Plan meldete einen besetzten Platz, den der Planner gar
    nicht anlegen durfte.
    """
    today = now.date()
    out: set[str] = set()
    rows = session.execute(
        select(Intent).where(
            Intent.kind == "bid", Intent.status.in_((STATUS_FAILED, STATUS_VETOED))
        )
    ).scalars()
    for r in rows:
        stamp = _as_utc(r.updated_at)
        if r.status == STATUS_VETOED:
            live_deadline = r.deadline is not None and _as_utc(r.deadline) > now
            if stamp >= now - VETO_BLOCK or live_deadline:
                out.add(str(r.player_id))
        elif stamp.date() == today:
            out.add(str(r.player_id))
    return frozenset(out)


def _draft_changes(row: Intent, draft: IntentDraft) -> tuple[dict[str, Any], dict[str, Any]]:
    """Was sich am offenen Vorhaben gegenüber dem frischen Entwurf ändert (reine Funktion).

    Rückgabe (timing, values): `timing` = execute_at/deadline — wandern nur NACH VORN
    (Deadline rückt näher, Sniper), nie nach hinten: Emils »Jetzt ausführen« und das
    beim ersten Lauf gesetzte Manager-Fenster bleiben; Sekunden-Drift aus `exs` zählt
    nicht. `values` = Betrag/Begründung/Quellen/Anzeige — gelten nur, solange Emil
    NICHT angepasst hat (adjusted_from NULL; erzwungen per konditionalem UPDATE).
    """
    timing: dict[str, Any] = {}
    execute_at = _as_utc(draft.execute_at)
    if execute_at < _as_utc(row.execute_at) - _DRIFT_TOLERANCE:
        timing["execute_at"] = execute_at
    deadline = None if draft.deadline is None else _as_utc(draft.deadline)
    if deadline is not None and (
        row.deadline is None or deadline < _as_utc(row.deadline) - _DRIFT_TOLERANCE
    ):
        timing["deadline"] = deadline
    values: dict[str, Any] = {}
    for attr in ("amount", "rationale", "listing_type"):
        if getattr(row, attr) != getattr(draft, attr):
            values[attr] = getattr(draft, attr)
    if (row.sources or {}) != draft.sources:
        values["sources"] = draft.sources
    for attr in ("player_name", "team_id", "position"):
        new = getattr(draft, attr)
        if new and getattr(row, attr) != new:
            values[attr] = new
    return timing, values


def _apply_draft(session: Session, row: Intent, draft: IntentDraft, now: datetime) -> bool:
    """Offenes Vorhaben mit dem Entwurf abgleichen; True, wenn sich etwas änderte.

    Schreibt wie die API mit konditionalen UPDATEs (WHERE status='planned' …):
    ein Veto/Adjust, das zwischen Laden und Schreiben eintrifft, gewinnt immer —
    Emils Betrag wird nie vom Plan überschrieben (WHERE adjusted_from IS NULL).
    """
    timing, values = _draft_changes(row, draft)
    changed = False
    if timing:
        res = session.execute(
            update(Intent)
            .where(Intent.id == row.id, Intent.status == STATUS_PLANNED)
            .values(**timing, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        changed |= res.rowcount > 0
    if row.adjusted_from is None and values:
        res = session.execute(
            update(Intent)
            .where(
                Intent.id == row.id,
                Intent.status == STATUS_PLANNED,
                Intent.adjusted_from.is_(None),
            )
            .values(**values, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        changed |= res.rowcount > 0
    elif row.adjusted_from is not None and draft.sniper and not (row.sources or {}).get("sniper"):
        # Sniper-Marker ist Zustands-, keine Bewertungsinformation — darf rein.
        res = session.execute(
            update(Intent)
            .where(Intent.id == row.id, Intent.status == STATUS_PLANNED)
            .values(sources={**(row.sources or {}), "sniper": True}, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        changed |= res.rowcount > 0
    if changed:
        session.expire(row)
    return changed


def _new_intent(draft: IntentDraft, now: datetime) -> Intent:
    return Intent(
        created_at=now,
        updated_at=now,
        kind=draft.kind,
        player_id=draft.player_id,
        player_name=draft.player_name,
        team_id=draft.team_id,
        position=draft.position,
        amount=draft.amount,
        rationale=draft.rationale,
        sources=draft.sources,
        listing_type=draft.listing_type,
        deadline=draft.deadline,
        execute_at=draft.execute_at,
        status=STATUS_PLANNED,
    )


def insert_cover_sells(
    session: Session, drafts: list[IntentDraft], now: datetime
) -> list[Intent]:
    """Deckungsverkaufs-Vorhaben des Pfand-Einlösers anlegen — OHNE Abgleich.

    Bewusst NICHT upsert_intents: Das würde alle offenen Vorhaben ohne Entwurf
    superseden. Hier gilt nur: existiert schon ein offenes Vorhaben für den
    Spieler (egal welcher Art) oder ist der Schlüssel gesperrt (blocked_keys),
    wird nichts angelegt — Idempotenz über die (kind, player_id)-Invariante.
    """
    by_pid = {r.player_id for r in open_intents(session)}
    blocked = blocked_keys(session, now)
    created: list[Intent] = []
    for draft in drafts:
        if draft.player_id in by_pid or draft.key in blocked:
            continue
        row = _new_intent(draft, now)
        session.add(row)
        by_pid.add(draft.player_id)
        created.append(row)
    if created:
        session.commit()
    return created


def hysteresis_keep_pids(
    candidates: list[ExplosionCandidate],
    need_positions: set[int],
    guardrails: Guardrails = DEFAULT_GUARDRAILS,
    *,
    purchase_reviews: dict[str, dict] | None = None,
    now: datetime | None = None,
) -> set[str]:
    """Spieler, deren Kauf-Score in der Hysterese-Toleranz liegt — reine Funktion.

    Ein Vorhaben entsteht nur ÜBER der Kauf-Schwelle (propose_buys); gehalten
    wird es, solange der Score nicht weiter als `plan_hysteresis_margin`
    DARUNTER fällt. Die Schwelle ist positionsabhängig (Positions-Not senkt sie
    um position_need_tolerance — dieselbe Rechnung wie in propose_buys), damit
    ein Not-Torwart nicht an der Normal-Schwelle gemessen wird. Kein Kandidat
    im Plan-Universum (Zombie, eigenes Gebot) ⇒ nicht in der Menge ⇒ das
    Vorhaben räumt wie bisher.
    """
    if purchase_reviews is not None:
        stamp = now or datetime.now(UTC)
        return {
            pid for pid, review in purchase_reviews.items()
            if purchase_review_veto(
                review, player_id=pid,
                amount=reviewed_bid_amount(review),
                now=stamp,
            ) is None
        }
    keep: set[str] = set()
    for c in candidates:
        threshold = guardrails.buy_score_threshold
        if need_positions and c.position in need_positions:
            threshold -= guardrails.position_need_tolerance
        if c.total_score >= threshold - guardrails.plan_hysteresis_margin:
            keep.add(c.player_id)
    return keep


def _dispo_sale_coupled(
    review: dict, pid: str, drafts: list[IntentDraft],
    by_key: dict[tuple[str, str], Intent], blocked: set[tuple[str, str]],
) -> bool:
    """Ein Dispo-Tauschgebot gilt nur mit seinem wartenden Verkauf (Emil 23.09.2026).

    Gekoppelt heißt: ein offenes Verkaufs-Vorhaben oder ein Verkaufsentwurf genau
    dieses Spielers, der auf genau dieses Ziel wartet. Ein gesperrter (heute
    vetoierter/gescheiterter) Verkauf koppelt nichts. Gebote ohne Tausch: immer True.
    """
    sale = _dispo_sale(review)
    if sale is None:
        return True
    key = ("sell", sale["player_id"])
    row = by_key.get(key)
    if row is not None:
        if (row.sources or {}).get("swap_for") == pid:
            return True
        if row.adjusted_from is not None or (row.sources or {}).get("manual"):
            return False
    return key not in blocked and any(
        d.key == key and d.sources.get("swap_for") == pid and d.sources.get("swap_dispo")
        for d in drafts
    )


def upsert_intents(
    session: Session,
    drafts: list[IntentDraft],
    now: datetime,
    *,
    market_pids: set[str] | None = None,
    counts: TodayCounts | None = None,
    guardrails: Guardrails = DEFAULT_GUARDRAILS,
    keep_pids: set[str] | frozenset[str] | None = None,
    purchase_reviews: dict[str, dict] | None = None,
    active_bid_targets: dict[str, str] | None = None,
    squad_pids: set[str] | None = None,
) -> PlannerStats:
    """Entwürfe mit den offenen Vorhaben abgleichen (Idempotenz §2) und committen.

    - vorhanden (kind, player_id, planned) → aktualisieren (_apply_draft)
    - fehlt → neu, solange der Tages-Cap (heute executed + offen geplant) Platz lässt
    - offen, aber kein Entwurf mehr → `superseded` („nicht mehr am Markt" wenn der
      Spieler aus dem Markt verschwunden ist, sonst „nicht mehr im Plan")
    - gesperrte Schlüssel (blocked_keys: heute executed/failed, frisches Veto) →
      kein neues Vorhaben (skipped_blocked)

    Plan-Hysterese (01.09., Emils Auftrag nach dem Nacht-Flattern): `keep_pids`
    sind Spieler, deren aktueller Kauf-Score noch in der Toleranz unter der
    Schwelle liegt (hysteresis_keep_pids). Ein GEBOTS-Vorhaben ohne frischen
    Entwurf wird für sie NICHT als „nicht mehr im Plan" geräumt, solange der
    Spieler am Markt steht — Raab/Boniface/Goto pendelten in der Nacht je an
    der Not-Schwelle rein und raus, drei Karten Lärm, null Ausführung. Einmal
    geplant heißt: kleine Score-Wackler werfen es nicht mehr um; es endet durch
    Ausführung, Veto, Listing-Ablauf oder echten Score-Verfall unter die Marge.
    „Nicht mehr am Markt" und der Accept-Pfad räumen unverändert.
    """
    stats = PlannerStats()
    by_key = {(r.kind, r.player_id): r for r in open_intents(session)}
    blocked = blocked_keys(session, now)
    current_reviews: dict[str, dict] = {}
    if purchase_reviews is not None:
        # Der gemeinsame Finanzierungsbeleg begrenzt die Auswahl; die Zahl der
        # Kaufziele und Verkäuferarten ist keine eigenständige Mannschaftsregel.
        current_amounts: dict[str, int] = {}
        # Tausch-Anrechnung nur mit wartendem gekoppeltem Verkauf (Befund 4): offen in
        # der DB oder als Entwurf dieses Laufs, der nicht gesperrt ist.
        backed = _backed_swap_pairs(session) | {
            (d.player_id, str(d.sources.get("swap_for")))
            for d in drafts
            if d.kind == "sell" and d.sources.get("swap_dispo") and d.key not in blocked
        }
        bid_drafts = {d.player_id: d for d in drafts if d.kind == "bid"}
        ordered_pids = list(dict.fromkeys([
            *bid_drafts,
            *(r.player_id for r in by_key.values() if r.kind == "bid"),
        ]))
        for pid in ordered_pids:
            row, draft = by_key.get(("bid", pid)), bid_drafts.get(pid)
            if row is not None and (row.sources or {}).get("manual"):
                continue
            review = deepcopy(_backed_swap_review(purchase_reviews.get(pid), backed))
            amount = (
                row.amount if row is not None and row.adjusted_from is not None
                else draft.amount if draft is not None else row.amount if row is not None else None
            )
            if row is not None and row.adjusted_from is not None and isinstance(review, dict):
                old = (row.sources or {}).get("purchase_review")
                old_cap = old.get("max_bid_eur") if isinstance(old, dict) else None
                new_cap = review.get("max_bid_eur")
                if type(old_cap) is int and old_cap > 0 and type(new_cap) is int:
                    # Eine manuelle Betragsanpassung darf den zuvor geprüften
                    # absoluten Höchstpreis auch beim nächsten Plan nicht erhöhen.
                    review["max_bid_eur"] = min(old_cap, new_cap)
            problem = purchase_review_veto(review, player_id=pid, amount=amount, now=now)
            if problem is not None:
                if draft is not None:
                    stats.drop(pid, problem)
                continue
            assert isinstance(review, dict)
            assert amount is not None
            if not _dispo_sale_coupled(review, pid, drafts, by_key, blocked):
                # Dispo-Gebot ohne seinen gekoppelten Verkauf: nie allein
                if draft is not None:
                    stats.drop(pid, "Dispo-Gebot ohne gekoppelten Tausch-Verkauf")
                continue
            current_reviews[pid] = review
            current_amounts[pid] = amount
        selection = team_selection_veto(current_reviews, current_amounts, now=now)
        if selection is not None:
            for pid, review in current_reviews.items():
                if is_engine_review(review) and pid in bid_drafts:
                    stats.drop(pid, selection)
            current_reviews = {pid: r for pid, r in current_reviews.items()
                               if not is_engine_review(r)}
        drafts = [
            d for d in drafts
            if d.kind != "bid" or d.player_id in current_reviews
            or (by_key.get(d.key) is not None and (by_key[d.key].sources or {}).get("manual"))
        ]
    retained_swaps = {
        key for key, pending_row in by_key.items()
        if _keep_waiting_swap_sell(
            session, pending_row, now=now, squad_pids=squad_pids,
            reviews=current_reviews, active_targets=active_bid_targets or {},
            market_pids=market_pids, active_reviews=purchase_reviews or {},
        )
    }
    # Ein normaler Verkaufsentwurf darf den verknüpften, wartenden Abgang
    # nicht in einen sofortigen Verkauf ohne Ersatzbedingung verwandeln.
    drafts = [d for d in drafts if d.key not in retained_swaps]
    # Tausch-Paare (Dispo, Emil 23.09.2026): Verkauf und Gebot entstehen nur gemeinsam.
    # Ein neuer Tausch-Verkauf, dessen Gebot die Kuratierung verworfen hat, entsteht
    # gar nicht erst (kein verwaister Abgang). Beim Dispo-Tausch wird der Verkauf VOR
    # seinem Gebot angelegt; fällt er weg (Kontingent, Sperre), fällt das Gebot mit.
    kept_bids = {d.player_id for d in drafts if d.kind == "bid"}
    drafts = sorted(
        (
            d for d in drafts
            if not (d.kind == "sell" and d.sources.get("rule_id") == "swap_sell"
                    and by_key.get(d.key) is None
                    and d.sources.get("swap_for") not in kept_bids
                    and by_key.get(("bid", str(d.sources.get("swap_for")))) is None)
        ),
        key=lambda d: 0 if d.kind == "sell" and d.sources.get("swap_dispo") else 1,
    )
    dispo_sales = {
        pid: sale["player_id"] for pid, review in current_reviews.items()
        if (sale := _dispo_sale(review)) is not None
    }
    wanted = {d.key for d in drafts}
    counts = counts or TodayCounts()

    for key, row in list(by_key.items()):
        if (row.sources or {}).get("manual") or is_trade(row.sources):
            # Manuelle Vorhaben (MCP/Mandat/Emil, Zwei-DB-Fix 24.08.) gehören nicht dem
            # Planner: nie als „nicht mehr im Plan" räumen — sie enden nur durch
            # Ausführung, Veto, Ablauf oder Emils Anpassung. Handelsvorhaben (Quelle
            # `trade`, Emil 01.10.2026) gehören dem Handels-Autopiloten, der sie räumt.
            continue
        reviewed_bid = row.kind == "bid" and purchase_reviews is not None
        if reviewed_bid and row.player_id not in current_reviews:
            result = RESULT_NOT_CURATED
        elif key in retained_swaps:
            stats.unchanged.append(row)
            continue
        elif key in wanted:
            continue
        elif reviewed_bid and (market_pids is None or row.player_id in market_pids):
            _refresh_purchase_review(session, row, current_reviews[row.player_id], now)
            (stats.superseded if row.status == STATUS_SUPERSEDED else stats.unchanged).append(row)
            continue
        elif (row.sources or {}).get("cover"):
            continue
        elif (
            row.kind == "bid"
            and keep_pids is not None
            and row.player_id in keep_pids
            and (market_pids is None or row.player_id in market_pids)
        ):
            stats.unchanged.append(row)
            continue
        elif (
            row.kind in ("bid", "delist")
            and market_pids is not None
            and row.player_id not in market_pids
        ):
            result = RESULT_NOT_ON_MARKET
        elif row.kind in ("accept", "decline"):
            result = RESULT_OFFER_GONE
        elif _dispo_auction_lost(session, row, squad_pids=squad_pids, now=now):
            result = RESULT_SWAP_LOST
        else:
            result = RESULT_NOT_IN_PLAN
        # Konditional wie die API: ein Veto dazwischen bleibt Veto (rowcount 0 ⇒ nichts).
        res = session.execute(
            update(Intent)
            .where(Intent.id == row.id, Intent.status == STATUS_PLANNED)
            .values(status=STATUS_SUPERSEDED, result=result, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        del by_key[key]
        if res.rowcount > 0:
            session.expire(row)
            stats.superseded.append(row)

    open_buys = sum(1 for k in by_key if k[0] == "bid")
    open_lists = sum(1 for k in by_key if k[0] == "list")
    open_sells = sum(1 for k in by_key if k[0] in ("sell", "accept"))
    for draft in drafts:
        row = by_key.get(draft.key)
        if row is not None:
            if (row.sources or {}).get("manual") or is_trade(row.sources):
                stats.unchanged.append(row)
                continue
            changed = _apply_draft(session, row, draft, now)
            if row.kind == "bid" and row.player_id in current_reviews:
                _refresh_purchase_review(session, row, current_reviews[row.player_id], now)
            if row.status == STATUS_SUPERSEDED:
                stats.superseded.append(row)
            else:
                (stats.updated if changed else stats.unchanged).append(row)
            continue
        if draft.key in blocked and not _smoothing_block_lifted(session, draft, now):
            stats.skipped_blocked.append(draft)
            if draft.kind == "bid":
                stats.drop(draft.player_id, "heute übersprungen oder gescheitert — gesperrt")
            continue
        if draft.kind == "sell" and draft.sources.get("swap_dispo"):
            target = str(draft.sources.get("swap_for") or "")
            if by_key.get(("bid", target)) is None and (
                ("bid", target) in blocked
                or counts.buys + open_buys >= guardrails.daily_buy_cap
            ):
                stats.skipped_blocked.append(draft)  # das Gebot käme nicht: kein Abgang
                continue
        if draft.kind == "bid" and draft.player_id in dispo_sales:
            coupled = by_key.get(("sell", dispo_sales[draft.player_id]))
            if coupled is None or (coupled.sources or {}).get("swap_for") != draft.player_id:
                stats.skipped_blocked.append(draft)  # ohne gekoppelten Verkauf kein Dispo
                stats.drop(draft.player_id,
                           "gekoppelter Tausch-Verkauf nicht angelegt (Kontingent oder Sperre)")
                continue
        if draft.kind == "cancel_bid" and (
            draft.deadline is None or draft.execute_at >= draft.deadline
            or draft.execute_at >= datetime.fromisoformat(
                draft.sources["funding_review"]["valid_until"])
        ):
            # Ein bestehendes Vorhaben hat sein Veto-Fenster bereits begonnen;
            # ein neues darf die verbleibende Frist nicht als Sofortpfad nutzen.
            stats.skipped_blocked.append(draft)
            continue
        if draft.kind == "bid":
            if counts.buys + open_buys >= guardrails.daily_buy_cap:
                stats.skipped_cap.append(draft)
                continue
            open_buys += 1
        elif draft.kind == "list":
            # L-077 vollendet (31.08.): Listings gegen ihr eigenes Kontingent —
            # hoffende Listings dürfen keine Verkaufs-/Accept-Slots verstopfen.
            if counts.lists + open_lists >= guardrails.daily_list_cap:
                stats.skipped_cap.append(draft)
                continue
            open_lists += 1
        elif draft.kind in ("sell", "accept"):
            if counts.sells + open_sells >= guardrails.daily_sell_cap:
                stats.skipped_cap.append(draft)
                continue
            open_sells += 1
        row = _new_intent(draft, now)
        if draft.kind == "bid" and draft.player_id in current_reviews:
            row.sources = {
                **(row.sources or {}),
                "purchase_review": deepcopy(current_reviews[draft.player_id]),
            }
        session.add(row)
        by_key[draft.key] = row
        stats.created.append(row)
    session.commit()
    return stats


def release_funding_pending(session: Session, recovery: dict, league_id: str, now: datetime):
    """Nur noch unveränderte automatische Reservierungen lokal freigeben."""
    released = []
    for intent_id in recovery.get("released_pending_ids", []):
        if type(intent_id) is not int:
            continue
        row = session.get(Intent, intent_id)
        # Handelsvorhaben deckt ihr Verkauf (Handels-Autopilot): die Engine gibt sie nicht frei.
        if (row is None or row.kind != "bid" or row.status != STATUS_PLANNED
                or row.adjusted_from is not None or (row.sources or {}).get("manual")
                or is_trade(row.sources)
                or str((row.sources or {}).get("league_id", league_id)) != league_id):
            continue
        result = session.execute(update(Intent).where(
            Intent.id == row.id, Intent.status == STATUS_PLANNED,
            Intent.adjusted_from.is_(None), Intent.amount == row.amount,
        ).values(status=STATUS_SUPERSEDED, updated_at=now,
                 result="Deckung reparieren: nicht ausgeführte Kaufreservierung freigegeben."))
        if result.rowcount:
            released.append(row.id)
    return released


def is_swap_backed_full_lineup(team_plan: Any) -> bool:
    """Reiner 1-zu-1-Dispo-Tausch bei voller Startelf (>= 11 Plätze): alle Käufe sind durch
    gekoppelte Tausch-Sofortverkäufe gedeckt, sodass Bank-Handelsflips parallel laufen dürfen."""
    if not isinstance(team_plan, dict):
        return False
    purchases = team_plan.get("purchase_ids")
    if not isinstance(purchases, list | tuple) or not purchases:
        return False
    if int(team_plan.get("baseline_filled_slots") or 0) < 11:
        return False
    swap_sales = team_plan.get("swap_sales")
    if not isinstance(swap_sales, list | tuple):
        return False
    covered = {
        str(s.get("for_player_id"))
        for s in swap_sales
        if isinstance(s, dict) and s.get("for_player_id")
    }
    return bool(covered) and {str(p) for p in purchases} <= covered


def yield_trade_bids(
    session: Session,
    league_id: str,
    now: datetime,
    *,
    only_player_ids: set[str] | None = None,
) -> list[Intent]:
    """Engine zuerst (Befund 03.10.2026, Emils GO): Wählt der Mannschaftsplan einen Kauf,
    weichen geplante Handelsgebote — noch nicht bei Kickbase abgegeben, also ohne Bindung.

    Konditional wie die API (ein Veto dazwischen gewinnt) und ohne Tagessperre (`superseded`):
    Will die Engine nichts mehr kaufen, darf der Autopilot denselben Spieler wieder planen.
    Bleiben: laufende oder ausgeführte Handelsgebote, Verkäufe von Handelsspielern und von
    Emil angepasste Beträge (sein Wille). Bei einem reinen 1-zu-1-Dispo-Tausch auf voller
    11er-Elf (`only_player_ids`) weichen nur Handelsgebote auf denselben Zielspieler."""
    cleared: list[Intent] = []
    for row in open_intents(session):
        sources = row.sources or {}
        if (row.kind != "bid" or not is_trade(sources)
                or str(sources.get("league_id", league_id)) != league_id):
            continue
        if only_player_ids is not None and str(row.player_id) not in only_player_ids:
            continue
        # Emils Anpassung prüft allein die Bedingung im UPDATE — so gewinnt sie auch, wenn sie
        # zwischen Lesen und Räumen eintrifft (Gegenstück: trading/trade_book.firm).
        result = session.execute(
            update(Intent)
            .where(Intent.id == row.id, Intent.status == STATUS_PLANNED,
                   Intent.adjusted_from.is_(None))
            .values(status=STATUS_SUPERSEDED, result=RESULT_TRADE_YIELDS, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount:
            session.expire(row)
            cleared.append(row)
    return cleared


def trade_bid_snapshot(session: Session) -> tuple[frozenset[int], int]:
    """Vor dem Planbau: Ids der offenen Handelsgebots-Vorhaben und die höchste Vorhaben-Id. So
    erkennt `late_firm_trade_bids` eine Ausführung während des Planbaus am Vorhaben selbst, nicht
    am Zeitstempel — der Executor stempelt den Beginn seines Takts (Codex-Nachprüfung)."""
    open_ids = frozenset(
        row.id for row in session.scalars(select(Intent).where(
            Intent.kind == "bid", Intent.status.in_((STATUS_PLANNED, STATUS_EXECUTING)),
        ))
        if is_trade(row.sources)
    )
    return open_ids, int(session.scalar(select(func.max(Intent.id))) or 0)


def late_firm_trade_bids(
    session: Session, league_id: str, counted: dict[str, int],
    open_before: frozenset[int], max_id: int,
) -> list[Intent]:
    """Handelsgebote, die jetzt fest sind (von Emil angepasst, in Ausführung oder während des
    Planbaus ausgeführt) und mehr binden, als der Plan je Spieler zählte (`Plan.trade_bound`).
    Dann rechnete der Plan dieses Geld als frei — seine Finanzierung ist überholt (Codex-Review
    03.10.2026). Maßstab ist der Betrag, den der Plan tatsächlich zählte, nicht ein früherer
    Stand: Senken vor und Erhöhen nach dem Lesen fällt so auch auf. Ausgeführte zählen nur, wenn
    sie vor dem Planbau noch offen waren oder danach entstanden; früher ausgeführte sah der Plan
    als Live-Gebot."""
    rows = session.scalars(select(Intent).where(
        Intent.kind == "bid",
        or_(
            Intent.status.in_((STATUS_PLANNED, STATUS_EXECUTING)),
            (Intent.status == STATUS_EXECUTED)
            & or_(Intent.id > max_id, Intent.id.in_(open_before)),
        ),
    ))
    late: list[Intent] = []
    for row in rows:
        sources = row.sources or {}
        if not is_trade(sources) or str(sources.get("league_id", league_id)) != league_id:
            continue
        firm = row.status != STATUS_PLANNED or row.adjusted_from is not None
        if firm and int(row.amount or 0) > counted.get(str(row.player_id), 0):
            late.append(row)
    return late


def _keep_waiting_swap_sell(
    session: Session,
    row: Intent,
    *,
    now: datetime,
    squad_pids: set[str] | None,
    reviews: dict[str, dict],
    active_reviews: dict[str, dict],
    active_targets: dict[str, str],
    market_pids: set[str] | None,
) -> bool:
    """Befristeten Abgang nur zum eindeutig verknüpften Kauf weiterführen."""
    source = row.sources or {}
    target = source.get("swap_for")
    if (
        row.kind != "sell" or source.get("rule_id") != "swap_sell" or source.get("manual")
        or not isinstance(target, str) or not target or target == row.player_id
        or row.deadline is None or _as_utc(row.deadline) <= now
    ):
        return False
    if source.get("swap_dispo") is True:
        keep = _keep_dispo_sale(session, row, target, now=now, squad_pids=squad_pids,
                                active_targets=active_targets)
        if keep is not None:
            return keep
    if squad_pids is None or row.player_id not in squad_pids:
        return False
    # Gegenseitige Spieler-IDs verbinden Kauf und Verkauf auch nach dem Zuschlag.
    # Ein neuerer fremder/manueller Kauf desselben Spielers gilt nicht als Ersatz
    # für das ursprüngliche Paar; ein Veto/Fehlschlag beendet die alte Bindung.
    linked = _linked_swap_bid(session, row, target)
    if linked is None:
        return False
    if linked.status not in (STATUS_PLANNED, STATUS_EXECUTING, STATUS_EXECUTED):
        return False
    if target in squad_pids:
        return linked.status in (STATUS_EXECUTING, STATUS_EXECUTED)
    if market_pids is not None and target not in market_pids:
        return False
    if linked.status == STATUS_EXECUTED and target not in active_targets:
        return False  # kein Zuschlag und kein offenes Angebot mehr: Paar beendet
    review = (reviews if linked.status == STATUS_PLANNED else active_reviews).get(target)
    if review is None and linked.status == STATUS_EXECUTING:
        review = (linked.sources or {}).get("purchase_review")
    return purchase_review_veto(
        review, player_id=target, amount=linked.amount, now=now,
    ) is None


#: Regel-ID der Dispo-Glättung (Review 23.09.2026, Befund 2b): neuer Sofortverkauf
#: desselben gekoppelten Spielers, wenn der Tausch-Verkauf nach dem Zuschlag scheiterte.
DISPO_SMOOTHING_RULE = "dispo_smoothing"

#: Karenz nach dem Ende einer Dispo-Auktion (Review 23.09.2026, Befund 1): Der Plan liest
#: den Kader VOR dem Markt; endet die Auktion dazwischen, fehlt das gewonnene Ziel im
#: Kader und im Markt. So lange gilt das Ende als noch nicht entschieden.
DISPO_SETTLEMENT_GRACE = timedelta(minutes=30)
#: Vermerk am ausgeführten Dispo-Gebot: Zuschlag aus genau diesem Paar gesehen (ISO-Zeit).
SWAP_WON_KEY = "swap_won_at"


def _linked_swap_bid(session: Session, row: Intent, target: str) -> Intent | None:
    """Jüngstes Gebot auf das Ziel, das genau diesen Abgang als Tauschpartner nennt."""
    linked = session.scalars(
        select(Intent).where(Intent.kind == "bid", Intent.player_id == target)
        .order_by(Intent.created_at.desc(), Intent.id.desc())
    ).first()
    if linked is None or (linked.sources or {}).get("swap_out") != row.player_id:
        return None
    sale_league = (row.sources or {}).get("league_id")
    bid_league = (linked.sources or {}).get("league_id")
    if sale_league is not None and bid_league is not None and sale_league != bid_league:
        return None
    return linked


def _keep_dispo_sale(
    session: Session, row: Intent, target: str, *, now: datetime,
    squad_pids: set[str] | None, active_targets: dict[str, str],
) -> bool | None:
    """Dispo-Tauschverkauf (Review 23.09.2026, Befund 1): Ist sein Gebot ausgeführt,
    bleibt er stehen, bis die Auktion entschieden ist — unabhängig davon, ob der neue
    Plan das Ziel noch auswählt (Detailabruf 503, KB-Stufe 4, unfit, Dispo-Frist).

    Endet nur, wenn der Verkaufsspieler nicht mehr im Kader steht (verkauft), das Gebot
    vor dem Auktionsende verschwunden ist (zurückgezogen) oder die Auktion seit
    `DISPO_SETTLEMENT_GRACE` ohne Zuschlag vorbei ist. None = Gebot (noch) nicht
    ausgeführt: dann gilt die normale Kopplung — fällt das geplante Gebot aus dem Plan,
    fällt der Verkauf mit.
    """
    linked = _linked_swap_bid(session, row, target)
    if linked is None or linked.status not in (STATUS_EXECUTING, STATUS_EXECUTED):
        return None
    if squad_pids is None:
        return True  # Kader unbekannt: nie einen laufenden Tausch abräumen
    if row.player_id not in squad_pids:
        return False
    if target in squad_pids:
        record_dispo_win(linked, now)
        return True
    end = None if linked.deadline is None else _as_utc(linked.deadline)
    if linked.status == STATUS_EXECUTING:
        return True
    # Zweites Review 23.09.2026, Auflage 4: Ein aktives eigenes Gebot auf das Ziel hält
    # den Verkauf nur bis zum Listingende DIESES Gebots. Danach ist es ein anderes Gebot
    # (z. B. Emils neues in der App) — der alte Verkauf läuft nicht mit.
    if target in active_targets and (end is None or now < end):
        return True
    return end is not None and end <= now < end + DISPO_SETTLEMENT_GRACE


def record_dispo_win(linked: Intent, now: datetime) -> bool:
    """Zuschlag genau dieses Dispo-Gebots vermerken (zweites Review 23.09.2026, Auflage 1b).

    Nur wer das Ziel spätestens `DISPO_SETTLEMENT_GRACE` nach dem Listingende des
    ausgeführten Gebots im Kader sieht, belegt den Zuschlag aus diesem Paar — solange
    das Gebot lief, war das Ziel nicht unseres, und in der Karenz kommt es auf keinem
    anderen Weg in den Kader. Der erste Beleg bleibt; der Aufrufer committet.
    """
    sources = linked.sources or {}
    if sources.get(SWAP_WON_KEY):
        return True
    if not _in_win_window(linked, now):
        return False
    linked.sources = {**sources, SWAP_WON_KEY: _as_utc(now).isoformat()}
    return True


def _in_win_window(linked: Intent, now: datetime) -> bool:
    return (
        linked.status == STATUS_EXECUTED and linked.deadline is not None
        and _as_utc(now) < _as_utc(linked.deadline) + DISPO_SETTLEMENT_GRACE
    )


def _dispo_auction_lost(
    session: Session, row: Intent, *, squad_pids: set[str] | None, now: datetime,
) -> bool:
    """Dispo-Tauschverkauf, dessen ausgeführtes Gebot ohne Zuschlag endete."""
    source = row.sources or {}
    target = source.get("swap_for")
    if (row.kind != "sell" or source.get("swap_dispo") is not True or squad_pids is None
            or not isinstance(target, str) or target in squad_pids):
        return False
    linked = _linked_swap_bid(session, row, target)
    return (
        linked is not None and linked.status == STATUS_EXECUTED
        and not (linked.sources or {}).get(SWAP_WON_KEY)
        and linked.deadline is not None and _as_utc(linked.deadline) <= now
    )


def _refresh_purchase_review(session: Session, row: Intent, review: dict, now: datetime) -> None:
    """Frische Belege übernehmen, ohne angepassten Betrag oder Nutzerhinweise zu ersetzen."""
    # Ein Adjust kann zwischen Plan-Lesen und Quellen-Update eintreffen.
    # Den tatsächlichen Betrag und dessen ursprüngliches Preislimit neu lesen.
    session.refresh(row)
    if row.status != STATUS_PLANNED:
        return
    refreshed = deepcopy(review)
    old = (row.sources or {}).get("purchase_review")
    old_cap = old.get("max_bid_eur") if isinstance(old, dict) else None
    if row.adjusted_from is not None and type(old_cap) is int and old_cap > 0:
        refreshed["max_bid_eur"] = min(refreshed["max_bid_eur"], old_cap)
    values: dict[str, Any] = {"updated_at": now}
    if purchase_review_veto(
        refreshed, player_id=row.player_id, amount=row.amount, now=now,
    ) is not None:
        values.update(status=STATUS_SUPERSEDED, result=RESULT_NOT_CURATED)
    else:
        values["sources"] = {**(row.sources or {}), "purchase_review": refreshed}
    session.execute(
        update(Intent)
        .where(
            Intent.id == row.id, Intent.status == STATUS_PLANNED, Intent.amount == row.amount,
            Intent.adjusted_from == row.adjusted_from,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    session.expire(row)


def today_intent_counts(session: Session, now: datetime) -> TodayCounts:
    """Heute (UTC) executed gelaufene Intent-Aktionen aus dem Audit-Log (Tages-Caps).

    Muster wie auto_trading.load_today_state: rule_id-Präfix 'intent-', Status
    'executed', Datumsprüfung in Python (SQLite: naive/aware gemischt).
    """
    today = now.date()
    buys = sells = lists = 0
    rows = session.query(TransferAction).filter(
        TransferAction.rule_id.like(f"{INTENT_PREFIX}%"),
        TransferAction.status == "executed",
    )
    for row in rows:
        if _as_utc(row.created_at).date() != today:
            continue
        if row.action_type == "bid":
            buys += 1
        elif row.action_type == "list":
            # L-077 vollendet (31.08.): Listings zählen getrennt.
            lists += 1
        elif row.action_type in ("sell", "accept"):
            sells += 1
    return TodayCounts(buys=buys, sells=sells, lists=lists)


def _intent_line(row: Intent) -> str:
    label = _kind_label(row.kind)
    big = " (groß)" if row.amount >= BIG_AMOUNT else ""
    return f"{label} {row.player_name or row.player_id} {_fmt_m(row.amount)}{big}"


def format_planner_report(
    stats: PlannerStats, armed: bool, *, discord_controls: bool = True
) -> str | None:
    """Bilanzzeile + Besonderheiten — None, wenn nichts Neues passiert ist.

    Meldungs-Umbau (Emil, 31.08.): Die je-Vorhaben-Zeilen für NEU und WEG sind
    raus — jedes neue Vorhaben ist seit dem 31.08. eine eigene Veto-Karte mit
    Buttons (Details + Zeiten stehen dort), und WEG zieht der Kehrer auf die
    Karte. Übrig bleibt, was KEINE Karte hat: die Bilanz, Cap-Ausfälle und der
    Unarmed-Hinweis.
    """
    if not (stats.created or stats.superseded or stats.skipped_cap):
        return None
    detail = "Details: Karten unten" if discord_controls else "Ausführung automatisch"
    lines = [f"**Steuerzentrale** — Planner: {stats.summary()} ({detail})"]
    for draft in stats.skipped_cap:
        lines.append(
            f"• CAP {_kind_label(draft.kind)} {draft.player_name} {_fmt_m(draft.amount)} — "
            "Tages-Cap erreicht, nicht angelegt"
        )
    if not armed:
        lines.append("-# AGENT_AUTO_INTENTS=0: Executor meldet ehrlich refused.")
    return _clip("\n".join(lines))


def _clip(text: str, limit: int = 1900) -> str:
    return text if len(text) <= limit else text[: limit - 2].rstrip() + " …"


# ------------------------------------------------- Discord-Karten (Spec 2026-08-31)

#: Abstand zwischen zwei Karten-Posts (Discord-Rate-Limit ~5 Nachrichten/5 s;
#: der Planner legt selten > 3 an). Tests drehen das auf 0.
CARD_POST_SPACING_S = 1.0


def card_jobs_for_created(rows: list[Intent], now: datetime) -> list[CardWork]:
    """Karten-Aufträge für NEUE Vorhaben — INNERHALB der Planner-Session bauen.

    Review-Fix (31.08. abends): Der frühere Post-Pfad las die ORM-Zeilen nach
    Session-Close und lebte nur davon, dass der Report-Formatter sie zufällig
    vorher anfasste (DetachedInstanceError, sobald das wegfiel). Embeds/Views
    sind reine Formatierung — sie entstehen jetzt strukturell dort, wo die
    Attribute sicher geladen sind; die Discord-I/O macht `apply_card_work`
    danach (derselbe Pfad wie der Kehrer, ein Code für Post und Edit).
    """
    return [
        CardWork(
            intent_id=int(row.id),
            msg_id=None,
            embed=format_intent_embed(row, now),
            view=build_intent_view(row),
            updated_at=row.updated_at,
        )
        for row in rows
    ]


def stale_cards(session: Session) -> list[Intent]:
    """Vorhaben mit Karte, deren Karte den DB-Stand nicht mehr zeigt.

    Sync-Formel (Spec §4.3): veraltet ⇔ `updated_at > discord_card_synced_at`
    (synced_at NULL zählt als veraltet). Prädikat in SQL (Review 31.08.): die
    Menge aller je gekarteten Vorhaben wächst über die Saison monoton — ein
    Python-Full-Scan alle 5 min skaliert nicht. Beide Spalten schreibt derselbe
    Codepfad im selben Format, der Vergleich ist deshalb spaltenrein möglich.
    """
    return list(
        session.execute(
            select(Intent).where(
                Intent.discord_msg_id.is_not(None),
                or_(
                    Intent.discord_card_synced_at.is_(None),
                    Intent.updated_at > Intent.discord_card_synced_at,
                ),
            )
        )
        .scalars()
        .all()
    )


#: Backfill-Mindestalter: Zeilen jünger als das überlässt der Kehrer dem
#: Planner-Post-Pfad (post_intent_cards läuft direkt nach der Anlage) — sonst
#: posteten beide im Renn-Fenster dieselbe Karte doppelt.
CARD_BACKFILL_MIN_AGE = timedelta(minutes=3)


def cards_needing_post(session: Session, now: datetime) -> list[Intent]:
    """Geplante Vorhaben, deren Karte nie entstand (Spec-§6-Nachtrag 31.08.).

    Das Review fand zwei reale Wege zur karten­losen Charge: ein transienter
    Discord-Fehler im Planner-Lauf und ein Tick vor `on_ready`. Der Kehrer
    postet solche Karten nach — aber NUR, wenn `discord_card_synced_at` leer
    ist: eine manuell gelöschte Karte (404-Pfad setzt synced_at beim Nullen)
    bleibt bewusst gelöscht, kein Repost.
    """
    rows = (
        session.execute(
            select(Intent).where(
                Intent.discord_msg_id.is_(None),
                Intent.discord_card_synced_at.is_(None),
                Intent.status == STATUS_PLANNED,
            )
        )
        .scalars()
        .all()
    )
    return [r for r in rows if _as_utc(r.created_at) <= now - CARD_BACKFILL_MIN_AGE]


@dataclass(frozen=True)
class CardWork:
    """Ein Karten-Auftrag, im Lock eingesammelt, außerhalb ausgeführt (Review 31.08.):
    Discord-I/O unter `_intent_lock` hielt die Sperre bei hängendem Discord
    minutenlang — Executor-Ticks fielen aus, der Planner serialisierte, fällige
    Sniper verpassten ihr Fenster. Jetzt trägt jeder Auftrag alles Nötige
    (Embed/View sind reine Formatierung) und die Sperre bleibt millisekundenkurz."""

    intent_id: int
    msg_id: str | None  # None ⇒ Backfill-Post statt Edit
    embed: Any
    view: Any
    updated_at: datetime  # Stand, den die Karte nach erfolgreichem I/O zeigt
    retire: bool = False  # Buttons entfernen und Message-Verknüpfung dauerhaft stilllegen


def collect_card_work(session: Session, now: datetime) -> list[CardWork]:
    """Im Lock: veraltete Karten + Backfill-Kandidaten als Aufträge einsammeln."""
    work: list[CardWork] = []
    for row in stale_cards(session):
        work.append(
            CardWork(
                intent_id=int(row.id),
                msg_id=str(row.discord_msg_id),
                embed=format_intent_embed(row, now),
                view=build_intent_view(row) if row.status == STATUS_PLANNED else None,
                updated_at=row.updated_at,
            )
        )
    for row in cards_needing_post(session, now):
        work.append(
            CardWork(
                intent_id=int(row.id),
                msg_id=None,
                embed=format_intent_embed(row, now),
                view=build_intent_view(row),
                updated_at=row.updated_at,
            )
        )
    return work


def collect_card_retire_work(session: Session, now: datetime) -> list[CardWork]:
    """Offene Discord-Steuerkarten ohne Veto am Intent dauerhaft deaktivieren."""
    rows = list(
        session.execute(
            select(Intent).where(
                Intent.discord_msg_id.is_not(None),
                Intent.status == STATUS_PLANNED,
            )
        )
        .scalars()
        .all()
    )
    return [
        CardWork(
            intent_id=int(row.id),
            msg_id=str(row.discord_msg_id),
            embed=format_intent_embed(row, now),
            view=None,
            updated_at=row.updated_at,
            retire=True,
        )
        for row in rows
    ]


async def apply_card_work(channel, work: list[CardWork]) -> int:
    """Außerhalb des Locks: Karten editieren/nachposten, Ergebnis kurz committen.

    Schreibt `synced_at` auf den im Lock EINGEFRORENEN `updated_at`-Stand —
    eine spätere Änderung ist danach wieder `updated_at > synced_at` und matcht
    im nächsten Tick (race-sicher per Wert, nicht per Sperre). 404 beim Edit:
    `discord_msg_id` nullen UND `synced_at` setzen (Marker „bewusst ohne
    Karte", schützt vor Backfill-Repost). Jeder Auftrag einzeln gefangen.
    """
    edit_card = getattr(channel, "edit_card", None)
    send_card = getattr(channel, "send_card", None)
    done = 0
    sent = 0
    for job in work:
        try:
            if job.msg_id is None and send_card is not None and sent > 0:
                # Abstand nur zwischen POSTS (Discord ~5 Nachrichten/5 s);
                # Edits desselben Laufs brauchen ihn nicht.
                await asyncio.sleep(CARD_POST_SPACING_S)
            if job.msg_id is not None:
                if edit_card is None:
                    continue
                ok = await edit_card(job.msg_id, embed=job.embed, view=job.view)
                values = (
                    {"discord_msg_id": None, "discord_card_synced_at": job.updated_at}
                    if job.retire or not ok
                    else {"discord_card_synced_at": job.updated_at}
                )
            else:
                if send_card is None:
                    continue
                sent += 1
                new_id = await send_card(embed=job.embed, view=job.view)
                if new_id is None:
                    continue  # Bot kennt den Kanal noch nicht — nächster Tick
                values = {
                    "discord_msg_id": str(new_id),
                    "discord_card_synced_at": job.updated_at,
                }
            with SessionLocal() as session:
                session.execute(
                    update(Intent)
                    .where(Intent.id == job.intent_id)
                    .values(**values)
                    .execution_options(synchronize_session=False)
                )
                session.commit()
            done += 1
        except Exception:  # noqa: BLE001 — Karten-Kosmetik: nächster Tick versucht erneut
            _log.warning(
                "Karten-Kehrer: Vorhaben %s nicht nachgezogen.", job.intent_id, exc_info=True
            )
    return done


async def handle_intent_action(verb: str, intent_id: int) -> IntentActionOutcome:
    """Discord-Button → gemeinsamer Aktions-Service (Spec §4.2), eigene Session.

    Läuft via `asyncio.to_thread` (Review 31.08.): Die synchrone SQLite-Arbeit
    (busy_timeout bis 30 s) blockierte sonst die Bot-Event-Loop — hielt der
    22:05-Snapshot gerade die Schreibsperre, war das sogar ein Selbst-Deadlock
    auf derselben Loop und Emils Veto ging nach 30 s still verloren. Im Thread
    darf das UPDATE ehrlich auf die Sperre warten, während Loop, Heartbeat und
    der sperrende Job weiterlaufen. Buttons mutieren NUR `intents`-Zeilen.
    """
    return await asyncio.to_thread(_handle_intent_action_sync, verb, intent_id)


def _handle_intent_action_sync(verb: str, intent_id: int) -> IntentActionOutcome:
    """Synchroner Kern: veto/adjust/execute-now mit actor='discord' (Spur in
    emil_note); typisierte Fehler werden in ein Outcome für die ephemere
    Antwort übersetzt."""
    now = datetime.now(UTC)
    armed = bool(get_settings().agent_auto_intents)
    with SessionLocal() as session:
        try:
            if verb == "exec":
                row = intent_actions.execute_intent_now(
                    session, intent_id, actor=intent_actions.ACTOR_DISCORD, now=now
                )
                return IntentActionOutcome(
                    verb=verb, intent_id=intent_id, ok=True, intent=row, armed=armed
                )
            if verb == "skip":
                row = intent_actions.veto_intent(
                    session, intent_id, note=None,
                    actor=intent_actions.ACTOR_DISCORD, now=now,
                )
                # Vormerkung statt Vollzug (Review 31.08.): Der Versuch läuft
                # gerade — status bleibt 'executing', outcome_reply erklärt das.
                return IntentActionOutcome(
                    verb=verb, intent_id=intent_id, ok=True, intent=row,
                    status=row.status, armed=armed,
                )
            if verb in ("p10", "p20"):
                before = session.get(Intent, intent_id)
                old_amount = int(before.amount or 0) if before is not None else 0
                row = intent_actions.adjust_intent(
                    session, intent_id, pct=10.0 if verb == "p10" else 20.0,
                    actor=intent_actions.ACTOR_DISCORD, now=now,
                )
                ceiling = int(int(row.adjusted_from or 0) * intent_actions.ADJUST_MAX_FACTOR)
                return IntentActionOutcome(
                    verb=verb, intent_id=intent_id, ok=True, intent=row,
                    old_amount=old_amount, new_amount=int(row.amount or 0), ceiling=ceiling,
                )
            return IntentActionOutcome(
                verb=verb, intent_id=intent_id, ok=False, error="unknown",
                detail=f"Unbekanntes Kommando '{verb}'.",
            )
        except intent_actions.IntentNotFound as exc:
            return IntentActionOutcome(
                verb=verb, intent_id=intent_id, ok=False, error="not_found", detail=exc.detail
            )
        except intent_actions.IntentNotPlanned as exc:
            # Frische Zeile mitgeben: die Karte kann sofort final gezogen werden.
            fresh = session.get(Intent, intent_id)
            return IntentActionOutcome(
                verb=verb, intent_id=intent_id, ok=False, error="not_planned",
                status=exc.status, intent=fresh, detail=exc.detail,
            )
        except intent_actions.IntentAdjustError as exc:
            error = "over_ceiling" if exc.ceiling is not None else "adjust"
            return IntentActionOutcome(
                verb=verb, intent_id=intent_id, ok=False, error=error, detail=exc.detail
            )


def filter_uncovered_bids(
    drafts: list[IntentDraft],
    *,
    deadline: datetime | None,
    cash_frei: int,
    existing_keys: set[tuple[str, str]] | frozenset[tuple[str, str]] = frozenset(),
) -> tuple[list[IntentDraft], int]:
    """Deadline-Draft-Filter (31.08., Maluze-Lehre; Review-Fassung) — reine Funktion.

    Ein Gebots-Entwurf, der zur geplanten Ausführungszeit absehbar am
    Executor-Freeze-Gate refused würde, wird gar nicht erst angelegt — der
    failed-Status sperrte den Spieler sonst via blocked_keys für den UTC-Tag.
    Statt einer eigenen Fenster-Formel fragt der Filter DIESELBE Funktion, die
    auch der Executor-Preflight benutzt (`bid_freeze_reason` — seit 09.09.
    einheitlich T-120; die frühere Eigenformel kannte nur halbe Fenster).

    Zwei Review-Korrekturen:
    - Summen-Prüfung: behaltene Gebots-Entwürfe binden dasselbe Bargeld —
      `committed` akkumuliert sie, zwei einzeln gedeckte Gebote passieren nicht
      mehr gemeinsam ungedeckt (chronologisch nach `execute_at`; die früheren
      gewinnen die Deckung).
    - `existing_keys`: Entwürfe zu (kind, player_id) mit bereits OFFENEM
      Vorhaben werden NIE gefiltert — sonst räumte der Supersede-Pfad ein
      Vorhaben ab, das Emil per +10/+20 % angepasst oder scharfgestellt hat.
      Ob so ein Bestands-Vorhaben gedeckt ist, entscheidet der Executor zur
      Tatzeit (nach etwaigen Verkäufen) — die alte Invariante. Sein Betrag
      zählt aber mit ins gebundene Bargeld.

    Verkäufe/Listings/Accepts sind nie betroffen (sie verbessern das Konto).
    """
    deadline_utc = _as_utc(deadline) if deadline is not None else None
    ordered = sorted(drafts, key=lambda d: (_as_utc(d.execute_at), d.key))
    committed = sum(d.amount for d in ordered if d.kind == "bid" and d.key in existing_keys)
    kept: list[IntentDraft] = []
    dropped = 0
    for d in ordered:
        if d.kind != "bid" or d.key in existing_keys:
            kept.append(d)
            continue
        reason = bid_freeze_reason(
            _as_utc(d.execute_at),
            deadline_utc,
            amount=d.amount,
            budget_after_bids=cash_frei - committed,
        )
        if reason is not None:
            dropped += 1
            continue
        committed += d.amount
        kept.append(d)
    # Ursprüngliche Reihenfolge wiederherstellen (Planner-Reports sind stabil).
    kept_ids = {id(d) for d in kept}
    return [d for d in drafts if id(d) in kept_ids], dropped


async def run_intent_planner(channel=None) -> str:
    """Planner-Lauf: Plan bauen, Markt lesen, Vorhaben anlegen/aktualisieren.

    Keine Kickbase-Writes. Rückgabe: Zusammenfassung („n neu, n aktualisiert,
    n superseded"); mit `channel` zusätzlich send_info mit kurzer Liste (nur
    wenn etwas Neues passiert ist — Updates allein bleiben leise).
    """
    # Sperrmenge VOR dem Planbau lesen (Paket 1, P0-1): Veto- und Fehlschlag-Ziele
    # dürfen keinen Platz im Mannschaftsplan belegen. Kurze Lese-Session, kein Lock.
    with SessionLocal() as _blocked_session:
        blocked_targets = blocked_bid_target_ids(_blocked_session, datetime.now(UTC))
        # Engine zuerst (Codex-Review 03.10.2026): offene Handelsgebote VOR dem Planbau merken —
        # wird eines während des Planbaus ausgeführt, erkennt der Planer das unter der Sperre.
        open_trade_bids, max_intent_id = trade_bid_snapshot(_blocked_session)
    plan = await build_plan(blocked_target_ids=blocked_targets)
    client, auth = await make_kickbase_client()
    user_id = plan.user_id or auth.user_id
    raw_market = await client._get(endpoints.market(plan.league_id))
    market_rows = market_rows_from_raw(raw_market, user_id)
    now = datetime.now(UTC)
    drafts = plan_intents(plan, market_rows, now)
    # Bestehende offene Vorhaben (kurze Lese-Session VOR dem Lock): deren Drafts
    # darf der Filter nie wegnehmen — sonst supersedet der Abgleich ein Vorhaben,
    # das Emil angepasst oder scharfgestellt hat (Review 31.08.).
    with SessionLocal() as _ro_session:
        _open = open_intents(_ro_session)
        existing_keys = {(r.kind, r.player_id) for r in _open}
        # Emils manuelle Verkäufe (auch bedingte) gehören nicht dem Planner: ein
        # Tausch-Verkauf desselben Spielers würde die Zeile sonst überschreiben
        # und manual/emil_go verlieren (02.09.).
        manual_sell_pids = {
            r.player_id for r in _open if r.kind == "sell" and (r.sources or {}).get("manual")
        }
        # Dispo-Glättung (Review 23.09.2026, Befund 2b): gescheiterter Tausch-Verkauf
        # nach dem Zuschlag bei Kontominus → derselbe Spieler erneut als Sofortverkauf.
        drafts += dispo_smoothing_drafts(_ro_session, plan, now)
    drafts = [
        d
        for d in drafts
        if not (
            d.kind == "sell"
            and d.sources.get("rule_id") in ("swap_sell", DISPO_SMOOTHING_RULE)
            and d.player_id in manual_sell_pids
        )
    ]
    drafts, dropped_uncovered = filter_uncovered_bids(
        drafts,
        deadline=plan.matchday_deadline_at,
        cash_frei=plan.plan_base - plan.momentum_reserve_eur,
        existing_keys=existing_keys,
    )
    # Begründetes Nichtstun (28.08.): Bleibt der Kaufpfad leer, wird der Grund
    # protokolliert — sonst ist „0 Vorhaben" von „Planner defekt" nicht zu
    # unterscheiden (heute kostete genau das eine halbe Morgen-Routine).
    buy_block = None
    if not any(d.kind == "bid" for d in drafts):
        team_plan = getattr(plan, "team_plan", None)
        if team_plan:
            buy_block = " ".join(team_plan.get("notes") or [
                "Der finanzierte Mannschaftsplan enthält aktuell kein neues Kaufvorhaben.",
            ])
        else:
            buy_block = buy_blocker_reason(
                plan.market_candidates, plan.plan_base,
                DEFAULT_GUARDRAILS, plan.need_positions,
            )
        if dropped_uncovered and not buy_block:
            buy_block = (
                f"{dropped_uncovered} Gebots-Entwurf/Entwürfe im T-120-Fenster "
                "cash-ungedeckt verworfen (Deckung erst nach Verkäufen)."
            )
        if buy_block:
            _log.info("Planner: kein Kaufvorhaben — %s", buy_block)
    # Accept-Pfad (24.08.): Angebote auf EIGENE Listings → Annahme-Vorhaben.
    # Kader-Deckung/Floor prüft propose_accepts; Caps deckelt upsert_intents.
    # Seit 16.09.: Emils Listungspreis ist die Untergrenze (darunter nur Meldung),
    # und der Kader-Floor folgt dem T-120-Fenster wie im Executor (L-095).
    offers = incoming_offers_from_raw(raw_market, user_id)
    below_asking: list[IncomingOffer] = []
    squad_positions = {
        sp.player.id: int(sp.player.position) for sp in plan.squad
    }
    if offers:
        accept_props = propose_accepts(
            offers, squad_positions, plan.league_id, DEFAULT_GUARDRAILS,
            min_squad_size=accept_squad_floor(now, plan.matchday_deadline_at),
            bench_ids=plan.li_bench_ids,
        )
        drafts += accept_drafts(
            accept_props, {o.player_id: o for o in offers}, now, bench_ids=plan.li_bench_ids
        )
        decline_props = propose_declines(
            offers, squad_positions, plan.league_id, DEFAULT_GUARDRAILS,
            bench_ids=plan.li_bench_ids,
        )
        drafts += decline_drafts(
            decline_props, {o.player_id: o for o in offers}, now
        )
        below_asking = offers_below_asking(
            offers, squad_positions, bench_ids=plan.li_bench_ids
        )
    # Aktiver Delist-Pfad (21.10.2026): Eigene Listings, die inzwischen LI-Starter,
    # geschützt oder für die Positions-Minima nötig sind, werden zurückgezogen.
    own_listings = own_listings_from_raw(raw_market, user_id)
    if own_listings:
        starter_ids = {
            str(sp.player.id) for sp in plan.squad if getattr(sp, "lineup_order", None) is not None
        }
        delist_props = propose_delists(
            own_listings, squad_positions, plan.league_id, DEFAULT_GUARDRAILS,
            bench_ids=plan.li_bench_ids, starter_ids=starter_ids,
        )
        drafts += delist_drafts(
            delist_props, {item.player_id: item for item in own_listings}, now
        )
        # Wenn ein eigenes Listing zurückgezogen wird, entfällt ein gleichzeitiges
        # Ablehnen/Verkaufen auf demselben Listing.
        delist_pids = {d.player_id for d in drafts if d.kind == "delist"}
        if delist_pids:
            drafts = [
                d for d in drafts
                if not (d.kind == "decline" and d.player_id in delist_pids)
            ]
    # Doktrin-Satz 3 (01.09., Emils GO „bau es"): eigene Manager-Listings ohne
    # ein einziges Angebot nach 48 h gelten als gescheitert (L-081) und werden
    # in Sofortverkaufs-Vorhaben überführt. Nur LI-Bank (fail-safe ohne
    # LI-Daten, L-090), Schutzliste + Kader-Deckung prüft die Policy; Angebote
    # auf dem Listing gehören dem Accept-Pfad (ofs leer ist Vorbedingung).
    failed = failed_listings_from_raw(raw_market, user_id, now)
    if failed:
        failed_positions = {sp.player.id: int(sp.player.position) for sp in plan.squad}
        fail_props = propose_failed_listing_sells(
            failed,
            failed_positions,
            plan.league_id,
            DEFAULT_GUARDRAILS,
            bench_ids=plan.li_bench_ids,
        )
        drafts += failed_listing_drafts(
            fail_props, {f.player_id: f for f in failed}, now
        )
    # Sperre nur um den DB-Abgleich (nicht um den langsamen Plan-Bau): der
    # 5-Minuten-Executor wartet so höchstens Millisekunden.
    async with _intent_lock:
        with SessionLocal() as session:
            team_plan = plan.team_plan or {}
            purchase_ids = {str(p) for p in (team_plan.get("purchase_ids") or [])}
            buying = bool(purchase_ids)
            swap_backed = is_swap_backed_full_lineup(team_plan)
            # Engine zuerst (Befund 03.10.2026): Geplante Handelsgebote weichen VOR dem
            # Abgleich — so kann die Engine im selben Lauf auch auf einen Spieler bieten,
            # den der Autopilot zum Handel geplant hatte. Bei reinem 1-zu-1-Dispo-Tausch
            # auf voller 11er-Elf weichen nur Handelsgebote auf denselben Zielspieler.
            yielded = (
                yield_trade_bids(
                    session,
                    plan.league_id,
                    now,
                    only_player_ids=purchase_ids if swap_backed else None,
                )
                if buying
                else []
            )
            # Bindet ein festes Handelsgebot (Emils Anpassung, Ausführung) mehr, als der Plan
            # zählte, rechnete er dessen Geld als frei: Dieser Lauf ändert keine Vorhaben, der
            # nächste plant neu — die geänderte Vorhaben-Zeile ändert den Fingerabdruck des
            # Engine-Zyklus (Codex-Review 03.10.2026). Sonst entstünde ein ungedecktes
            # Engine-Vorhaben, das beim Write scheitert und das Ziel für den Tag sperrt.
            late = (late_firm_trade_bids(session, plan.league_id,
                                         getattr(plan, "trade_bound", {}),
                                         open_trade_bids, max_intent_id)
                    if buying else [])
            stale = ", ".join(sorted(r.player_name or r.player_id for r in late))
            if late:
                _log.warning("Planner: Plan überholt — Handelsgebot(e) %s seit dem Planbau "
                             "fest; keine Vorhaben geändert.", stale)
                plan.team_plan.setdefault("notes", []).append(
                    f"Nicht angewendet: Handelsgebot(e) {stale} seit dem Planbau fest — der "
                    "nächste Lauf plant neu.")
            else:
                release_funding_pending(
                    session, (plan.team_plan or {}).get("funding_recovery") or {},
                    plan.league_id, now,
                )
            stats = PlannerStats() if late else upsert_intents(
                session,
                drafts,
                now,
                market_pids={r.player_id for r in market_rows},
                counts=today_intent_counts(session, now),
                # Plan-Hysterese: aktueller Score je Markt-Kandidat entscheidet,
                # ob ein Vorhaben ohne frischen Entwurf ueberlebt (01.09.).
                keep_pids=hysteresis_keep_pids(
                    plan.market_candidates, plan.need_positions,
                    purchase_reviews=getattr(plan, "purchase_reviews", None), now=now,
                ),
                purchase_reviews=getattr(plan, "purchase_reviews", None),
                squad_pids={sp.player.id for sp in plan.squad},
                active_bid_targets={
                    **{r.player_id: "manager" if r.is_manager else "kickbase"
                       for r in market_rows if r.our_bid is not None},
                    **{r.player_id: r.listing_type for r in session.scalars(
                        select(Intent).where(
                            Intent.kind == "bid", Intent.status == STATUS_EXECUTING,
                        )
                    ) if (r.sources or {}).get("league_id", plan.league_id) == plan.league_id},
                },
            )
            from kickbase_agent.worker.engine_cycle import record_team_plan

            record_team_plan(session, plan, now)
            session.commit()
            settings = get_settings()
            msg = format_planner_report(
                stats,
                armed=bool(settings.agent_auto_intents),
                discord_controls=bool(settings.discord_intent_controls),
            )
            # Karten-Aufträge IN der Session bauen (Attribute sicher geladen,
            # Review-Fix 31.08.) — die Discord-I/O läuft nach der Sperre.
            card_jobs = (
                card_jobs_for_created(stats.created, datetime.now(UTC))
                if channel is not None and stats.created and settings.discord_intent_controls
                else []
            )
    # Summary-Post einzeln geschützt (Review 31.08.): Er stand ungeschützt VOR
    # den Karten — ein transienter Discord-Fehler ließ die frisch committeten
    # Vorhaben dauerhaft ohne Veto-Karten laufen, während job_runs den Lauf als
    # fehlerfrei verbuchte. Discord darf den Planner nie brechen — auch hier.
    if channel is not None and msg is not None:
        try:
            await channel.send_info(msg, kind="planner")
        except Exception:  # noqa: BLE001 — Karten sind wichtiger als die Summary
            _log.warning("Planner-Summary nicht gepostet — Karten folgen trotzdem.",
                         exc_info=True)
    # Angebote unter Emils Listungspreis (16.09.): nie autonom angenommen, aber
    # genau einmal je Offer gemeldet (Bericht + Discord), damit Emil entscheiden
    # kann. Dedupe über die gespeicherten Berichte — überlebt Worker-Neustarts.
    fresh_below = record_below_asking(below_asking)
    if fresh_below:
        notice = format_below_asking_notice(fresh_below)
        _log.info("Planner: %d Angebot(e) unter Listungspreis gemeldet.", len(fresh_below))
        if channel is not None:
            try:
                await channel.send_info(notice, kind="planner")
            except Exception:  # noqa: BLE001 — Meldung darf den Planner nie brechen
                _log.warning("Angebots-Meldung nicht gepostet.", exc_info=True)
    # Discord-Veto-Karten (Spec 2026-08-31 §4.4): NUR für neu angelegte Vorhaben —
    # Updates/superseded zieht der Kehrer im Executor-Tick nach; nie gepostete
    # Karten holt der Kehrer-Backfill nach (Spec-§6-Nachtrag 31.08.). Bewusst NACH
    # der Sperre (Posts brauchen ~1 s Abstand und sollen den Executor nicht blocken).
    if card_jobs:
        await apply_card_work(channel, card_jobs)
    summary = stats.summary()
    if late:
        summary += f" | Plan überholt: Handelsgebot(e) {stale} seit dem Planbau fest"
    if yielded:
        summary += f" | Handel geräumt: {len(yielded)} (Engine plant einen Kauf)"
    if dropped_uncovered:
        summary += f" | T-120-Filter: {dropped_uncovered} ungedeckt verworfen"
    if plan.pledge_dropped:
        summary += f" | Pfand-Budget: {plan.pledge_dropped} über Risiko-Summe verworfen"
    return f"{summary} | kein Kauf: {buy_block}" if buy_block else summary


# ------------------------------------------------------------------ Executor


def due_intents(session: Session, now: datetime) -> list[Intent]:
    """Fällige Vorhaben: planned UND execute_at ≤ jetzt — früheste zuerst.

    Zeitvergleich in Python (SQLite liefert naive Zeitstempel).
    """
    rows = open_intents(session)
    due = [r for r in rows if _as_utc(r.execute_at) <= now]
    due.sort(key=lambda r: (_as_utc(r.execute_at), r.id))
    return due


def expire_intents(session: Session, now: datetime) -> list[Intent]:
    """planned mit überschrittener Deadline → `expired` (commit); gibt die Zeilen zurück.

    Konditional (WHERE status='planned') wie die API: ein Veto dazwischen bleibt Veto.
    """
    expired: list[Intent] = []
    for row in open_intents(session):
        if row.deadline is None or _as_utc(row.deadline) > now:
            continue
        res = session.execute(
            update(Intent)
            .where(Intent.id == row.id, Intent.status == STATUS_PLANNED)
            .values(status=STATUS_EXPIRED, result=RESULT_EXPIRED, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if res.rowcount > 0:
            session.expire(row)
            expired.append(row)
    if expired:
        session.commit()
    return expired


def cap_reason(intent: Intent, counts: TodayCounts, guardrails: Guardrails) -> str | None:
    """Tages-Cap-Prüfung (5 Käufe / 5 Verkäufe / 12 Listings) — None, wenn Platz ist.

    L-077 vollendet (31.08.): `list` zählt gegen daily_list_cap statt gegen den
    Verkaufs-Cap — dieselbe Trennung wie an der einzigen Schreibstelle
    (ActionExecutor, executor.py-Tagesnetz).
    """
    if intent.kind == "bid" and counts.buys >= guardrails.daily_buy_cap:
        return (
            f"Tages-Cap Käufe erreicht ({counts.buys}/{guardrails.daily_buy_cap}) — "
            "nicht ausgeführt"
        )
    if intent.kind == "list" and counts.lists >= guardrails.daily_list_cap:
        return (
            f"Tages-Cap Listings erreicht ({counts.lists}/{guardrails.daily_list_cap}) — "
            "nicht ausgeführt"
        )
    if intent.kind in ("sell", "accept") and counts.sells >= guardrails.daily_sell_cap:
        return (
            f"Tages-Cap Verkäufe erreicht ({counts.sells}/{guardrails.daily_sell_cap}) — "
            "nicht ausgeführt"
        )
    return None


def action_for(intent: Intent, league_id: str) -> PlannedAction:
    """PlannedAction aus dem Vorhaben: rule_id `intent-<kind>`, Begründung inkl. Emil-Spur.

    Arten: bid, list, sell, accept, cancel_bid (Offer-ID aus den Quellen) und seit
    22.09.2026 `delist` (Emils Auftrag 21.09.: eigenes Listing zurückziehen) —
    ohne Betrag und ohne Offer-ID; der Executor-Dispatch ruft
    client.delist_player(lg, pid). Manuelle Vorhaben tragen `-manual`.
    """
    rationale = intent.rationale or ""
    if intent.adjusted_from is not None:
        rationale += (
            f" — Emil-Anpassung: {_fmt_m(intent.adjusted_from)} → {_fmt_m(intent.amount)}"
        )
    if intent.emil_note:
        rationale += f" — Notiz: {intent.emil_note}"
    return PlannedAction(
        action_type=intent.kind,
        league_id=league_id,
        player_id=intent.player_id,
        # Auch Sofortverkauf trägt den Planungs-MW: der Executor-Preflight prüft
        # damit die ±20-%-Drift (Zustand geändert ⇒ refused statt Blindverkauf).
        amount=intent.amount if intent.amount > 0 else None,
        # Annahme-Vorhaben: die Offer-ID (`uoid`) kommt aus den Quellen — ohne
        # sie schlägt der Dispatch kontrolliert fehl (_req → failed, ehrlich).
        offer_id=(intent.sources or {}).get("offer_id") or None,
        # Manuelle Remote-Vorhaben (Zwei-DB-Fix): '-manual'-Suffix — bleibt vom
        # intent-Präfix geärmt, gilt aber NICHT als autonom (Starter-Schutz aus;
        # Codex-Folge-Review 24.08., P2: Transport ändert sonst die Semantik).
        # Annahme zum Listungspreis (16./17.09.): `asking_price` in den Quellen
        # heißt, Emils gesetzter Preis wurde erreicht — der Executor behandelt
        # die Annahme wie Emils Entscheidung (kein Starter-Schutz, kein
        # T-120-Floor autonomer Pfade), die Zeile bleibt aber Planner-Eigentum.
        rule_id=(
            f"{INTENT_PREFIX}{intent.kind}-manual"
            if (intent.sources or {}).get("manual")
            or (intent.kind == "accept" and (intent.sources or {}).get("asking_price"))
            else "intent-funding-cancel" if intent.kind == "cancel_bid"
            and isinstance((intent.sources or {}).get("funding_review"), dict)
            else f"{INTENT_PREFIX}{intent.kind}"
        ),
        rationale=rationale,
        # Kader-Bedarf (26.08.): Der Planner (Positions-Not) bzw. das manuelle
        # MCP-Gebot legt den Marker in die Quellen; hier wird er zurück in die
        # Aktion gehoben, damit im Executor-Preflight die höhere Overpay-Stufe
        # gilt (135 % statt 110 % MW). Ohne diesen Weg wäre der Marker beim
        # Sprung über die Intent-Zeile verloren.
        squad_need=bool((intent.sources or {}).get("squad_need")),
        # Tausch-Verkauf (02.09.) bzw. Konto-Deckungsverkauf eines Nicht-Starters:
        # der Starter-Schutz autonomer Pfade greift für ihn nicht.
        swap=bool(
            (intent.sources or {}).get("after_squad_size")
            or (intent.sources or {}).get("rule_id") == "sell_deficit_cover"
        ),
        purchase_review=deepcopy((intent.sources or {}).get("purchase_review")),
        funding_review=deepcopy((intent.sources or {}).get("funding_review")),
        # Emils Wortlaut (17.09.): wird nur ins Audit mitgeführt. Er hebt den
        # Zwang zur Engine-Auswahl NICHT auf (Richtigstellung 21.09.2026, P0-6).
        emil_go=str((intent.sources or {}).get("emil_go") or ""),
    )


def result_text(res: ActionResult) -> str:
    """Executor-Ergebnis → ehrlicher Kurztext fürs Vorhaben („<status>: <Grund>")."""
    if res.status == "executed":
        return RESULT_EXECUTED
    if res.status == "skipped_dry_run":
        return RESULT_KILL_SWITCH
    if res.status == "duplicate":
        return (
            "duplicate: dieselbe Aktion steht heute schon im Audit-Log "
            f"({res.message or 'Idempotenz'})"
        )
    if res.status == "unknown":
        return (
            "unknown: Timeout beim API-Write — Remote-Zustand unklar, der Reconcile "
            "prüft gegen Kickbase (kein blinder Retry)"
        )
    return f"{res.status}: {res.message or 'ohne Begründung'}"


def _finish(
    session: Session,
    intent: Intent,
    status: str,
    result: str,
    now: datetime,
    action_id: int | None = None,
) -> str:
    intent.status = status
    intent.result = result
    if action_id is not None:
        intent.executed_action_id = action_id
    intent.updated_at = now
    session.commit()
    return status


def claim_intent(session: Session, intent: Intent, now: datetime) -> bool:
    """Vorhaben atomar für die Ausführung claimen: planned → executing.

    Die API mutiert Vorhaben mit `UPDATE … WHERE status='planned'` (Veto, Adjust,
    Execute-now). Nur wer die Zeile von `planned` auf `executing` dreht, darf
    schreiben — ein Veto, das zwischen Laden und Write eintrifft, gewinnt (rowcount
    0 ⇒ False, nichts passiert). Nach dem Claim wird die Zeile frisch gelesen, damit
    ein Adjust kurz vor dem Claim (Betrag/Notiz) in die Aktion einfließt.
    """
    claimed = session.execute(
        update(Intent)
        .where(Intent.id == intent.id, Intent.status == STATUS_PLANNED)
        .values(status=STATUS_EXECUTING, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount == 0:
        session.rollback()
        session.refresh(intent)
        return False
    session.commit()
    session.refresh(intent)
    return True


def recover_stale_executing(session: Session, now: datetime) -> list[Intent]:
    """Verwaiste `executing`-Zeilen (Prozess-Absturz mitten im Write) → failed.

    Ein Vorhaben darf nie in `executing` hängen bleiben: nach EXECUTING_GRACE gilt
    es als abgebrochen; ob der Write bei Kickbase durchging, klärt der Reconcile
    über die 'pending'-Audit-Zeile — nicht ein blinder Retry.
    """
    stale = [
        r
        for r in session.execute(
            select(Intent).where(Intent.status == STATUS_EXECUTING)
        ).scalars()
        if _as_utc(r.updated_at) <= now - EXECUTING_GRACE
    ]
    for r in stale:
        # Vorgemerktes Veto gewinnt auch hier (Review 31.08.): Emil wollte das
        # Vorhaben nicht — ein hängen gebliebener Versuch macht daraus vetoed,
        # nicht failed (failed sperrte den Spieler zudem für den Tag).
        if r.veto_requested_at is not None:
            r.status = STATUS_VETOED
            r.result = "Veto vorgemerkt — hängender Versuch abgeräumt, übersprungen."
        else:
            r.status = STATUS_FAILED
            r.result = RESULT_STALE_EXECUTING
        r.updated_at = now
    if stale:
        session.commit()
    return stale


def _dispo_coupling_missing(session: Session, intent: Intent) -> str | None:
    """Grund, warum ein Dispo-Tauschgebot nicht ausgeführt werden darf (sonst None)."""
    if intent.kind != "bid":
        return None
    sale = _dispo_sale((intent.sources or {}).get("purchase_review"))
    if sale is None:
        return None
    waiting = session.scalars(
        select(Intent).where(
            Intent.kind == "sell",
            Intent.player_id == sale["player_id"],
            Intent.status == STATUS_PLANNED,
        )
    ).all()
    if any(
        (row.sources or {}).get("swap_for") == intent.player_id
        and (row.sources or {}).get("swap_dispo")
        for row in waiting
    ):
        return None
    return (
        f"Tausch-Kopplung fehlt: kein wartender Sofortverkauf von {sale['player_id']} "
        "für dieses Gebot — ein Dispo-Gebot geht nie ohne seinen Verkauf raus."
    )


def _is_dispo_sale(intent: Intent) -> bool:
    sources = intent.sources or {}
    return intent.kind == "sell" and (
        sources.get("swap_dispo") is True
        or sources.get("rule_id") in {DISPO_SMOOTHING_RULE, "sell_deficit_cover"}
    )


def _record_dispo_win_at_sale(session: Session, intent: Intent, now: datetime) -> None:
    """Tausch-Bedingung erfüllt = Ziel steht im Kader: Zuschlag des Paars vermerken.

    Der Executor sieht den Zuschlag oft vor dem Planner (Befund 2b: der Verkauf scheitert
    im selben Schritt). Nur Dispo-Tauschverkäufe, nur in der Karenz (`record_dispo_win`).
    """
    sources = intent.sources or {}
    target = sources.get("swap_for")
    if intent.kind != "sell" or sources.get("swap_dispo") is not True or not isinstance(
        target, str
    ):
        return
    linked = _linked_swap_bid(session, intent, target)
    if linked is not None and record_dispo_win(linked, now):
        session.commit()


async def deficit_cover_unneeded(
    intent: Intent, executor: ActionExecutor, league_id: str,
) -> str | None:
    """Entscheidungs-Gate vor einem Konto-Deckungsverkauf (21.10.2026).

    Ist das Konto (inkl. `pbaa`) unmittelbar vor der Ausführung bereits
    ≥ KICKOFF_MIN_BALANCE (z. B. durch Prämie, abgelaufenes Gebot oder
    Schaufenster-Verkauf), entfällt der Deckungsverkauf an den Computer.
    """
    sources = intent.sources or {}
    if (
        intent.kind != "sell"
        or sources.get("rule_id") != "sell_deficit_cover"
        or sources.get("manual")
    ):
        return None
    client = getattr(executor, "_client", None)
    if client is None:
        return None
    try:
        budget = int(await client.get_budget(league_id))
        effective = budget
        read_fields = getattr(client, "get_budget_fields", None)
        if read_fields is not None:
            fields = await read_fields(league_id)
            if isinstance(fields, dict) and fields.get("pbaa") is not None:
                effective = min(budget, int(fields["pbaa"]))
    except Exception:  # noqa: BLE001 — bei Lesefehler nicht blind überspringen
        return None
    if effective >= KICKOFF_MIN_BALANCE:
        return (
            f"Entscheidungs-Gate: Konto bereits gedeckt ({effective:,} € ≥ "
            f"{KICKOFF_MIN_BALANCE:,} €) — Deckungsverkauf entfällt."
        ).replace(",", ".")
    return None


async def _refresh_dispo_sale_amount(
    session: Session, intent: Intent, executor: ActionExecutor, league_id: str,
) -> None:
    """Dispo-Verkauf: Betrag unmittelbar vor der Übergabe auf den frischen Marktwert.

    Review 23.09.2026, Befund 2a: Der Verkauf wartet oft Tage auf den Zuschlag. Am
    veralteten Planbetrag verweigerte die ±20-%-Drift-Sicherung des Executors den
    Verkauf, und das Konto blieb im Minus. Kickbase zahlt beim Sofortverkauf ohnehin
    den aktuellen Marktwert; gelesen wird derselbe Kader wie im Preflight. Lesefehler
    ändern nichts (der Executor entscheidet dann am alten Betrag).
    """
    if not _is_dispo_sale(intent):
        return
    client = getattr(executor, "_client", None)
    try:
        squad = await client.get_squad(league_id) if client is not None else None
    except Exception:  # noqa: BLE001 — Lesefehler: Betrag bleibt, Preflight entscheidet
        return
    member = next(
        (sp for sp in squad or [] if str(sp.player.id) == str(intent.player_id)), None,
    )
    value = int(getattr(member.player, "market_value", 0) or 0) if member else 0
    if value > 0 and value != intent.amount:
        intent.amount = value
        session.commit()


async def execute_intent(
    session: Session,
    intent: Intent,
    executor: ActionExecutor,
    league_id: str,
    *,
    now: datetime,
    counts: TodayCounts | None = None,
    guardrails: Guardrails = DEFAULT_GUARDRAILS,
) -> str:
    """EIN Vorhaben ausführen und das Ergebnis ehrlich zurückschreiben.

    Rückgabe: Intent-Status danach (`executed` | `failed` — oder der fremde Status,
    wenn der Claim verloren ging, z. B. `vetoed`). Der ActionExecutor ist die
    einzige Schreibstelle: Kill-Switch (→ skipped_dry_run), Interlock (→ refused
    ohne AGENT_AUTO_INTENTS), Idempotenz, Preflight. `executed_action_id` verknüpft
    die Audit-Zeile, auch bei Fehlschlag (Protokoll: „Warum?").
    """
    if not claim_intent(session, intent, now):
        return intent.status  # Veto/Adjust/Execute-now kam dazwischen — nicht anfassen
    reason = cap_reason(intent, counts, guardrails) if counts is not None else None
    if reason is not None:
        return _finish(session, intent, STATUS_FAILED, reason, now)
    # Selbst-Einfrieren (02.09.): Der Freeze ist ein menschlicher Riegel
    # (`kba unfreeze --by emil`), kein Fehler des Vorhabens. Ein Geld-Vorhaben
    # wartet deshalb in 15-min-Schritten auf das Entsperren, statt als failed zu
    # enden und den Spieler für den Tag zu sperren — und der Executor wird gar
    # nicht erst gerufen (sonst stünde alle 15 min eine refused-Zeile im Audit).
    # Befristet: Deadline − Puffer, ohne Deadline 48 h ab Anlage.
    if intent.kind in ("bid", "sell", "list") and fz.is_frozen(session):
        retry_at = now + CONDITION_RETRY_DELAY
        latest = (
            _as_utc(intent.deadline) - EXECUTE_BEFORE_DEADLINE
            if intent.deadline is not None
            else _as_utc(intent.created_at) + timedelta(hours=48)
        )
        if retry_at <= latest:
            intent.status = STATUS_PLANNED
            intent.execute_at = retry_at
            intent.result = (
                "Selbst-Einfrieren aktiv — warte auf Emils Entsperren "
                f"(kba unfreeze --by emil), nächste Prüfung {_fmt_clock(retry_at)} Uhr"
            )
            intent.updated_at = now
            session.commit()
            return STATUS_PLANNED
        return _finish(
            session,
            intent,
            STATUS_FAILED,
            "Selbst-Einfrieren aktiv bis kurz vor der Deadline — Vorhaben verworfen "
            "(Entsperren kam nicht rechtzeitig).",
            now,
        )
    # Dispo-Tausch (Emil 23.09.2026): Ein Gebot, das nur mit seinem gekoppelten
    # Verkauf finanziert ist, geht nie allein raus — auch nicht, wenn Emil den Verkauf
    # zwischen Planung und Ausführung übersprungen hat.
    missing = _dispo_coupling_missing(session, intent)
    if missing is not None:
        return _finish(session, intent, STATUS_FAILED, missing, now)
    # Tausch-Anrechnung (Review 23.09.2026, Befund 4): Ein Kaufbeleg darf den Erlös
    # eines gekoppelten Verkaufs nur zählen, solange dieser Verkauf wartet (oder lief).
    # Hat Emil ihn übersprungen, prüft der Beleg ohne diese Anrechnung — hier und im
    # Preflight des ActionExecutors, der den bereinigten Beleg erhält.
    review = (intent.sources or {}).get("purchase_review") if intent.kind == "bid" else None
    backed_review = _backed_swap_review(review, _backed_swap_pairs(session))
    if backed_review is not review:
        problem = purchase_review_veto(
            backed_review, player_id=intent.player_id, amount=intent.amount, now=now,
        )
        if problem is not None:
            return _finish(
                session, intent, STATUS_FAILED,
                "Tausch-Anrechnung entfällt (gekoppelter Verkauf wartet nicht mehr): "
                f"{problem}",
                now,
            )
    # Tausch-Bedingung (02.09., Emils Auftrag): Ein Sofortverkauf mit
    # sources.after_squad_size UND swap_for verlangen Kadergröße und den exakten
    # Ersatz: ein beliebiger anderer Zuschlag darf keinen falschen Abgang auslösen.
    # Kein Zähler: begrenzt wird durch die Deadline des Vorhabens; ohne Deadline
    # ist die Bedingung nicht prüfbar befristet → ehrlich failed.
    wait = await squad_condition_unmet(intent, executor, league_id, now=now)
    if wait is not None:
        status, text = wait
        if status == STATUS_PLANNED:
            if "noch nicht im Kader" not in text and "nicht lesbar" not in text:
                _record_dispo_win_at_sale(session, intent, now)
            intent.status = STATUS_PLANNED
            intent.execute_at = now + CONDITION_RETRY_DELAY
            intent.result = text
            intent.updated_at = now
            session.commit()
            return STATUS_PLANNED
        return _finish(session, intent, STATUS_FAILED, text, now)
    already_covered = await deficit_cover_unneeded(intent, executor, league_id)
    if already_covered is not None:
        return _finish(session, intent, STATUS_SUPERSEDED, already_covered, now)
    _record_dispo_win_at_sale(session, intent, now)
    await _refresh_dispo_sale_amount(session, intent, executor, league_id)
    action = action_for(intent, league_id)
    if backed_review is not review:
        action = replace(action, purchase_review=deepcopy(backed_review))
    try:
        res = await executor.execute(action, dry_run=False, now=now)
    except Exception as exc:
        # Nie in `executing` hängen lassen — ehrlich failed, Fehler nach oben (Discord).
        _finish(session, intent, STATUS_FAILED, f"failed: {type(exc).__name__}: {exc}", now)
        raise
    # pbaa-Lücke ist transient (31.08., Maluze-Fall: 0,96-M-Gebot bei Konto
    # 3,53 M starb daran, und blocked_keys sperrte den Spieler für den Tag):
    # Der Preflight bleibt bewusst fail-closed (versiegelte Zusicherung) — aber
    # ein daran gescheitertes Vorhaben wird nicht beerdigt, sondern in ≤ 3
    # Anläufen je +10 min erneut versucht, solange die Deadline es hergibt.
    # Veto-Vormerkung einlösen (Review 31.08.): Hat Emil während des laufenden
    # Versuchs Überspringen geklickt (actions.veto_intent merkt das atomar in
    # veto_requested_at vor), gilt bei JEDEM nicht-executed-Ausgang sein Wille —
    # kein Retry, kein failed mit Tagessperre, sondern ehrlich vetoed. Nur ein
    # bereits durchgegangener Write (executed) ist ehrlich zu spät.
    session.refresh(intent, attribute_names=["veto_requested_at"])
    if res.status != "executed" and intent.veto_requested_at is not None:
        return _finish(
            session,
            intent,
            STATUS_VETOED,
            "Veto während der Ausführung vorgemerkt — Versuch scheiterte, "
            "Vorhaben übersprungen (kein Retry).",
            now,
            action_id=res.action_id,
        )
    # Lesefehler im Preflight sind ebenso vorübergehend (Befund 02.10.2026, Emils GO): Ein
    # kurzer Kickbase-Aussetzer beim frischen Lesen beerdigte sonst den Pflichtverkauf oder
    # das Elf-Gebot bis Tagesende (blocked_keys). Inhaltliche Verweigerungen bleiben failed.
    read_error = preflight_read_error(res.message) if res.status == "refused" else None
    if res.status == "refused" and ("pbaa" in (res.message or "") or read_error is not None):
        # Zähler als SPALTE (Review 31.08.): in `sources` wurde er vom
        # stündlichen Planner-Update wholesale überschrieben — das 3er-Budget
        # verdoppelte sich faktisch (dasselbe Argument wie bei discord_msg_id).
        # Gemeinsam für pbaa-Lücke und Lesefehler: höchstens 3 Versuche je Vorhaben.
        tries = int(intent.pbaa_retries or 0)
        retry_at = now + PBAA_RETRY_DELAY
        latest = (
            _as_utc(intent.deadline) - PBAA_RETRY_CUTOFF
            if intent.deadline is not None
            else None
        )
        if tries < PBAA_MAX_RETRIES and (latest is None or retry_at <= latest):
            intent.status = STATUS_PLANNED
            intent.execute_at = retry_at
            intent.pbaa_retries = tries + 1
            cause = (
                f"Lesefehler im Preflight ({read_error}, transient)"
                if read_error is not None and "pbaa" not in (res.message or "")
                else "pbaa fehlte (transient)"
            )
            intent.result = (
                f"{cause} — Versuch {tries + 1}/{PBAA_MAX_RETRIES}, "
                f"nächster um {_fmt_clock(retry_at)} Uhr"
            )
            intent.updated_at = now
            session.commit()
            return STATUS_PLANNED
    status = STATUS_EXECUTED if res.status == "executed" else STATUS_FAILED
    return _finish(session, intent, status, result_text(res), now, action_id=res.action_id)


def format_executor_report(
    results: list[tuple[Intent, str]], expired: list[Intent], armed: bool
) -> str | None:
    """Kurz-Report des Executor-Laufs — None, wenn nichts MELDENSWERTES passiert ist.

    Meldungs-Umbau (Emil, 31.08.): Erfolge posten nicht mehr — die Karte des
    Vorhabens springt im selben Tick auf „✅ Ausgeführt" (Kehrer), ein zweiter
    Text dazu war doppelt. Gemeldet werden nur noch Fehlschläge und Abläufe
    (die brauchen Emils Blick); der Audit-Beweis liegt ohnehin im Protokoll.
    """
    n_ok = sum(1 for _, s in results if s == STATUS_EXECUTED)
    bad = [(row, s) for row, s in results if s != STATUS_EXECUTED]
    if not bad and not expired:
        return None
    lines = [
        f"**Steuerzentrale** — Executor: {n_ok} ausgeführt, {len(bad)} fehlgeschlagen, "
        f"{len(expired)} abgelaufen"
    ]
    for row, _status in bad:
        lines.append(f"• XX {_intent_line(row)} → {row.result}")
    for row in expired:
        lines.append(f"• -- {_intent_line(row)} → {row.result}")
    if bad and not armed:
        lines.append(
            "-# AGENT_AUTO_INTENTS=0: der Intent-Pfad ist nicht geärmt — Vorhaben enden "
            "ehrlich als failed/refused, bis Emil »scharf« sagt."
        )
    return _clip("\n".join(lines))


#: Tausch-Bedingung (02.09.): Prüfabstand, wenn der Kader die verlangte Größe
#: noch nicht hat — kurz genug, dass ein Zuschlag (Listing-Ablauf) binnen einer
#: Viertelstunde den wartenden Verkauf auslöst.
CONDITION_RETRY_DELAY = timedelta(minutes=15)


async def squad_condition_unmet(
    intent: Intent, executor: ActionExecutor, league_id: str, *, now: datetime
) -> tuple[str, str] | None:
    """Wartegrund für ein Verkaufs-Vorhaben mit `sources.after_squad_size`.

    None = keine Bedingung oder erfüllt (ausführen). Sonst (Status, Text):
    STATUS_PLANNED → weiter warten (execute_at wird vom Aufrufer geschoben),
    STATUS_FAILED → Bedingung bis zur Deadline nie erfüllt bzw. ohne Deadline
    nicht befristbar. Liest den Kader über denselben Client wie der Executor
    (Produktionspfad, Beweisregel 3) — ein Lesefehler heißt WARTEN, nicht
    verkaufen (fail-safe: ein Verkauf zu früh ist teurer als einer zu spät).
    """
    if intent.kind != "sell":
        return None
    sources = intent.sources or {}
    target = sources.get("swap_for")
    is_swap = sources.get("rule_id") == "swap_sell" or target is not None
    if is_swap and (
        not isinstance(target, str) or not target or target == intent.player_id
    ):
        return STATUS_FAILED, "Tausch-Bedingung ohne eindeutig verknüpften Ersatz — verworfen."
    need = sources.get("after_squad_size")
    if need in (None, "", 0):
        if is_swap:
            return STATUS_FAILED, "Tausch-Bedingung ohne erforderliche Kadergröße — verworfen."
        return None
    try:
        need_n = int(need)
    except (TypeError, ValueError):
        return STATUS_FAILED, f"after_squad_size unlesbar: {need!r}"
    if isinstance(need, bool) or need_n < 1:
        return STATUS_FAILED, f"after_squad_size ungültig: {need!r}"
    if intent.deadline is None:
        return STATUS_FAILED, "Tausch-Bedingung ohne Deadline — nicht befristbar, verworfen."
    if now >= _as_utc(intent.deadline):
        return STATUS_FAILED, "Tausch-Deadline überschritten — Verkauf verworfen."
    latest = _as_utc(intent.deadline) - EXECUTE_BEFORE_DEADLINE
    client = getattr(executor, "_client", None)
    try:
        squad = await client.get_squad(league_id) if client is not None else None
    except Exception as exc:  # noqa: BLE001 — Lesefehler = warten, nie blind verkaufen
        squad = None
        read_error = f"{type(exc).__name__}: {exc}"
    else:
        read_error = None
    size = len(squad) if squad is not None else None
    target_owned = not target or (
        squad is not None and any(str(sp.player.id) == target for sp in squad)
    )
    effective_need = min(need_n, 12) if (is_swap and target and target_owned) else need_n
    if size is not None and size >= effective_need and target_owned:
        return None
    missing = (
        f"Ersatz {target} noch nicht im Kader"
        if squad is not None and not target_owned else f"Kader {size} < {effective_need}"
    )
    if now + CONDITION_RETRY_DELAY > latest:
        return (
            STATUS_FAILED,
            f"Tausch-Bedingung bis zur Deadline nicht erfüllt ({missing}) "
            "— Verkauf verworfen, Ersatz kam nicht.",
        )
    grund = (
        f"{missing} — warte auf Ersatz"
        if size is not None
        else f"Kader nicht lesbar ({read_error}) — warte"
    )
    return STATUS_PLANNED, f"{grund}, nächste Prüfung {_fmt_clock(now + CONDITION_RETRY_DELAY)} Uhr"


#: pbaa-Retry (31.08., Maluze-Lehre): Liefert Kickbase das Konto-nach-Geboten
#: (pbaa) nicht, refused der Preflight fail-closed — das Vorhaben bleibt dann
#: planned und wird bis zu PBAA_MAX_RETRIES-mal im Abstand PBAA_RETRY_DELAY
#: erneut versucht, nie später als Deadline − PBAA_RETRY_CUTOFF (3 min, damit ein
#: planmäßig bei Deadline − 15 min gestartetes Auktionsgebot bei Deadline − 5 min
#: tatsächlich noch einmal versucht werden kann). Seit 02.10.2026 gilt dasselbe
#: Budget (gemeinsamer Zähler `pbaa_retries`) auch für Lesefehler im Preflight.
PBAA_RETRY_DELAY = timedelta(minutes=10)
PBAA_MAX_RETRIES = 3
PBAA_RETRY_CUTOFF = timedelta(minutes=3)

#: Lesefehler im Preflight der Schreibstelle (Befund 02.10.2026, Emils GO): Der Executor
#: meldet JEDEN Fehler beim frischen Lesen als „Preflight fehlgeschlagen (<Typ>: <Text>) —
#: Live-Write abgebrochen.“ Vorübergehend sind nur Netz- und Zeitfehler des HTTP-Clients,
#: Serverfehler (5xx) und 429 (nachdem der Client selbst dreimal gewartet hat). Inhaltliche
#: Verweigerungen meldet der Preflight als ValueError („Spielplan leer“, „Kaufprüfung: …“)
#: oder als „Preflight: …“ (Kader-Deckung, Starter-Schutz, Kappen …) — nie wiederholt.
_PREFLIGHT_FAILED = re.compile(r"Preflight fehlgeschlagen \((\w+): (.*)", re.DOTALL)
_TRANSIENT_READ_ERRORS = frozenset({
    # httpx: Verbindung, Zeitüberschreitung, abgebrochene Antwort
    "ConnectError", "ConnectTimeout", "ReadError", "ReadTimeout", "WriteError", "WriteTimeout",
    "PoolTimeout", "CloseError", "RemoteProtocolError", "NetworkError", "TimeoutException",
    "TransportError",
    # asyncio/Betriebssystem
    "TimeoutError", "ConnectionError", "ConnectionResetError", "ConnectionAbortedError",
    "ConnectionRefusedError",
})


def preflight_read_error(message: str | None) -> str | None:
    """Typ eines vorübergehenden Lesefehlers im Preflight der Schreibstelle, sonst None."""
    found = _PREFLIGHT_FAILED.match(message or "")
    if found is None:
        return None
    kind, text = found.groups()
    if kind in _TRANSIENT_READ_ERRORS:
        return kind
    if kind == "HTTPStatusError" and (
        text.startswith("Server error '") or text.startswith("Client error '429 ")
    ):
        return kind
    return None

#: Derselbe Fehler (z. B. Auth/API down) wird im 5-Minuten-Takt höchstens einmal
#: pro Stunde nach oben gereicht (Discord) — dazwischen nur Log. Vorhaben bleiben
#: dabei unangetastet (planned) und werden beim nächsten Tick erneut versucht.
ERROR_REPOST_AFTER = timedelta(hours=1)
_error_memo: dict[str, datetime] = {}
_log = logging.getLogger(__name__)


async def run_intent_executor(channel=None) -> str:
    """5-Minuten-Takt: abgelaufene Vorhaben schließen, fällige ausführen.

    Rückgabe: 'quiet' | 'no_league' | „n ausgeführt, n fehlgeschlagen, n abgelaufen"
    | „error (gedämpft): …". Zwischen zwei Live-Writes liegt
    AGENT_WRITE_SPACING_SECONDS (Default 2 s). Wiederholt sich derselbe Fehler
    (Client-Aufbau, DB), wird er nur einmal pro Stunde weitergereicht.
    """
    try:
        return await _run_intent_executor(channel)
    except Exception as exc:
        key = f"{type(exc).__name__}: {exc}"
        now = datetime.now(UTC)
        last = _error_memo.get(key)
        if last is not None and now - last < ERROR_REPOST_AFTER:
            _log.warning("Intent-Executor-Fehler (gedämpft, bekannt): %s", key)
            return f"error (gedämpft): {key}"
        _error_memo[key] = now
        raise


async def _run_intent_executor(channel=None) -> str:
    settings = get_settings()
    async with _intent_lock:
        now = datetime.now(UTC)
        with SessionLocal() as session:
            results: list[tuple[Intent, str]] = [
                (r, STATUS_FAILED) for r in recover_stale_executing(session, now)
            ]
            expired = expire_intents(session, now)
            due = due_intents(session, now)
            no_league = False
            if due:
                client, auth = await make_kickbase_client()
                league_id = auth.league_id
                if league_id is None:
                    no_league = True
                else:
                    executor = ActionExecutor(client, session, user_id=auth.user_id)
                    counts = today_intent_counts(session, now)
                    for i, intent in enumerate(due):
                        if i > 0:
                            await asyncio.sleep(settings.agent_write_spacing_seconds)
                        status = await execute_intent(
                            session, intent, executor, league_id, now=now, counts=counts
                        )
                        if status not in (STATUS_EXECUTED, STATUS_FAILED):
                            continue  # Claim verloren (Veto dazwischen) — gehört Emil
                        results.append((intent, status))
                        if status == STATUS_EXECUTED:
                            # L-077 vollendet: drei getrennte Töpfe fortschreiben.
                            if intent.kind == "bid":
                                counts = TodayCounts(
                                    counts.buys + 1, counts.sells, counts.lists
                                )
                            elif intent.kind == "list":
                                counts = TodayCounts(
                                    counts.buys, counts.sells, counts.lists + 1
                                )
                            elif intent.kind in ("sell", "accept"):
                                counts = TodayCounts(
                                    counts.buys, counts.sells + 1, counts.lists
                                )
            # Karten-Kehrer (Spec §4.5; L-088): Aufträge nur EINSAMMELN — die
            # Discord-I/O läuft nach der Sperre (Review 31.08.: hängendes
            # Discord hielt sonst _intent_lock minutenlang, Ticks fielen aus,
            # der Planner serialisierte und Sniper verpassten ihr Fenster).
            card_work = []
            if channel is not None:
                card_work = (
                    collect_card_work(session, datetime.now(UTC))
                    if settings.discord_intent_controls
                    else collect_card_retire_work(session, datetime.now(UTC))
                )
            msg = format_executor_report(
                results, expired, armed=bool(settings.agent_auto_intents)
            )
    if channel is not None and card_work:
        await apply_card_work(channel, card_work)
    if no_league:
        return "no_league"
    if msg is None:
        return "quiet"
    if channel is not None:
        await channel.send_info(msg, kind="executor")
    n_ok = sum(1 for _, s in results if s == STATUS_EXECUTED)
    return f"{n_ok} ausgeführt, {len(results) - n_ok} fehlgeschlagen, {len(expired)} abgelaufen"
