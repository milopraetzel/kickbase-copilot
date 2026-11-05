"""ActionExecutor — die einzige Stelle, an der Schreib-Aktionen ausgeführt werden.

Jede Aktion durchläuft dieselben Schutzschichten:
0. Verfassung — geschützte Spieler (`protected_player_ids`, Default Olise 8329)
   werden NIE verkauft, gelistet oder per Angebot abgegeben. Steht bewusst VOR
   dem Kill-Switch und gilt auch für die Pfade, die von allen anderen
   Invarianten ausgenommen sind (Anpfiff-Notbremse `kickoff-*`).
1. Idempotenz — kein Doppel-Gebot (unique idempotency_key, Race-sicher: ein
   paralleler Insert wird als 'duplicate' beantwortet, nie als Crash).
2. Kill-Switch — AGENT_DRY_RUN=1 ist der globale Not-Aus: es wird NIE live
   geschrieben, egal welche Parameter oder Auto-Flags gesetzt sind.
3. Live-Interlock — echte Writes nur in der Test-Liga, außer ein Schalter ärmt
   GENAU diesen Aktionstyp: agent_allow_live NUR Trades (bid, cancel_bid, sell,
   list, delist, accept, decline), lineup/bonus AUSSCHLIESSLICH über ihre
   eigenen Auto-Schalter, die Anpfiff-Notbremse über agent_auto_purge.
   Gilt AUCH im Freigabe-Pfad: ein Approval-Klick allein schreibt nur live,
   wenn der Interlock es erlaubt ODER agent_approve_live den Pfad ärmt.
4. Audit-Reihenfolge — 'pending' wird VOR dem API-Call committed: ein Absturz
   mitten im Request hinterlässt eine lokale Spur statt eines stillen
   Remote-Writes. Timeouts enden als 'unknown' (der Call KANN durch sein).
5. Freigabe-TTL + Preflight — Vorschläge verfallen nach 45 min (Alt-Zeilen ohne
   valid_until: created_at + 45 min); unmittelbar vor Live-Geld-Writes prüft
   ein Preflight frische Marktdaten (Standard: default_preflight am Client).

Status-Werte in transfer_actions:
- proposed        — wartet auf menschliche Freigabe (Discord-Buttons); nach
                    Ablauf der TTL recyclebar (zählt wie 'expired').
- pending         — geclaimt und committed, Dispatch läuft (oder Prozess starb mittendrin).
- executed        — Live-Write bestätigt.
- failed          — API-Call mit Fehler beendet; erneuter Versuch erlaubt.
- unknown         — Timeout: der Write KANN durchgegangen sein. KEIN blinder
                    Retry — erst manuell/per Reconcile gegen Kickbase prüfen.
- refused         — Interlock oder Preflight hat den Live-Write verweigert; Retry erlaubt.
- rejected        — Mensch hat abgelehnt; erneutes Staging erlaubt.
- expired         — Freigabe kam nach Ablauf von valid_until; erneutes Staging erlaubt.
- skipped_dry_run — Dry-Run: protokolliert, keine API berührt. Blockt KEINEN
                    späteren Live-Versuch am selben Tag (retryable).
- noop            — API-Call kam mit 200 zurück, hat aber NACHWEISLICH nichts
                    bewirkt (Prämie: Konto-Delta 0 — heute schon per App
                    kassiert oder noch nicht freigeschaltet). Kein Fehler, kein
                    Incident, retryable: der nächste Slot prüft erneut.
Nur als Ergebnis (nie in der DB): 'duplicate' — dieselbe Aktion existiert bereits.

Der Executor entscheidet NICHT, ob eine Aktion sinnvoll ist (das macht die
decision-Engine in Phase 2) — er führt eine bereits geplante Aktion sicher aus.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kickbase_agent.agent.freeze import freeze_veto
from kickbase_agent.config import get_settings
from kickbase_agent.db.models import Intent, TransferAction
from kickbase_agent.decision.funding_recovery import funding_review_veto
from kickbase_agent.decision.guardrails import (
    DEFAULT_GUARDRAILS,
    PROTECTED_ENV_VAR,
    protected_player_ids,
)
from kickbase_agent.decision.kickoff_freeze import EXPOSURE_GUARD_HOURS, bid_freeze_reason
from kickbase_agent.decision.lineup import MIN_SQUAD_SIZE, can_field_any_formation
from kickbase_agent.decision.purchase_review import (
    TEAM_PURPOSES,
    emergency_bench_fallback_allowed,
    is_engine_review,
    purchase_review_veto,
    team_funding_veto,
)
from kickbase_agent.kickbase import endpoints
from kickbase_agent.trading.expectation import estimate_bid_success
from kickbase_agent.trading.finance import overdraft_floor
from kickbase_agent.trading.trade_book import regular_fill


def abandoned_bid_ids(session: Session, league_id: str, now: datetime) -> set[str]:
    """Ein belegter Rückzug sperrt Wiedereinstieg bis zum Auktionsende.

    Pending/unknown bleiben gesperrt: ein Timeout beweist keinen Fehlschlag.
    """
    result: set[str] = set()
    for row in session.scalars(select(TransferAction).where(
        TransferAction.league_id == league_id, TransferAction.action_type == "cancel_bid",
        TransferAction.status.in_(("pending", "executed", "unknown")),
        TransferAction.dry_run.is_(False),
    )):
        review = (row.payload or {}).get("funding_review")
        if not isinstance(review, dict) or review.get("purpose") != "funding_abandon_bid":
            continue
        try:
            until = datetime.fromisoformat(review["abandon_until"])
        except (KeyError, TypeError, ValueError):
            continue
        if until.tzinfo is not None and now < until and row.player_id:
            result.add(str(row.player_id))
    return result


def confirmed_autonomous_bid(
    row: TransferAction | None, bid: dict, *, expires_at: datetime | None, now: datetime,
) -> bool:
    """Offer-ID ist nicht auktions-eindeutig: datiertes Listingende mitbinden."""
    if row is None or row.status != "executed" or not _is_autonomous(row.rule_id):
        return False
    proof = (row.payload or {}).get("confirmed_bid")
    if not isinstance(proof, dict) or expires_at is None or expires_at.tzinfo is None:
        return False
    try:
        original_end = datetime.fromisoformat(proof["expires_at"])
        observed = datetime.fromisoformat(proof["observed_at"])
    except (KeyError, TypeError, ValueError):
        return False
    if (original_end.tzinfo is None or observed.tzinfo is None
            or not observed <= now < original_end
            or abs((original_end - expires_at).total_seconds()) > 120):
        return False
    return (isinstance(proof, dict) and bool(bid.get("offer_id"))
            and proof.get("source") == "own_bids_after_executed_bid"
            and str(proof.get("offer_id")) == str(bid["offer_id"])
            and str(proof.get("player_id")) == str(bid.get("player_id"))
            and proof.get("amount_eur") == bid.get("amount") == row.amount)

# Blockierende Status: Aktion ist offen, erfolgreich oder ungeklärt — ein
# erneutes Anlegen derselben Aktion wäre ein potenzieller Doppel-Write.
# WICHTIG: 'proposed' gehört dazu (Review-Fund: fehlte → IntegrityError beim
# Re-Stage), ebenso 'unknown' (Timeout: kein blinder Retry vor dem Reconcile).
# Ein zeitlich abgelaufenes 'proposed' wird VOR diesem Check zu 'expired'
# umgeschrieben und blockt damit nicht mehr (Review-Fund: TTL-Lücke b).
_BLOCKING = ("pending", "proposed", "executed", "unknown")
#: Transportfehler, bei denen der Request den Server erreicht haben KANN
#: (Verbindung brach nach dem Senden ab) — werden als 'unknown' behandelt.
#: ConnectError (Verbindung gar nicht aufgebaut) bleibt bewusst 'failed'/retrybar.
_AMBIGUOUS_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    TimeoutError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.CloseError,
)
#: Gateway-Statuscodes, bei denen der Upstream den Write schon verarbeitet haben kann.
_AMBIGUOUS_STATUS = frozenset({502, 503, 504})
# Endzustände, die einen ECHTEN Retry erlauben: die alte Zeile wird ATOMAR
# geclaimt und wiederverwendet — ein zweiter Insert scheitert am unique
# idempotency_key, ein paralleler Claim an rowcount 0 (⇒ 'duplicate').
# 'skipped_dry_run' gehört dazu (Review-Fund): eine Dry-Run-Vormerkung darf
# ein Live-Nachziehen am selben Tag („mach den Trade" nach der Vorschau) nie
# blocken — der Live-Versuch recycelt die Vormerkungs-Zeile.
# 'noop' (Live-Fund 23.08.): /bonus/collect antwortet IMMER 200 + leeres `it`,
# auch wenn 0 € fließen (Prämie schon per App kassiert — Feed t=22 um 02:28 —
# oder noch nicht frei); die 'executed'-Zeile des 02:53-Nachholers blockte dann
# den 08:03-Slot ('duplicate'). Ein wirkungsloser Call darf den Tag nicht
# verbrauchen — nur eine GEMESSENE Gutschrift zählt.
_RETRYABLE = ("failed", "refused", "rejected", "expired", "skipped_dry_run", "noop")

# Freigabe-TTL: ein Discord-Klick Stunden später darf kein veraltetes Gebot
# ausführen — nach Ablauf endet die Freigabe als 'expired'.
PROPOSAL_TTL = timedelta(minutes=45)
# Preflight: liegt der aktuelle Marktpreis mehr als +10 % über dem beim Staging
# gespeicherten Betrag, ist der Vorschlag veraltet → refused statt Kauf.
PREFLIGHT_MAX_PRICE_DRIFT = 0.10
# Preflight sell/list: weicht der aktuelle Marktwert mehr als ±20 % vom beim
# Staging gespeicherten Betrag ab, hat sich der Zustand geändert → refused,
# neu entscheiden. AUSNAHME: Kickoff-Notverkäufe (rule_id 'kickoff-*') dürfen
# auch bei Preisrutsch verkaufen — die Notbremse rettet den Spieltag, nicht
# den Kurs (der Kader-Check gilt für sie trotzdem).
PREFLIGHT_MAX_VALUE_DRIFT = 0.20

# Geld-/Trade-Aktionstypen: NUR diese ärmt AGENT_ALLOW_LIVE. Aufstellung und
# Bonus laufen AUSSCHLIESSLICH über ihre eigenen Auto-Schalter (Review-Fund P0:
# vorher schaltete allow_live ALLES frei — Auto-Lineup/Bonus/Purge konnten live
# laufen, obwohl ihre Flags aus waren, während die Job-Nachrichten das
# Gegenteil behaupteten).
_TRADE_ACTION_TYPES = ("bid", "cancel_bid", "sell", "list", "delist", "accept", "decline")

#: Aktionen, mit denen ein Spieler den Kader VERLASSEN kann. Genau sie sperrt die
#: Verfassung für geschützte Spieler (`delist` bleibt erlaubt — ein Listing
#: zurückzuziehen schützt ihn ja gerade).
_DISPOSAL_ACTION_TYPES = ("sell", "list", "accept")


def _protected_veto(action: PlannedAction) -> str | None:
    """Verfassungs-Sperre: geschützte Spieler werden NIE abgegeben; None = frei.

    Emils Anweisung vom 25.08.26 („Verkaufe nicht Olise, aber sonst mach einfach
    die Entscheidung") war bis zum 26.08. blosse Doktrin — `protected_player_ids`
    war in JEDEM Produktionspfad leer, weil alle Aufrufer DEFAULT_GUARDRAILS
    benutzen und dessen Liste per Default leer ist. Damit hätte jeder autonome
    Verkaufspfad (Anpfiff-Notbremse, Auto-Trading, Intent-Executor,
    propose_sells) ihn verkaufen können.

    Der Schutz sitzt deshalb HIER, an der einzigen Schreibstelle: Was hier
    scheitert, scheitert für alle Pfade — auch für die, die von den übrigen
    Invarianten ausgenommen sind (kickoff-*: von Caps und MW-Drift befreit,
    von der Verfassung nicht).
    """
    if action.action_type not in _DISPOSAL_ACTION_TYPES:
        return None
    pid = str(action.player_id or "")
    if not pid or pid not in protected_player_ids():
        return None
    return (
        f"Geschützter Spieler {pid}: {action.action_type} verweigert — "
        "Verfassung (protected_player_ids), gilt für JEDEN Pfad inklusive der "
        f"Anpfiff-Notbremse. Änderbar nur über {PROTECTED_ENV_VAR} (.env, "
        "Emils Hoheit), nicht durch den Agenten."
    )

#: Autonome rule_id-Präfixe: für sie gilt der Starter-Schutz hart — Emils
#: manuelle Wege (mcp-*, mit confirm/emil_go) und die Notbremse (kickoff-*)
#: dürfen aufgestellte Spieler weiter verkaufen (Tausch/letzte Rettung).
_AUTONOMOUS_RULE_PREFIXES = ("intent-", "auto-trading")


def _is_autonomous(rule_id: str | None) -> bool:
    """Autonome Pfade — MANUELLE Remote-Vorhaben (Suffix '-manual', Zwei-DB-Fix)
    tragen zwar das intent-Präfix (Arming), sind aber Emils Wille: der
    Starter-Schutz greift für sie NICHT (Codex-Folge-Review 24.08., P2)."""
    rid = rule_id or ""
    if rid.endswith("-manual"):
        return False
    return any(rid.startswith(p) for p in _AUTONOMOUS_RULE_PREFIXES)


def _curated_bid_target_veto(
    fitness: Any,
    probability: Any,
    change: Any,
    *,
    purpose: str | None = None,
    allow_deficit_filler: bool = False,
) -> str | None:
    """Mannschaftskäufe nach Rolle prüfen; Tagesdrift ist dort kein Kaufverbot."""
    if type(fitness) is not int or (not allow_deficit_filler and fitness != 0):
        return "Kaufprüfung: Zielspieler ist nicht ausdrücklich fit oder Fitness unbekannt."
    allowed = (1, 2, 3, 4, 5) if purpose == "lineup_completion" else (1, 2, 3)
    if type(probability) is not int or probability not in allowed:
        return "Kaufprüfung: Kickbase-Bank-Veto oder Startelf-Stufe unbekannt."
    if purpose not in TEAM_PURPOSES and (type(change) is not int or change <= 0):
        return "Kaufprüfung: frische 24h-Marktwertänderung nicht messbar positiv."
    return None


#: Positions-Minima über alle 9 Kickbase-Formationen (1 TW / 3 ABW / 2 MF / 1 ST):
#: Solange sie stehen, lässt sich JEDE Formation durch Zukäufe wieder auffüllen.
_POSITION_MINIMUMS = {1: 1, 2: 3, 3: 2, 4: 1}


def _position_minimums_kept(counts: dict[int, int]) -> bool:
    return all(counts.get(pos, 0) >= need for pos, need in _POSITION_MINIMUMS.items())

# Eigener Logger je API-Write: bei einem 22:15-Timeout sofort sichtbar, welcher
# Request wie lange hing (systemd/journalctl fängt stdout).
_write_log = logging.getLogger("kickbase_agent.writes")


class WriteClient(Protocol):
    async def place_bid(self, league_id: str, player_id: str, price: int) -> dict[str, Any]: ...
    async def cancel_bid(
        self, league_id: str, player_id: str, offer_id: str
    ) -> dict[str, Any]: ...
    async def accept_offer(
        self, league_id: str, player_id: str, offer_id: str
    ) -> dict[str, Any]: ...
    async def decline_offer(
        self, league_id: str, player_id: str, offer_id: str
    ) -> dict[str, Any]: ...
    async def list_player(
        self, league_id: str, player_id: str, price: int
    ) -> dict[str, Any]: ...
    async def delist_player(self, league_id: str, player_id: str) -> dict[str, Any]: ...
    async def sell_to_kickbase(self, league_id: str, player_id: str) -> dict[str, Any]: ...
    async def set_lineup(
        self, league_id: str, formation: str, player_ids: list[str | None]
    ) -> dict[str, Any]: ...
    async def collect_bonus(self) -> dict[str, Any]: ...


@dataclass
class PlannedAction:
    action_type: str  # bid|cancel_bid|accept|decline|list|delist|sell|lineup|bonus
    league_id: str
    player_id: str | None = None
    amount: int | None = None
    offer_id: str | None = None
    formation: str | None = None
    # Aufstellung: Platzliste; None = leerer Platz einer Teil-Elf (L-109).
    player_ids: list[str | None] | None = None
    rule_id: str | None = None
    rationale: str | None = None
    # Kader-Bedarf (26.08.): Dieses Gebot schließt eine Lücke, ohne die die
    # Formation nicht steht (Positions-Not im Planner, `squad_need=True` beim
    # manuellen MCP-Gebot). NUR dann gilt die höhere Overpay-Kappe
    # (max_bid_premium_need statt max_bid_premium). Default False — die höhere
    # Prämie ist die begründete Ausnahme, nicht der neue Normalfall.
    # Landet über `asdict` im Payload und kommt über `_action_from_payload`
    # zurück; Alt-Zeilen ohne das Feld fallen konservativ auf False.
    squad_need: bool = False
    # Tausch-Verkauf (02.09.): Der Verkauf hat gewartet, bis der Ersatz im Kader
    # steht (after_squad_size) — für ihn gilt der Starter-Schutz autonomer Pfade
    # nicht (Emils Regel „Verlierer nicht halten"; Kader-Deckung prüft (a2)).
    swap: bool = False
    # Kuratierte Kaufentscheidung mit unveränderlicher wirtschaftlicher Preisgrenze.
    # Alte autonome Aktionen ohne diesen Nachweis dürfen keine neuen Gebote auslösen.
    purchase_review: dict[str, Any] | None = None
    funding_review: dict[str, Any] | None = None
    # Emils Wortlaut (17.09.) wird NUR mitgeführt und im Audit dokumentiert. Er
    # ersetzt den Engine-Beleg NICHT: Ein Operator-Gebot (mcp-bid /
    # intent-bid-manual) braucht weiterhin eine frische Engine-Auswahl
    # (tests/agent/test_operator_engine_gate.py); kein Code-Pfad wertet dieses
    # Feld aus. Richtigstellung 21.09.2026 (Paket 1, P0-6) — der frühere Kommentar
    # beschrieb einen nie eingespielten Override und kostete am 18.09. Zeit.
    # Landet über `asdict` im Payload und kommt über `_action_from_payload` zurück.
    emil_go: str = ""


@dataclass
class ActionResult:
    # executed|skipped_dry_run|refused|duplicate|failed|unknown|proposed|rejected|expired
    status: str
    action_id: int | None
    raw_response: dict[str, Any] | None = None
    message: str = ""


@dataclass
class PreflightState:
    """Frischer Markt-Blick unmittelbar vor einem Live-Geld-Write (bid/sell/list).

    None-Felder bedeuten "nicht ermittelbar" — geprüft wird nur, was da ist.
    """

    budget: int | None = None  # aktuelles Konto
    # bid: Konto NACH Annahme aller offenen Gebote (Kickbase-Feld `pbaa`) — genau
    # die Größe, die Kickbase selbst gegen die 33-%-Regel prüft. None ⇒ Konto.
    budget_after_bids: int | None = None
    team_value: int | None = None  # bid: Summe der Kader-Marktwerte (33-%-Basis)
    on_market: bool | None = None  # bid: Spieler noch am Transfermarkt?
    current_price: int | None = None  # bid: aktueller Marktpreis
    in_squad: bool | None = None  # sell/list: Spieler noch im eigenen Kader?
    current_value: int | None = None  # sell/list: aktueller Marktwert des Kaderspielers
    # bid: nächste Spieltags-Deadline (frühester Anpfiff, aware UTC) fürs
    # einheitliche T-120-Sicherheitsgrenze (decision/kickoff_freeze.py).
    # None = keine bekannte Deadline (Saisonpause) ⇒ kein Fenster aktiv.
    matchday_deadline: datetime | None = None
    # sell/accept: FRISCHE Positions-Zählung des Kaders (pid → 1..4) für die
    # zentrale Deckungs-Prüfung (Codex-Re-Check 24.08., P1: getrennt geplante
    # Verkäufe/Annahmen dürfen GEMEINSAM nie unter 11/Formation führen).
    squad_positions: dict[str, int] | None = None
    # accept: existiert das Angebot (uoid) mit GENAU dem Staging-Betrag noch am
    # eigenen Listing? (Codex-Re-Check 24.08., P2: zurückgezogene/ersetzte
    # Angebote nicht blind dispatchen.) None = nicht ermittelbar.
    offer_intact: bool | None = None
    # bid: aktueller MARKTWERT des Zielspielers (≠ Listing-Preis) — Basis der
    # zentralen Overpay-Kappe (max_bid_premium). None = nicht ermittelbar.
    current_mv: int | None = None
    # bid: Manager-Listing (Markt-`u`-Verkäufer)? Kickbase lehnt dort Gebote
    # unter dem aktuellen MW mit err 5080 „UnderpayNotAllowed" ab (24.08.);
    # Kickbase-Listings (kein Verkäufer) erlauben Underpay. None = unbekannt.
    is_manager_listing: bool | None = None
    # bid: Vereinslimit-Rechnung fürs Ziel-Team — Kaderspieler + offene Gebote
    # desselben Clubs NACH diesem Gebot vs. Liga-Limit (lpc, live aus den Regeln).
    club_count_after: int | None = None
    club_limit: int | None = None
    # sell/accept: player_ids der AKTUELL AUFGESTELLTEN Elf (Squad-Feld `lo`,
    # live verifiziert) — Starter-Schutz für autonome Pfade. None = nicht geholt.
    starter_ids: frozenset[str] | None = None
    # Kuratierte Gebote: Markt-Fitness/KB-Stufe und echte Tagesänderung aus einem
    # unmittelbar zuvor gelesenen Spieler-Detail; der mvt-Pfeil ist kein Ersatz.
    target_fitness_status: int | None = None
    target_lineup_prob: int | None = None
    target_mv_change_24h: int | None = None
    # Vollständige eigene Verpflichtungen für die gemeinsame Engine-Finanzierung.
    own_bids: list[dict] | None = None
    listing_expires_at: datetime | None = None
    # accept: liegt am selben eigenen Listing bereits ein HÖHERES Angebot vor?
    higher_offer_eur: int | None = None
    # sell: Fitness, Startelf-Stufe und 24h-Trend des eigenen Kaderspielers.
    squad_fitness_status: int | None = None
    squad_lineup_prob: int | None = None
    squad_mv_change_24h: int | None = None
    # lineup: unfitness-markierter Starter trotz fittem Bank-Ersatz derselben Position.
    lineup_unfit_starter: str | None = None


# Callback, der zur Aktion frische Daten holt. Wird im Konstruktor automatisch
# auf default_preflight(client) verdrahtet, sobald ein Client da ist (Review-
# Fund: der optionale Callback war in keinem produktiven Aufrufer gesetzt).
PreflightCb = Callable[[PlannedAction], Awaitable[PreflightState]]


def default_preflight(
    client: Any, user_id: str | None = None, *, now: datetime | None = None,
) -> PreflightCb:
    """Produktions-Preflight am echten Client: frische Daten vor Geld-Writes.

    Für Gebote holt der Callback unmittelbar vor dem Dispatch Kontostand
    (client.get_budget, dazu `pbaa` = Konto nach offenen Geboten), Kader
    (Teamwert) und Transfermarkt (client.get_market) und baut daraus den
    PreflightState für _preflight_veto: Spieler nicht mehr gelistet? Preis
    > +10 % über dem Staging-Betrag? Gebot sprengt die 33-%-Regel (Emils
    Regel 23.08.: bieten zuerst, verkaufen erst bei Zuschlag — kein Bargeld
    nötig; ohne Teamwert konservativ cash-only)?
    Für sell/list holt er den eigenen Kader (client.get_squad): Spieler nicht
    (mehr) im Kader? Marktwert > ±20 % vom Staging-Betrag gedriftet (Review-
    Fund P0: sell/list liefen mit leerem State durch)? Wirft der Client
    (Methode fehlt, API down), greift fail-closed in _preflight_veto: kein
    Live-Write ohne frische Daten.
    """

    async def cb(action: PlannedAction) -> PreflightState:
        if action.action_type == "cancel_bid" and action.funding_review is not None:
            if not user_id:
                raise ValueError("Deckungsrückzug: eigene Manager-ID fehlt")
            squad = await client.get_squad(action.league_id)
            bids = await client.get_own_bids(action.league_id, user_id)
            listing = next((r for r in await client.get_market(action.league_id)
                            if str(r.player.id) == str(action.player_id)), None)
            return PreflightState(
                budget=await client.get_budget(action.league_id), own_bids=bids,
                squad_positions={str(sp.player.id): int(sp.player.position) for sp in squad},
                listing_expires_at=listing.expires_at if listing else None,
            )
        if action.action_type == "bid":
            engine_review = is_engine_review(action.purchase_review)
            review_dict = action.purchase_review or {}
            swap_sale = review_dict.get("swap_sale")
            team_plan_dict = review_dict.get("team_plan") or {}
            budget = await client.get_budget(action.league_id)
            deficit_swap_completion = (
                isinstance(swap_sale, dict)
                and bool(swap_sale.get("player_id"))
                and (
                    (budget is not None and budget < 0)
                    or int(team_plan_dict.get("budget_eur") or 0) < 0
                )
            )
            purpose = (
                "lineup_completion"
                if deficit_swap_completion
                else review_dict.get("purpose")
            )
            allow_deficit_filler = (
                deficit_swap_completion
                and type(action.amount) is int
                and action.amount <= 1_000_000
            )
            squad_rows = None
            own_bid_rows = None
            if engine_review:
                if not user_id:
                    raise ValueError("Mannschaftsplan: eigene Manager-ID für Gebotsprüfung fehlt")
                squad_rows = await client.get_squad(action.league_id)
                own_bid_rows = await client.get_own_bids(action.league_id, user_id)
            listing = next(
                (
                    mp
                    for mp in await client.get_market(action.league_id)
                    if str(mp.player.id) == str(action.player_id)
                ),
                None,
            )
            # Zentrale Invarianten (Codex-P1, 24.08.): Overpay-Kappe braucht den
            # MW des Ziels; das Vereinslimit zählt Kader + offene Gebote des
            # Ziel-Clubs (Regel 5) gegen das Liga-Limit (lpc, live).
            current_mv: int | None = None
            is_manager: bool | None = None
            club_count_after: int | None = None
            club_limit: int | None = None
            target_fitness: int | None = None
            target_probability: int | None = None
            target_change: int | None = None
            if listing is not None:
                current_mv = int(listing.player.market_value)
                if _is_autonomous(action.rule_id) or action.purchase_review is not None:
                    target_fitness = getattr(listing.player, "fitness_status", None)
                    target_probability = getattr(listing.player, "lineup_prob", None)
                    # Marktlisten liefern tfhmvt oft gar nicht. Genau EIN frischer
                    # Detail-GET prüft das ausgewählte Ziel ohne Pfeil-/Cache-Ersatz.
                    detail = await client.get_player(action.league_id, str(action.player_id))
                    if str(getattr(detail, "id", "")) != str(action.player_id):
                        raise ValueError("Kaufprüfung: Spielerdetail gehört nicht zum Zielspieler")
                    target_change = getattr(detail, "mv_change_24h", None)
                    market_veto = _curated_bid_target_veto(
                        target_fitness,
                        target_probability,
                        target_change,
                        purpose=purpose,
                        allow_deficit_filler=allow_deficit_filler,
                    )
                    if market_veto is not None:
                        raise ValueError(market_veto)
                    target_veto = _curated_bid_target_veto(
                        getattr(detail, "fitness_status", None),
                        getattr(detail, "lineup_prob", None),
                        target_change,
                        purpose=purpose,
                        allow_deficit_filler=allow_deficit_filler,
                    )
                    if target_veto is not None:
                        raise ValueError(target_veto)
                    detail_mv = getattr(detail, "market_value", None)
                    if type(detail_mv) is not int or detail_mv <= 0:
                        raise ValueError("Kaufprüfung: aktueller Detail-Marktwert unbekannt")
                    current_mv = detail_mv
                    target_fitness = getattr(detail, "fitness_status", None)
                    target_probability = max(target_probability, detail.lineup_prob)
                # Verkäufer-Feld = Manager-Listing (MarketPlayer.seller_id;
                # leer = Kickbase-Listing) — Basis der Underpay-Sperre unten.
                is_manager = bool(getattr(listing, "seller_id", None) or "")
                target_tid = str(listing.player.team_id or "")
                if target_tid:
                    rules = await client.get_league_rules(action.league_id)
                    club_limit = int(rules.get("max_players_per_club") or 0) or None
                    if club_limit is not None:
                        count = sum(
                            1
                            for sp in (squad_rows if squad_rows is not None
                                       else await client.get_squad(action.league_id))
                            if str(sp.player.team_id or "") == target_tid
                        )
                        if user_id:
                            count += sum(
                                1
                                for b in (own_bid_rows if own_bid_rows is not None
                                          else await client.get_own_bids(action.league_id, user_id))
                                if str(b.get("team_id") or "") == target_tid
                                and str(b.get("player_id") or "")
                                != str(action.player_id)
                            )
                        club_count_after = count + 1
            # 33-%-Regel (Emils Regel 23.08.: bieten zuerst, verkaufen bei
            # Zuschlag): Teamwert aus dem frischen Kader, offene Gebote über
            # Kickbase' eigenes `pbaa`. Fehlt eines davon, fällt _preflight_veto
            # auf den konservativen cash-only-Vertrag zurück.
            team_value: int | None = None
            try:
                team_value = sum(
                    int(sp.player.market_value)
                    for sp in (squad_rows if squad_rows is not None
                               else await client.get_squad(action.league_id))
                )
            except Exception:  # noqa: BLE001 — ohne Teamwert gilt cash-only
                team_value = None
            after_bids: int | None = None
            read_fields = getattr(client, "get_budget_fields", None)
            if read_fields is not None:
                try:
                    fields = await read_fields(action.league_id)
                    if isinstance(fields, dict) and fields.get("pbaa") is not None:
                        after_bids = int(fields["pbaa"])
                except Exception:  # noqa: BLE001 — dann zählt nur das Konto
                    after_bids = None
            # pbaa-Präzisierung (01.09., L-093, Emils GO + Neu-Siegelung):
            # Kickbase schickt `pbaa` NUR bei offenen eigenen Geboten — bei null
            # Geboten fehlt das Feld STRUKTURELL (Produktionspfad-Messung:
            # /me/budget liefert dann nur b/pbas). Der pauschale fail-closed-
            # Refuse sperrte damit genau das ERSTE Gebot aus (kein Gebot ⇒ kein
            # pbaa ⇒ kein Gebot — Maluze und Nguefack, 31.08.). Bei frisch
            # verifizierten NULL offenen Geboten ist Konto-nach-Geboten
            # mathematisch exakt das Konto (nichts zu subtrahieren). Bei
            # vorhandenen Geboten ohne pbaa oder scheiterndem Gebots-Read
            # bleibt fail-closed.
            if after_bids is None and user_id:
                try:
                    if own_bid_rows is None:
                        own_bid_rows = await client.get_own_bids(action.league_id, user_id)
                    if len(own_bid_rows) == 0:
                        after_bids = budget
                except Exception:  # noqa: BLE001 — unbestimmbar bleibt fail-closed
                    pass
            # Spieltags-Deadline fürs T-120-Fenster: BEWUSST ohne try —
            # wirft get_matchdays (API down), bricht der gesamte Preflight
            # fail-closed ab (Regel: Konto ≥ 0 beim Anpfiff schlägt jeden Trade).
            # Lokaler Import wie kickoff_guard→executor: kein Schicht-Zyklus.
            from kickbase_agent.worker.kickoff_guard import matchday_deadline

            fixtures = await client.get_matchdays()
            if not fixtures:
                # Codex-Re-Check 24.08. (P1): eine ERFOLGREICHE, aber leere
                # Antwort würde deadline=None liefern und Freeze/Exposure
                # still abschalten. Leerer Spielplan = Datenfehler, nicht
                # Saisonpause (die hat bekannte, vergangene Fixtures).
                raise ValueError(
                    "Spielplan leer — Deadline-Fenster nicht bestimmbar (fail-closed)"
                )
            upcoming = matchday_deadline(
                fixtures, now if now is not None else datetime.now(UTC),
            )
            if engine_review:
                # Das Konto zuletzt lesen: vorherige erfolgreiche Plankäufe sind
                # bereits bezahlt; ihre neuen Kaderplätze werden nicht erneut reserviert.
                budget = await client.get_budget(action.league_id)
                if own_bid_rows is not None and all(
                    type(b.get("amount")) is int and b["amount"] > 0 for b in own_bid_rows
                ):
                    after_bids = budget - sum(b["amount"] for b in own_bid_rows)
            return PreflightState(
                budget=budget,
                budget_after_bids=after_bids,
                team_value=team_value,
                on_market=listing is not None,
                current_price=None if listing is None else listing.price,
                matchday_deadline=None if upcoming is None else upcoming[1],
                current_mv=current_mv,
                is_manager_listing=is_manager,
                club_count_after=club_count_after,
                club_limit=club_limit,
                target_fitness_status=target_fitness,
                target_lineup_prob=target_probability,
                target_mv_change_24h=target_change,
                own_bids=own_bid_rows,
                squad_positions=(
                    {str(sp.player.id): int(sp.player.position) for sp in squad_rows}
                    if squad_rows is not None else None
                ),
            )
        if action.action_type in ("sell", "list", "accept"):
            squad = await client.get_squad(action.league_id)
            member = next(
                (sp for sp in squad if str(sp.player.id) == str(action.player_id)),
                None,
            )
            positions = {str(sp.player.id): int(sp.player.position) for sp in squad}
            # Starter-Schutz (Emils Regel, zentral seit 24.08.): die AKTUELL
            # aufgestellte Elf aus dem rohen Squad (`lo` = Lineup-Order, live
            # verifiziert) — nur für autonome Pfade geholt (intent-/auto-
            # trading); Emils manuelle Wege und die Notbremse bleiben frei.
            starter_ids: frozenset[str] | None = None
            deadline: datetime | None = None
            if action.action_type in ("sell", "accept") and _is_autonomous(action.rule_id):
                raw_squad = await client._get(f"/v4/leagues/{action.league_id}/squad")
                starter_ids = frozenset(
                    str(it.get("i"))
                    for it in raw_squad.get("it", []) or []
                    if "lo" in it
                )
                # T-120-Kader-Fenster (09.09., Emils Präzisierung): dieselbe Frist wie im
                # Gebotszweig, ebenso fail-closed. Ohne sie war das Fenster im Betrieb seit
                # 02.09. immer zu (Befund 02.10.2026, Emils GO). Nur autonome Abgänge prüfen
                # das Fenster — Notbremse und Emils Wege bekommen keinen neuen Abruf, an
                # dem sie scheitern könnten.
                from kickbase_agent.worker.kickoff_guard import matchday_deadline

                fixtures = await client.get_matchdays()
                if not fixtures:
                    raise ValueError(
                        "Spielplan leer — Deadline-Fenster nicht bestimmbar (fail-closed)"
                    )
                upcoming = matchday_deadline(
                    fixtures, now if now is not None else datetime.now(UTC),
                )
                deadline = None if upcoming is None else upcoming[1]
            offer_intact: bool | None = None
            higher_offer_eur: int | None = None
            if action.action_type == "accept":
                # Angebot frisch verifizieren: das eigene Listing (it.i ==
                # player_id) muss in ofs[] GENAU die uoid mit dem Staging-
                # Betrag tragen — sonst wurde es zurückgezogen/ersetzt.
                # Roher Markt wie im Planner; wirft der Call, greift der
                # äußere fail-closed-Vertrag des Preflights.
                raw = await client._get(endpoints.market(action.league_id))
                offer_intact = any(
                    str(it.get("i")) == str(action.player_id)
                    and any(
                        str(o.get("uoid", "")) == str(action.offer_id or "")
                        and int(o.get("uop", 0) or 0) == int(action.amount or 0)
                        for o in it.get("ofs", []) or []
                    )
                    for it in raw.get("it", []) or []
                )
                higher_offers = [
                    int(o.get("uop", 0) or 0)
                    for it in raw.get("it", []) or []
                    if str(it.get("i")) == str(action.player_id)
                    for o in it.get("ofs", []) or []
                    if str(o.get("uoid", ""))
                    and int(o.get("uop", 0) or 0) > int(action.amount or 0)
                ]
                higher_offer_eur = max(higher_offers) if higher_offers else None
            return PreflightState(
                in_squad=member is not None,
                current_value=None if member is None else member.player.market_value,
                squad_positions=positions,
                offer_intact=offer_intact,
                higher_offer_eur=higher_offer_eur,
                starter_ids=starter_ids,
                matchday_deadline=deadline,
                squad_fitness_status=(
                    getattr(member.player, "fitness_status", None) if member is not None else None
                ),
                squad_lineup_prob=(
                    getattr(member.player, "lineup_prob", None) if member is not None else None
                ),
                squad_mv_change_24h=(
                    getattr(member.player, "mv_change_24h", None) if member is not None else None
                ),
            )
        if action.action_type == "delist":
            raw = await client._get(endpoints.market(action.league_id))
            items = raw.get("it", []) or []
            on_market = (
                any(str(it.get("i")) == str(action.player_id) for it in items)
                if items else None
            )
            return PreflightState(on_market=on_market)
        if action.action_type == "decline":
            raw = await client._get(endpoints.market(action.league_id))
            items = raw.get("it", []) or []
            offer_intact = (
                any(
                    str(it.get("i")) == str(action.player_id)
                    and any(
                        str(o.get("uoid", "")) == str(action.offer_id or "")
                        for o in it.get("ofs", []) or []
                    )
                    for it in items
                )
                if items else None
            )
            return PreflightState(offer_intact=offer_intact)
        if action.action_type == "lineup":
            squad = await client.get_squad(action.league_id)
            by_id = {str(sp.player.id): sp.player for sp in squad}
            fielded_ids = [str(pid) for pid in (action.player_ids or []) if pid is not None]
            unfit_starter: str | None = None
            if fielded_ids and all(pid in by_id for pid in fielded_ids):
                fielded_set = set(fielded_ids)
                for pid in fielded_ids:
                    p = by_id[pid]
                    fit = getattr(p, "fitness_status", None)
                    pos = getattr(p, "position", None)
                    if type(fit) is int and fit != 0 and type(pos) is int:
                        has_fit_bench = any(
                            str(other_id) not in fielded_set
                            and getattr(other_p, "position", None) == pos
                            and type(getattr(other_p, "fitness_status", None)) is int
                            and getattr(other_p, "fitness_status", None) == 0
                            for other_id, other_p in by_id.items()
                        )
                        if has_fit_bench:
                            unfit_starter = pid
                            break
            return PreflightState(lineup_unfit_starter=unfit_starter)
        return PreflightState()

    return cb


def idempotency_key(action: PlannedAction, day: str) -> str:
    if (action.action_type == "cancel_bid" and isinstance(action.funding_review, dict)
            and action.funding_review.get("purpose") == "funding_abandon_bid"):
        # Offer-ID kann die Manager-ID sein. Die ursprüngliche Audit-ID bindet
        # das bestätigte Listing; Review-Zeitdrift/Mitternacht öffnen keinen Retry.
        day = f"funding-auction-{action.funding_review.get('original_bid_action_id')}"
    parts = [
        action.league_id,
        action.action_type,
        action.player_id or "",
        str(action.amount if action.amount is not None else ""),
        action.offer_id or "",
        # Aufstellung: die konkrete Elf gehört in den Schlüssel — eine GEÄNDERTE
        # Aufstellung am selben Tag (z. B. Spätausfall nach dem 19:45-Set) muss
        # durchgehen; nur die IDENTISCHE Elf ist ein Duplikat.
        action.formation or "",
        ",".join(pid or "-" for pid in action.player_ids or []),
        day,
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


class ActionExecutor:
    def __init__(
        self,
        client: WriteClient | None,
        session: Session,
        preflight: PreflightCb | Literal[False] | None = None,
        user_id: str | None = None,
    ) -> None:
        self._client = client
        self._user_id = user_id
        self._reanchored: tuple[int, int] | None = None
        self._session = session
        # Zustand des zuletzt gelaufenen Preflights (nur Diagnose/Erwartung).
        self._last_preflight: PreflightState | None = None
        # Preflight-Verdrahtung: None (Default) heißt, mit Client wird automatisch
        # default_preflight(client) benutzt — damit ist JEDE produktive Instanz
        # geschützt, ohne dass Aufrufer etwas ändern müssen (Review-Fund: der
        # Callback war nirgends gesetzt). Bewusstes Abschalten: preflight=False.
        # Ein eigener Callback ersetzt den Default. Ohne Client (z. B. reiner
        # Reject-Pfad) gibt es nichts zu prüfen — kein Preflight.
        self._preflight: PreflightCb | None
        self._uses_default_preflight = preflight is None and client is not None
        if preflight is None and client is not None:
            self._preflight = default_preflight(client, user_id=user_id)
        elif preflight is None or preflight is False:
            self._preflight = None
        else:
            self._preflight = preflight

    async def execute(
        self, action: PlannedAction, *, dry_run: bool | None = None, now: datetime | None = None
    ) -> ActionResult:
        settings = get_settings()
        stamp = now or datetime.now(UTC)
        # Kill-Switch: AGENT_DRY_RUN=1 gewinnt IMMER. Der Parameter kann Dry-Run
        # nur zusätzlich erzwingen (dry_run=True), aber nie aufheben — vorher
        # konnte ein Aufrufer mit dry_run=False am globalen Not-Aus vorbei live
        # schreiben (Review-Fund).
        effective_dry = bool(settings.agent_dry_run) or dry_run is True
        key = idempotency_key(action, stamp.date().isoformat())

        existing = self._find_existing(key)
        if existing is not None:
            # TTL-Lücke b (Review-Fund): ein zeitlich abgelaufenes 'proposed'
            # zählt als recyclebar (wie 'expired') und blockt nicht mehr.
            self._expire_stale_proposed(existing, stamp)
        if existing is not None and existing.status in _BLOCKING:
            return ActionResult(
                status="duplicate", action_id=existing.id, message="bereits vorhanden"
            )
        if existing is not None:
            # Endzustand (failed/refused/rejected/expired/skipped_dry_run):
            # echte Zeile ATOMAR claimen und recyceln statt Insert-Konflikt
            # auf dem unique idempotency_key.
            row = self._recycle(existing, action, status="pending", stamp=stamp, dry=effective_dry)
            if row is None:
                return ActionResult(
                    status="duplicate",
                    action_id=existing.id,
                    message="bereits vorhanden (paralleler Retry-Claim)",
                )
        else:
            row = TransferAction(
                league_id=action.league_id,
                action_type=action.action_type,
                player_id=action.player_id,
                amount=action.amount,
                status="pending",
                idempotency_key=key,
                rule_id=action.rule_id,
                rationale=action.rationale,
                dry_run=effective_dry,
                created_at=stamp,
            )
            self._session.add(row)

        if action.purchase_review is not None:
            row.payload = {**(row.payload or {}),
                           "purchase_review": asdict(action)["purchase_review"]}
        if action.funding_review is not None:
            row.payload = {**(row.payload or {}), "offer_id": action.offer_id,
                           "funding_review": asdict(action)["funding_review"]}
        # Audit VOR dem Dispatch: der Commit persistiert 'pending', BEVOR die
        # externe API angefasst wird — ein Absturz mittendrin hinterlässt eine
        # Spur statt eines stillen Remote-Writes. Zugleich materialisiert der
        # Commit den Unique-Konflikt, falls ein paralleler Prozess denselben
        # Schlüssel zwischen Check und Insert angelegt hat (Race).
        if (dup := self._commit_or_duplicate(key)) is not None:
            return dup

        # VERFASSUNG vor allem anderen — auch vor dem Kill-Switch (26.08.):
        # Ein geschützter Spieler darf im Schatten-Modus nicht als harmloses
        # 'skipped_dry_run' durchlaufen, sonst liest sich der Bericht so, als
        # wäre der Verkauf bloß wegen AGENT_DRY_RUN unterblieben. Geschrieben
        # wird in beiden Fällen nichts; hier steht nur der ehrliche Grund.
        if (protected := _protected_veto(action)) is not None:
            _write_log.warning("verfassung: %s", protected)
            return self._resolve(row, "refused", stamp, rationale=protected)

        if effective_dry:
            return self._resolve(row, "skipped_dry_run", stamp)

        # Selbst-Einfrieren (agent/freeze.py) steht VOR dem Interlock: Hat der
        # Agent sich stillgelegt, ist DAS der Grund, den man hören will — nicht
        # die Frage, welches Flag zufällig auch noch fehlt.
        frozen = freeze_veto(self._session, action.action_type, action.rule_id)
        if frozen is not None:
            return self._resolve(row, "refused", stamp, rationale=frozen)

        if not self._live_allowed(action, settings):
            return self._resolve(
                row, "refused", stamp, rationale=_live_refusal_reason(action)
            )

        cap = self._daily_cap_veto(action, stamp)
        if cap is not None:
            return self._resolve(row, "refused", stamp, rationale=cap)

        self._reanchored = None
        veto = await self._preflight_veto(action, stamp)
        if veto is not None:
            return self._resolve(row, "refused", stamp, rationale=veto)
        self._record_reanchor(row, action)
        self._attach_expectation(row, action)
        return await self._dispatch_and_resolve(row, action, stamp)

    def _record_reanchor(self, row: TransferAction, action: PlannedAction) -> None:
        """Beide Ausführungswege protokollieren den tatsächlich geprüften Gebotsbetrag."""
        if self._reanchored is not None and row.amount != action.amount:
            # Nachanker: die Audit-Zeile trägt den Betrag, der wirklich geboten
            # wird — der Payload behält den geplanten Wert als Beleg.
            row.amount = action.amount
            row.rationale = action.rationale
            payload = dict(row.payload or {})
            payload["reanchored_from"] = self._reanchored[0]
            row.payload = payload
            self._session.commit()

    def _daily_cap_veto(self, action: PlannedAction, now: datetime) -> str | None:
        """Zentrale Tages-Caps (Codex-P1, 24.08.): 5 Käufe / 5 Verkäufe je
        UTC-Tag über ALLE Pfade (Intents, Auto-Trading, MCP) — gezählt werden
        echte `executed`-Zeilen (dry_run=0) im Audit-Log. AUSNAHME 'kickoff-*':
        die Notbremse verkauft so viel wie nötig (Spieltag retten schlägt Cap).
        Die Planner-/Executor-eigenen Zählungen bleiben als Vorstufe bestehen;
        dieses Netz fängt, was an ihnen vorbeikommt."""
        if (action.rule_id or "").startswith("kickoff-"):
            return None
        if action.action_type == "bid":
            kinds, cap, label = ("bid",), DEFAULT_GUARDRAILS.daily_buy_cap, "Käufe"
        elif action.action_type in ("sell", "accept"):
            # Nur ECHTE Abgänge (L-077, 26.08.): `list` hatte hier mitgezählt,
            # obwohl ein Listing den Kader nicht verkleinert — fünf Listings
            # sperrten den einzigen nötigen Verkauf.
            kinds = ("sell", "accept")
            cap, label = DEFAULT_GUARDRAILS.daily_sell_cap, "Verkäufe"
        elif action.action_type == "list":
            kinds = ("list",)
            cap, label = DEFAULT_GUARDRAILS.daily_list_cap, "Listings"
        else:
            return None
        today = now.date()
        # `pending` zählt als RESERVIERUNG mit (Codex-Folge-Review 24.08., P1):
        # zwei parallele Tasks sollen nicht beide Slot 5/5 sehen und dispatchen.
        # Vollständige Cross-Prozess-Serialisierung liefert die Ein-Schreiber-
        # Architektur (Box-Worker); dieser Zähler ist das Netz darunter.
        rows = self._session.execute(
            select(TransferAction).where(
                TransferAction.status.in_(("executed", "pending")),
                TransferAction.action_type.in_(kinds),
                TransferAction.dry_run.is_(False),
            )
        ).scalars()
        n = sum(1 for r in rows if _as_utc(r.created_at).date() == today)
        if n >= cap:
            return (
                f"Zentrale Invariante: Tages-Cap {label} erreicht ({n}/{cap} heute "
                "über alle Pfade) — nicht ausgeführt."
            )
        return None

    async def stage(
        self, action: PlannedAction, *, now: datetime | None = None
    ) -> ActionResult:
        """Vorschlag vormerken (Status 'proposed'), ohne ihn auszuführen.

        Setzt valid_until = jetzt + 45 min: ein später geklickter Approve-Button
        führt kein veraltetes Gebot mehr aus (endet als 'expired').

        Geschützte Spieler werden gar nicht erst vorgemerkt: ein Vorschlag, der
        beim Klick garantiert `refused` endet, gehört nicht in Emils Zentrale.
        """
        stamp = now or datetime.now(UTC)
        if (protected := _protected_veto(action)) is not None:
            _write_log.warning("verfassung (stage): %s", protected)
            return ActionResult(status="refused", action_id=None, message=protected)
        key = idempotency_key(action, stamp.date().isoformat())
        existing = self._find_existing(key)
        if existing is not None:
            # TTL-Lücke b (Review-Fund): ein zeitlich abgelaufenes 'proposed'
            # darf ein Re-Staging nicht ewig blocken — es zählt wie 'expired'.
            self._expire_stale_proposed(existing, stamp)
        if existing is not None and existing.status in _BLOCKING:
            return ActionResult(
                status="duplicate", action_id=existing.id, message="bereits vorhanden"
            )
        if existing is not None:
            row = self._recycle(
                existing,
                action,
                status="proposed",
                stamp=stamp,
                dry=False,
                payload=asdict(action),
                valid_until=stamp + PROPOSAL_TTL,
            )
            if row is None:
                return ActionResult(
                    status="duplicate",
                    action_id=existing.id,
                    message="bereits vorhanden (paralleler Retry-Claim)",
                )
        else:
            row = TransferAction(
                league_id=action.league_id,
                action_type=action.action_type,
                player_id=action.player_id,
                amount=action.amount,
                status="proposed",
                idempotency_key=key,
                rule_id=action.rule_id,
                rationale=action.rationale,
                dry_run=False,
                payload=asdict(action),
                valid_until=stamp + PROPOSAL_TTL,
                created_at=stamp,
            )
            self._session.add(row)
        if (dup := self._commit_or_duplicate(key)) is not None:
            return dup
        return ActionResult(status="proposed", action_id=row.id)

    async def execute_staged(
        self, action_id: int, *, now: datetime | None = None
    ) -> ActionResult:
        """Einen 'proposed'-Vorschlag nach menschlicher Freigabe ausführen.

        Der Klick allein ist KEINE Live-Freigabe (Review-Fund: der Approval-Pfad
        umging AGENT_ALLOW_LIVE — DRY_RUN=0 + ALLOW_LIVE=0 führte ein
        freigegebenes Gebot trotzdem live aus). Live läuft ein Vorschlag nur,
        wenn der reguläre Live-Interlock (_live_allowed) es erlaubt ODER
        AGENT_APPROVE_LIVE=1 gezielt den Freigabe-Pfad ärmt. Zusätzlich
        bindend: der globale Kill-Switch AGENT_DRY_RUN, die Freigabe-TTL
        (valid_until; Alt-Zeilen ohne TTL: created_at + 45 min) und der
        Preflight vor Geld-Writes.
        """
        stamp = now or datetime.now(UTC)
        row = self._session.get(TransferAction, action_id)
        if row is None:
            return ActionResult(status="failed", action_id=action_id, message="unbekannt")

        # Atomarer Claim gegen Doppelklick/parallele Worker: nur wer die Zeile
        # von 'proposed' auf 'pending' dreht, darf dispatchen — der Verlierer
        # sieht rowcount 0 und liefert 'duplicate' statt eines zweiten API-Calls.
        claimed = self._session.execute(
            update(TransferAction)
            .where(TransferAction.id == action_id, TransferAction.status == "proposed")
            .values(status="pending")
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount == 0:
            self._session.rollback()
            return ActionResult(
                status="duplicate", action_id=row.id, message=f"Status bereits {row.status}"
            )
        # Audit VOR dem Dispatch: 'pending' committen, bevor die API angefasst wird.
        self._session.commit()

        # Freigabe-TTL: abgelaufene Vorschläge werden NIE ausgeführt. Alt-Zeilen
        # ohne valid_until (vor der TTL-Migration gestaged) sind NICHT unbegrenzt
        # ausführbar (Review-Fund): für sie gilt created_at + 45 min.
        if stamp > self._proposal_deadline(row):
            return self._resolve(
                row,
                "expired",
                stamp,
                rationale="Vorschlag abgelaufen — bitte neu rechnen lassen.",
            )

        # Auch nach Approval gilt der Dry-Run-Schalter: erst wenn AGENT_DRY_RUN=0,
        # löst ein Klick echte Kickbase-Aktionen aus (sicherer erster Betrieb).
        # WICHTIG (Review-Fund): ehrlich als skipped_dry_run melden — vorher stand
        # hier "executed", und der Nutzer glaubte, der Trade sei real passiert.
        settings = get_settings()
        if settings.agent_dry_run:
            return self._resolve(
                row, "skipped_dry_run", stamp,
                raw_response={"dry_run": True},
                rationale="DRY-RUN: Freigabe protokolliert, KEINE echte Kickbase-Aktion.",
                mark_dry=True,
            )
        action = _action_from_payload(row.payload or {})
        # Verfassung auch im Freigabe-Pfad: Ein vor der Schutz-Einführung
        # gestagter Vorschlag (oder eine nachträglich erweiterte Liste) darf
        # durch einen Klick nicht doch noch ausgeführt werden.
        if (protected := _protected_veto(action)) is not None:
            _write_log.warning("verfassung (staged): %s", protected)
            return self._resolve(row, "refused", stamp, rationale=protected)

        # Auch ein Freigabe-Klick hebt das Selbst-Einfrieren nicht auf: Der Zustand
        # sagt „ich traue mir gerade nicht", und daran ändert ein Knopfdruck nichts
        # (Entsperren ist ein eigener, bewusster Schritt: `kba unfreeze`).
        frozen = freeze_veto(self._session, action.action_type, action.rule_id)
        if frozen is not None:
            return self._resolve(row, "refused", stamp, rationale=frozen)
        # Zweite Verteidigungslinie (Review-Fund: der Approval-Pfad umging den
        # Live-Interlock): der Klick ärmt nichts — es gilt der reguläre
        # Interlock, oder AGENT_APPROVE_LIVE=1 ärmt gezielt den Freigabe-Pfad.
        if not (settings.agent_approve_live or self._live_allowed(action, settings)):
            return self._resolve(
                row,
                "refused",
                stamp,
                rationale=(
                    "Freigabe-Pfad nicht geärmt (AGENT_APPROVE_LIVE=0): Live-Write in "
                    f"Liga {action.league_id} trotz Freigabe-Klick verweigert — "
                    "AGENT_APPROVE_LIVE=1 oder AGENT_ALLOW_LIVE=1 setzen."
                ),
            )
        cap = self._daily_cap_veto(action, stamp)
        if cap is not None:
            return self._resolve(row, "refused", stamp, rationale=cap)
        self._reanchored = None
        veto = await self._preflight_veto(action, stamp)
        if veto is not None:
            return self._resolve(row, "refused", stamp, rationale=veto)
        self._record_reanchor(row, action)
        self._attach_expectation(row, action)
        return await self._dispatch_and_resolve(row, action, stamp)

    async def reject_staged(
        self, action_id: int, *, now: datetime | None = None
    ) -> ActionResult:
        """Ablehnung: nur ein noch offener 'proposed'-Vorschlag ist ablehnbar.

        Atomar geclaimt wie execute_staged — sonst könnte ein später Reject-Klick
        eine bereits laufende/ausgeführte Aktion im Audit-Log zu 'rejected'
        umetikettieren (falsche Historie).
        """
        stamp = now or datetime.now(UTC)
        row = self._session.get(TransferAction, action_id)
        if row is None:
            return ActionResult(status="failed", action_id=action_id, message="unbekannt")
        claimed = self._session.execute(
            update(TransferAction)
            .where(TransferAction.id == action_id, TransferAction.status == "proposed")
            .values(status="rejected", resolved_at=stamp)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount == 0:
            self._session.rollback()
            return ActionResult(
                status="duplicate", action_id=row.id, message=f"Status bereits {row.status}"
            )
        self._session.commit()
        return ActionResult(status="rejected", action_id=row.id)

    # ------------------------------------------------------------------ intern

    def _find_existing(self, key: str) -> TransferAction | None:
        return self._session.execute(
            select(TransferAction).where(TransferAction.idempotency_key == key)
        ).scalar_one_or_none()

    def _commit_or_duplicate(self, key: str) -> ActionResult | None:
        """Commit; ein Unique-Konflikt (paralleler Insert) wird zu 'duplicate'."""
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            racing = self._find_existing(key)
            return ActionResult(
                status="duplicate",
                action_id=racing.id if racing is not None else None,
                message="bereits vorhanden (paralleler Insert)",
            )
        return None

    @staticmethod
    def _proposal_deadline(row: TransferAction) -> datetime:
        """Bis wann ist ein 'proposed' ausführbar? valid_until, sonst created_at+TTL.

        Alt-Zeilen (vor der TTL-Migration gestaged) haben valid_until=NULL — die
        waren unbegrenzt ausführbar (Review-Fund). Fallback: created_at + 45 min.
        """
        if row.valid_until is not None:
            return _as_utc(row.valid_until)
        return _as_utc(row.created_at) + PROPOSAL_TTL

    def _expire_stale_proposed(self, row: TransferAction, stamp: datetime) -> None:
        """Zeitlich abgelaufenes 'proposed' recyclebar machen (→ 'expired').

        Ein Vorschlag mit verstrichener TTL darf ein neues Staging/Ausführen
        derselben Aktion nicht ewig blocken (Review-Fund: der Status blieb
        'proposed' und damit blockierend). Der UPDATE ist konditional
        (WHERE status='proposed'), damit ein paralleler Claim nie überschrieben
        wird; danach wird die Zeile frisch gelesen — entweder 'expired' oder
        der Status des schnelleren Prozesses.
        """
        if row.status != "proposed" or stamp <= self._proposal_deadline(row):
            return
        self._session.execute(
            update(TransferAction)
            .where(TransferAction.id == row.id, TransferAction.status == "proposed")
            .values(status="expired", resolved_at=stamp)
            .execution_options(synchronize_session=False)
        )
        self._session.expire(row)

    def _recycle(
        self,
        row: TransferAction,
        action: PlannedAction,
        *,
        status: str,
        stamp: datetime,
        dry: bool,
        payload: dict[str, Any] | None = None,
        valid_until: datetime | None = None,
    ) -> TransferAction | None:
        """Endzustand erneut versuchen: Zeile ATOMAR claimen und wiederverwenden.

        Der konditionale UPDATE (WHERE status IN _RETRYABLE) lässt von zwei
        parallelen Retries derselben Zeile nur EINEN durch — der Verlierer
        sieht rowcount 0 und bekommt None (⇒ 'duplicate') statt eines zweiten
        API-Calls (Review-Fund: Doppel-Dispatch beim parallelen Retry).
        """
        claimed = self._session.execute(
            update(TransferAction)
            .where(TransferAction.id == row.id, TransferAction.status.in_(_RETRYABLE))
            .values(status=status)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount == 0:
            self._session.rollback()
            return None
        row.status = status
        row.created_at = stamp
        row.resolved_at = None
        row.raw_response = None
        row.player_id = action.player_id
        row.amount = action.amount
        row.rule_id = action.rule_id
        row.rationale = action.rationale
        row.dry_run = dry
        row.payload = payload
        row.valid_until = valid_until
        return row

    def _live_allowed(self, action: PlannedAction, settings: Any) -> bool:
        """Live-Interlock: welcher Schalter ärmt welchen Aktionstyp?

        | Schalter               | ärmt                                          |
        |------------------------|-----------------------------------------------|
        | AGENT_ALLOW_LIVE=1     | NUR Trades (bid, cancel_bid, sell, list,      |
        |                        | delist, accept, decline) — z. B. Emils        |
        |                        | manuelle Trades per `env AGENT_ALLOW_LIVE=1`  |
        | AGENT_AUTO_LINEUP=1    | AUSSCHLIESSLICH lineup                        |
        | AGENT_AUTO_BONUS=1     | AUSSCHLIESSLICH bonus                         |
        | AGENT_AUTO_PURGE=1     | sell/cancel_bid mit rule_id 'kickoff-*'       |
        |                        | (Anpfiff-Notbremse; als Trade-Typen ärmt      |
        |                        | auch AGENT_ALLOW_LIVE sie)                    |
        | AGENT_AUTO_TRADING=1   | bid/sell/list mit rule_id 'auto-trading*'     |
        |                        | (Stufe-2-Worker auto_trading.py, Default      |
        |                        | dormant; als Trade-Typen ärmt auch            |
        |                        | AGENT_ALLOW_LIVE sie)                         |
        | AGENT_AUTO_INTENTS=1   | bid/sell/list mit rule_id 'intent-*'          |
        |                        | (Steuerzentrale worker/intents.py: Vorhaben   |
        |                        | mit Veto-Fenster, Default aus; als Trade-     |
        |                        | Typen ärmt auch AGENT_ALLOW_LIVE sie)         |
        | AGENT_TEST_LEAGUE_ID   | alle Aktionstypen, aber nur in dieser Liga    |

        Review-Fund P0: `agent_allow_live` stand früher VOR den Spezialschaltern
        und schaltete damit ALLES frei — Auto-Lineup/Bonus konnten live laufen,
        obwohl ihre eigenen Flags aus waren, und die Job-Nachrichten (deren
        Dry-Run-Anzeige den Auto-Flags folgt) behaupteten das Gegenteil.
        """
        if settings.agent_allow_live and action.action_type in _TRADE_ACTION_TYPES:
            return True
        # Aufstellung hat einen eigenen Schalter: kein Geld im Spiel, bis Anpfiff
        # überschreibbar — kann geärmt werden, ohne Trades freizugeben. Umgekehrt
        # gilt dasselbe: allow_live ärmt KEINE Aufstellung und KEINEN Bonus.
        if action.action_type == "lineup" and settings.agent_auto_lineup:
            return True
        if action.action_type == "bonus" and settings.agent_auto_bonus:
            return True
        # Anpfiff-Notbremse: AGENT_AUTO_PURGE ärmt GENAU die Guard-Aktionen des
        # Kickoff-Wächters (T-15-Notverkauf 'kickoff-purge', T-120-Gebots-Storno
        # 'kickoff-bid-cancel') — vorher endete die Notbremse bei
        # AGENT_ALLOW_LIVE=0 als 'refused' und das Konto blieb im Minus.
        if (
            settings.agent_auto_purge
            and action.action_type in ("sell", "cancel_bid")
            and (action.rule_id or "").startswith("kickoff-")
        ):
            return True
        # Stufe 2 (Auto-Trading, Default dormant): AGENT_AUTO_TRADING ärmt GENAU
        # die Geld-Writes des Auto-Trading-Workers (worker/auto_trading.py) —
        # bid/sell/list mit rule_id-Präfix 'auto-trading' (Muster wie die
        # Kickoff-Notbremse). Fremde rule_ids (z. B. 'buy_explosion') bleiben
        # gesperrt: das Flag schaltet den PFAD frei, nie Trades allgemein.
        # Freischalt-Kriterien der Strategie: Docstring des Workers.
        if (
            settings.agent_auto_trading
            and action.action_type in ("bid", "sell", "list")
            and (action.rule_id or "").startswith("auto-trading")
        ):
            return True
        # Steuerzentrale (Vorhaben mit Veto-Fenster, worker/intents.py): AGENT_AUTO_INTENTS
        # ärmt GENAU die Geld-Writes des Intent-Executors — bid/sell/list/accept/delist/decline
        # mit rule_id-Präfix 'intent-' (gleiches Muster; accept = Annahme eines
        # Angebots auf ein eigenes Listing, 24.08.). Default aus: Vorhaben sind
        # sichtbar, der Executor meldet ehrlich refused.
        if (
            settings.agent_auto_intents
            and action.action_type in ("bid", "sell", "list", "accept", "delist", "decline")
            and (action.rule_id or "").startswith("intent-")
        ):
            return True
        if (settings.agent_auto_intents and action.action_type == "cancel_bid"
                and action.rule_id == "intent-funding-cancel"
                and isinstance(action.funding_review, dict)
                and action.funding_review.get("purpose") == "funding_abandon_bid"):
            return True  # Fachliche Belege und Ist-Defizit werden vor dem Write geprüft.
        return (
            bool(settings.agent_test_league_id)
            and action.league_id == settings.agent_test_league_id
        )

    async def _preflight_veto(self, action: PlannedAction, now: datetime) -> str | None:
        """Frische Markt-Prüfung unmittelbar vor Live-Geld-Writes; None = kein Einwand.

        Fail-closed: schlägt der Preflight selbst fehl, gibt es KEINEN Live-Write
        auf Basis veralteter Daten.
        """
        started = time.monotonic()
        funding_cancel = action.action_type == "cancel_bid" and (
            action.funding_review is not None or action.rule_id == "intent-funding-cancel"
        )
        if funding_cancel and (self._preflight is None
                               or action.rule_id != "intent-funding-cancel"):
            return "Deckungsrückzug: eigener geprüfter Ausführungspfad erforderlich."
        operator_review_missing = (
            action.action_type == "bid"
            and action.rule_id in ("intent-bid-manual", "mcp-bid")
            and not is_engine_review(action.purchase_review)
        )
        if action.action_type == "bid":
            if str(action.player_id) in abandoned_bid_ids(self._session, action.league_id, now):
                return "Gebot zur Deckung aufgegeben; bis Listingablauf kein Wiedereinstieg."
            review_veto = purchase_review_veto(
                action.purchase_review, player_id=str(action.player_id or ""),
                amount=action.amount, now=now, required=_is_autonomous(action.rule_id),
            )
            if review_veto is not None:
                return review_veto
            if self._preflight is None and operator_review_missing:
                return "Operator-Kauf braucht eine aktuelle Engine-Mannschaftsprüfung."
            if self._preflight is None and (
                _is_autonomous(action.rule_id) or action.purchase_review is not None
            ):
                return "Kaufprüfung: frischer Zielspieler-Preflight fehlt — Gebot verweigert."
        if self._preflight is None or action.action_type not in (
            "bid", "sell", "list", "accept",
            *(["delist", "decline", "lineup"] if self._uses_default_preflight else []),
            *(["cancel_bid"] if funding_cancel else []),
        ):
            return None
        try:
            callback = (
                default_preflight(self._client, user_id=self._user_id, now=now)
                if self._uses_default_preflight else self._preflight
            )
            state = await callback(action)
        except Exception as exc:  # noqa: BLE001 — ohne frische Daten kein Live-Write
            return (
                f"Preflight fehlgeschlagen ({type(exc).__name__}: {exc}) — "
                "Live-Write abgebrochen."
            )
        # Frischen Zustand für die Gebots-Erwartung merken (25.08.): Die Signatur
        # bleibt bewusst unverändert — sie hat 15 Rückgabestellen, und ein reiner
        # Diagnose-Wert rechtfertigt keinen Umbau des kritischsten Pfads.
        self._last_preflight = state
        if funding_cancel:
            review_now = now + timedelta(seconds=time.monotonic() - started)
            if str(action.player_id) in protected_player_ids():
                return "Deckungsrückzug: geschützter Spieler bleibt unangetastet."
            if state.budget is None or state.own_bids is None or state.squad_positions is None:
                return "Deckungsrückzug: frische Finanzierungsdaten fehlen."
            problem = funding_review_veto(
                action.funding_review, league_id=action.league_id,
                player_id=str(action.player_id or ""), offer_id=str(action.offer_id or ""),
                amount=action.amount, now=review_now, budget=state.budget,
                live_bids=state.own_bids, owned_player_ids=set(state.squad_positions),
            )
            if problem:
                return problem
            assert action.funding_review is not None
            until = datetime.fromisoformat(action.funding_review["abandon_until"])
            if (state.listing_expires_at is None or state.listing_expires_at.tzinfo is None
                    or abs((state.listing_expires_at - until).total_seconds()) > 120):
                return "Deckungsrückzug: Listingablauf nicht frisch bestätigt — neu planen."
            latest = self._session.scalars(select(TransferAction).where(
                TransferAction.league_id == action.league_id,
                TransferAction.player_id == action.player_id, TransferAction.action_type == "bid",
                TransferAction.status.in_(("executed", "pending", "unknown")),
                TransferAction.dry_run.is_(False),
            ).order_by(TransferAction.created_at.desc(), TransferAction.id.desc())).first()
            if not confirmed_autonomous_bid(latest, {
                "player_id": action.player_id, "offer_id": action.offer_id, "amount": action.amount,
            }, expires_at=state.listing_expires_at, now=review_now):
                return "Deckungsrückzug: Gebotshistorie fehlt oder manuelles Gebot hat Vorrang."
            assert latest is not None
            original_end = (latest.payload or {})["confirmed_bid"]["expires_at"]
            if (action.funding_review.get("original_bid_action_id") != latest.id
                    or action.funding_review.get("original_listing_expires_at") != original_end):
                return "Deckungsrückzug: ursprünglicher Gebots-/Auktionsbeleg verändert."
            for pending in self._session.scalars(select(Intent).where(
                Intent.player_id == action.player_id, Intent.kind == "bid",
                Intent.status.in_(("planned", "executing")),
            )):
                sources = pending.sources or {}
                if str(sources.get("league_id", action.league_id)) != action.league_id:
                    continue
                if (sources.get("manual") or pending.adjusted_from is not None
                        or pending.status == "executing"):
                    return "Deckungsrückzug: manuelles oder laufendes Kaufvorhaben hat Vorrang."
            return None
        if action.action_type == "bid":
            review_now = now + timedelta(seconds=time.monotonic() - started)
            engine_review = is_engine_review(action.purchase_review)
            review_veto = purchase_review_veto(
                action.purchase_review, player_id=str(action.player_id or ""),
                amount=action.amount, now=review_now, required=_is_autonomous(action.rule_id),
            )
            if review_veto is not None:
                return review_veto
            # Deadline-Fenster ZUERST: T-120-Freeze — gilt für JEDEN Gebots-Pfad (Intents,
            # Auto-Trading, MCP/manuell), Details decision/kickoff_freeze.py.
            freeze = bid_freeze_reason(
                now,
                state.matchday_deadline,
                amount=max(0, (action.amount or 0) - sum(
                    b.get("amount", 0) for b in (state.own_bids or [])
                    if engine_review and b.get("player_id") == action.player_id
                    and type(b.get("amount")) is int
                )),
                budget_after_bids=state.budget_after_bids,
            )
            if freeze is not None:
                return f"Preflight: {freeze}"
            if state.on_market is False:
                return "Preflight: Spieler ist nicht mehr am Transfermarkt."
            if _is_autonomous(action.rule_id) or action.purchase_review is not None:
                review_dict = action.purchase_review or {}
                swap_sale = review_dict.get("swap_sale")
                team_plan_dict = review_dict.get("team_plan") or {}
                deficit_swap_completion = (
                    isinstance(swap_sale, dict)
                    and bool(swap_sale.get("player_id"))
                    and (
                        (state.budget is not None and state.budget < 0)
                        or int(team_plan_dict.get("budget_eur") or 0) < 0
                    )
                )
                effective_purpose = (
                    "lineup_completion"
                    if deficit_swap_completion
                    else review_dict.get("purpose")
                )
                allow_deficit_filler = (
                    deficit_swap_completion
                    and type(action.amount) is int
                    and action.amount <= 1_000_000
                )
                target_veto = _curated_bid_target_veto(
                    state.target_fitness_status,
                    state.target_lineup_prob,
                    state.target_mv_change_24h,
                    purpose=effective_purpose,
                    allow_deficit_filler=allow_deficit_filler,
                )
                if target_veto is not None:
                    return target_veto
                snapshot = team_plan_dict.get(
                    "forecast_inputs", {},
                ).get(str(action.player_id), {})
                li_status = snapshot.get("ligainsider_status")
                if li_status == "out" and not allow_deficit_filler:
                    return "Kaufprüfung: dokumentierter LigaInsider-Ausfall verhindert den Kauf."
                bank_role = state.target_lineup_prob in (4, 5) or li_status == "bench"
                if bank_role:
                    if (effective_purpose != "lineup_completion"
                            or not emergency_bench_fallback_allowed(
                                review_now, state.matchday_deadline,
                            )):
                        return "Notbesetzung: Bankrolle nur innerhalb von 96 Stunden " \
                            "vor der frisch bestätigten offiziellen Frist."
                    sold_pid = (
                        str(swap_sale.get("player_id"))
                        if deficit_swap_completion and isinstance(swap_sale, dict)
                        else None
                    )
                    pos_values = (
                        [
                            pos
                            for pid, pos in state.squad_positions.items()
                            if pid != sold_pid
                        ]
                        if state.squad_positions
                        else []
                    )
                    filled_slots = (
                        regular_fill(pos_values)
                        if len(set(pos_values)) > 1
                        else len(pos_values)
                    )
                    if state.squad_positions is None or filled_slots >= 11:
                        return "Mannschaftsplan: Bankspieler nur bei belegter eigener Kaderlücke."
            # Kappen-Härtung (01.09., Pflicht-Bedingung der Pfand-Rechnung):
            # Ohne ermittelbaren Marktwert ist die Overpay-Kappe unten NICHT
            # prüfbar — vorher rutschte ein Gebot dann UNGEKAPPT durch, und ein
            # per +%-Buttons auf 1,5× gehobenes Need-Gebot hätte einen
            # Worst-Case-Verlustanteil von ~56 % gehabt (> pfand_risk_fraction
            # 0.35, die Pfand-Garantie wäre Prosa). Der Text nennt bewusst
            # nicht das Budget-Feld-Kürzel: der Intent-Retry erkennt transiente
            # Fälle am Substring, diese Sperre ist nicht transient heilbar.
            if state.current_mv is None and action.amount is not None:
                return (
                    "Preflight: aktueller Marktwert nicht ermittelbar — "
                    "Overpay-Kappe nicht prüfbar, Gebot verweigert "
                    "(fail-closed; später erneut versuchen)."
                )
            # Nachanker (02.09., Onyeka-Lehre): Zwischen Planung (12:25) und
            # Ausführung (22:12) stieg der Marktwert im 22-Uhr-Update — das
            # Gebot 8,16 M lag unter dem aktuellen MW 8,36 M, Kickbase lehnte
            # mit err 5080 ab, AUCH auf dem Kickbase-Listing, und das Vorhaben
            # starb still. Kleine Drift (bis bid_reanchor_max des geplanten
            # Betrags) wird auf MW + Psycho-Offset nachgezogen statt verweigert;
            # größere Lücken bleiben, was sie waren (Manager-Listing: Sperre
            # unten, Kickbase-Listing: Versuch — versiegelte Zusicherung).
            if (
                state.current_mv is not None
                and action.amount is not None
                and action.amount < state.current_mv
            ):
                # Enginepreise werden nur aus bereits bewerteten Szenarien gewählt.
                # Ein pauschaler Psycho-Aufschlag würde deren Mannschaftsvergleich verlassen.
                if engine_review:
                    assert action.purchase_review is not None
                    prices = [s["amount"] for s in action.purchase_review["bid_scenarios"]
                              if state.current_mv <= s["amount"]
                              <= action.purchase_review["max_bid_eur"]]
                    if not prices:
                        return "Mannschaftsplan: Marktwert gestiegen — Preisszenarien neu bewerten."
                    anchored = min(prices)
                else:
                    anchored = int(state.current_mv) + DEFAULT_GUARDRAILS.bid_psych_offset
                if anchored <= int(action.amount * DEFAULT_GUARDRAILS.bid_reanchor_max):
                    review_veto = purchase_review_veto(
                        action.purchase_review, player_id=str(action.player_id or ""),
                        amount=anchored, now=review_now, required=_is_autonomous(action.rule_id),
                    )
                    if review_veto is not None:
                        return review_veto
                    old_amount = action.amount
                    action.amount = anchored
                    action.rationale = (
                        f"{action.rationale or ''} | Nachanker: {old_amount} → {anchored} "
                        f"(MW {state.current_mv} über dem geplanten Gebot)"
                    ).strip(" |")
                    self._reanchored = (old_amount, anchored)
                elif engine_review:
                    return "Mannschaftsplan: Preisdrift zu groß — Preisszenarien neu bewerten."
            # Zentrale Overpay-Kappe (Codex-P1): Gebot nie über MW × Premium —
            # galt bisher nur zur Planner-Zeit; Emils +%-Buttons und manuelle
            # Gebote laufen jetzt gegen dieselbe Kappe (die App bleibt sein
            # Override). Kappen-Basis ist der aktuelle MARKTWERT, nicht der
            # Listing-Preis.
            # GESTAFFELT seit 26.08.: Standard 110 %, markierter Kader-Bedarf
            # (action.squad_need) 135 %. Vorher sperrte die 110-%-Kappe genau
            # die Gebote, die die eigene Regel („für gebrauchte Starter ≥ 120 %")
            # und die Zuschlag-Erwartung (0.60 ab 120 %, 0.80 ab 135 %)
            # verlangten — der Agent hatte sich eine Regel gegeben, die sein
            # eigener Wächter nicht durchließ.
            # Stufe „Handel“ (Emil 01.10.2026: „auf dem normalen Transfermarkt auch bis 130%,
            # aber nicht bei Angeboten an andere Spieler“): nur Kaufzweck `handel` UND ein
            # sicher erkanntes Kickbase-Listing (is_manager_listing ausdrücklich False);
            # unbekannt oder Manager-Listing bleibt Standard.
            trade = (
                (action.purchase_review or {}).get("purpose") == "handel"
                and state.is_manager_listing is False
            )
            upgrade = (
                engine_review
                and (action.purchase_review or {}).get("purpose") == "lineup_upgrade"
                and state.is_manager_listing is False
            )
            premium, stufe = (
                (DEFAULT_GUARDRAILS.max_bid_premium_need, "Kader-Bedarf")
                if action.squad_need
                else (DEFAULT_GUARDRAILS.max_bid_premium_trade, "Handel")
                if trade
                else (DEFAULT_GUARDRAILS.max_bid_premium_upgrade, "Elf-Upgrade")
                if upgrade
                else (DEFAULT_GUARDRAILS.max_bid_premium, "Standard")
            )
            if (
                state.current_mv is not None
                and action.amount is not None
                and action.amount > state.current_mv * premium
            ):
                cap_eur = int(state.current_mv * premium)
                hinweis = (
                    ""
                    if action.squad_need
                    else " Für Spieler, ohne die die Formation nicht steht, das "
                    "Gebot als Kader-Bedarf markieren (squad_need) — dann gilt "
                    f"{int(DEFAULT_GUARDRAILS.max_bid_premium_need * 100)} %."
                )
                return (
                    f"Preflight: Gebot {action.amount} liegt über der Overpay-Kappe "
                    f"{cap_eur} ({int(premium * 100)} % des Marktwerts "
                    f"{state.current_mv}, Stufe {stufe}) — Kappe ist das "
                    f"Maximalgebot.{hinweis}"
                )
            # Underpay-Sperre für Manager-Listings (err 5080 „UnderpayNotAllowed",
            # Leiter-Beweis 24.08.: 90/95 % ✗, 100 % ✓): Kickbase lehnt Gebote
            # unter dem AKTUELLEN Marktwert auf Manager-Listings mit HTTP 500 ab —
            # der Listing-Preis (prc) ist Deko. Zentral HIER statt nur im Planner:
            # auch MCP-Gebote, Emils Betrags-Anpassungen und ein 22-Uhr-MW-Anstieg
            # zwischen Planung und Ausführung laufen gegen dieselbe Wand (refused
            # ist billiger und slot-sicherer als ein garantierter 500er).
            # Kickbase-Listings (kein Verkäufer) dürfen weiter underpayen.
            if (
                state.is_manager_listing
                and state.current_mv is not None
                and action.amount is not None
                and action.amount < state.current_mv
            ):
                return (
                    f"Preflight: Manager-Listing verlangt ≥ 100 % MW (err 5080 "
                    f"UnderpayNotAllowed) — Gebot {action.amount} liegt unter dem "
                    f"aktuellen Marktwert {state.current_mv}; Anker ist MW + "
                    f"{DEFAULT_GUARDRAILS.bid_psych_offset}, neu rechnen lassen."
                )
            # Zentrales Vereinslimit (Regel 5): Kader + offene Gebote des
            # Ziel-Clubs inklusive DIESES Gebots dürfen das Liga-Limit (lpc)
            # nicht sprengen — tote Gebote kosten nur Slots.
            if (
                state.club_limit is not None
                and state.club_count_after is not None
                and state.club_count_after > state.club_limit
            ):
                return (
                    f"Preflight: Vereinslimit — mit diesem Gebot hätte der Club "
                    f"{state.club_count_after} Spieler/Gebote bei Limit "
                    f"{state.club_limit} (lpc). Erst Bestand abbauen."
                )
            # Preis-Drift nur für KICKBASE-Listings: bei Manager-Listings ist
            # `prc` Deko (err-5080-Beweis) — ein hoher Wunschpreis darf ein
            # regelkonformes 100–110-%-MW-Gebot nicht blockieren (Codex-Folge-
            # Review 24.08., P2); dort schützen Underpay-Sperre + MW-Kappe.
            if (
                not state.is_manager_listing
                and state.current_price is not None
                and action.amount is not None
                and state.current_price > action.amount * (1 + PREFLIGHT_MAX_PRICE_DRIFT)
            ):
                return (
                    f"Preflight: aktueller Preis {state.current_price} liegt mehr als "
                    f"10 % über dem Staging-Betrag {action.amount} — bitte neu rechnen lassen."
                )
            # Bestehende spezifische Verbote (etwa Wiedereinstieg oder Preisdrift)
            # bleiben sichtbar. Eine positive Freigabe braucht immer den Engine-Beleg.
            if operator_review_missing:
                return "Operator-Kauf braucht eine aktuelle Engine-Mannschaftsprüfung."
            if engine_review:
                assert action.amount is not None
                return team_funding_veto(
                    action.purchase_review, player_id=str(action.player_id),
                    amount=action.amount, budget=state.budget, open_bids=state.own_bids,
                    owned_player_ids=(set(state.squad_positions)
                                      if state.squad_positions is not None else None),
                )
            if state.budget is not None and action.amount is not None:
                if state.team_value is not None:
                    # Offizielle 33-%-Regel: Konto nach Annahme ALLER offenen
                    # Gebote minus dieses Gebot darf nicht unter −⅓ des
                    # (bereinigten) Teamwerts fallen. Kein Bargeld nötig —
                    # verkauft wird erst nach dem Zuschlag (vor dem Anpfiff).
                    if state.budget_after_bids is None:
                        # Review-Fund 23.08. (P0): ohne Kickbase-`pbaa` (Konto NACH
                        # offenen Geboten) waere die 33-%-Rechnung blind fuer die
                        # bestehende Exposure -> fail-closed statt Cash-Fallback.
                        return (
                            "Preflight: Kickbase lieferte kein Konto-nach-Geboten (pbaa) — "
                            "Kaufkraft inkl. offener Gebote nicht sicher bestimmbar; "
                            "Gebot verweigert (fail-closed). Spaeter erneut versuchen."
                        )
                    after = state.budget_after_bids
                    floor = overdraft_floor(state.team_value, state.budget)
                    if after - action.amount < floor:
                        return (
                            f"Preflight: Gebot {action.amount} sprengt die 33-%-Regel — "
                            f"Budget nach offenen Geboten {after}, Minus-Limit {floor} "
                            f"(Teamwert {state.team_value}); Spielraum "
                            f"{max(0, after - floor)}. Erst verkaufen oder Gebot senken."
                        )
                elif action.amount > state.budget:
                    # Ohne Teamwert kein 33-%-Spielraum berechenbar → cash-only.
                    return (
                        f"Preflight: Gebot {action.amount} übersteigt das aktuelle "
                        f"Budget {state.budget} (Teamwert unbekannt, daher cash-only)."
                    )
        if action.action_type in ("sell", "list", "accept"):
            # (a) Kader-Check — gilt IMMER, auch für die Anpfiff-Notbremse:
            # ein Spieler, der nicht (mehr) im Kader ist, ist nicht verkaufbar.
            if state.in_squad is False:
                return "Preflight: Spieler ist nicht (mehr) im eigenen Kader."
            # (a2) Zentrale Kader-Deckung (Codex-Re-Check 24.08., P1): ein
            # ENDGÜLTIGER Abgang (sell/accept — Listings reduzieren nichts)
            # wird gegen den FRISCHEN Kader geprüft: danach ≥ 11 Spieler UND
            # eine stellbare Formation. Fängt getrennt geplante Verkäufe/
            # Annahmen und parallele Pfade zur Laufzeit ab. AUSNAHME
            # 'kickoff-*': die T-15-Notbremse darf bewusst unter 11 (lieber
            # 10 Spieler mit Punkten als 11 mit null).
            if (
                action.action_type in ("sell", "accept")
                and not (action.rule_id or "").startswith("kickoff-")
                and state.squad_positions is not None
            ):
                pos = state.squad_positions.get(str(action.player_id))
                counts: dict[int, int] = {}
                for p in state.squad_positions.values():
                    counts[p] = counts.get(p, 0) + 1
                if pos is not None:
                    counts[pos] = counts.get(pos, 0) - 1
                remaining = len(state.squad_positions) - 1
                # Kader-Fenster (09.09., Emils Präzisierung): Das harte Gesetz gilt
                # in der tatsächlichen Anpfiffminute. Der Executor hält vorsorglich
                # ab T-120 die volle Elf; davor genügt die Positionsdeckung, damit
                # Ersatz zuerst gekauft und der Verlierer danach verkauft werden kann.
                # und bei einem 11er-Kader, der komplett die Elf ist, war damit
                # konstruktionsbedingt nie ein Verkauf möglich (sechs Wertverlierer
                # mit ~1,9 M Verlust/Tag blieben stehen). Außerhalb des Fensters ist
                # der Kader ein Portfolio, das nachgefüllt wird: Pflicht bleiben nur
                # die Positions-Minima. Innerhalb T-120 halten AUTONOME Pfade den
                # vollen Floor; Emils manuelle Wege (mcp-*, -manual) dürfen auch dort
                # tauschen — das -100-Risiko je leerem Platz ist dann sein Entscheid.
                inside_window = (
                    state.matchday_deadline is not None
                    and state.matchday_deadline - now <= timedelta(hours=EXPOSURE_GUARD_HOURS)
                )
                if inside_window and _is_autonomous(action.rule_id):
                    if remaining < MIN_SQUAD_SIZE or not can_field_any_formation(counts):
                        return (
                            "Preflight: Verkauf risse die Kader-Deckung — danach "
                            f"{remaining} Spieler bzw. keine stellbare Formation mehr "
                            "(≥ 11 und 1 TW/3 ABW/2 MF/1 ST Pflicht ab T-120). "
                            "Erst Ersatz kaufen."
                        )
                elif not _position_minimums_kept(counts):
                    return (
                        "Preflight: Verkauf risse das Positions-Minimum "
                        "(1 TW/3 ABW/2 MF/1 ST) — keine Formation mehr auffüllbar. "
                        "Erst Ersatz auf dieser Position kaufen."
                    )
            # (a2b) Starter-Schutz (Emils Regel 23.08., zentral seit 24.08.):
            # AUTONOME Pfade verkaufen nie einen aktuell aufgestellten Spieler —
            # Tausch gegen einen besseren Starter läuft manuell (MCP/Emil);
            # die Notbremse (kickoff-*) holt hier gar keine starter_ids.
            if (
                action.action_type in ("sell", "accept")
                and state.starter_ids is not None
                and str(action.player_id) in state.starter_ids
                and _is_autonomous(action.rule_id)
                and not action.swap
            ):
                return (
                    "Preflight: Spieler steht in der aktuellen Aufstellung — "
                    "autonome Pfade verkaufen keine Starter (Emils Regel; "
                    "Tausch nur manuell, Notbremse ausgenommen)."
                )
            # (a3) Angebot frisch? (Codex-Re-Check 24.08., P2) Nur für accept:
            # uoid + Betrag müssen im Markt noch exakt so stehen — sonst wurde
            # das Angebot zurückgezogen/ersetzt und die Annahme wäre blind.
            if action.action_type == "accept" and state.offer_intact is False:
                return (
                    "Preflight: das Angebot (uoid/Betrag) steht nicht mehr am "
                    "eigenen Listing — zurückgezogen oder ersetzt; Vorhaben neu "
                    "planen lassen."
                )
            # (a3b) Entscheidungs-Gate für accept (21.10.2026): Liegt am eigenen
            # Listing inzwischen ein HÖHERES Angebot vor als der geplante Betrag,
            # wird das niedrigere Angebot verweigert — der nächste Planner-Lauf
            # nimmt stattdessen das höhere Angebot an.
            if (
                action.action_type == "accept"
                and state.higher_offer_eur is not None
                and action.amount is not None
                and state.higher_offer_eur > action.amount
            ):
                return (
                    f"Preflight: Entscheidungs-Gate — am eigenen Listing liegt bereits "
                    f"ein höheres Angebot ({state.higher_offer_eur} > {action.amount}) "
                    "vor; niedrigeres Angebot verweigert."
                )
            # (a4) Entscheidungs-Gate für autonome Nicht-Tausch-Verkäufe (21.10.2026):
            # Hat sich ein zum Abbau geplanter Bankspieler zwischen Planung und
            # Ausführung in einen fitten KB-1-Stammspieler mit positivem 24h-Trend
            # verwandelt, stoppt der Executor den Verkauf.
            if (
                action.action_type == "sell"
                and _is_autonomous(action.rule_id)
                and not action.swap
                and not (action.rule_id or "").startswith("kickoff-")
                and state.squad_fitness_status == 0
                and state.squad_lineup_prob == 1
                and type(state.squad_mv_change_24h) is int
                and state.squad_mv_change_24h > 0
            ):
                return (
                    "Preflight: Entscheidungs-Gate — Kaderspieler ist inzwischen "
                    "fitter KB-1-Stammspieler mit positivem 24h-Trend; Verkauf gestoppt."
                )
            # (b) MW-Drift-Check — Kickoff-Notverkäufe (rule_id 'kickoff-*')
            # sind AUSGENOMMEN: die Notbremse muss auch bei Preisrutsch
            # verkaufen dürfen (Spieltag retten schlägt Kurspflege). Annahmen
            # ('accept') ebenfalls: dort ist amount der FIXE Angebotsbetrag,
            # kein Planungs-MW — der Floor wurde beim Anlegen geprüft, und ein
            # 22-Uhr-MW-Sprung darf den Weg-A-Abschluss nicht kippen.
            # Listungen (16.09., Olise zu 100 M): Der Betrag einer Listung ist
            # unser PREIS, kein Planungs-MW. Liegt er ÜBER dem aktuellen MW, ist
            # das kein veränderter Zustand, sondern Emils Aufschlag — schlimmsten-
            # falls kauft niemand. Drift zählt bei list nur nach unten: Ist der MW
            # inzwischen deutlich über den Preis gestiegen, würden wir unter Wert
            # verkaufen (versiegelter Fall: Staging 5,0 M, MW 6,5 M → refused).
            premium_listing = (
                action.action_type == "list"
                and state.current_value is not None
                and action.amount is not None
                and action.amount >= state.current_value
            )
            if (
                action.action_type != "accept"
                and not premium_listing
                and not (action.rule_id or "").startswith("kickoff-")
                and state.current_value is not None
                and action.amount is not None
                and abs(state.current_value - action.amount)
                > action.amount * PREFLIGHT_MAX_VALUE_DRIFT
            ):
                return (
                    f"Preflight: aktueller Marktwert {state.current_value} weicht mehr "
                    f"als 20 % vom Staging-Betrag {action.amount} ab — Zustand hat "
                    "sich geändert, bitte neu entscheiden."
                )
        if action.action_type == "delist" and state.on_market is False:
            return "Preflight: Spieler steht nicht (mehr) am Transfermarkt — Rückzug überflüssig."
        if action.action_type == "decline" and state.offer_intact is False:
            return "Preflight: das abzulehnende Angebot steht nicht mehr am eigenen Listing."
        if action.action_type == "lineup" and state.lineup_unfit_starter is not None:
            return (
                f"Preflight: Entscheidungs-Gate — Spieler {state.lineup_unfit_starter} "
                "ist nicht fit, obwohl ein fitter Ersatz derselben Position im Kader steht; "
                "Aufstellung bitte neu berechnen."
            )
        return None

    def _attach_expectation(self, row: TransferAction, action: PlannedAction) -> None:
        """Jedes Gebot bekommt eine überprüfbare Erwartung (Emils Auftrag 25.08.).

        Bewusst HIER statt beim Aufrufer: So trägt JEDES Gebot eine — ob es aus
        dem Planner, dem MCP oder einem Sofort-Eingriff kommt. Die Schätzung ist
        selbst eine Hypothese; `trading.expectation.calibration` hält sie gegen
        die tatsächlichen Zuschläge und meldet, wenn sie systematisch danebenliegt.
        """
        if action.action_type != "bid" or action.amount is None:
            return
        state = self._last_preflight
        mv = int(getattr(state, "current_mv", 0) or 0) if state else 0
        if not mv:
            return  # ohne frischen Marktwert keine ehrliche Schätzung
        payload = dict(row.payload or {})
        if payload.get("expectation") and not is_engine_review(action.purchase_review):
            return  # ein bewusst mitgegebener Wert gewinnt
        if is_engine_review(action.purchase_review):
            review = action.purchase_review
            assert review is not None
            scenario: dict[str, Any] = next((s for s in review["bid_scenarios"]
                                            if s["amount"] == action.amount), {})
            probability = scenario.get("win_probability")
            payload["expectation"] = {
                "p": probability,
                "kind": "manager" if getattr(state, "is_manager_listing", False) else "kickbase",
                "why": ("Zuschlagswahrscheinlichkeit unbekannt; belegte Gewinnerpreise sind "
                        "keine vollständigen Auktionsgebote." if probability is None
                        else "Wahrscheinlichkeit aus dem geprüften Engine-Preisszenario."),
                "model_version": review.get("bid_model_version", "unknown"),
                "points_model_version": review.get("model_version"),
                "engine_version": review["engine_version"], "plan_id": review["plan_id"],
                "ratio": action.amount / mv, "observed_at": review["observed_at"],
                "market_value_eur": mv, "position": review.get("position"),
            }
            row.payload = payload
            self._session.commit()
            _write_log.info("BID-ERWARTUNG player=%s p=%s (Engine)", action.player_id, probability)
            return
        est = estimate_bid_success(
            kind="manager" if getattr(state, "is_manager_listing", False) else "kickbase",
            amount=int(action.amount),
            market_value=mv,
            listing_price=getattr(state, "current_price", None),
        )
        payload["expectation"] = est.as_dict()
        row.payload = payload
        self._session.commit()
        _write_log.info(
            "BID-ERWARTUNG player=%s p=%.2f (%s)", action.player_id, est.p, est.why
        )

    async def _dispatch_and_resolve(
        self, row: TransferAction, action: PlannedAction, stamp: datetime
    ) -> ActionResult:
        """Live-Write mit Ausgangs-Mapping: Erfolg, Fehler oder Timeout (= unklar)."""
        try:
            response = await self._dispatch(action)
        except _AMBIGUOUS_TRANSPORT_ERRORS as exc:
            # Timeout/Abbruch NACH dem Senden heisst NICHT "fehlgeschlagen": der
            # Write kann serverseitig durchgegangen sein (Review-Fund 23.08.:
            # RemoteProtocolError/ReadError/5xx-Gateway fuehrten zu retrybaren
            # 'failed' und damit zu DOPPELTEN Writes). Kein blinder Retry —
            # 'unknown' blockiert die Aktion, bis Reconcile gegen Kickbase prueft.
            return self._resolve(
                row,
                "unknown",
                stamp,
                rationale=(
                    f"{type(exc).__name__}: Transportfehler beim API-Write — Remote-Zustand "
                    "unklar. WARNUNG: Die Aktion kann bei Kickbase ausgeführt worden sein; "
                    "bitte manuell oder per Reconcile prüfen, bevor erneut versucht wird."
                ),
            )
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code if exc.response is not None else 0
            if code in _AMBIGUOUS_STATUS:
                # 502/503/504 kommen vom Proxy/Gateway — der Upstream kann den
                # Write bereits verarbeitet haben.
                return self._resolve(
                    row,
                    "unknown",
                    stamp,
                    rationale=(
                        f"HTTP {code} vom Gateway beim API-Write — Remote-Zustand unklar; "
                        "per Reconcile prüfen, bevor erneut versucht wird."
                    ),
                )
            # Response-Body in die rationale: Kickbase erklärt den echten Grund
            # eines 500ers NUR dort (z. B. {"err":5080,"errMsg":"UnderpayNot-
            # Allowed"}) — der Status allein kostete 5 Fehlversuche über 2
            # Sessions (24.08.).
            body = _response_excerpt(exc)
            return self._resolve(
                row,
                "failed",
                stamp,
                rationale=f"HTTP {code}: {exc}" + (f" — Body: {body}" if body else ""),
            )
        except json.JSONDecodeError:
            return self._resolve(
                row, "unknown", stamp,
                rationale="Write-Antwort nicht dekodierbar; Ausführung unklar, kein blinder Retry.",
            )
        except Exception as exc:  # noqa: BLE001 — Fehler wird protokolliert, nicht verschluckt
            return self._resolve(row, "failed", stamp, rationale=f"{type(exc).__name__}: {exc}")
        if action.action_type == "bid" and self._client is not None and self._user_id:
            try:
                read_bids = getattr(self._client, "get_own_bids", None)
                observed = datetime.now(UTC)
                bids = await read_bids(action.league_id, self._user_id)
                matches = [bid for bid in bids if str(bid.get("player_id")) == action.player_id
                           and bid.get("amount") == action.amount and bid.get("offer_id")]
                if (len(matches) == 1 and type(matches[0].get("expires_in_s")) is int
                        and matches[0]["expires_in_s"] > 0 and not matches[0].get("seller")):
                    expiry = observed + timedelta(seconds=matches[0]["expires_in_s"])
                    row.payload = {**(row.payload or {}), "confirmed_bid": {
                        "source": "own_bids_after_executed_bid", "player_id": action.player_id,
                        "amount_eur": action.amount, "offer_id": str(matches[0]["offer_id"]),
                        "observed_at": observed.isoformat(), "expires_at": expiry.isoformat(),
                    }}
            except Exception:  # noqa: BLE001 — fehlender Beleg ändert den Write-Ausgang nicht
                _write_log.warning("Gebots-ID nach Write nicht bestätigt: %s", action.player_id)
        measured = isinstance(response, dict) and "kba_credited" in response
        if action.action_type == "bonus" and measured:
            credited = int(response["kba_credited"])
            after = response.get("kba_budget_after")
            if credited <= 0:
                return self._resolve(
                    row,
                    "noop",
                    stamp,
                    raw_response=response,
                    rationale=(
                        f"Prämie: nichts gutgeschrieben (Konto unverändert {after:,} €) — "
                        "heute bereits kassiert (App/früherer Slot) oder noch nicht frei; "
                        "nächster Slot prüft erneut."
                    ).replace(",", "."),
                )
            return self._resolve(
                row,
                "executed",
                stamp,
                raw_response=response,
                rationale=(
                    f"Tägliche Prämie eingesammelt: +{credited:,} € (Konto jetzt {after:,} €)"
                ).replace(",", "."),
            )
        return self._resolve(row, "executed", stamp, raw_response=response)

    async def _dispatch(self, a: PlannedAction) -> dict[str, Any]:
        """Echten API-Write ausführen — mit Start/Ende/Dauer-Logging je Request."""
        start = time.monotonic()
        _write_log.info(
            "API-WRITE start type=%s league=%s player=%s amount=%s",
            a.action_type, a.league_id, a.player_id, a.amount,
        )
        try:
            result = await self._dispatch_raw(a)
        except Exception as exc:
            # Bei HTTP-Fehlern den Response-Body mitloggen — dort steht der
            # echte Kickbase-Grund (err/errMsg), der Statuscode allein ist stumm.
            body = _response_excerpt(exc) if isinstance(exc, httpx.HTTPStatusError) else ""
            _write_log.error(
                "API-WRITE FAILED type=%s player=%s after=%.0fms err=%s: %s%s",
                a.action_type, a.player_id, (time.monotonic() - start) * 1000,
                type(exc).__name__, exc, f" | body={body}" if body else "",
            )
            raise
        _write_log.info(
            "API-WRITE ok type=%s player=%s took=%.0fms",
            a.action_type, a.player_id, (time.monotonic() - start) * 1000,
        )
        return result

    async def _dispatch_raw(self, a: PlannedAction) -> dict[str, Any]:
        client = self._client
        if client is None:
            raise ValueError("Kein WriteClient injiziert — Live-Dispatch unmöglich")
        lg = a.league_id
        if a.action_type == "bid":
            return await client.place_bid(lg, _req(a.player_id), _req(a.amount))
        if a.action_type == "cancel_bid":
            return await client.cancel_bid(lg, _req(a.player_id), _req(a.offer_id))
        if a.action_type == "accept":
            return await client.accept_offer(lg, _req(a.player_id), _req(a.offer_id))
        if a.action_type == "decline":
            return await client.decline_offer(lg, _req(a.player_id), _req(a.offer_id))
        if a.action_type == "list":
            return await client.list_player(lg, _req(a.player_id), _req(a.amount))
        if a.action_type == "delist":
            return await client.delist_player(lg, _req(a.player_id))
        if a.action_type == "sell":
            return await client.sell_to_kickbase(lg, _req(a.player_id))
        if a.action_type == "lineup":
            return await client.set_lineup(lg, _req(a.formation), _req(a.player_ids))
        if a.action_type == "bonus":
            return await self._collect_bonus_measured(client, lg)
        raise ValueError(f"Unbekannter action_type: {a.action_type}")

    async def _collect_bonus_measured(self, client: Any, league_id: str) -> dict[str, Any]:
        """Prämie einsammeln UND die Gutschrift messen (Konto vor/nach).

        Kickbase antwortet auf /bonus/collect IMMER 200 + `it: []` — auch wenn
        nichts mehr zu holen ist (Tagesprämie schon per App kassiert oder noch
        nicht frei; Live-Fund 23.08.). Die einzige verlässliche Erfolgsquelle
        ist das Konto-Delta (plus Feed-Ereignis t=22 als Gegenprobe). Ergebnis landet
        als kba_*-Felder in raw_response; ist das Konto nicht lesbar, fehlen die
        Felder und es gilt der alte Vertrag (200 ⇒ executed).
        """
        read_budget = getattr(client, "get_budget", None)
        before = await _try_budget(read_budget, league_id)
        response = await client.collect_bonus()
        if before is None:
            return response
        after = await _try_budget(read_budget, league_id)
        if after is not None and after <= before:
            # Kurze Nachfrist: falls Kickbase asynchron gutschreibt.
            await asyncio.sleep(1.0)
            after = await _try_budget(read_budget, league_id) or after
        if after is None:
            return response
        merged = dict(response) if isinstance(response, dict) else {"raw": response}
        merged["kba_budget_before"] = before
        merged["kba_budget_after"] = after
        merged["kba_credited"] = max(after - before, 0)
        return merged

    def _resolve(
        self,
        row: TransferAction,
        status: str,
        stamp: datetime,
        raw_response: dict[str, Any] | None = None,
        rationale: str | None = None,
        mark_dry: bool = False,
    ) -> ActionResult:
        row.status = status
        row.resolved_at = stamp
        if raw_response is not None:
            row.raw_response = raw_response
        if rationale is not None:
            row.rationale = rationale
        if mark_dry:
            row.dry_run = True
        self._session.commit()
        return ActionResult(
            status=status, action_id=row.id, raw_response=raw_response, message=rationale or ""
        )


def _live_refusal_reason(action: PlannedAction) -> str:
    """Ehrliche Interlock-Begründung: benennt den Schalter, der WIRKLICH fehlt.

    lineup/bonus dürfen nicht auf AGENT_ALLOW_LIVE verweisen — das Flag ärmt
    sie seit dem P0-Fix nicht mehr (nur Trades).
    """
    if action.action_type == "lineup":
        flag = "AGENT_AUTO_LINEUP=0 (AGENT_ALLOW_LIVE ärmt keine Aufstellung)"
    elif action.action_type == "bonus":
        flag = "AGENT_AUTO_BONUS=0 (AGENT_ALLOW_LIVE ärmt keinen Bonus)"
    elif (action.rule_id or "").startswith("intent-"):
        # Steuerzentrale: der fehlende Schalter ist der Intent-Pfad selbst.
        flag = "AGENT_AUTO_INTENTS=0 (Intent-Pfad nicht geärmt; AGENT_ALLOW_LIVE=0)"
    else:
        flag = "AGENT_ALLOW_LIVE=0"
    return (
        f"Live-Write in Liga {action.league_id} verweigert: nicht die Test-Liga "
        f"und {flag}."
    )


def _req(value: Any) -> Any:
    if value is None:
        raise ValueError("Pflichtfeld der Aktion fehlt")
    return value


async def _try_budget(read_budget: Any, league_id: str) -> int | None:
    """Konto lesen, ohne den Write zu gefährden: Fehler ⇒ None (kein Delta)."""
    if read_budget is None:
        return None
    try:
        return int(await read_budget(league_id))
    except Exception as exc:  # noqa: BLE001 — Messung ist Zusatz, kein Blocker
        _write_log.warning("Konto-Lesung für Prämien-Delta fehlgeschlagen: %s", exc)
        return None


def _response_excerpt(exc: httpx.HTTPStatusError, limit: int = 300) -> str:
    """Antwort-Körper eines HTTP-Fehlers, gekürzt und einzeilig.

    Kickbase erklärt den echten Grund eines 500ers NUR im Body (z. B.
    {"err":5080,"errMsg":"UnderpayNotAllowed"}, Leiter-Beweis 24.08.) — der
    Statuscode allein ist stumm. Lesefehler liefern "" statt einer Exception:
    die Zusatzinfo darf den Fehlerpfad nie brechen.
    """
    try:
        text = (exc.response.text or "").strip()
    except Exception:  # noqa: BLE001 — Zusatzinfo, nie neuer Fehler
        return ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _as_utc(dt: datetime) -> datetime:
    """SQLite liefert naive Zeitstempel zurück — als UTC interpretieren."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _action_from_payload(payload: dict[str, Any]) -> PlannedAction:
    known = {f.name for f in fields(PlannedAction)}
    return PlannedAction(**{k: v for k, v in payload.items() if k in known})
