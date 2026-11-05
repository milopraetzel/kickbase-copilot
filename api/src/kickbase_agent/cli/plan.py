"""`kba plan` — heutige Kauf-/Verkaufsvorschläge berechnen (führt nichts aus)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from itertools import combinations
from math import comb
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

import typer
from sqlalchemy import select

from kickbase_agent.agent.executor import (
    PlannedAction,
    abandoned_bid_ids,
    confirmed_autonomous_bid,
)
from kickbase_agent.agent.rules import load_agent_rules
from kickbase_agent.config import get_settings
from kickbase_agent.db.models import PlayerHistory, TransferAction
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.decision.bid_premium import (
    CONVICTION_BONUS,
    CONVICTION_CAP,
    BidPremium,
    bid_premium,
)
from kickbase_agent.decision.bidding import BidDecision, TeamValue, choose_bid
from kickbase_agent.decision.commitments import merge_commitments
from kickbase_agent.decision.funding_recovery import build_funding_recovery
from kickbase_agent.decision.guardrails import DEFAULT_GUARDRAILS, protected_player_ids
from kickbase_agent.decision.kickoff_freeze import BID_FREEZE_MINUTES, KICKOFF_MIN_BALANCE
from kickbase_agent.decision.models import Proposal
from kickbase_agent.decision.overdraft import (
    MIDWEEK_SWAP_BRIDGE_EUR,
    PLANNING_OVERDRAFT_UNTIL,
    midweek_swap_bridge,
    overdraft_active,
    planning_overdraft,
    swap_sale_proceeds,
)
from kickbase_agent.decision.policies import SwapPair, propose_sells
from kickbase_agent.decision.purchase_review import (
    REVIEW_LIFETIME,
    emergency_bench_fallback_allowed,
)
from kickbase_agent.decision.team_optimizer import PlayerOption, TeamPlan, optimize_team
from kickbase_agent.intel.collector import collect_from_players
from kickbase_agent.intel.community import apply_community_boost, load_recent_tips
from kickbase_agent.intel.forecasting.dataset import LIVE_SETTLEMENT, clean_rows
from kickbase_agent.intel.forecasting.market_value import forecast_market_value
from kickbase_agent.intel.forecasting.service import forecast_player, load_active
from kickbase_agent.intel.history import load_history, load_performance
from kickbase_agent.intel.models import ExplosionCandidate
from kickbase_agent.intel.news_signal import judge_returns
from kickbase_agent.intel.role_judge import (
    NEWS_WINDOW,
    RoleVerdict,
    judge_roles,
    role_rejection,
)
from kickbase_agent.intel.signals import current_and_last_season, fixture_horizon_multiplier
from kickbase_agent.kickbase.factory import make_kickbase_client
from kickbase_agent.kickbase.models import SquadPlayer
from kickbase_agent.news.store import load_recent_articles
from kickbase_agent.scouts.ligainsider.models import Status
from kickbase_agent.scouts.ligainsider.profiteur import Profiteur, find_profiteurs
from kickbase_agent.scouts.ligainsider.service import enrich_candidates
from kickbase_agent.trading.competition import load_competition, predict_probability
from kickbase_agent.trading.trade_book import (
    is_trade,
    load_trade_book,
    remaining_updates_until_friday,
    sale_deadline,
    trade_sale_proceeds,
)
from kickbase_agent.worker.kickoff_guard import matchday_deadline

log = logging.getLogger(__name__)
_DEFAULT_LOAD_PERFORMANCE = load_performance
_DEFAULT_FORECAST_PLAYER = forecast_player

AUCTION_SETTLEMENT_BUFFER = timedelta(minutes=5)  # Planungsreserve, keine API-Zusage.
BERLIN = ZoneInfo("Europe/Berlin")

# Paket 1 der Turnaround-Studie (19.09.2026), Decision-Records in parameter_history.json.
#: Einzelrahmen bei Elf-Lücke (P0-3): Ein fehlender Platz kostet 100 Punkte je
#: Spieltag; der 0,70-Rahmen lehnte Remberg in 47 von 47 Plänen ab. Verbesserungen
#: einer besetzbaren Elf behalten DEFAULT_GUARDRAILS.max_bid_pct_of_budget (0,70).
COMPLETION_BID_FRACTION = 1.0
#: Staffel des Einzelrahmens bei MEHREREN offenen Elf-Plätzen (Emil, 21.09.2026): ein
#: einzelnes teures Listing darf nicht das Geld für die übrigen Plätze binden.
COMPLETION_BID_FRACTION_TWO_OPEN = 0.60
COMPLETION_BID_FRACTION_MANY_OPEN = 0.45


def completion_bid_fraction(open_slots: int) -> float:
    """Anteil des Planungsgelds für EIN Gebot, abhängig von den offenen Elf-Plätzen."""
    if open_slots <= 1:
        return COMPLETION_BID_FRACTION
    if open_slots == 2:
        return COMPLETION_BID_FRACTION_TWO_OPEN
    return COMPLETION_BID_FRACTION_MANY_OPEN
#: Rollenbeleg (P0-5, erweitert 19.10.2026 um 4-Pfade-Starterbeleg & <5M-Joker-Filter):
ROLE_PROOF_MIN_VALUE = 5_000_000
ROLE_PROOF_MIN_MINUTES = 60
ROLE_PROOF_RECENT_MIN_MINUTES = 70
ROLE_PROOF_SUB_5M_MIN_MINUTES = 45
ROLE_PROOF_MATCHDAYS = 2
ROLE_PROOF_KB3_MIN_MV_CHANGE_24H = 150_000
ROLE_PROOF_BREAKOUT_MIN_MINUTES = 75
ROLE_PROOF_BREAKOUT_MIN_POINTS = 100
#: Gebotsaufschlag-Deckel für KB-Stufe 3 („Unsicher"): maximal 10 % über Marktwert
#: (Rotationsrisiko; 15–20 % bleiben verifizierten KB-1/2-Stammspielern vorbehalten).
KB3_PREMIUM_CAP = 0.10
#: Mindest-Quotenfaktor über die nächsten 2 Spieltage (horizon=2), ab dem ein formstarker
#: steigender Starter im Tausch-Planer geschützt bleibt (Emil 22.10.2026: Tigges für HSV +
#: Gladbach schützen, bei zwei schweren Brocken wie Frankfurt + Leverkusen wieder freigeben).
STARTER_PROTECT_MIN_FIXTURE_MULT_2 = 0.96
#: Starter-Prior für verifizierte Comeback-/Breakout-Kandidaten ohne Saisonminuten.
COMEBACK_EXPECTED_POINTS = 75.0
COMEBACK_HORIZON_POINTS = 300.0


def _comeback_expected_points(market_value_eur: int) -> float:
    """Marktwert-skalierter Starter-Prior (55 bis 125 Pkt/Spieltag) statt Pauschale."""
    mv_m = max(0.5, float(market_value_eur or 0) / 1_000_000.0)
    if mv_m <= 6.0:
        return max(55.0, min(COMEBACK_EXPECTED_POINTS, round(45.0 + mv_m * 5.0, 1)))
    return min(125.0, round(COMEBACK_EXPECTED_POINTS + (mv_m - 6.0) * 2.0, 1))


def estimate_rival_scarcity(
    market_listings: list,
    *,
    position: int,
    rival_squads: list[dict] | None = None,
) -> int:
    """Schätzt die Anzahl der Mitspieler mit akutem Bedarf auf ``position``."""
    pos_min = {1: 1, 2: 3, 3: 2, 4: 1}.get(int(position), 1)
    needy_rivals: set[str] = set()
    for mgr in rival_squads or []:
        if mgr.get("me"):
            continue
        uid = str(mgr.get("user_id") or mgr.get("id") or "")
        players = mgr.get("spieler") or mgr.get("players") or []
        pos_count = sum(
            1
            for p in players
            if int(p.get("pos") or p.get("position") or 0) == int(position)
            and int(p.get("prob") or p.get("lineup_prob") or 1) in {1, 2}
        )
        if uid and pos_count < pos_min:
            needy_rivals.add(uid)
    for mp in market_listings:
        seller = str(getattr(mp, "seller_id", "") or "")
        mp_pos = int(getattr(getattr(mp, "player", None), "position", 0) or 0)
        if seller and mp_pos == int(position):
            needy_rivals.add(seller)
    return len(needy_rivals)
#: Regel-ID für den gezielten Abbau von Nicht-Startern bei negativem Kontostand.
DEFICIT_COVER_RULE = "sell_deficit_cover"
#: Konto-heilender Downgrade-Tausch (25.10.2026): Wenn das Konto bei 11er-Kader im Minus
#: steht, darf ein teurer Nicht-Starter oder fallender Starter gegen einen günstigeren
#: verifizierten Starter (>= 150 Horizontpunkte) getauscht werden, sofern der 90-%-Erlös
#: das Defizit um mindestens 1,5 M € abbaut und nach dem Tausch höchstens 500k € Rest-
#: Überhang verbleiben (gedeckt durch die 10-%-Erlösreserve und tägliche Login-Boni).
DEFICIT_HEAL_MIN_RELIEF_EUR = 1_500_000
DEFICIT_HEAL_MAX_RESIDUAL_EUR = 500_000
DEFICIT_HEAL_MIN_HORIZON_POINTS = 150.0
#: Freitag-Notbesetzungs-Tausch (Emil 30.10.2026): Steht das Konto am Spieltag (< 24 h bis
#: Anpfiff, vor T-120) im Minus und ist kein günstiger verifizierter Starter am Markt, darf
#: ein günstiger fitter Feldspieler (<= 1,0 M €, auch von Mitspielern bis 101,5 % MW) als
#: 11.-Mann-Lückenfüller gegen einen teuren fallenden Spieler getauscht werden, um die
#: -100-Punkte-Leerplatzstrafe bei vollem Konto-Ausgleich zu vermeiden.
DEFICIT_FILLER_MAX_PRICE_EUR = 1_000_000
#: Gleicher Text wie local_engine.py: ein Manager nimmt an oder nicht, und wann.
MANAGER_LISTING_REJECTION = "Manager-Angebot: Zuschlag und Abschlusszeitpunkt nicht bestätigt."
#: Spieltheoretisches Manager-Listing-Gate (Emil 20.10.2026 & 30.10.2026, Decision-Record in
#: parameter_history.json): KB-Stufe-1-Stammspieler mit LI-STARTER, Minutenbeleg,
#: nicht-fallendem 24h-Marktwert, fairem Anker (prc <= 101,5 % MW) sowie bis T-120 vor Anpfiff
#: auch für gekoppelte Tausche und Freitag-Defizit-Heilung freigegeben.
MANAGER_MAX_PRICE_FACTOR = 1.015
MANAGER_MAX_LISTING_AGE = timedelta(hours=36)
MANAGER_MIN_DEADLINE_LEAD = timedelta(minutes=BID_FREEZE_MINUTES)
MANAGER_MIN_HORIZON_GAIN = 15.0
MANAGER_BARGAIN_MIN_HORIZON_GAIN = 5.0
MANAGER_BARGAIN_MIN_MV_CHANGE_24H = 50_000
MANAGER_MAX_OPEN_BIDS = 1
MANAGER_MAX_SELLER_SHOWCASE = 4
#: Tauschbremse (Emil 12.10.2026, verschärft nach Profi-Audit 20.10.2026): Einen selbst
#: gekauften Spieler gibt die Engine in den ersten 3 Tagen nicht per Tausch wieder ab
#: (außer er fällt auf KB-Stufe 4/5).
SWAP_HOLD_AFTER_PURCHASE = timedelta(days=3)
#: Mindest-Punktgewinn im 4-Spieltage-Horizont, ab dem ein noch nicht abgegebenes
#: (`planned`) Tausch-Vorhaben durch ein stärkeres Tausch-Paar abgelöst wird.
SWAP_UPGRADE_MIN_GAIN_POINTS = 15.0
#: Unter der Woche (> 48 h vor Anpfiff) löst ein kleines Defizit innerhalb der
#: Wochen-Brücke (`MIDWEEK_SWAP_BRIDGE_EUR`) keinen Downgrade-Tausch aus.
MIDWEEK_DEFICIT_DOWNGRADE_LEAD = timedelta(hours=48)
#: So weit liest load_recent_purchases das Audit zurück (mehr als die Haltefrist).
PURCHASE_LOOKBACK = timedelta(days=30)


@dataclass
class Plan:
    league_id: str
    budget: int
    buys: list[Proposal]
    sells: list[Proposal]
    # Kontext fürs Briefing (spart doppelte API-Calls):
    squad: list[SquadPlayer] = field(default_factory=list)
    user_id: str = ""
    league_name: str = ""
    # Markt-Starter, deren Teamkollege ausfällt (Kaufchance vor MW-Anstieg):
    profiteurs: list[Profiteur] = field(default_factory=list)
    # Interne 200er-Regel: verbrauchte Transfers der Halbserie.
    # trade_count_saturated=True → der Activity-Feed kam voll zurück, trades_used
    # ist nur eine UNTERGRENZE (Anzeige als "≥ N", Engine-Sperre aktiv).
    trades_used: int = 0
    # Aufteilung der verbrauchten Trades (Emils Fund 25.08.): Die Liga-Regel
    # zählt Käufe UND Verkäufe — die Anzeige muss zeigen, wofür.
    trades_buys: int = 0
    trades_sells: int = 0
    trade_limit: int = 200
    trade_count_saturated: bool = False
    # Fürs Cockpit: ALLE fertig gescorten Markt-Kandidaten (nach LI-Anreicherung
    # + Community-Boost, VOR dem Kauf-Schwellen-Filter).
    market_candidates: list[ExplosionCandidate] = field(default_factory=list)
    # Positionen ohne Reserve überm Formations-Minimum (Kader-Gesetz): dort gilt
    # die gesenkte Kauf-Schwelle. Im Plan, damit der Planner die Kauf-Diagnose
    # (buy_blocker_reason) mit derselben Schwelle rechnet wie propose_buys.
    need_positions: set[int] = field(default_factory=set)
    # Momentum-Doktrin (31.08.): Basis, auf der die Gebotskappe wirklich
    # gerechnet wurde (Konto − Exposure + Momentum-Reserve, > 48 h vor der
    # Deadline) — die Kauf-Diagnose des Planners muss mit DERSELBEN Zahl
    # rechnen wie propose_buys, sonst erklärt sie das falsche Nichtstun.
    plan_base: int = 0
    momentum_reserve_eur: int = 0
    # Pfand-Hebel (01.09.): Risiko-Budget der Pfand-Buchführung und Anzahl der
    # Kaufvorschläge, die der exakte Pfand-Filter verworfen hat (Transparenz
    # für Planner-Report und Diagnose).
    risk_budget_eur: int = 0
    pledge_dropped: int = 0
    # Nächste S...[truncated]
    # Filter des Planners; None, wenn keine Termine bekannt sind.
    matchday_deadline_at: datetime | None = None
    # LigaInsider-Bank-Status aus dem Plan-Bau (L-090: Status statt Score-Proxy).
    # None = LI-Daten fehlten — geld-riskante Status-Pfade im Planner (Doktrin-
    # Satz 3: gescheiterte Listings → Sofortverkauf) laufen dann fail-safe leer.
    li_bench_ids: frozenset[str] | None = None
    # Tausch-Paare (02.09.): Verlierer MIT Startelf-Pfad + Gebot auf den Ersatz —
    # der Worker macht daraus Gebot + wartenden Sofortverkauf (after_squad_size).
    swaps: list[SwapPair] = field(default_factory=list)
    # Nachvollziehbare Ablehnung teurer Gleichwertigkeit/ungedeckter Tausche.
    roster_value_notes: list[str] = field(default_factory=list)
    # None nur für alte isolierte Aufrufer; echte Pläne liefern immer ein Dict.
    purchase_reviews: dict[str, dict] | None = None
    curation_notes: list[str] = field(default_factory=list)
    team_plan: dict = field(default_factory=dict)
    projections: dict[str, dict] = field(default_factory=dict)
    # Handelsspieler (Handels-Autopilot, Emil 01.10.2026: „sie bleiben Bank“): im Kader, aber
    # nie in der Elf, nie Verkaufs- oder Tauschware der Engine.
    trade_player_ids: frozenset[str] = frozenset()
    # Handelsgebote, deren Geld dieser Plan als gebunden zählte (Spieler → Betrag; abgegebene
    # und feste). Wurde danach eines fest oder höher, ist seine Finanzierung überholt
    # (worker/intents.late_firm_trade_bids, Codex-Review 03.10.2026).
    trade_bound: dict[str, int] = field(default_factory=dict)

    @property
    def lineup_squad(self) -> list[SquadPlayer]:
        """Kader für die Aufstellung — ohne Handelsspieler."""
        return [sp for sp in self.squad if sp.player.id not in self.trade_player_ids]


def load_pending_bid_commitments(session, league_id: str, now: datetime) -> list[dict]:
    """Echte Beträge offener Vorhaben; fehlende Legacy-Liga konservativ mitzählen."""
    from kickbase_agent.db.models import Intent

    result = []
    for row in (
        session.query(Intent)
        .filter(Intent.kind == "bid", Intent.status.in_(("planned", "executing")))
        .all()
    ):
        sources = row.sources or {}
        saved_league = sources.get("league_id")
        if saved_league is not None and str(saved_league) != str(league_id):
            continue
        deadline = row.deadline
        if deadline is not None:
            deadline = deadline.replace(tzinfo=UTC) if deadline.tzinfo is None else deadline
        if row.status == "planned" and deadline is not None and deadline <= now:
            continue
        result.append(
            {
                "player_id": row.player_id,
                "amount": row.amount,
                "team_id": row.team_id,
                "position": row.position,
                "intent_id": row.id,
                "status": row.status,
                "manual": bool(sources.get("manual")) or row.adjusted_from is not None,
                "listing_type": row.listing_type,
                "purchase_review": sources.get("purchase_review"),
                "trade": is_trade(sources),
            }
        )
    return result


def record_premium(
    premiums: dict[str, BidPremium], pid: str, premium: BidPremium | None
) -> None:
    """Gebotsregel V3 je Ziel merken. Eine spätere Bewertung ohne beobachtetes `tfhmvt`
    löscht den alten Eintrag (Review 25.09.2026), sonst trüge der Kaufbeleg eine
    Herleitung, die für das tatsächliche Gebot nicht galt."""
    if premium is None:
        premiums.pop(pid, None)
    else:
        premiums[pid] = premium


def _premium_record(
    pid: str, premiums: dict[str, BidPremium], commitments: dict[str, dict], bid_eur: int
) -> dict | None:
    """V3-Herleitung für den Kaufbeleg samt Planbetrag.

    Ein geplantes Vorhaben trägt die Herleitung seines ersten Belegs weiter (Review
    25.09.2026, Befund 2): Ab dem zweiten Planlauf ist es eine Verpflichtung ohne neues
    Zielgebot, und der Beleg geht ins Vorhaben und ins Audit. Ein Live-Gebot trägt keinen
    Beleg; seine Herleitung steht im Audit der Ausführung. `plan_bid_eur`/`plan_applied`
    sind Planwerte (Re-Review): Emils +10 % vor dem nächsten Lauf, ein manuelles Vorhaben
    oder der Nachanker des Executors ändern den tatsächlichen Betrag danach. Maßgeblich
    für die Auswertung ist `transfer_actions.amount` gegen `amount_eur`."""
    if pid in commitments:
        prior = (commitments[pid].get("purchase_review") or {}).get("bid_premium")
        if not isinstance(prior, dict) or type(prior.get("amount_eur")) is not int:
            return None
        record = {k: v for k, v in prior.items() if k not in ("plan_bid_eur", "plan_applied")}
    elif pid in premiums:
        record = premiums[pid].as_dict()
    else:
        return None
    record["plan_bid_eur"] = bid_eur
    record["plan_applied"] = record["amount_eur"] == bid_eur
    return record


def _standing_bid_reason(commitment: dict, fallback: str) -> str:
    """Grund eines stehenden Gebots: V3-Herkunft statt „Kleinster sinnvoller Einstieg" —
    aber nur, solange der Betrag das V3-Ziel ist (Re-Review 25.09.2026)."""
    prior = (commitment.get("purchase_review") or {}).get("bid_premium")
    if isinstance(prior, dict) and type(prior.get("amount_eur")) is int:
        target = f"{prior['amount_eur']:,}".replace(",", ".")
        amount = commitment.get("amount")
        if amount == prior["amount_eur"]:
            return (
                f"Vorhandener Betrag nach Gebotsregel V3 (Ziel {target} €, Regel "
                f"{prior.get('rule', 'unbekannt')}) bleibt bis zur neuen Freigabe bestehen."
            )
        shown = f"{int(amount or 0):,}".replace(",", ".")
        return (
            f"Vorhandener Betrag {shown} € weicht vom V3-Ziel {target} € ab (gekappt, "
            "gekürzt oder angepasst) und bleibt bis zur neuen Freigabe bestehen."
        )
    return fallback + " Vorhandener Betrag bleibt bis zur neuen Freigabe bestehen."


def load_swap_couplings(session, league_id: str) -> list[dict]:
    """Wartende Tausch-Verkäufe auf Dispo (Emil 23.09.2026): Verkaufsspieler → Kaufziel.

    Nur Vorhaben, die der Planner selbst als Dispo-Tausch (`swap_dispo`) oder als
    Dispo-Glättung (`dispo_smoothing`) angelegt hat, noch `planned` sind und genau
    einen Ersatz nennen. Ein vetoierter, gescheiterter
    oder abgelaufener Verkauf koppelt nichts mehr: dann deckt der Verkauf auch nichts.
    """
    from kickbase_agent.db.models import Intent

    result = []
    for row in (
        session.query(Intent)
        .filter(Intent.kind == "sell", Intent.status == "planned")
        .all()
    ):
        sources = row.sources or {}
        saved_league = sources.get("league_id")
        target = sources.get("swap_for")
        dispo_sale = sources.get("rule_id") == "swap_sell" and sources.get("swap_dispo") is True
        # Eine offene Dispo-Glättung (Befund 2b) ist ebenfalls ein offenes Dispo-Paar: Sie
        # blockiert jeden neuen Tausch (Befund 3); Anrechnung gibt es für sie nicht, weil
        # ihr Ziel schon im Kader steht und damit keine Verpflichtung mehr ist.
        smoothing = sources.get("rule_id") == "dispo_smoothing"
        if (
            not (dispo_sale or smoothing)
            or sources.get("manual")
            or not isinstance(target, str)
            or not target
            or (saved_league is not None and str(saved_league) != str(league_id))
        ):
            continue
        result.append({"player_id": str(row.player_id), "for_player_id": target})
    return result


def load_blocked_sell_ids(session, league_id: str, now: datetime) -> frozenset[str]:
    """Kaderspieler, deren Verkaufs-Vorhaben heute (executed/executing/failed) oder per
    aktivem Veto (24 h bzw. laufende Frist) gesperrt ist — sie dürfen im selben Lauf
    nicht erneut als Tausch-Verkaufsseite (`swap_sell`) gewählt werden."""
    if session is None or not hasattr(session, "query"):
        return frozenset()
    from kickbase_agent.db.models import Intent

    now_utc = now if now.tzinfo else now.replace(tzinfo=UTC)
    today = now_utc.date()
    blocked: set[str] = set()
    for row in (
        session.query(Intent)
        .filter(
            Intent.kind == "sell",
            Intent.status.in_(("executed", "executing", "failed", "vetoed")),
        )
        .all()
    ):
        sources = row.sources or {}
        saved_league = sources.get("league_id")
        if saved_league is not None and str(saved_league) != str(league_id):
            continue
        stamp = (
            row.updated_at if row.updated_at.tzinfo else row.updated_at.replace(tzinfo=UTC)
        )
        if row.status == "vetoed":
            deadline = (
                row.deadline
                if row.deadline is None or row.deadline.tzinfo
                else row.deadline.replace(tzinfo=UTC)
            )
            live_deadline = (
                deadline is not None
                and deadline > now_utc
                and sources.get("rule_id") != "swap_sell"
            )
            if stamp >= now_utc - timedelta(hours=24) or live_deadline:
                blocked.add(str(row.player_id))
        elif stamp.date() == today:
            blocked.add(str(row.player_id))
    return frozenset(blocked)


def load_recent_purchases(session, league_id: str, now: datetime) -> dict[str, datetime]:
    """Jüngstes echtes eigenes Gebot je Spieler (Tauschbremse): ausgeführt, kein Probelauf,
    diese Liga, höchstens PURCHASE_LOOKBACK alt. Als Kaufzeit gilt der Gebots-Write — das
    Gebot geht kurz vor dem Auktionsende hinaus. Emils Käufe in der App kennt das Audit
    nicht. Ohne lesbare Datenbank (Read-only-Replay) leer."""
    if session is None or not hasattr(session, "query"):
        return {}
    since = (now if now.tzinfo else now.replace(tzinfo=UTC)) - PURCHASE_LOOKBACK
    latest: dict[str, datetime] = {}
    for action in (
        session.query(TransferAction)
        .filter(
            TransferAction.action_type == "bid",
            TransferAction.status == "executed",
            TransferAction.dry_run.is_(False),
            TransferAction.league_id == league_id,
        )
        .all()
    ):
        pid = str(action.player_id or "")
        at = action.created_at if action.created_at.tzinfo else action.created_at.replace(
            tzinfo=UTC)
        if pid and at >= since and (pid not in latest or at > latest[pid]):
            latest[pid] = at
    return latest


def swap_pays_off(
    *,
    gain_points: float,
    premium_eur: int,
    team_value_eur: int,
    team_points: float,
    mv_advantage_eur: int = 0,
) -> bool:
    """Lohnt ein Tausch? Der erwartete Punktgewinn muss mehr wert sein als der Aufschlag beim
    Kauf (Tauschbremse, Emil 12.10.2026), abzüglich eines positiven Marktwert-Trendvorteils.
    Ein Prognosepunkt kostet so viel, wie unsere Elf für ihre Punkte bezahlt hat:
    Teamwert ÷ Prognosepunkte der Elf über den Horizont."""
    if team_points <= 0 or gain_points <= 0:
        return False
    effective_premium = max(0, premium_eur - max(0, int(mv_advantage_eur)))
    return gain_points * team_value_eur / team_points > effective_premium


def load_recovery_audit(
    session, league_id: str, live_bids: list[dict], now: datetime, market_by_id: dict | None = None,
):
    """Nur zum Livebetrag passende autonome Writes belegen die Herkunft."""
    manual = {str(row.get("player_id")) for row in live_bids}
    # Read-only Replay-Clients können ausdrücklich ohne Audit arbeiten. Dann
    # sind alle Livegebote manuell/ungeklärt und werden niemals zurückgezogen.
    if session is None or not hasattr(session, "scalars"):
        return manual, set()
    for bid in live_bids:
        pid = str(bid.get("player_id"))
        latest = session.scalars(select(TransferAction).where(
            TransferAction.league_id == league_id, TransferAction.player_id == pid,
            TransferAction.action_type == "bid", TransferAction.dry_run.is_(False),
            TransferAction.status.in_(("executed", "pending", "unknown")),
        ).order_by(TransferAction.created_at.desc(), TransferAction.id.desc())).first()
        expires = getattr((market_by_id or {}).get(pid), "expires_at", None)
        seconds = bid.get("expires_in_s")
        if expires is None and type(seconds) is int and seconds > 0 and not bid.get("seller"):
            expires = now + timedelta(seconds=seconds)
        if confirmed_autonomous_bid(latest, bid, expires_at=expires, now=now):
            manual.discard(pid)
            bid["funding_provenance"] = {
                "original_bid_action_id": latest.id,
                "original_listing_expires_at": latest.payload["confirmed_bid"]["expires_at"],
            }
    return manual, abandoned_bid_ids(session, league_id, now)


def cash_deficit_alternative(squad, budget: int, deadline, projections: dict) -> dict:
    """Kleinste Verkaufsauswahl dokumentieren; erst die Notbremse darf handeln."""
    eligible = [sp.player for sp in squad if sp.player.id not in protected_player_ids()
                and sp.player.market_value > 0]
    eligible.sort(key=lambda p: p.market_value, reverse=True)
    selected = []
    for count in range(1, len(eligible) + 1):
        if sum(p.market_value for p in eligible[:count]) < -budget:
            continue
        if comb(len(eligible), count) > 10_000:
            selected = eligible[:count]
            break
        choices = [group for group in combinations(eligible, count)
                   if sum(p.market_value for p in group) >= -budget]
        if choices:
            selected = list(min(choices, key=lambda group: (
                sum(projections.get(p.id, {}).get("expected_points") or 0 for p in group),
                sum(p.market_value for p in group), tuple(p.id for p in group),
            )))
            break
    total = sum(p.market_value for p in selected)
    return {
        "status": "conditional_estimate" if selected else "blocked",
        "cash_deficit_eur": -budget, "estimated_proceeds_eur": total,
        "remaining_deficit_eur": max(0, -budget - total),
        "remaining_squad_size": len(squad) - len(selected),
        "sales": [{"player_id": p.id, "name": f"{p.first_name} {p.last_name}".strip(),
                   "estimated_proceeds_eur": p.market_value} for p in selected],
        "proceeds_source": "current_market_value_estimate", "guaranteed_proceeds": False,
        "responsible": "kickoff_guard", "execution_authorized": False,
        "guard_at": (deadline - timedelta(minutes=15)).isoformat() if deadline else None,
        "note": "Verkaufsalternative zur Deckung; Marktwerte sind keine zugesagten Erlöse. "
                "Anpfiff-Notbremse prüft bei T−15 frisch. Kadersicherungen bleiben aktiv.",
    }


def is_verified_starter(
    player_id: str,
    lineup_prob: int | None,
    li_status_by_pid: dict[str, Status],
    fitness_status: int | None,
) -> bool:
    """Belastbarer Startelfpfad: LI-Starter oder ersatzweise KB-Stufe 1/2.

    Ein ausdrücklicher LI-Bank-/Ausfallstatus schlägt die Kickbase-Zweitquelle.
    Fehlende Daten und KB-Stufe 3 (offenes 50/50) sind kein positives Signal und
    zählen deshalb nicht als sichere Kaderdeckung.
    """
    status = li_status_by_pid.get(str(player_id), Status.UNKNOWN)
    if lineup_prob is not None:
        try:
            kb_prob = int(lineup_prob)
        except (TypeError, ValueError):
            return False
        if kb_prob not in {1, 2, 3, 4, 5}:
            return False
        if kb_prob in {4, 5}:
            return False
    else:
        kb_prob = None
    if fitness_status is not None and int(fitness_status) != 0:
        return False
    if status is Status.STARTER:
        return True
    if status in {Status.BENCH, Status.OUT}:
        return False
    return kb_prob in {1, 2} and fitness_status == 0


def verified_deployable_counts(
    players: list,
    li_status_by_pid: dict[str, Status],
    own_listed_ids: set[str],
    unavailable_ids: set[str] | frozenset[str] = frozenset(),
) -> dict[int, int]:
    """Belastbare Starter je Position, ohne Listings und bekannte Ausfälle."""
    counts: dict[int, int] = {}
    for player in players:
        if player.id in own_listed_ids or player.id in unavailable_ids:
            continue
        if not is_verified_starter(
            player.id,
            player.lineup_prob,
            li_status_by_pid,
            player.fitness_status,
        ):
            continue
        position = int(player.position)
        counts[position] = counts.get(position, 0) + 1
    return counts


async def build_plan(
    league_id: str | None = None,
    *,
    blocked_target_ids: frozenset[str] | set[str] = frozenset(),
    blocked_sell_ids: frozenset[str] | set[str] = frozenset(),
) -> Plan:
    """Mannschaftsplan bauen.

    `blocked_target_ids` (Paket 1, P0-1): Ziele, die der Aufrufer heute für neue
    Gebote gesperrt hat (Veto, Fehlschlag). Sie werden VOR dem Optimierer
    ausgeschlossen, damit kein Platz auf ein Ziel geplant wird, das der Planner
    ohnehin nicht anlegen darf (Pieringer/Al Dakhil wurden trotz Sperre erneut
    eingeplant). Ein bereits stehendes Gebot bleibt gebundene Option — es ist
    Wirklichkeit, und sein Ausschluss erzeugte einen Doppelkauf.
    """
    # Zentrale Fabrik (Review-Fund P0): Client MIT Token-Refresh — ein 401 mitten
    # im Plan-Bau erneuert den Token, statt Briefing/Puls/Scan zu killen.
    client, auth = await make_kickbase_client()
    lg = league_id or auth.league_id
    if lg is None:
        raise typer.BadParameter("Keine Liga gefunden — --league/-l angeben.")

    squad = await client.get_squad(lg)
    market = await client.get_market(lg)
    budget = await client.get_budget(lg)

    squad_players = [sp.player for sp in squad]
    # Gelistete EIGENE Spieler stehen bei Kickbase in Kader UND Markt —
    # aus dem Kauf-Pfad fliegen sie raus (Hermes-Audit: sonst tauchen sie als
    # "Kaufkandidat" auf und verfaelschen Kadergroesse/Slots).
    # Zombie-Listings (Manager-Listing, Alter > 48 h) fliegen VOR dem Scoring
    # aus dem Kauf-Universum: Kickbase nimmt auf sie keine Offers mehr an
    # (HTTP 500, Live-Probe 23.08.) — sonst verbrennt jeder Pfad (Briefing,
    # Planner, Cockpit-Kaufliste) Vorschläge auf unkaufbare Schnäppchen.
    squad_ids = {p.id for p in squad_players}
    now = datetime.now(UTC)
    alive = [mp for mp in market if not mp.is_zombie(now)]
    market_players = [mp.player for mp in alive if mp.player.id not in squad_ids]
    # Manager-Listing-Preise für den Gebots-Anker der Kauf-Policy (Empirie:
    # Underpay verliert Manager-Deals — am Listing-Preis ankern).
    manager_prices = {
        mp.player.id: mp.price for mp in alive if mp.seller_id and mp.player.id not in squad_ids
    }
    ids = [p.id for p in squad_players + market_players]

    fixtures = await client.get_matchdays()
    team_names = await client.get_team_names()

    with SessionLocal() as session:
        history = load_history(session, ids)
        perf = load_performance(session, ids)
        tips = load_recent_tips(session)
        pending_bids = load_pending_bid_commitments(session, lg, now)
        couplings = load_swap_couplings(session, lg)
        recent_purchases = load_recent_purchases(session, lg, now)
        blocked_sell_ids = frozenset(blocked_sell_ids) | load_blocked_sell_ids(session, lg, now)
    # Untertags neu gelistete Kickbase-Auktionen (vor dem 22:05-Snapshot, z. B. Hennes Behrens
    # oder Spieler ohne den jüngsten Spieltag in der DB) live von der API nachladen, damit
    # Rollenbeleg und Punktprognose nicht bis 22:05 blind bleiben.
    if load_performance is _DEFAULT_LOAD_PERFORMANCE and hasattr(client, "get_player_performance"):
        latest_db_md = max(
            ((r.season, r.matchday) for rows in perf.values() for r in rows if r.minutes > 0),
            default=None,
        )
        valid_seasons = set(current_and_last_season())
        for mp in alive:
            pid = mp.player.id
            if pid in squad_ids or mp.player.market_value < 1_500_000:
                continue
            if mp.seller_id and (
                mp.player.lineup_prob != 1
                or mp.price > int(mp.player.market_value * MANAGER_MAX_PRICE_FACTOR)
            ):
                continue
            existing = perf.get(pid, [])
            player_max_md = max(((r.season, r.matchday) for r in existing), default=None)
            if not existing or (latest_db_md is not None and player_max_md < latest_db_md):
                try:
                    live_perf = await client.get_player_performance(lg, pid)
                    played = [
                        r for r in live_perf
                        if (r.minutes or 0) > 0 and r.season in valid_seasons
                    ]
                    played.sort(key=lambda r: (r.season, r.matchday))
                    if played:
                        perf[pid] = played
                except Exception:
                    pass
            existing_hist = history.get(pid, [])
            last_snap_dt = (
                existing_hist[-1].recorded_at.replace(tzinfo=UTC)
                if existing_hist and existing_hist[-1].recorded_at.tzinfo is None
                else (existing_hist[-1].recorded_at if existing_hist else None)
            )
            if (
                len(existing_hist) < 2
                or (last_snap_dt is not None and now - last_snap_dt > timedelta(days=2))
            ) and hasattr(client, "get_player_mv_history"):
                try:
                    snaps = await client.get_player_mv_history(lg, pid, timeframe=30)
                    if snaps:
                        history[pid] = [
                            PlayerHistory(
                                player_id=pid,
                                market_value=int(s.value),
                                points=0,
                                is_starter=False,
                                recorded_at=(
                                    s.date if s.date.tzinfo else s.date.replace(tzinfo=UTC)
                                ),
                            )
                            for s in sorted(snaps, key=lambda x: x.date)
                            if s.value > 0
                        ]
                except Exception:
                    pass
    # Tauschbremse: frisch gekaufte Kaderspieler sind keine Tausch-Verkaufsseite,
    # solange sie ihre Startelfrolle und Fitness behalten (KB-Stufe 4/5, Verletzung oder
    # LI-Bank/Ausfall hebt die Haltefrist auf).
    squad_prob = {p.id: p.lineup_prob for p in squad_players}
    squad_fitness = {p.id: p.fitness_status for p in squad_players}
    held = {
        pid: bought for pid, bought in recent_purchases.items()
        if pid in squad_ids
        and now - bought < SWAP_HOLD_AFTER_PURCHASE
        and squad_prob.get(pid) not in {4, 5}
        and squad_fitness.get(pid) == 0
    }

    squad_cands = await collect_from_players(squad_players, fixtures, team_names, history, perf)
    market_cands = await collect_from_players(market_players, fixtures, team_names, history, perf)

    # LigaInsider-Veto: verletzte/gesperrte Spieler auf xPoints 0, Bankspieler ×0.4 —
    # verhindert Fehl-Verkäufe (0 Punkte ≠ Formschwäche) und Fehl-Käufe.
    squad_scout = await enrich_candidates(squad_cands, purpose="kader")
    market_scout = await enrich_candidates(market_cands, purpose="kaufpruefung")
    # Rückkopplung der Recherche: Community-/Insider-Tipps boosten die KAUF-Scores
    # (Nachfrage-Proxy). Seit 31.08. (Momentum-Doktrin, Emils „Ausnahmen, die die
    # Recherche kennt") auch auf den KADER: ein positiver Tipp hebt den
    # total_score eines Verkaufskandidaten (±0.08) und rettet ihn vor Pass 1 —
    # der Recherche-Hebel wirkt damit auf beide Seiten des Handels. Die
    # Aufstellung bleibt beweisbar hype-frei: der Boost ändert nie den
    # lineup_score (intel/community.py).
    # Kader nur in Schutz-Richtung (Review 31.08.): Der Decision-Record verspricht
    # den Boost als VERKAUFS-Schutz — ein negativer Tipp (Gewichte bis −1.0 sind
    # dokumentiertes Feature) würde sonst einen Kaderspieler erst unter die
    # 0.35-Schwelle drücken und aus einem Gerücht ein Listing machen. Am Markt
    # bleibt der Boost symmetrisch (dort blockt ein negativer Tipp nur den Kauf).
    squad_cands = apply_community_boost(squad_scout.candidates, tips, only_positive=True)
    market_cands = apply_community_boost(market_scout.candidates, tips)

    profiteurs: list[Profiteur] = []
    if market_scout.snapshot is not None:
        profiteurs = find_profiteurs(
            market_cands,
            squad_cands + market_cands,
            market_scout.availabilities,
            market_scout.snapshot.injuries,
        )

    # Vereinslimit (live aus den Liga-Regeln, BASE: lpc=11) + offene Gebote:
    # Kader UND laufende Gebote zählen pro Club; Gebots-Summen mindern die
    # Kaufkraft (Review-Fund: 3 Zuschläge gleichzeitig → unerwartet tief im Minus).
    rules = await client.get_league_rules(lg)
    club_counts: dict[str, int] = {}
    for p in squad_players:
        club_counts[p.team_id] = club_counts.get(p.team_id, 0) + 1
    # Offene Gebote LIVE aus dem Markt (Review-Fund: die frühere Rekonstruktion
    # aus bis zu 4 Tage alten transfer_actions-Audit-Zeilen zählte längst
    # gewonnene/stornierte Gebote mit). get_own_bids liefert pro Gebot Betrag
    # UND team_id (tid direkt aus dem Markt-Payload) — Exposure wie Club-Zählung
    # laufen damit vollständig über Live-Daten.
    open_bid_exposure = 0
    open_bid_pids: set[str] = set()
    live_bids = await client.get_own_bids(lg, auth.user_id)
    market_by_id = {mp.player.id: mp for mp in alive if mp.player.id not in squad_ids}
    # Handelsbuch (Handels-Autopilot, Emil 01.10.2026): Handelsspieler stehen nie in der Elf
    # („sie bleiben Bank“) und sind keine Verkaufs- oder Tauschware der Engine. Feste
    # Handelsgebote (abgegeben, in Ausführung oder von Emil angepasst) binden Geld — die Engine
    # plant nur mit dem Rest —, sind aber keine Engine-Ziele, keine Verpflichtung der Engine und
    # nie Stoff der Deckungsreparatur: Ihr Geld deckt der Verkauf des Handelsspielers spätestens
    # am Verkaufstermin. Nur geplante Handelsgebote binden nichts und sperren keinen Spieler:
    # Wählt die Engine einen Kauf, weichen sie im selben Planer-Lauf
    # (worker/intents.yield_trade_bids). Befund 03.10.2026: Sonst nahm ein geplantes
    # Handelsgebot der Engine Geld und Spieler, und sie wählte nichts mehr.
    with SessionLocal() as session:
        book = load_trade_book(
            session, now=now, squad_ids=squad_ids, league_id=lg,
            live_bids={str(b.get("player_id")): int(b.get("amount") or 0)
                       for b in live_bids if b.get("player_id")},
        )
    trade_ids = frozenset(book.positions)
    firm_trade_bids = book.firm_bids
    raw_market_by_id = dict(market_by_id)
    engine_live_bids = [b for b in live_bids if str(b.get("player_id")) not in book.live_bids]
    trade_teams = {str(b.get("player_id")): str(b.get("team_id") or "")
                   for b in [*pending_bids, *live_bids]
                   if str(b.get("player_id")) in firm_trade_bids}
    pending_bids = [b for b in pending_bids if not b.get("trade")]
    market_by_id = {pid: row for pid, row in market_by_id.items() if pid not in firm_trade_bids}
    squad_cands = [c for c in squad_cands if c.player_id not in trade_ids]
    with SessionLocal() as session:
        manual_live_ids, abandoned_ids = load_recovery_audit(
            session, lg, engine_live_bids, now, market_by_id,
        )
    market_by_id = {pid: row for pid, row in market_by_id.items() if pid not in abandoned_ids}
    commitments = merge_commitments(engine_live_bids, pending_bids, market_by_id)
    commitments = {pid: row for pid, row in commitments.items() if pid not in squad_ids}
    for pid, amount in firm_trade_bids.items():
        open_bid_exposure += amount
        if trade_teams.get(pid):
            club_counts[trade_teams[pid]] = club_counts.get(trade_teams[pid], 0) + 1

    live_pids = {str(bid.get("player_id") or "") for bid in live_bids}

    def judged_by_news(pid: str) -> bool:
        """Jev-Rolle nur für neue Ziele und eigene, noch nicht ausgeführte Engine-Vorhaben
        (Review 29.09.2026) — nie für ein Live-Gebot oder ein manuelles bzw. von Emil
        angepasstes Vorhaben. Stehende Gebote bleiben, wie sie sind. Live zählt über die
        Gebotsliste selbst, nicht über eine womöglich leere Offer-ID (Nach-Review)."""
        row = commitments.get(pid)
        return row is None or (
            row.get("status") == "planned" and not row.get("manual") and pid not in live_pids
        )

    # Jev-Rolle (Emil 29.09.2026): kicker-Artikel der letzten 96 h nach dem letzten Spiel
    # des Vereins (intel/role_judge.py). Ein Fehler kostet nur die News-Sperre, nie den
    # Plan: ohne Urteil plant die Engine wie vorher.
    role_verdicts: dict[str, RoleVerdict] = {}
    comeback_ids: set[str] = set()
    try:
        with SessionLocal() as session:
            articles = load_recent_articles(session, now - NEWS_WINDOW)
        news_targets = [
            row.player
            for pid, row in market_by_id.items()
            if judged_by_news(pid)
            and (
                not row.seller_id
                or (
                    row.player.lineup_prob == 1
                    and row.price <= int(row.player.market_value * MANAGER_MAX_PRICE_FACTOR)
                )
            )
        ]
        role_verdicts = await judge_roles(
            news_targets,
            articles,
            team_names,
            now=now,
            fixtures=fixtures,
        )
        if articles and (
            bool(getattr(get_settings(), "typesafe_api_key", None))
            or getattr(judge_returns, "__module__", "") != "kickbase_agent.intel.news_signal"
        ):
            returns = await judge_returns(
                news_targets,
                articles,
                team_names,
                now=now,
                fixtures=fixtures,
            )
            comeback_ids = set(returns.keys())
    except Exception as exc:  # noqa: BLE001 — optionaler Eingang, darf den Plan nie kippen
        log.warning("Jev-Rolle übersprungen (%s): keine News-Sperre in diesem Plan",
                    type(exc).__name__)
    # Ein eigenes, noch nicht ausgeführtes Vorhaben mit klarem „fehlt" ist keine
    # Verpflichtung mehr; der Planner räumt es in diesem Lauf (Nach-Review 29.09.2026).
    # Bliebe sein Betrag reserviert, machte ein nur über seinen Verkauf gedecktes
    # Dispo-Tauschgebot den Plan unlösbar und räumte jedes andere geplante Gebot mit ab.
    # Als neues Ziel lehnt reject_reason es weiter mit dem Artikel ab.
    dropped = {pid for pid in commitments if pid in role_verdicts and judged_by_news(pid)}
    if dropped:
        commitments = {pid: row for pid, row in commitments.items() if pid not in dropped}
        pending_bids = [b for b in pending_bids if str(b.get("player_id") or "") not in dropped]
    for bid in commitments.values():
        open_bid_exposure += int(bid.get("amount") or 0)
        open_bid_pids.add(str(bid.get("player_id") or ""))
        tid = str(bid.get("team_id") or "")
        if tid:  # ohne tid im Payload keine Club-Zuordnung — Exposure zählt trotzdem
            club_counts[tid] = club_counts.get(tid, 0) + 1

    # Interne 200er-Regel: Transfers zählen; Notreserve schützen. Bei vollem
    # (= abgeschnittenem) Feed ist die Zählung nur eine Untergrenze → Sperre
    # aktiv, Flag wandert für die Anzeige in den Plan (nie stilles "3/200").
    from kickbase_agent.trading.trade_budget import get_trade_usage, trades_blocked

    usage = await get_trade_usage(client, lg, auth.user_id)
    trades_used = usage.used
    trade_limit = DEFAULT_GUARDRAILS.league_trade_limit_per_half

    # Kein geplanter Verkauf oder Rückverkauf ist bereits Geld.
    cash_frei = budget - open_bid_exposure
    # Teamwert = Summe der Marktwerte des eigenen Kaders (Kickbase-Kaufkraftbasis).
    team_value = sum(int(p.market_value) for p in squad_players)
    plan_base = cash_frei
    # Planungsgeld (P0-3): Drei Wächter verlangen zur Frist 10.000 € (T-120-Storno,
    # T-15-Notbremse, Pfand-Einlöser), der Plan rechnete aber „Konto ≥ 0". Ein Gebot
    # über 100 % des freien Geldes würde bei T-120 storniert (Slot verbrannt) oder
    # löste bei T-15 einen Elf-Verkauf aus. Der Wächter-Puffer selbst bleibt gleich.
    cash_plan = cash_frei - KICKOFF_MIN_BALANCE
    # Tausch-Finanzierung auf Planungs-Dispo (Emil 22./23.09.2026, bis 31.10.2026 24:00
    # Berlin): Ein Kaufziel darf mehr kosten als das freie Geld, wenn derselbe Plan den
    # bedingten Sofortverkauf des Spielers koppelt, den es ersetzt. Bereits angelegte
    # Paare (wartender Verkauf `swap_dispo` + Verpflichtung auf das Ziel) rechnen hier
    # ihren Verkauf an (Marktwert × 0,90, höchstens der Kaufbetrag) — sonst verwürfe
    # der nächste Plan das eigene Gebot als ungedeckt. Der Verkaufsspieler zählt dann
    # nicht mehr für die Elf. Lücken füllt weiter nur echtes Geld (kein Dispo-Zuschlag
    # auf cash_plan); die 33-%-Grenze (`frame`) bleibt Obergrenze jedes Tauschs.
    dispo_active = overdraft_active(now, until=PLANNING_OVERDRAFT_UNTIL)
    frame = planning_overdraft(now, team_value, cash_frei, until=PLANNING_OVERDRAFT_UNTIL)
    agent_rules = load_agent_rules()
    custom_rules_active = agent_rules.is_custom_active()
    protected = protected_player_ids() | DEFAULT_GUARDRAILS.protected_player_ids
    if custom_rules_active:
        for p in squad_players:
            full_name = f"{p.first_name} {p.last_name}".strip()
            if (
                agent_rules.is_player_protected(p.id, full_name)
                or agent_rules.is_player_protected(p.id, p.last_name)
            ):
                protected = protected | {p.id}
    squad_by_id = {p.id: p for p in squad_players}
    reserve = risk_budget = pledge_dropped = 0
    deadline_pair = matchday_deadline(fixtures, now)
    deadline = deadline_pair[1] if deadline_pair else None
    updates_until_friday = remaining_updates_until_friday(
        now, sale_deadline(deadline, 11)
    )
    haircut_pct = (
        float(agent_rules.friday_buffer_haircut_pct) if custom_rules_active else 0.0
    )
    trade_held_cover = sum(
        trade_sale_proceeds(
            squad_by_id[pid].market_value,
            updates_until_friday=updates_until_friday,
            haircut_per_update_pct=haircut_pct,
        )
        for pid in trade_ids
        if pid in squad_by_id
    ) + sum(
        trade_sale_proceeds(
            raw_market_by_id[pid].player.market_value,
            updates_until_friday=updates_until_friday,
            haircut_per_update_pct=haircut_pct,
        )
        for pid in firm_trade_bids
        if pid in raw_market_by_id
    )
    li_bench_ids = (
        {a.player_id for a in squad_scout.availabilities if a.status is Status.BENCH}
        if squad_scout.availabilities
        else None
    )
    li_status = {
        a.player_id: a.status for a in [*squad_scout.availabilities, *market_scout.availabilities]
    }
    held = {
        pid: bought
        for pid, bought in held.items()
        if li_status.get(pid) not in {Status.BENCH, Status.OUT}
    }

    def _observed_24h_change(player) -> int:
        if getattr(player, "mv_change_24h_observed", False):
            return int(player.mv_change_24h or 0)
        snaps = history.get(player.id, [])
        if len(snaps) >= 2:
            latest_dt = (
                snaps[-1].recorded_at.replace(tzinfo=UTC)
                if snaps[-1].recorded_at.tzinfo is None
                else snaps[-1].recorded_at
            )
            if now - latest_dt <= timedelta(days=2):
                return int(snaps[-1].market_value) - int(snaps[-2].market_value)
        return int(getattr(player, "mv_change_24h", 0) or 0)

    def _sold_allows_bridge(sold_player) -> bool:
        if (
            li_status.get(sold_player.id) in {Status.BENCH, Status.OUT}
            or int(sold_player.lineup_prob or 5) in {4, 5}
            or (int(sold_player.lineup_prob or 5) == 3 and int(sold_player.points or 0) < 0)
        ):
            return True
        if _observed_24h_change(sold_player) >= ROLE_PROOF_KB3_MIN_MV_CHANGE_24H:
            return False
        return (
            not is_verified_starter(
                sold_player.id, sold_player.lineup_prob, li_status, sold_player.fitness_status
            )
            or _observed_24h_change(sold_player) < 0
            or int(getattr(sold_player, "mv_trend", 0) or 0) == 2
            or (budget < 0 and _observed_24h_change(sold_player) < 50_000)
        )

    swap_links: dict[str, str] = {}
    # Ein ausgeführtes (live stehendes) Dispo-Gebot bleibt mit seinem wartenden Verkauf
    # eine Einheit, bis die Auktion entschieden ist (Review 23.09.2026, Befund 1): Der
    # Worker räumt den Verkauf dann nicht mehr ab, also rechnet der Plan ihn auch nach
    # der Dispo-Frist und ohne erneute Auswahl des Ziels weiter an.
    live_ids = {str(bid.get("player_id")) for bid in live_bids}
    for link in couplings:
        target, sold = str(link["for_player_id"]), str(link["player_id"])
        if (
            (dispo_active or target in live_ids)
            and target in commitments
            and sold in squad_by_id
            and sold not in protected
            and int(squad_by_id[sold].market_value) > 0
            and target not in swap_links
            and sold not in swap_links.values()
            # Tauschbremse: Ein noch nicht abgegebenes Tauschgebot auf einen frisch Gekauften
            # verliert seine Anrechnung (Codex-Review 12.10.2026); ein live stehendes bleibt
            # mit seinem Verkauf bis zum Auktionsende eine Einheit.
            and (sold not in held or target in live_ids)
        ):
            swap_links[target] = sold
    base_deficit = max(
        0,
        -(
            cash_plan
            + trade_held_cover
            + sum(int(commitments[t]["amount"]) for t in swap_links)
        ),
    )
    swap_sale_only_credit = {
        target: min(
            swap_sale_proceeds(squad_by_id[sold].market_value),
            int(commitments[target]["amount"]) + base_deficit,
        )
        for target, sold in swap_links.items()
    }
    uncommitted_cash = max(
        0,
        cash_plan
        + trade_held_cover
        + sum(int(commitments[t]["amount"]) for t in swap_links),
    )
    swap_bridge_credit = {
        target: min(
            midweek_swap_bridge(
                now,
                deadline,
                sold_is_weak_or_falling=_sold_allows_bridge(squad_by_id[sold]),
                until=PLANNING_OVERDRAFT_UNTIL,
            ),
            max(
                0,
                int(commitments[target]["amount"])
                + base_deficit
                - swap_sale_only_credit[target]
                - uncommitted_cash,
            ),
        )
        for target, sold in swap_links.items()
    }
    swap_credit = {
        target: swap_sale_only_credit[target] + swap_bridge_credit[target]
        for target in swap_links
    }
    swap_trade_cover = (
        min(trade_held_cover, max(0, -(cash_plan + sum(swap_credit.values()))))
        if swap_links
        else 0
    )
    cash_plan += sum(swap_credit.values()) + swap_trade_cover
    candidates = {c.player_id: c for c in [*squad_cands, *market_cands]}
    artifact = load_active()
    projections: dict[str, dict] = {}
    profiteur_ids = {pr.player_id for pr in profiteurs if pr.same_position}

    def observed(r) -> bool:
        return (
            r.minutes > 0
            and r.minutes_observed is not False
            and r.is_finished is not False
            and (r.match_date is None or r.match_date <= now)
        )

    observed_matchdays = sorted(
        {(r.season, r.matchday) for rows in perf.values() for r in rows if observed(r)},
        reverse=True,
    )[:ROLE_PROOF_MATCHDAYS]

    def project(player) -> dict:
        if forecast_player is _DEFAULT_FORECAST_PLAYER:
            forecast = forecast_player(
                player,
                perf.get(player.id, []),
                as_of=now,
                artifact=artifact,
                settlement=LIVE_SETTLEMENT,
            )
        else:
            forecast = forecast_player(
                player, perf.get(player.id, []), as_of=now, artifact=artifact
            )
        forecast["market_value"] = forecast_market_value(history.get(player.id, []), as_of=now)
        if li_status.get(player.id) in {Status.BENCH, Status.OUT}:
            forecast = {
                **forecast,
                "expected_points": 0.0,
                "horizon_points": 0.0,
                "reasons": [
                    *forecast["reasons"],
                    "Aktueller LI-Bank-/Ausfallstatus: konservative Nullbasis.",
                ],
            }
        elif (
            int(forecast.get("observations") or 0) == 0
            and int(getattr(player, "lineup_prob", 5) or 5) == 1
            and li_status.get(player.id) is Status.STARTER
            and (player.id in comeback_ids or player.id in profiteur_ids)
        ):
            comeback_pts = _comeback_expected_points(
                int(getattr(player, "market_value", 0) or 0)
            )
            forecast = {
                **forecast,
                "expected_points": max(
                    float(forecast.get("expected_points") or 0.0), comeback_pts
                ),
                "horizon_points": max(
                    float(forecast.get("horizon_points") or 0.0), comeback_pts * 4.0
                ),
                "reasons": [
                    *forecast["reasons"],
                    "Comeback-/Breakout-Prior: KB-Stufe 1 + LI STARTER + News/Profiteur "
                    f"({comeback_pts:.1f} Pkt/Spieltag).",
                ],
            }
        if (
            forecast_player is _DEFAULT_FORECAST_PLAYER
            and float(forecast.get("horizon_points") or 0.0) > 0
        ):
            kb_prob = int(getattr(player, "lineup_prob", 1) or 1)
            li_st = li_status.get(player.id)
            role_factor = 1.0
            if kb_prob == 2 and li_st is not Status.STARTER:
                role_factor = 0.92
            elif kb_prob == 3:
                role_factor = 0.88 if li_st is Status.STARTER else 0.72
            if (
                int(forecast.get("observations") or 0) > 0
                and observed_matchdays
                and kb_prob >= 2
                and li_st is not Status.STARTER
                and int(getattr(player, "fitness_status", 0) or 0) == 0
            ):
                played_mds = {
                    (r.season, r.matchday) for r in perf.get(player.id, []) if observed(r)
                }
                missed_recent = sum(1 for md in observed_matchdays if md not in played_mds)
                if missed_recent > 0:
                    role_factor *= 0.85**missed_recent
            cand = candidates.get(player.id)
            raw_factor = 1.0
            if cand is not None:
                base_sig = next(
                    (s for s in getattr(cand, "signals", ()) if s.name == "base_points"),
                    None,
                )
                if base_sig is not None:
                    raw_factor = 0.90 + 0.20 * float(base_sig.score)
            combined_factor = role_factor * raw_factor
            if abs(combined_factor - 1.0) >= 0.01:
                forecast = {
                    **forecast,
                    "expected_points": float(forecast["expected_points"]) * combined_factor,
                    "horizon_points": float(forecast["horizon_points"]) * combined_factor,
                }
            mult = fixture_horizon_multiplier(str(getattr(player, "team_id", "") or ""), fixtures)
            if abs(mult - 1.0) >= 0.005:
                forecast = {
                    **forecast,
                    "horizon_points": float(forecast["horizon_points"]) * mult,
                    "reasons": [
                        *forecast["reasons"],
                        f"Spielplan-Horizontfaktor (4 Spieltage): ×{mult:.2f}.",
                    ],
                }
        projections[player.id] = forecast
        return forecast

    options = [
        PlayerOption(
            p.id,
            int(p.position),
            p.team_id,
            project(p)["horizon_points"],
            owned=True,
            name=p.last_name,
        )
        for p in squad_players
        if p.id not in trade_ids  # Handelsspieler bleiben auf der Bank (Emil 01.10.2026)
    ]
    max_club = rules["max_players_per_club"]
    baseline = optimize_team(
        options, cash_available=max(0, cash_plan), club_counts=club_counts, max_per_club=max_club
    )
    completion = baseline.filled_slots < 11
    purpose = "lineup_completion" if completion else "lineup_upgrade"
    bid_fraction = (
        completion_bid_fraction(11 - baseline.filled_slots)
        if completion
        else DEFAULT_GUARDRAILS.max_bid_pct_of_budget
    )
    single_limit = int(max(0, cash_plan) * bid_fraction)
    if custom_rules_active:
        single_limit = min(single_limit, int(float(agent_rules.max_bid_million) * 1_000_000))

    def role_proof_gap(player, *, force_full_proof: bool = False) -> str | None:
        minutes = {
            (r.season, r.matchday): r.minutes
            for r in perf.get(player.id, [])
            if observed(r)
        }
        points_by_md = {
            (r.season, r.matchday): r.points
            for r in perf.get(player.id, [])
            if observed(r)
        }
        if player.market_value < ROLE_PROOF_MIN_VALUE and not force_full_proof:
            if (
                not completion
                and observed_matchdays
                and len(observed_matchdays) >= ROLE_PROOF_MATCHDAYS
                and bool(perf.get(player.id))
                and max(minutes.get(md, 0) for md in observed_matchdays)
                < ROLE_PROOF_SUB_5M_MIN_MINUTES
            ):
                return (
                    "Unter 5 M kein Elf-Upgrade mit reinen Kurzeinsätzen "
                    "(< 45 Minuten an den letzten beiden Spieltagen)."
                )
            return None  # Füller: bestehende Fitness-/Rollensperre genügt.
        standard_two_matchdays = (
            len(observed_matchdays) >= ROLE_PROOF_MATCHDAYS
            and all(matchday in minutes for matchday in observed_matchdays)
            and min(minutes[matchday] for matchday in observed_matchdays) >= ROLE_PROOF_MIN_MINUTES
        )
        if player.lineup_prob == 3:
            if (
                standard_two_matchdays
                and li_status.get(player.id) not in {Status.BENCH, Status.OUT}
                and (
                    _observed_24h_change(player) >= ROLE_PROOF_KB3_MIN_MV_CHANGE_24H
                    or player.id in comeback_ids
                    or player.id in profiteur_ids
                )
            ):
                return None
            return "Ab 5 M nur mit Kickbase-Startelfstufe 1 oder 2 (Rollenbeleg)."
        if player.lineup_prob not in {1, 2}:
            return "Ab 5 M nur mit Kickbase-Startelfstufe 1 oder 2 (Rollenbeleg)."
        # Pfad 1 (Standard): >= 60 Minuten an beiden jüngsten beobachteten Spieltagen.
        if standard_two_matchdays:
            return None
        # Pfad 2 (Jüngster Stammbeleg nach Sperre/Rotation/Pause): fehlt einer der beiden
        # Spieltage (0 Minuten), genügt >= 70 min am anderen Spieltag bei gleichzeitigem
        # KB-Stufe-1- und LI-STARTER-Konsens (kein Kurzeinsatz-Joker wie 31' + 75').
        if (
            len(observed_matchdays) >= ROLE_PROOF_MATCHDAYS
            and sum(1 for md in observed_matchdays if md in minutes) == 1
            and player.lineup_prob == 1
            and li_status.get(player.id) is Status.STARTER
            and max(minutes.get(md, 0) for md in observed_matchdays)
            >= ROLE_PROOF_RECENT_MIN_MINUTES
        ):
            return None
        # Pfad 3 (Verifiziertes Comeback / Breakout): KB-Stufe 1 + LI STARTER + positives
        # Rückkehr-Signal (News-Radar) oder LigaInsider-Profiteur.
        if (
            player.lineup_prob == 1
            and li_status.get(player.id) is Status.STARTER
            and (player.id in comeback_ids or player.id in profiteur_ids)
        ):
            return None
        # Pfad 4 (Aktueller Spieltags-Breakout): >= 75 Minuten UND >= 100 Punkte am jüngsten
        # beobachteten Spieltag bei KB-Stufe 1/2 und keinem LI-Bank-/Ausfallstatus.
        if (
            observed_matchdays
            and minutes.get(observed_matchdays[0], 0) >= ROLE_PROOF_BREAKOUT_MIN_MINUTES
            and points_by_md.get(observed_matchdays[0], 0) >= ROLE_PROOF_BREAKOUT_MIN_POINTS
            and li_status.get(player.id) not in {Status.BENCH, Status.OUT}
        ):
            return None
        if len(observed_matchdays) < ROLE_PROOF_MATCHDAYS or any(
            matchday not in minutes for matchday in observed_matchdays
        ):
            # Fehlende Minuten sind keine beobachteten Nullen: kein Auto-Kauf,
            # Entscheidung bei Emil (App-Weg bleibt).
            return "Rollenbeleg fehlt: Minuten der letzten beiden Spieltage nicht beobachtet."
        return "Rolle nicht belegt: unter 60 Minuten in einem der letzten beiden Spieltage."

    premium_limit = (
        DEFAULT_GUARDRAILS.max_bid_premium_need
        if completion
        else DEFAULT_GUARDRAILS.max_bid_premium
    )
    if custom_rules_active:
        user_overpay_factor = 1.0 + max(0.0, float(agent_rules.max_overpay_pct)) / 100.0
        premium_limit = min(premium_limit, user_overpay_factor)
    notes: list[str] = []
    if held:
        squad_names = {p.id: p.last_name for p in squad_players}
        notes.append("Tauschbremse: " + ", ".join(
            f"{squad_names.get(pid, pid)} (gekauft {bought.astimezone(BERLIN):%d.%m.}, "
            f"Tausch frühestens {(bought + SWAP_HOLD_AFTER_PURCHASE).astimezone(BERLIN):%d.%m.})"
            for pid, bought in sorted(held.items(), key=lambda item: item[1])
        ) + " — frisch gekaufte Spieler gehen nicht per Tausch.")
    if 0 <= cash_frei < KICKOFF_MIN_BALANCE:
        # Das Konto ist im Plus; nur der Puffer lässt keinen Kauf zu. Ohne diesen Satz
        # stünde im Bericht allein „fehlen … € Deckung" und läse sich wie ein Minus.
        notes.append(
            f"Freies Geld {cash_frei:,} € liegt unter dem Anpfiff-Puffer "
            f"{KICKOFF_MIN_BALANCE:,} €: Konto im Plus, aber kein Kauf planbar."
        )
    if trade_ids or book.bids:
        sale_by = sale_deadline(deadline, baseline.filled_slots)
        until = (f"; Verkauf spätestens {sale_by.astimezone(BERLIN):%d.%m. %H:%M}"
                 if sale_by is not None else "")
        notes.append(
            f"Handel (Autopilot): {len(trade_ids)} Handelsspieler auf der Bank, feste "
            f"Handelsgebote {sum(firm_trade_bids.values()):,} € gebunden{until} — die Engine plant "
            "mit dem Rest; geplante Handelsgebote weichen einem Kauf der Engine.".replace(",", ".")
        )
    reviews: dict[str, dict] = {}
    prices: dict[str, int] = {}
    caps: dict[str, int] = {}

    def reject_reason(row) -> str | None:
        player = row.player
        # Erwerbsrealismus (P0-1): 359 von 528 Zielen waren Manager-Listings, seit
        # 13.09. nahm kein Verkäufer an (0 von 8) — der Plan meldete trotzdem
        # „8 → 11" und band 7,64 M von 11,65 M. Ein laufendes Manager-Gebot bleibt
        # über open_bid_exposure als Geld reserviert, zählt aber keinen Platz —
        # außer als bereits gekoppeltes Tauschgebot (swap_links).
        is_committed_deficit_filler = (
            player.id in swap_links
            and base_deficit > 0
            and deadline is not None
            and (deadline - now) <= timedelta(hours=24)
            and max(int(player.market_value), int(row.price or 0) if row.seller_id else 0)
            <= DEFICIT_FILLER_MAX_PRICE_EUR
        )
        if row.seller_id and player.id not in swap_links:
            return MANAGER_LISTING_REJECTION
        if custom_rules_active and not agent_rules.allow_bids and player.id not in commitments:
            return "Gebote laut persönlichem KI-Regelwerk deaktiviert."
        if player.id in blocked_target_ids and player.id not in commitments:
            return "Ziel heute gesperrt (Veto oder Fehlschlag); kein neuer Platz darauf geplant."
        if (
            (not is_committed_deficit_filler and player.fitness_status != 0)
            or player.fitness_status is None
            or player.lineup_prob not in {1, 2, 3, 4, 5}
        ):
            return "Fitness oder bekannte Einsatzrolle fehlt."
        if not is_committed_deficit_filler and (
            li_status.get(player.id) is Status.OUT or candidates[player.id].injury_severity
        ):
            return "Aktueller Ausfall verhindert den Kauf."
        if (
            player.lineup_prob in {4, 5} or li_status.get(player.id) is Status.BENCH
        ):
            if not completion and not is_committed_deficit_filler:
                return "Bankspieler verbessert keine bereits besetzbare Elf."
            if not emergency_bench_fallback_allowed(now, deadline):
                return "Bankrolle nur als Notbesetzung in den letzten 96 Stunden vor der Frist."
        if deadline is None:
            return "Aufstellungsfrist unbekannt; kein neuer autonomer Kauf."
        if not row.seller_id and not (
            now < row.expires_at and row.expires_at + AUCTION_SETTLEMENT_BUFFER <= deadline
        ):
            return "Auktion endet nicht vor der nächsten Aufstellungsfrist."
        if player.market_value <= 0:
            return "Positiver aktueller Marktwert fehlt."
        # Jev-Rolle: ein klares „fehlt" sperrt. Vor der Gebots-Ausnahme, damit ein eigenes,
        # noch nicht ausgeführtes Vorhaben geräumt wird; ein Live-Gebot trägt nie ein
        # Urteil (zweite Sicherung neben der Auswahl oben).
        news = role_verdicts.get(player.id) if judged_by_news(player.id) else None
        if news is not None:
            return role_rejection(news)
        # Der Rollenbeleg entscheidet über NEUE Ziele. Ein stehendes Gebot (auch aus
        # der App) ist Wirklichkeit: verlöre es hier seinen Platz, plante die Engine
        # denselben Platz ein zweites Mal (Review-Befund 21.09.2026).
        if player.id in commitments:
            return None
        return role_proof_gap(player)

    for pid, row in sorted(market_by_id.items()):
        player = row.player
        projection = project(player)
        rejection = reject_reason(row)
        minimum = max(player.market_value, manager_prices.get(pid, 0))
        target_factor = (
            max(premium_limit, DEFAULT_GUARDRAILS.max_bid_premium_upgrade)
            if not row.seller_id
            and (player.mv_change_24h_observed or pid in comeback_ids or pid in profiteur_ids)
            else premium_limit
        )
        maximum = min(single_limit, int(player.market_value * target_factor))
        if pid in commitments:
            amount = int(commitments[pid]["amount"])
            prices[pid] = amount
            prior = commitments[pid].get("purchase_review") or {}
            prior_cap = prior.get("max_bid_eur")
            # Ein gekoppeltes Tauschgebot darf das Konto um seinen Verkauf übersteigen.
            cap = min(
                int(player.market_value * target_factor),
                budget + (trade_held_cover if pid in swap_links else 0) + swap_credit.get(pid, 0),
            )
            if type(prior_cap) is int and prior_cap > 0:
                cap = min(cap, prior_cap)
            caps[pid] = cap
            if rejection is None:
                options.append(
                    PlayerOption(
                        pid,
                        int(player.position),
                        player.team_id,
                        max(projection["horizon_points"], 1.0 if pid in swap_links else 0.0),
                        committed=True,
                        name=player.last_name,
                    )
                )
        elif rejection is None and minimum <= maximum:
            prices[pid], caps[pid] = minimum, maximum
            options.append(
                PlayerOption(
                    pid,
                    int(player.position),
                    player.team_id,
                    projection["horizon_points"],
                    minimum,
                    name=player.last_name,
                )
            )
        elif rejection is None:
            rejection = "Mindestpreis über dem verfügbaren technischen Einzelrahmen."
        reviews[pid] = {
            "selected": False,
            "eligible": rejection is None,
            "player_id": pid,
            "reasons": [rejection] if rejection else [],
            "market_value_eur": player.market_value,
            "purpose": purpose,
            # P0-2: fuer abgelehnte Kandidaten fehlte die Listingart ganz — das
            # Marktangebot war nicht nach Kickbase- und Manager-Listing trennbar.
            "listing_type": "manager" if row.seller_id else "kickbase",
            "seller_id": row.seller_id,
        }
    if trades_blocked(trades_used, trade_limit, count_saturated=usage.count_saturated):
        options = [o for o in options if o.owned or o.committed]
        notes.append("Transferreserve oder unvollständige Zählung sperrt neue Käufe.")
    if any(
        not row.get("team_id") or int(row.get("amount") or 0) <= 0 for row in commitments.values()
    ):
        options = [o for o in options if o.owned or o.committed]
        notes.append("Offene Verpflichtung ohne verlässlichen Betrag/Verein; neue Käufe gesperrt.")
    squad_limit = getattr(get_settings(), "kickbase_squad_limit", 18)
    max_new = max(
        0,
        min(
            DEFAULT_GUARDRAILS.daily_buy_cap - len(commitments),
            squad_limit - len(squad_ids) - len(commitments),
        ),
    )
    solve_cache: dict[tuple, TeamPlan] = {}
    solver_seconds = 0.0

    def solve(
        *,
        target: str | None = None,
        amount: int | None = None,
        exclude: frozenset[str] = frozenset(),
    ) -> TeamPlan:
        nonlocal solver_seconds
        key = (target, amount, tuple(sorted(exclude)))
        if key not in solve_cache:
            if solver_seconds >= 20:
                return TeamPlan(
                    baseline.formation,
                    baseline.filled_slots,
                    baseline.horizon_points,
                    0,
                    cash_plan,
                    owned_filled_slots=baseline.filled_slots,
                    solver_status="time_budget_exhausted",
                    optimal=False,
                    notes=("Gesamtes Rechenzeitbudget ausgeschöpft; keine neue Kaufentscheidung.",),
                )
            trial = options
            cash = cash_plan
            clubs = club_counts
            # Gekoppelte Verkaufsspieler fehlen nur, solange ihr Tauschgebot zählt;
            # bei einem Konto-heilenden Downgrade-Tausch müsste der teure Spieler ohne
            # den Tausch ebenfalls zur Defizitdeckung weichen.
            coupled_out = frozenset(
                sold
                for target_id, sold in swap_links.items()
                if target_id not in exclude
                or (
                    base_deficit > 0
                    and swap_sale_proceeds(squad_by_id[sold].market_value)
                    - int(commitments[target_id]["amount"])
                    >= DEFICIT_HEAL_MIN_RELIEF_EUR
                    and projections.get(target_id, {}).get("horizon_points", 0.0)
                    <= projections.get(sold, {}).get("horizon_points", 0.0)
                )
            )
            released = len(exclude & commitments.keys()) + int(target in commitments)
            limit = max(
                0,
                min(
                    DEFAULT_GUARDRAILS.daily_buy_cap - len(commitments) + released,
                    squad_limit - len(squad_ids) - len(commitments) + released,
                ),
            )
            if target is not None:
                trial = [
                    replace(o, cost_eur=int(amount or 0), committed=False)
                    if o.player_id == target
                    else o
                    for o in options
                ]
                if target in commitments:
                    cash += int(commitments[target]["amount"])
                    if target in swap_credit:
                        sold_p = squad_by_id[swap_links[target]]
                        bridge = midweek_swap_bridge(
                            now,
                            deadline,
                            sold_is_weak_or_falling=_sold_allows_bridge(sold_p),
                            until=PLANNING_OVERDRAFT_UNTIL,
                        )
                        cash += (
                            min(
                                swap_sale_proceeds(sold_p.market_value) + bridge,
                                int(amount or 0) + base_deficit,
                            )
                            - swap_credit[target]
                        )
                    clubs = dict(club_counts)
                    tid = str(commitments[target].get("team_id") or "")
                    clubs[tid] = max(0, clubs.get(tid, 0) - 1)
            if exclude & commitments.keys():
                clubs = dict(clubs)
                for pid in exclude & commitments.keys():
                    # Verlorenes Tauschgebot: auch sein Verkauf entfällt.
                    cash += int(commitments[pid]["amount"]) - swap_credit.get(pid, 0)
                    tid = str(commitments[pid].get("team_id") or "")
                    clubs[tid] = max(0, clubs.get(tid, 0) - 1)
                if (
                    target is None
                    and base_deficit > 0
                    and cash < 0
                    and (exclude & swap_links.keys())
                ):
                    cash = 0
                    limit = 0
            if target is None and cash < 0 and cash + trade_held_cover >= 0:
                cash = 0
                limit = 0
            started = monotonic()
            solve_cache[key] = optimize_team(
                trial,
                cash_available=cash,
                club_counts=clubs,
                max_per_club=max_club,
                max_new_purchases=limit,
                required_ids=frozenset({target}) if target else frozenset(),
                excluded_ids=exclude | coupled_out,
                time_limit=min(2.0, 20 - solver_seconds),
            )
            solver_seconds += monotonic() - started
        return solve_cache[key]

    decisions: dict[str, BidDecision] = {}
    bid_model_versions: dict[str, str] = {}
    # Gebotsregel V3 (Emil 25.09.2026) je neuem Kickbase-Ziel; stehende Gebote behalten
    # ihren Betrag und bekommen keinen Eintrag.
    premiums: dict[str, BidPremium] = {}
    refreshed: set[str] = set()
    detail_failed: set[str] = set()
    # Details der besten gemeinsamen Auswahl prüfen, bei Ausfall neu optimieren.
    attempts = 0
    while True:
        result = solve()
        targets = set(result.purchase_ids) | (set(result.lineup_ids) & commitments.keys())
        todo = sorted(targets - refreshed)
        if not todo or attempts >= 16:
            break
        pid = todo[0]
        attempts += 1
        refreshed.add(pid)
        try:
            fresh = await client.get_player(lg, pid)
            row = market_by_id[pid].model_copy(update={"player": fresh})
            if fresh.id != pid or reject_reason(row) is not None:
                raise ValueError("Rolle oder Identität nicht frisch bestätigt")
        except Exception:
            detail_failed.add(pid)
            options = [o for o in options if o.player_id != pid]
            reviews[pid]["reasons"] = ["Aktueller Detailabruf bestätigt den Kauf nicht."]
            reviews[pid]["eligible"] = False
            solve_cache.clear()
            continue
        market_by_id[pid] = row
        if pid in commitments:
            previous_team = str(commitments[pid].get("team_id") or "")
            if previous_team != fresh.team_id:
                club_counts[previous_team] = max(0, club_counts.get(previous_team, 0) - 1)
                club_counts[fresh.team_id] = club_counts.get(fresh.team_id, 0) + 1
                commitments[pid]["team_id"] = fresh.team_id
            commitments[pid]["position"] = int(fresh.position)
        candidates[pid] = candidates[pid].model_copy(
            update={
                "team_id": fresh.team_id,
                "position": int(fresh.position),
                "current_market_value": fresh.market_value,
            }
        )
        projection = project(fresh)
        current_price = (
            prices[pid]
            if pid in commitments
            else max(fresh.market_value, manager_prices.get(pid, 0))
        )
        if pid in commitments and current_price < max(
            fresh.market_value, manager_prices.get(pid, 0)
        ):
            options = [o for o in options if o.player_id != pid]
            reviews[pid]["reasons"] = [
                "Bestehender Betrag unter aktuellem Mindestanker; neu planen."
            ]
            reviews[pid]["eligible"] = False
            solve_cache.clear()
            continue
        prices[pid] = current_price
        fresh_factor = (
            max(premium_limit, DEFAULT_GUARDRAILS.max_bid_premium_upgrade)
            if not row.seller_id
            and (fresh.mv_change_24h_observed or pid in comeback_ids or pid in profiteur_ids)
            else premium_limit
        )
        if pid in commitments:
            prior = commitments[pid].get("purchase_review") or {}
            prior_cap = prior.get("max_bid_eur")
            cap = min(
                int(fresh.market_value * fresh_factor),
                budget + (trade_held_cover if pid in swap_links else 0) + swap_credit.get(pid, 0),
            )
            if type(prior_cap) is int and prior_cap > 0:
                cap = min(cap, prior_cap)
            caps[pid] = cap
        else:
            caps[pid] = min(single_limit, int(fresh.market_value * fresh_factor))
        options = [
            replace(
                o,
                horizon_points=max(
                    projection["horizon_points"], 1.0 if pid in swap_links else 0.0
                ),
                cost_eur=0 if o.committed else current_price,
                position=int(fresh.position),
                team_id=fresh.team_id,
            )
            if o.player_id == pid
            else o
            for o in options
        ]
        solve_cache.clear()
        loss_plan = solve(exclude=frozenset({pid}))
        if not loss_plan.optimal:
            options = [o for o in options if o.player_id != pid]
            reviews[pid]["reasons"] = [
                "Beste Ersatzmannschaft innerhalb Rechenzeit nicht bewiesen."
            ]
            reviews[pid]["eligible"] = False
            solve_cache.clear()
            continue
        listing_type = "manager" if row.seller_id else "kickbase"
        competition = load_competition(lg, listing_type)
        bid_model_versions[pid] = (
            str(competition.get("version") or "unknown") if competition else "unknown"
        )

        def win_value(amount: int, target=pid) -> TeamValue | None:
            scenario = solve(target=target, amount=amount)
            if not scenario.optimal or scenario.remaining_cash < 0:
                return None
            return TeamValue(
                scenario.filled_slots, scenario.horizon_points, scenario.remaining_cash
            )

        def probability(amount: int, player=fresh, model=competition, kind=listing_type):
            return (
                predict_probability(
                    model,
                    amount=amount,
                    market_value=player.market_value,
                    position=int(player.position),
                    league_id=lg,
                    listing_type=kind,
                    as_of=now,
                )
                if model is not None
                else None
            )

        def loss_value(lost: TeamPlan = loss_plan) -> TeamValue:
            return TeamValue(lost.filled_slots, lost.horizon_points, lost.remaining_cash)

        premium_kwargs = (
            {
                "conviction_bonus": CONVICTION_BONUS,
                "cap": CONVICTION_CAP,
            }
            if (
                int(getattr(fresh, "lineup_prob", 5) or 5) == 1
                and li_status.get(pid) is Status.STARTER
                and (pid in comeback_ids or pid in profiteur_ids)
            )
            else (
                {"cap": KB3_PREMIUM_CAP}
                if int(getattr(fresh, "lineup_prob", 5) or 5) >= 3
                and (not completion or int(fresh.market_value) >= ROLE_PROOF_MIN_VALUE)
                else {}
            )
        )
        slot_gain_horizon = (
            float(projection["horizon_points"])
            if loss_plan.filled_slots < result.filled_slots
            else max(0.0, float(result.horizon_points - loss_plan.horizon_points))
        )
        starter_gain_pts = slot_gain_horizon / 4.0
        rival_scarcity = estimate_rival_scarcity(
            alive,
            position=int(fresh.position),
            rival_squads=getattr(client, "rival_squads", None),
        )
        premium = (
            bid_premium(
                fresh.market_value,
                fresh.mv_change_24h,
                fresh.mv_change_24h_observed,
                rival_scarcity_count=rival_scarcity,
                starter_gain_points=starter_gain_pts,
                **premium_kwargs,
            )
            if listing_type == "kickbase" and pid not in commitments
            else None
        )
        record_premium(premiums, pid, premium)
        decision = choose_bid(
            market_value=fresh.market_value,
            minimum_bid=current_price,
            maximum_bid=caps[pid],
            evaluate_win=win_value,
            evaluate_loss=loss_value,
            probability=probability if competition is not None else None,
            target_amount=premium.amount if premium is not None else None,
            target_reason=premium.describe() if premium is not None else None,
        )
        decisions[pid] = decision
        if pid in commitments and decision.amount is not None:
            decision = replace(
                decision,
                amount=current_price,
                scenarios=[
                    {**scenario, "selected": scenario["amount"] == current_price}
                    for scenario in decision.scenarios
                ],
                reason=_standing_bid_reason(commitments[pid], decision.reason),
            )
            decisions[pid] = decision
        if decision.amount is None:
            options = [o for o in options if o.player_id != pid]
            reviews[pid]["reasons"] = [decision.reason]
            reviews[pid]["eligible"] = False
        elif pid not in commitments:
            prices[pid] = decision.amount
            options = [
                replace(o, cost_eur=decision.amount) if o.player_id == pid else o for o in options
            ]
        solve_cache.clear()
    alternative_options = list(options)
    options = [
        o
        for o in options
        if o.owned
        or o.committed
        or o.player_id in decisions
        and decisions[o.player_id].amount is not None
    ]
    selected_ids: set[str] = set()
    seller_listing_counts: dict[str, int] = {}
    for mp in alive:
        if mp.seller_id:
            seller_listing_counts[mp.seller_id] = seller_listing_counts.get(mp.seller_id, 0) + 1

    def manager_reject_reason(row, *, ignore_open_for: str | None = None) -> str | None:
        player = row.player
        base = MANAGER_LISTING_REJECTION[:-1]
        if player.id in live_ids:
            return MANAGER_LISTING_REJECTION
        if player.id in blocked_target_ids and player.id not in commitments:
            return "Ziel heute gesperrt (Veto oder Fehlschlag); kein neuer Platz darauf geplant."
        if completion:
            return f"{base} (nur bei voll besetzter 11er-Elf zulässig)."
        if deadline is None or (deadline - now) < MANAGER_MIN_DEADLINE_LEAD:
            return f"{base} (unter {BID_FREEZE_MINUTES} Minuten bis zur Aufstellungsfrist)."
        if (now - row.expires_at) > MANAGER_MAX_LISTING_AGE:
            return f"{base} (Listing älter als 36 Stunden)."
        other_mgr_bids = (
            {
                str(b.get("player_id") or "")
                for b in live_bids
                if str(b.get("player_id") or "") != (ignore_open_for or "")
                and (
                    bool(b.get("seller"))
                    or b.get("listing_type") == "manager"
                    or (
                        str(b.get("player_id") or "") in market_by_id
                        and bool(market_by_id[str(b.get("player_id") or "")].seller_id)
                    )
                )
            }
            | {
                c_pid
                for c_pid, c_row in commitments.items()
                if c_pid != (ignore_open_for or "")
                and (
                    c_row.get("listing_type") == "manager"
                    or (c_pid in market_by_id and bool(market_by_id[c_pid].seller_id))
                )
            }
            | {
                s_pid
                for s_pid in selected_ids
                if s_pid != (ignore_open_for or "")
                and s_pid in market_by_id
                and bool(market_by_id[s_pid].seller_id)
            }
        )
        if len(other_mgr_bids) >= MANAGER_MAX_OPEN_BIDS:
            return f"{base} (bereits ein offenes Manager-Gebot aktiv)."
        if seller_listing_counts.get(row.seller_id, 0) > MANAGER_MAX_SELLER_SHOWCASE:
            return (
                f"{base} (Verkäufer listet > {MANAGER_MAX_SELLER_SHOWCASE} Spieler "
                "als Schaufenster)."
            )
        if player.market_value <= 0:
            return "Positiver aktueller Marktwert fehlt."
        if player.fitness_status != 0 or player.lineup_prob != 1:
            return f"{base} (nur fitte KB-Stufe-1-Stammspieler)."
        if (
            li_status.get(player.id) is not Status.STARTER
            or candidates[player.id].injury_severity
        ):
            return f"{base} (LigaInsider-Starter-Beleg fehlt)."
        news = role_verdicts.get(player.id) if judged_by_news(player.id) else None
        if news is not None:
            return role_rejection(news)
        if player.id not in commitments:
            gap = role_proof_gap(player, force_full_proof=True)
            if gap is not None:
                return f"{base} ({gap})"
        if (
            _observed_24h_change(player) < 0
            or int(getattr(player, "market_value_trend", 0) or 0) in {-1, 2}
        ):
            return f"{base} (Marktwert fällt — Adverse-Selection-Schutz)."
        asking = max(int(player.market_value), int(row.price or 0))
        if asking > int(player.market_value * MANAGER_MAX_PRICE_FACTOR):
            return f"{base} (Forderung über 101,5 % Marktwert)."
        return None

    async def _plan_overdraft_swap() -> tuple[dict[str, str], dict[str, int], TeamPlan | None]:
        """Höchstens ein neuer Tausch auf Planungs-Dispo je Lauf (Emil 23.09.2026).

        Ein besserer Starter darf mehr kosten als das nach dem Paket freie Planungsgeld,
        wenn der Sofortverkauf des schwächeren eigenen Spielers gekoppelt wird:
        Preis − Marktwert_verkauft × 0,90 ≤ freies Planungsgeld (netto echtes Geld,
        Puffer inklusive) und Preis ≤ Kickbase-Kaufkraft (33 %, `frame`). Nicht-Starter
        (KB 3–5 / LI-Bank) dürfen auch positionsübergreifend über Formationswechsel
        ersetzt werden; steigende fitte KB-1-Stammspieler bleiben geschützt, solange
        Nicht-Starter im Kader stehen.
        """
        nonlocal attempts
        empty: tuple[dict[str, str], dict[str, int], TeamPlan | None] = ({}, {}, None)
        replaceable_swap: tuple[str, str] | None = None
        if len(couplings) == 1 and len(swap_links) == 1:
            old_target, old_sold = next(iter(swap_links.items()))
            old_comm = commitments.get(old_target) or {}
            if (
                old_target not in live_ids
                and old_target in selected_ids
                and old_comm.get("status") == "planned"
                and not old_comm.get("manual")
            ):
                replaceable_swap = (old_target, old_sold)
        old_amount = int(commitments[replaceable_swap[0]]["amount"]) if replaceable_swap else 0
        old_credit = swap_credit.get(replaceable_swap[0], 0) if replaceable_swap else 0
        new_ids = {pid for pid in selected_ids if pid not in commitments}
        spent = sum(prices[pid] for pid in new_ids)
        remaining = (
            cash_plan - swap_trade_cover + trade_held_cover + old_amount - old_credit - spent
        )
        raw_cash_frei = cash_frei + old_amount
        raw_frame = (
            planning_overdraft(now, team_value, raw_cash_frei, until=PLANNING_OVERDRAFT_UNTIL)
            if replaceable_swap is not None
            else frame
        )
        room_33 = raw_cash_frei - spent + raw_frame
        base_plan = result if result.optimal else baseline
        no_swap_plan = (
            solve(exclude=frozenset({replaceable_swap[0]}))
            if replaceable_swap is not None
            else base_plan
        )
        if not no_swap_plan.optimal:
            no_swap_plan = baseline
        coupled_out = (
            frozenset() if replaceable_swap is not None else frozenset(swap_links.values())
        )
        lineup_for_sales = set(no_swap_plan.lineup_ids) | (
            {replaceable_swap[1]} if replaceable_swap is not None else set()
        )
        faller_ids = {c.player_id for c in squad_cands}
        max_sale_proceeds = max(
            (
                swap_sale_proceeds(p.market_value)
                for p in squad_players
                if p.id in lineup_for_sales
                and p.id not in protected
                and p.id not in coupled_out
                and p.id not in blocked_sell_ids
                and p.id in faller_ids
                and int(p.market_value) > 0
                and p.id not in held
            ),
            default=0,
        )
        deficit_healable = (
            remaining < 0
            and (not commitments or replaceable_swap is not None)
            and base_plan.optimal
            and remaining + max_sale_proceeds > 0
        )
        if (
            not dispo_active
            or (
                custom_rules_active
                and (not agent_rules.allow_bids or agent_rules.allow_sells == "none")
            )
            # Höchstens EIN offenes Dispo-Paar (Review 23.09.2026, Befund 3): Solange
            # ein Tausch-Verkauf wartet (Gebot live oder schon gewonnen), entsteht kein
            # zweites Paar; ein noch ungebotenes (`planned`) Paar darf nur durch ein
            # um >= SWAP_UPGRADE_MIN_GAIN_POINTS stärkeres Paar ersetzt werden.
            or (couplings and replaceable_swap is None)
            or raw_frame <= 0
            or deadline is None
            or (remaining < 0 and not deficit_healable)
            or (remaining >= 0 and not result.optimal)
            or len(new_ids) >= max_new + (1 if replaceable_swap is not None else 0)
            # Ein Tausch sind zwei Transfers: Kauf und gekoppelter Sofortverkauf.
            or trades_blocked(trades_used + 2, trade_limit, count_saturated=usage.count_saturated)
            or any(
                not row.get("team_id") or int(row.get("amount") or 0) <= 0
                for row in commitments.values()
            )
        ):
            return empty
        latest_end = deadline - timedelta(minutes=BID_FREEZE_MINUTES)
        sale_candidates = [
            p for p in squad_players
            if p.id in lineup_for_sales and p.id not in protected and p.id not in coupled_out
            and p.id not in blocked_sell_ids
            and p.id in faller_ids and int(p.market_value) > 0 and p.id not in held
        ]
        non_starter_sales = [
            p for p in sale_candidates
            if not is_verified_starter(p.id, p.lineup_prob, li_status, p.fitness_status)
        ]
        if custom_rules_active and agent_rules.allow_sells == "bench_only":
            sale_candidates = list(non_starter_sales)
        sale_side = (
            [
                p for p in sale_candidates
                if not (
                    is_verified_starter(p.id, p.lineup_prob, li_status, p.fitness_status)
                    and (
                        int(getattr(p, "mv_trend", 0) or 0) == 1
                        or _observed_24h_change(p) > 0
                    )
                )
            ]
            if non_starter_sales
            else sale_candidates
        )
        def _favorable_next_two_games(player_obj) -> bool:
            return (
                fixture_horizon_multiplier(
                    str(getattr(player_obj, "team_id", "") or ""),
                    fixtures,
                    horizon=2,
                )
                >= STARTER_PROTECT_MIN_FIXTURE_MULT_2
            )

        starter_pos_counts = verified_deployable_counts(
            squad_players, li_status, set(), coupled_out
        )
        pos_min = {1: 1, 2: 3, 3: 2, 4: 1}

        def _sale_tier(sold_p) -> int:
            if (
                li_status.get(sold_p.id) in {Status.BENCH, Status.OUT}
                or int(sold_p.lineup_prob or 5) in {4, 5}
                or (int(sold_p.lineup_prob or 5) == 3 and int(sold_p.points or 0) < 0)
            ):
                return 0
            if not is_verified_starter(
                sold_p.id, sold_p.lineup_prob, li_status, sold_p.fitness_status
            ):
                return 1
            if _observed_24h_change(sold_p) <= -100_000:
                return 1
            pos = int(sold_p.position)
            sole_pos = starter_pos_counts.get(pos, 0) <= pos_min.get(pos, 1)
            rising = (
                _observed_24h_change(sold_p) >= ROLE_PROOF_KB3_MIN_MV_CHANGE_24H
                or int(getattr(sold_p, "mv_trend", 0) or 0) == 1
            )
            if (sole_pos or rising) and _favorable_next_two_games(sold_p):
                return 3
            return 2

        non_surging = [
            p for p in sale_side
            if _sale_tier(p) == 0
            or not (
                _observed_24h_change(p) >= ROLE_PROOF_KB3_MIN_MV_CHANGE_24H
                and _favorable_next_two_games(p)
            )
        ]
        if non_surging:
            sale_side = non_surging

        def _swap_target_role_ok(target_p) -> bool:
            li_is_starter = li_status.get(target_p.id) is Status.STARTER
            mv24 = _observed_24h_change(target_p)
            if mv24 < (-100_000 if li_is_starter else 0) or (
                not li_is_starter and int(getattr(target_p, "mv_trend", 0) or 0) == 2
            ):
                return False
            if target_p.lineup_prob in {1, 2}:
                return True
            if target_p.lineup_prob == 3:
                return (
                    mv24 >= ROLE_PROOF_KB3_MIN_MV_CHANGE_24H
                    or (li_is_starter and role_proof_gap(target_p) is None)
                    or target_p.id in comeback_ids
                    or target_p.id in profiteur_ids
                )
            return False

        def _pair_bridge(sold_p, target_mv: int) -> int:
            proceeds_p = swap_sale_proceeds(sold_p.market_value)
            if remaining + proceeds_p >= target_mv:
                return 0
            if remaining < 0:
                if _sale_tier(sold_p) == 0:
                    return midweek_swap_bridge(
                        now,
                        deadline,
                        sold_is_weak_or_falling=_sold_allows_bridge(sold_p),
                        until=PLANNING_OVERDRAFT_UNTIL,
                    )
                if (
                    proceeds_p - target_mv >= DEFICIT_HEAL_MIN_RELIEF_EUR
                    and remaining + proceeds_p + DEFICIT_HEAL_MAX_RESIDUAL_EUR >= target_mv
                ):
                    return min(
                        DEFICIT_HEAL_MAX_RESIDUAL_EUR,
                        midweek_swap_bridge(
                            now,
                            deadline,
                            sold_is_weak_or_falling=_sold_allows_bridge(sold_p),
                            until=PLANNING_OVERDRAFT_UNTIL,
                        ),
                    )
                return 0
            return midweek_swap_bridge(
                now,
                deadline,
                sold_is_weak_or_falling=_sold_allows_bridge(sold_p),
                until=PLANNING_OVERDRAFT_UNTIL,
            )

        def is_deficit_filler(row) -> bool:
            if (
                remaining >= 0
                or deadline is None
                or (deadline - now) > timedelta(hours=24)
                or (deadline - now) < timedelta(minutes=BID_FREEZE_MINUTES)
            ):
                return False
            p = row.player
            if (
                p.id in live_ids
                or (p.id in blocked_target_ids and p.id not in commitments)
                or p.fitness_status is None
                or p.lineup_prob not in {1, 2, 3, 4, 5}
                or int(p.position) not in {2, 3, 4}
                or p.market_value <= 0
            ):
                return False
            ask = max(int(p.market_value), int(row.price or 0) if row.seller_id else 0)
            if ask > DEFICIT_FILLER_MAX_PRICE_EUR:
                return False
            if row.seller_id:
                if (
                    (now - row.expires_at) > MANAGER_MAX_LISTING_AGE
                    or ask > int(p.market_value * MANAGER_MAX_PRICE_FACTOR)
                    or seller_listing_counts.get(row.seller_id, 0) > MANAGER_MAX_SELLER_SHOWCASE
                ):
                    return False
                return True
            return bool(
                now < row.expires_at and row.expires_at + AUCTION_SETTLEMENT_BUFFER <= latest_end
            )

        pairs = []
        for pid, row in sorted(market_by_id.items()):
            player = row.player
            if pid in commitments or pid in selected_ids or pid in detail_failed:
                continue
            price = max(int(player.market_value), int(row.price or 0) if row.seller_id else 0, 1)
            deficit_filler = False
            if row.seller_id:
                if new_ids or price <= remaining:
                    continue
                mgr_ok = manager_reject_reason(row, ignore_open_for=pid) is None
                deficit_filler = not mgr_ok and is_deficit_filler(row)
                if not mgr_ok and not deficit_filler:
                    continue
            else:
                if row.expires_at + AUCTION_SETTLEMENT_BUFFER > latest_end:
                    continue
                kb_ok = (
                    _swap_target_role_ok(player)
                    and li_status.get(pid) is not Status.BENCH
                    and reject_reason(row) is None
                )
                deficit_filler = not kb_ok and is_deficit_filler(row)
                if not kb_ok and not deficit_filler:
                    continue
            has_preferred_relief = any(
                (
                    not is_verified_starter(s.id, s.lineup_prob, li_status, s.fitness_status)
                    or _observed_24h_change(s) < 50_000
                    or int(
                        getattr(s, "market_value_trend", None)
                        or getattr(s, "mv_trend", 0)
                        or 0
                    )
                    == 2
                )
                and swap_sale_proceeds(s.market_value) - price
                >= max(DEFICIT_HEAL_MIN_RELIEF_EUR, -remaining)
                for s in sale_side
            )
            for sold in sale_side:
                non_starter = not is_verified_starter(
                    sold.id, sold.lineup_prob, li_status, sold.fitness_status
                )
                falling_starter = (
                    _observed_24h_change(sold) < 0
                    or int(
                        getattr(sold, "market_value_trend", None)
                        or getattr(sold, "mv_trend", 0)
                        or 0
                    )
                    == 2
                )
                proceeds = swap_sale_proceeds(sold.market_value)
                deficit_relief_ok = (
                    remaining < 0
                    and _sale_tier(sold) <= 2
                    and _observed_24h_change(sold) < 50_000
                    and proceeds - price >= DEFICIT_HEAL_MIN_RELIEF_EUR
                )
                full_deficit_covered = (
                    remaining < 0
                    and _sale_tier(sold) <= 2
                    and proceeds - price >= max(DEFICIT_HEAL_MIN_RELIEF_EUR, -remaining)
                )
                emergency_deficit_relief = not has_preferred_relief and full_deficit_covered
                if (
                    int(sold.position) != int(player.position)
                    and not (
                        non_starter
                        or falling_starter
                        or deficit_relief_ok
                        or emergency_deficit_relief
                    )
                ):
                    continue
                bridge = _pair_bridge(sold, price)
                gain = projections[pid]["horizon_points"] - projections[sold.id]["horizon_points"]
                credit_p = min(proceeds + bridge, price + max(0, -remaining))
                midweek_bridge_tolerated = (
                    deadline is not None
                    and (deadline - now) > MIDWEEK_DEFICIT_DOWNGRADE_LEAD
                    and -remaining <= MIDWEEK_SWAP_BRIDGE_EUR
                )
                heals_deficit_downgrade = (
                    remaining < 0
                    and not midweek_bridge_tolerated
                    and gain <= 0
                    and (
                        non_starter
                        or falling_starter
                        or deficit_relief_ok
                        or emergency_deficit_relief
                    )
                    and (
                        projections[pid]["horizon_points"] >= DEFICIT_HEAL_MIN_HORIZON_POINTS
                        or (deficit_filler and full_deficit_covered)
                    )
                    and proceeds - price >= DEFICIT_HEAL_MIN_RELIEF_EUR
                )
                if (
                    (gain > 0 or heals_deficit_downgrade)
                    and price - credit_p <= remaining
                    and price <= room_33
                ):
                    prio = (
                        _sale_tier(sold)
                        + (
                            0
                            if gain > 0
                            else (
                                40
                                if deficit_filler
                                else (
                                    10
                                    if non_starter or falling_starter
                                    else (20 if deficit_relief_ok else 30)
                                )
                            )
                        )
                        + (5 if row.seller_id else 0)
                        + (
                            2
                            if deficit_filler
                            and (
                                player.fitness_status != 0
                                or li_status.get(pid) is Status.OUT
                                or candidates[pid].injury_severity
                            )
                            else 0
                        )
                    )
                    pairs.append(
                        (prio, -gain, price - proceeds, pid, sold.id)
                    )
        if not pairs:
            return empty
        clubs = dict(club_counts)
        if replaceable_swap is not None:
            old_tid = str(commitments[replaceable_swap[0]].get("team_id") or "")
            if old_tid:
                clubs[old_tid] = max(0, clubs.get(old_tid, 0) - 1)
        for pid in new_ids:
            tid = market_by_id[pid].player.team_id
            clubs[tid] = clubs.get(tid, 0) + 1
        base = [
            replace(o, cost_eur=0, committed=True) if o.player_id in new_ids else o
            for o in options
            if (o.owned or o.committed or o.player_id in new_ids)
            and (replaceable_swap is None or o.player_id != replaceable_swap[0])
        ]
        loss = TeamValue(
            no_swap_plan.filled_slots, no_swap_plan.horizon_points, max(0, remaining)
        )

        def _swap_loss(option_pid: str, sold_id: str) -> TeamValue:
            if (
                remaining < 0
                and projections[option_pid]["horizon_points"]
                <= projections[sold_id]["horizon_points"]
            ):
                filler_margin = 1.0 if projections[option_pid]["horizon_points"] <= 0.0 else 0.0
                return TeamValue(
                    no_swap_plan.filled_slots,
                    max(
                        0.0,
                        no_swap_plan.horizon_points
                        - projections[sold_id]["horizon_points"]
                        - filler_margin,
                    ),
                    0,
                )
            return loss

        def swap_solve(
            option: PlayerOption, sold_id: str, amount: int, *, target_mv: int | None = None
        ) -> TeamPlan | None:
            nonlocal solver_seconds
            if solver_seconds >= 20 or amount > room_33:
                return None
            sold_p = squad_by_id[sold_id]
            proceeds = swap_sale_proceeds(sold_p.market_value)
            bridge = _pair_bridge(sold_p, target_mv or amount)
            credit_avail = (
                proceeds
                if remaining < 0 and bridge == 0
                else min(proceeds + bridge, amount + max(0, -remaining))
            )
            started = monotonic()
            plan = optimize_team(
                [*base, replace(option, cost_eur=amount)],
                cash_available=remaining + credit_avail,
                club_counts=clubs,
                max_per_club=max_club,
                max_new_purchases=1,
                required_ids=frozenset({option.player_id}),
                excluded_ids=frozenset({sold_id}) | coupled_out,
                time_limit=min(2.0, 20 - solver_seconds),
            )
            solver_seconds += monotonic() - started
            if not plan.optimal or plan.remaining_cash < 0:
                return None
            pair_loss = _swap_loss(option.player_id, sold_id)
            # Nur ein echter Gewinn an besetzten Plätzen oder Punkten rechtfertigt Tausch.
            if (plan.filled_slots, round(plan.horizon_points, 6)) <= (
                pair_loss.filled_slots, round(pair_loss.points, 6)
            ):
                return None
            if replaceable_swap is not None and (
                plan.filled_slots < base_plan.filled_slots
                or (
                    plan.filled_slots == base_plan.filled_slots
                    and round(plan.horizon_points - base_plan.horizon_points, 6)
                    < SWAP_UPGRADE_MIN_GAIN_POINTS
                )
            ):
                return None
            return plan

        ranked = []
        for prio, _gain, net, pid, sold_id in sorted(pairs):
            if len(ranked) >= 8:
                break
            row = market_by_id[pid]
            player = row.player
            option = PlayerOption(
                pid, int(player.position), player.team_id,
                projections[pid]["horizon_points"], name=player.last_name,
            )
            mv_min = max(
                int(player.market_value), int(row.price or 0) if row.seller_id else 0, 1
            )
            plan = swap_solve(option, sold_id, mv_min, target_mv=mv_min)
            if plan is not None:
                ranked.append(
                    (
                        (
                            int(not row.seller_id),
                            plan.filled_slots,
                            round(plan.horizon_points, 6),
                            -prio,
                            -net,
                        ),
                        pid,
                        sold_id,
                    )
                )
        for _key, pid, sold_id in sorted(ranked, reverse=True):
            if attempts >= 16:
                break
            attempts += 1
            try:
                fresh = await client.get_player(lg, pid)
                row = market_by_id[pid].model_copy(update={"player": fresh})
                is_mgr = bool(row.seller_id)
                is_filler = is_deficit_filler(row)
                valid_fresh = fresh.id == pid and (
                    is_filler
                    if is_filler
                    else (
                        manager_reject_reason(row, ignore_open_for=pid) is None
                        if is_mgr
                        else (reject_reason(row) is None and _swap_target_role_ok(fresh))
                    )
                )
                if not valid_fresh:
                    raise ValueError("Rolle oder Identität nicht frisch bestätigt")
            except Exception:
                detail_failed.add(pid)
                reviews[pid]["reasons"] = ["Aktueller Detailabruf bestätigt den Tausch nicht."]
                reviews[pid]["eligible"] = False
                continue
            market_by_id[pid] = row
            candidates[pid] = candidates[pid].model_copy(
                update={
                    "team_id": fresh.team_id,
                    "position": int(fresh.position),
                    "current_market_value": fresh.market_value,
                }
            )
            option = PlayerOption(
                pid, int(fresh.position), fresh.team_id, project(fresh)["horizon_points"],
                name=fresh.last_name,
            )
            sold_p = squad_by_id[sold_id]
            proceeds = swap_sale_proceeds(sold_p.market_value)
            is_mgr = bool(row.seller_id)
            minimum = max(int(fresh.market_value), int(row.price or 0) if is_mgr else 0, 1)
            bridge = _pair_bridge(sold_p, minimum)
            max_factor = (
                MANAGER_MAX_PRICE_FACTOR
                if is_mgr
                else DEFAULT_GUARDRAILS.max_bid_premium_upgrade
            )
            maximum = min(
                int(fresh.market_value * max_factor),
                remaining + proceeds + bridge,
                room_33,
            )
            listing_kind = "manager" if is_mgr else "kickbase"
            competition = load_competition(lg, listing_kind)
            bid_model_versions[pid] = (
                str(competition.get("version") or "unknown") if competition else "unknown"
            )

            def win_value(
                amount: int, option=option, sold_id=sold_id, minimum=minimum
            ) -> TeamValue | None:
                plan = swap_solve(option, sold_id, amount, target_mv=minimum)
                if plan is None:
                    return None
                return TeamValue(plan.filled_slots, plan.horizon_points, plan.remaining_cash)

            def probability(
                amount: int, player=fresh, model=competition, kind=listing_kind
            ):
                return predict_probability(
                    model,
                    amount=amount,
                    market_value=player.market_value,
                    position=int(player.position),
                    league_id=lg,
                    listing_type=kind,
                    as_of=now,
                )

            swap_premium_kwargs = (
                {
                    "conviction_bonus": CONVICTION_BONUS,
                    "cap": CONVICTION_CAP,
                }
                if (
                    int(getattr(fresh, "lineup_prob", 5) or 5) == 1
                    and li_status.get(pid) is Status.STARTER
                    and (pid in comeback_ids or pid in profiteur_ids)
                )
                else (
                    {"cap": KB3_PREMIUM_CAP}
                    if int(getattr(fresh, "lineup_prob", 5) or 5) >= 3
                    else {}
                )
            )
            swap_gain_horizon = max(
                0.0,
                float(option.horizon_points)
                - float(projections[sold_id]["horizon_points"]),
            )
            swap_starter_gain_pts = swap_gain_horizon / 4.0
            swap_rival_scarcity = estimate_rival_scarcity(
                alive,
                position=int(fresh.position),
                rival_squads=getattr(client, "rival_squads", None),
            )
            premium = (
                None
                if is_mgr
                else bid_premium(
                    fresh.market_value,
                    fresh.mv_change_24h,
                    fresh.mv_change_24h_observed,
                    rival_scarcity_count=swap_rival_scarcity,
                    starter_gain_points=swap_starter_gain_pts,
                    **swap_premium_kwargs,
                )
            )
            record_premium(premiums, pid, premium)
            pair_loss = _swap_loss(pid, sold_id)
            decision = choose_bid(
                market_value=fresh.market_value,
                minimum_bid=minimum,
                maximum_bid=maximum,
                evaluate_win=win_value,
                evaluate_loss=lambda pair_loss=pair_loss: pair_loss,
                probability=probability if competition is not None else None,
                target_amount=(
                    minimum
                    if is_mgr
                    else (premium.amount if premium is not None else None)
                ),
                target_reason=(
                    "Manager-Tausch zum Mindestanker (max. 101,5 % MW)."
                    if is_mgr
                    else (premium.describe() if premium is not None else None)
                ),
            )
            won = (
                swap_solve(option, sold_id, decision.amount, target_mv=minimum)
                if decision.amount is not None
                else None
            )
            if decision.amount is None or won is None:
                reviews[pid]["reasons"] = [decision.reason]
                continue
            gain = won.horizon_points - pair_loss.points
            premium_eur = max(0, decision.amount - int(fresh.market_value))
            mv_advantage = (
                max(0, _observed_24h_change(fresh) - _observed_24h_change(sold_p))
                if _observed_24h_change(sold_p) < 0
                else 0
            )
            if won.filled_slots <= pair_loss.filled_slots and not swap_pays_off(
                gain_points=gain,
                premium_eur=premium_eur,
                team_value_eur=team_value,
                team_points=max(1.0, pair_loss.points),
                mv_advantage_eur=mv_advantage,
            ):
                # Tauschbremse (Emil 12.10.2026): Der Punktgewinn muss den Aufschlag tragen.
                worth = round(gain * team_value / loss.points) if loss.points > 0 else 0
                reason = (
                    f"Tausch {fresh.last_name} für {squad_by_id[sold_id].last_name} lohnt "
                    f"nicht: {gain:.1f} Prognosepunkte (≈ {worth:,} €) gegen "
                    f"{premium_eur:,} € Aufschlag."
                ).replace(",", ".")
                reviews[pid]["reasons"] = [reason]
                notes.append(reason)
                continue
            if replaceable_swap is not None:
                old_target_id = replaceable_swap[0]
                old_tid = str(commitments[old_target_id].get("team_id") or "")
                if old_tid:
                    club_counts[old_tid] = max(0, club_counts.get(old_tid, 0) - 1)
                selected_ids.discard(old_target_id)
                decisions.pop(old_target_id, None)
                reviews[old_target_id]["selected"] = False
                reviews[old_target_id]["eligible"] = False
                reviews[old_target_id]["reasons"] = [
                    f"Durch stärkeren Tausch ({fresh.last_name}) abgelöst."
                ]
            decisions[pid] = decision
            prices[pid] = decision.amount
            caps[pid] = decision.max_bid_eur or decision.amount
            reviews[pid]["eligible"] = True
            reviews[pid]["reasons"] = []
            selected_ids.add(pid)
            prior_purchases = set(base_plan.purchase_ids) - (
                {replaceable_swap[0]} if replaceable_swap is not None else set()
            )
            prior_cost = base_plan.cost_eur - (
                old_amount
                if replaceable_swap is not None and replaceable_swap[0] in base_plan.purchase_ids
                else 0
            )
            merged = replace(
                won,
                purchase_ids=tuple(sorted({*prior_purchases, pid})),
                cost_eur=prior_cost + decision.amount,
                owned_filled_slots=base_plan.owned_filled_slots,
            )
            used_sale = min(proceeds, decision.amount + max(0, -remaining))
            used_bridge = min(bridge, max(0, decision.amount - remaining - proceeds))
            return (
                {pid: sold_id},
                {pid: used_sale + used_bridge},
                merged,
            )
        return empty

    solve_cache.clear()
    result = solve()
    # Ein angerechneter Tauschverkauf gilt nur, solange sein Kaufziel in der Elf steht;
    # sonst wäre der Verkauf nicht geplant und sein Geld keine Deckung.
    dropped_links = {
        target for target in swap_links
        if target not in result.lineup_ids and target not in live_ids
    }
    if dropped_links:
        for target in dropped_links:
            cash_plan -= swap_credit.pop(target)
            swap_sale_only_credit.pop(target, None)
            swap_bridge_credit.pop(target, None)
            del swap_links[target]
        solve_cache.clear()
        result = solve()
    selected_ids = set(result.purchase_ids) | (set(result.lineup_ids) & commitments.keys())
    selected_ids = {
        pid for pid in selected_ids if pid in decisions and decisions[pid].amount is not None
    }
    new_swaps, new_swap_credit, swap_plan = await _plan_overdraft_swap()
    if swap_plan is not None:
        if swap_links:
            for old_target in list(swap_links):
                old_amt = int(commitments.get(old_target, {}).get("amount") or 0)
                open_bid_exposure = max(0, open_bid_exposure - old_amt)
                cash_frei += old_amt
                cash_plan += old_amt - swap_credit.pop(old_target, 0)
                swap_sale_only_credit.pop(old_target, None)
                swap_bridge_credit.pop(old_target, None)
                swap_links.pop(old_target, None)
                commitments.pop(old_target, None)
            frame = planning_overdraft(
                now, team_value, cash_frei, until=PLANNING_OVERDRAFT_UNTIL
            )
            pending_bids = [
                b for b in pending_bids if str(b.get("player_id") or "") in commitments
            ]
        result = swap_plan
    manager_gains: dict[str, float] = {}

    async def _plan_manager_snipe() -> None:
        nonlocal attempts, solver_seconds
        new_kb_ids = {k for k in selected_ids if k not in commitments}
        spent_kb = sum(prices[k] - new_swap_credit.get(k, 0) for k in new_kb_ids)
        sold_in_swaps = set(swap_links.values()) | set(new_swaps.values())
        post_kb_base = [
            PlayerOption(
                p.id,
                int(p.position),
                p.team_id,
                projections[p.id]["horizon_points"],
                owned=True,
                name=p.last_name,
            )
            for p in squad_players
            if p.id not in trade_ids and p.id not in sold_in_swaps
        ] + [
            PlayerOption(
                k,
                int(market_by_id[k].player.position),
                market_by_id[k].player.team_id,
                projections[k]["horizon_points"],
                cost_eur=0,
                committed=True,
                name=market_by_id[k].player.last_name,
            )
            for k in sorted(selected_ids | (set(result.lineup_ids) & commitments.keys()))
            if k in market_by_id and not market_by_id[k].seller_id
        ]
        post_kb_clubs = dict(club_counts)
        for k in new_kb_ids:
            tid = market_by_id[k].player.team_id
            post_kb_clubs[tid] = post_kb_clubs.get(tid, 0) + 1
        for s_id in new_swaps.values():
            tid = squad_by_id[s_id].team_id
            post_kb_clubs[tid] = max(0, post_kb_clubs.get(tid, 0) - 1)
        mgr_candidates: list[tuple[float, int, str]] = []
        base = MANAGER_LISTING_REJECTION[:-1]
        blocked_global = trades_blocked(
            trades_used, trade_limit, count_saturated=usage.count_saturated
        ) or any(
            not r.get("team_id") or int(r.get("amount") or 0) <= 0
            for r in commitments.values()
        )
        for pid, row in sorted(market_by_id.items()):
            if not row.seller_id or pid in selected_ids:
                continue
            reason = manager_reject_reason(row, ignore_open_for=pid)
            if reason is not None:
                reviews[pid]["eligible"] = False
                reviews[pid]["reasons"] = [reason]
                continue
            if blocked_global:
                reviews[pid]["eligible"] = False
                reviews[pid]["reasons"] = [
                    f"{base} (Transfersperre oder offene Verpflichtung unklar)."
                ]
                continue
            own_comm = int(commitments[pid]["amount"]) if pid in commitments else 0
            avail_cash = cash_plan - spent_kb + own_comm
            avail_slots = max_new - len(new_kb_ids) + int(pid in commitments)
            min_anchor = max(int(row.player.market_value), int(row.price or 0))
            asking = own_comm if pid in commitments else min_anchor
            if pid in commitments and asking < min_anchor:
                reviews[pid]["eligible"] = False
                reviews[pid]["reasons"] = [
                    "Bestehender Betrag unter aktuellem Mindestanker; neu planen."
                ]
                continue
            cap = min(single_limit, int(row.player.market_value * MANAGER_MAX_PRICE_FACTOR))
            if min_anchor > cap:
                reviews[pid]["eligible"] = False
                reviews[pid]["reasons"] = [
                    f"{base} (Mindestpreis über dem verfügbaren technischen Einzelrahmen)."
                ]
                continue
            if avail_slots <= 0 or asking > avail_cash or not result.optimal:
                reviews[pid]["eligible"] = False
                reviews[pid]["reasons"] = [
                    f"{base} (Nach vorrangigen Kickbase-Auktionen kein freies "
                    "Restbudget/Kaderplatz — Zero-Crowding-Out)."
                ]
                continue
            clubs_for_pid = dict(post_kb_clubs)
            if pid in commitments:
                tid = str(commitments[pid].get("team_id") or "")
                clubs_for_pid[tid] = max(0, clubs_for_pid.get(tid, 0) - 1)
            opt = PlayerOption(
                pid,
                int(row.player.position),
                row.player.team_id,
                projections[pid]["horizon_points"],
                cost_eur=asking,
                name=row.player.last_name,
            )
            started = monotonic()
            trial = optimize_team(
                [*post_kb_base, opt],
                cash_available=avail_cash,
                club_counts=clubs_for_pid,
                max_per_club=max_club,
                max_new_purchases=1,
                required_ids=frozenset({pid}),
                time_limit=min(2.0, max(0.2, 20 - solver_seconds)),
            )
            solver_seconds += monotonic() - started
            gain = (trial.horizon_points - result.horizon_points) if trial.optimal else -1.0
            is_bargain = (
                int(row.price or 0) <= int(row.player.market_value)
                and _observed_24h_change(row.player) >= MANAGER_BARGAIN_MIN_MV_CHANGE_24H
            )
            req_gain = (
                MANAGER_BARGAIN_MIN_HORIZON_GAIN if is_bargain else MANAGER_MIN_HORIZON_GAIN
            )
            if not trial.optimal or trial.remaining_cash < 0 or gain < req_gain:
                reviews[pid]["eligible"] = False
                reviews[pid]["reasons"] = [
                    f"{base} (Startelf-Gewinn +{max(0.0, gain):.1f} Pkt unter "
                    f"Mindestschwelle +{req_gain:.0f} Pkt)."
                ]
                continue
            mgr_candidates.append((gain, -asking, pid))

        picked_mgr: str | None = None
        for _gain, _neg_ask, pid in sorted(mgr_candidates, reverse=True):
            if picked_mgr is not None:
                reviews[pid]["eligible"] = False
                reviews[pid]["reasons"] = [
                    f"{base} (Höchstens 1 Manager-Gebot gleichzeitig; "
                    "stärkeres Ziel bevorzugt)."
                ]
                continue
            if attempts >= 16:
                reviews[pid]["eligible"] = False
                reviews[pid]["reasons"] = [f"{base} (Detailabruf-Budget erschöpft)."]
                continue
            attempts += 1
            try:
                fresh = await client.get_player(lg, pid)
                row = market_by_id[pid].model_copy(update={"player": fresh})
                if (
                    fresh.id != pid
                    or manager_reject_reason(row, ignore_open_for=pid) is not None
                ):
                    raise ValueError("Manager-Listing frisch nicht bestätigt")
            except Exception:
                detail_failed.add(pid)
                reviews[pid]["eligible"] = False
                reviews[pid]["reasons"] = [
                    "Aktueller Detailabruf bestätigt den Manager-Kauf nicht."
                ]
                continue
            market_by_id[pid] = row
            candidates[pid] = candidates[pid].model_copy(
                update={
                    "team_id": fresh.team_id,
                    "position": int(fresh.position),
                    "current_market_value": fresh.market_value,
                }
            )
            projection = project(fresh)
            own_comm = int(commitments[pid]["amount"]) if pid in commitments else 0
            avail_cash = cash_plan - spent_kb + own_comm
            min_anchor = max(int(fresh.market_value), int(row.price or 0))
            asking = own_comm if pid in commitments else min_anchor
            cap = min(
                single_limit,
                avail_cash,
                int(fresh.market_value * MANAGER_MAX_PRICE_FACTOR),
            )
            if min_anchor > cap or asking > cap or asking < min_anchor:
                reviews[pid]["eligible"] = False
                reviews[pid]["reasons"] = [
                    f"{base} (Frischer Preis außerhalb des 101,5-%-Deckels/Rahmens)."
                ]
                continue
            clubs_for_pid = dict(post_kb_clubs)
            if pid in commitments:
                tid = str(commitments[pid].get("team_id") or "")
                clubs_for_pid[tid] = max(0, clubs_for_pid.get(tid, 0) - 1)
            loss_tv = TeamValue(result.filled_slots, result.horizon_points, avail_cash)

            def mgr_solve(
                amount: int,
                target_pid: str = pid,
                fresh_p=fresh,
                hp: float = projection["horizon_points"],
                avail: int = avail_cash,
                clubs_map=clubs_for_pid,
            ) -> TeamPlan | None:
                nonlocal solver_seconds
                opt_p = PlayerOption(
                    target_pid,
                    int(fresh_p.position),
                    fresh_p.team_id,
                    hp,
                    cost_eur=amount,
                    name=fresh_p.last_name,
                )
                started_t = monotonic()
                plan_t = optimize_team(
                    [*post_kb_base, opt_p],
                    cash_available=avail,
                    club_counts=clubs_map,
                    max_per_club=max_club,
                    max_new_purchases=1,
                    required_ids=frozenset({target_pid}),
                    time_limit=min(2.0, max(0.2, 20 - solver_seconds)),
                )
                solver_seconds += monotonic() - started_t
                if not plan_t.optimal or plan_t.remaining_cash < 0:
                    return None
                if plan_t.horizon_points - result.horizon_points < MANAGER_MIN_HORIZON_GAIN:
                    return None
                return plan_t

            def mgr_win(amount: int) -> TeamValue | None:
                plan_w = mgr_solve(amount)
                if plan_w is None:
                    return None
                return TeamValue(
                    plan_w.filled_slots, plan_w.horizon_points, plan_w.remaining_cash
                )

            competition = load_competition(lg, "manager")
            bid_model_versions[pid] = (
                str(competition.get("version") or "unknown") if competition else "unknown"
            )

            def mgr_prob(amount: int, player=fresh, model=competition):
                return (
                    predict_probability(
                        model,
                        amount=amount,
                        market_value=player.market_value,
                        position=int(player.position),
                        league_id=lg,
                        listing_type="manager",
                        as_of=now,
                    )
                    if model is not None
                    else None
                )

            decision = choose_bid(
                market_value=fresh.market_value,
                minimum_bid=min_anchor,
                maximum_bid=cap,
                evaluate_win=mgr_win,
                evaluate_loss=lambda lost=loss_tv: lost,
                probability=mgr_prob if competition is not None else None,
                target_amount=asking,
                target_reason=(
                    "Manager-Direktkauf zum Mindestanker (max. 101,5 % MW, "
                    "Anti-Lemon & Zero-Crowding-Out)."
                ),
            )
            if pid in commitments and decision.amount is not None:
                decision = replace(
                    decision,
                    amount=asking,
                    scenarios=[
                        {**s, "selected": s["amount"] == asking} for s in decision.scenarios
                    ],
                    reason=_standing_bid_reason(commitments[pid], decision.reason),
                )
            won_plan = mgr_solve(decision.amount) if decision.amount is not None else None
            if decision.amount is None or won_plan is None:
                reviews[pid]["eligible"] = False
                reviews[pid]["reasons"] = [decision.reason]
                continue
            decisions[pid] = decision
            prices[pid] = decision.amount
            caps[pid] = decision.max_bid_eur or decision.amount
            reviews[pid]["eligible"] = True
            reviews[pid]["reasons"] = []
            manager_gains[pid] = won_plan.horizon_points - result.horizon_points
            selected_ids.add(pid)
            picked_mgr = pid

    await _plan_manager_snipe()
    observed = now
    plan_id = uuid4().hex
    swap_pairs = {**swap_links, **new_swaps}
    swap_rows = [
        {
            "player_id": sold,
            "for_player_id": target,
            "market_value_eur": int(squad_by_id[sold].market_value),
            "proceeds_eur": swap_sale_proceeds(squad_by_id[sold].market_value),
        }
        for target, sold in sorted(swap_pairs.items())
        if target in selected_ids
    ]
    new_deficit = max(
        0,
        -(
            cash_plan
            + trade_held_cover
            - sum(prices[p] for p in selected_ids if p not in commitments and p not in new_swaps)
        ),
    )
    new_sale_only = {
        target: min(
            swap_sale_proceeds(squad_by_id[sold].market_value),
            prices[target] + new_deficit,
        )
        for target, sold in new_swaps.items()
    }
    new_bridge_only = {
        target: max(0, new_swap_credit[target] - new_sale_only[target])
        for target in new_swaps
    }
    credit_total = sum(swap_sale_only_credit.values()) + sum(new_sale_only.values())
    bridge_total = sum(swap_bridge_credit.values()) + sum(new_bridge_only.values())
    # Deckung der Engine-Verpflichtungen: Handelsgebote zählen nicht mit, Handelsspieler mit
    # 100 % ihres Marktwerts (der Autopilot verkauft sie spätestens am Verkaufstermin) — sonst
    # gäbe die Engine für ein geplantes Minus aus dem Handel ihre eigenen Gebote auf.
    funding_budget = budget + sum(
        trade_sale_proceeds(
            squad_by_id[pid].market_value,
            updates_until_friday=updates_until_friday,
            haircut_per_update_pct=haircut_pct,
        )
        for pid in trade_ids
    )
    recovery = build_funding_recovery(
        league_id=lg, plan_id=plan_id, budget=funding_budget, owned_player_ids=squad_ids,
        live_bids=engine_live_bids, pending_bids=pending_bids, now=now,
        market_by_id=market_by_id, protected_ids=protected, manual_live_ids=manual_live_ids,
        points_by_id={pid: p.get("expected_points") or 0 for pid, p in projections.items()},
    )
    covered_by_swaps = sum(swap_credit.values())
    sale_only_swaps = sum(swap_sale_only_credit.values())
    if (
        covered_by_swaps
        and (budget >= 0 or sale_only_swaps > 0)
        and recovery["status"] != "funded"
        and recovery["deficit_eur"] <= covered_by_swaps
    ):
        # Die Unterdeckung ist genau der gekoppelte Tausch-Überhang (Planungs-Dispo):
        # der wartende Sofortverkauf deckt ihn. Kein Rückzug, keine Freigabe des
        # eigenen Tauschgebots — sonst räumte der nächste Lauf jedes Dispo-Paar ab.
        recovery = {
            **recovery,
            "status": "funded",
            "released_pending_ids": [],
            "actions": [],
            "swap_credit_eur": covered_by_swaps,
            "notes": [
                f"Unterdeckung {recovery['deficit_eur']:,} € ist durch gekoppelte "
                f"Tausch-Sofortverkäufe ({covered_by_swaps:,} € angerechnet, Planungs-Dispo "
                f"bis {PLANNING_OVERDRAFT_UNTIL:%d.%m.%Y}) gedeckt; kein Rückzug.".replace(
                    ",", "."
                )
            ],
        }
    provenance = {str(bid.get("player_id")): bid.get("funding_provenance") for bid in live_bids}
    for action in recovery["actions"]:
        action["funding_review"].update(provenance.get(action["player_id"]) or {})
    if funding_budget < 0:
        recovery["sale_alternative"] = cash_deficit_alternative(
            [sp for sp in squad if sp.player.id not in trade_ids], funding_budget, deadline,
            projections,
        )
    if recovery["status"] == "blocked":
        recovery["blocked_bid_alternatives"] = [{
            "player_id": str(bid.get("player_id")), "offer_id": bid.get("offer_id"),
            "amount_eur": bid.get("amount"), "execution_authorized": False,
            "reason": "Manuelle/ungeklärte Herkunft; bewusste Entscheidung nötig."
            if str(bid.get("player_id")) in manual_live_ids else
            "Auktions-ID/Ende oder ausreichende freigebbare Deckung nicht belegt.",
        } for bid in engine_live_bids]
        for bid in engine_live_bids:
            pid = str(bid.get("player_id"))
            if pid in manual_live_ids and type(bid.get("amount")) is int:
                remaining = budget - sum(int(b.get("amount") or 0) for b in live_bids)
                remaining += bid["amount"]
                recovery["notes"].append(
                    f"{bid.get('name') or pid}: manuelles/ungeklärtes Gebot über "
                    f"{bid['amount']:,} € prüfen. Vollständige Aufgabe ergäbe nach übrigen "
                    f"Geboten {remaining:,} € frei; kein automatischer Rückzug freigegeben."
                    .replace(",", ".")
                )
    live_money: dict[str, int] = {}
    for live_bid in live_bids:
        pid = str(live_bid.get("player_id") or "")
        if pid and pid not in squad_ids and int(live_bid.get("amount") or 0) > 0:
            live_money[pid] = max(live_money.get(pid, 0), int(live_bid["amount"]))
    purchases: dict[str, dict] = {
        pid: {
            "player_id": pid,
            "amount_eur": int(row["amount"]),
            "max_bid_eur": max(int(row["amount"]), caps.get(pid, 0)),
        }
        for pid, row in commitments.items()
        if pid not in squad_ids and int(row.get("amount") or 0) > 0
    }
    for pid in selected_ids:
        decision = decisions[pid]
        purchases[pid] = {
            "player_id": pid,
            "amount_eur": prices[pid],
            "max_bid_eur": decision.max_bid_eur,
        }
    reserved: dict[str, int] = {pid: row["amount_eur"] for pid, row in purchases.items()}
    for pid, amount in live_money.items():
        reserved[pid] = max(reserved.get(pid, 0), amount)
    forecast_inputs = {}
    players_by_id = {p.id: p for p in squad_players}
    players_by_id.update({pid: row.player for pid, row in market_by_id.items()})
    record_ids = sorted(squad_ids | selected_ids) + sorted(
        set(players_by_id) - squad_ids - selected_ids
    )
    for pid in record_ids[:40]:
        cleaned, _ = clean_rows(pid, perf.get(pid, []), as_of=now, settlement=LIVE_SETTLEMENT)
        accepted = {(r.season, r.matchday) for r in cleaned[-12:]}
        forecast_inputs[pid] = {
            "captured_at": now.isoformat(),
            "player": players_by_id[pid].model_dump(mode="json"),
            "performances": [
                r.model_dump(mode="json")
                for r in perf.get(pid, [])
                if (r.season, r.matchday) in accepted
            ],
            "ligainsider_status": li_status.get(pid, Status.UNKNOWN).value,
        }
    team_plan = {
        **asdict(result),
        "notes": [*result.notes, *recovery["notes"]],
        "schema_version": 1,
        "engine_version": "team-milp-v1",
        "plan_id": plan_id,
        "observed_at": observed.isoformat(),
        "matchday_deadline": deadline.isoformat() if deadline is not None else None,
        "budget_eur": budget,
        "baseline_filled_slots": baseline.filled_slots,
        "owned_player_ids": sorted(squad_ids),
        "open_bids": [
            {"player_id": pid, "amount_eur": amount} for pid, amount in sorted(live_money.items())
        ],
        "open_bid_exposure_eur": sum(live_money.values()),
        "purchases": list(purchases.values()),
        "reserved_cash_eur": sum(reserved.values()),
        "forecasts": projections,
        "forecast_inputs": forecast_inputs,
        "fallbacks": [],
        "funding_basis": "actual_cash_no_assumed_sales",
        "funding_recovery": recovery,
        "projection_basis": "conditional_on_appearance_with_explicit_role_heuristics",
        # Tausch-Finanzierung (Planungs-Dispo): gekoppelte Sofortverkäufe und der Teamwert
        # für die 33-%-Prüfung in Kaufbeleg und Executor-Preflight (team_funding_veto).
        "swap_sales": swap_rows,
        "team_value_eur": team_value,
        "technical_limits": {
            "single_bid_budget_fraction": bid_fraction,
            "kickoff_buffer_eur": KICKOFF_MIN_BALANCE,
            "planning_cash_eur": cash_plan,
            "planning_overdraft_eur": frame,
            "overdraft_until": PLANNING_OVERDRAFT_UNTIL.isoformat(),
            "swap_credit_eur": credit_total,
            "midweek_swap_bridge_eur": bridge_total,
            "trade_sale_cover_eur": trade_held_cover,
            "market_value_factor": premium_limit,
            "daily_buy_cap": DEFAULT_GUARDRAILS.daily_buy_cap,
            "squad_limit": squad_limit,
            "new_package_capacity": max_new,
            "daily_used": None,
            "daily_quota_rechecked_by_executor": True,
        },
    }
    buys: list[Proposal] = []
    for pid in sorted(selected_ids):
        row = market_by_id[pid]
        decision = decisions[pid]
        loss_at = row.expires_at + AUCTION_SETTLEMENT_BUFFER if not row.seller_id else observed
        # Bedingter Ersatzweg: andere bereits geplante Zuschläge gelten hier als
        # gewonnen und bezahlt. Nur noch verfügbare, bisher ungebotene Alternativen
        # können anschließend den verlorenen Spieler ersetzen.
        other_new = set(result.purchase_ids) - {pid}
        unavailable = frozenset(
            {pid}
            | {
                other
                for other, listing in market_by_id.items()
                if other not in other_new
                and other not in commitments
                and not listing.seller_id
                and listing.expires_at <= loss_at
            }
            # Andere Tauschziele gelten als gewonnen: ihr Verkaufsspieler ist dann weg.
            | {sold for target, sold in swap_pairs.items() if target != pid}
        )
        fallback_cash = cash_plan - sum(
            prices[other] - new_swap_credit.get(other, 0) for other in other_new
        )
        fallback_clubs = dict(club_counts)
        if pid in commitments:
            # Verlorenes Tauschgebot: auch sein Verkauf entfällt.
            fallback_cash += int(commitments[pid]["amount"]) - swap_credit.get(pid, 0)
            tid = str(commitments[pid].get("team_id") or "")
            fallback_clubs[tid] = max(0, fallback_clubs.get(tid, 0) - 1)
        for other in other_new:
            tid = market_by_id[other].player.team_id
            fallback_clubs[tid] = fallback_clubs.get(tid, 0) + 1
        fallback = optimize_team(
            [
                replace(o, cost_eur=0, committed=True) if o.player_id in other_new else o
                for o in alternative_options
            ],
            cash_available=fallback_cash,
            club_counts=fallback_clubs,
            max_per_club=max_club,
            excluded_ids=unavailable,
            max_new_purchases=max(
                0,
                min(
                    DEFAULT_GUARDRAILS.daily_buy_cap
                    - len(commitments)
                    + int(pid in commitments)
                    - len(other_new),
                    squad_limit
                    - len(squad_ids)
                    - len(commitments)
                    + int(pid in commitments)
                    - len(other_new),
                ),
            ),
        )
        fallback_data = {
            "lost_player_id": pid,
            "known_at": None if row.seller_id else loss_at.isoformat(),
            "evaluated_as_of": observed.isoformat(),
            **asdict(fallback),
            "guaranteed": False,
            "assumed_other_wins": sorted(other_new | (commitments.keys() - {pid})),
            "condition": "Andere geplante Zuschläge gewonnen; Ersatz noch verfügbar."
            " Keine Zusage für künftige Auktionsergebnisse.",
        }
        if row.seller_id:
            fallback_data["condition"] += (
                " Zeitpunkt der Managerentscheidung unbekannt;"
                " Ersatzweg gilt nur zum Abrufzeitpunkt."
            )
        team_plan["fallbacks"].append(fallback_data)
        expiry = observed + REVIEW_LIFETIME
        if not row.seller_id:
            expiry = min(expiry, row.expires_at)
        reasons = [
            f"Mannschaft: {baseline.filled_slots} → {result.filled_slots} besetzbare Plätze "
            f"({result.owned_filled_slots} im Besitz, "
            f"{result.filled_slots - result.owned_filled_slots} über Kickbase-Auktionen); "
            f"{result.horizon_points:.1f} bedingte Punkte über vier Spieltage.",
            f"Alle Verpflichtungen {sum(reserved.values()):,} € bei {budget:,} € Konto.",
            decision.reason,
        ]
        if row.seller_id:
            reasons.insert(
                0,
                (
                    "Manager-Direktkauf (Anti-Lemon & Zero-Crowding-Out): KB-Stufe 1 + LI-Starter, "
                    f"Anker ≤ 101,5 % MW ({prices[pid]:,} €), +{manager_gains.get(pid, 0.0):.1f} "
                    "Pkt Startelf-Gewinn aus freiem Restbudget nach Kickbase-Auktionen."
                ).replace(",", "."),
            )
        if row.player.lineup_prob in (4, 5) or li_status.get(pid) is Status.BENCH:
            reasons.insert(0, "Notbesetzung: Bankrolle nur in den letzten 96 Stunden vor "
                           "der offiziellen Frist; keine behauptete Starterrolle.")
        if pid in swap_pairs and pid in selected_ids:
            sold = squad_by_id[swap_pairs[pid]]
            proceeds = swap_sale_proceeds(sold.market_value)
            reasons.insert(0, (
                f"Tausch auf Planungs-Dispo (bis {PLANNING_OVERDRAFT_UNTIL:%d.%m.%Y}): "
                f"{sold.first_name} {sold.last_name}".strip() + f" (MW {sold.market_value:,} €) "
                f"geht per Sofortverkauf, sobald das Ziel im Kader steht; angerechnet "
                f"{proceeds:,} € (90 %), netto {max(0, prices[pid] - proceeds):,} € aus "
                "echtem Geld."
            ).replace(",", "."))
        review = {
            "schema_version": 1,
            "player_id": pid,
            "selected": True,
            "eligible": True,
            "engine_version": "team-milp-v1",
            "plan_id": plan_id,
            "model_version": projections[pid]["model_version"],
            "bid_model_version": bid_model_versions.get(pid, "unknown"),
            "position": int(row.player.position),
            # P0-2: aus DIESER Marktzeile, nicht aus der Schleifenvariable des
            # Detailabrufs (trug in 134 von 528 Belegen die Art des Vorgaengers).
            "listing_type": "manager" if row.seller_id else "kickbase",
            "seller_id": row.seller_id,
            "market_value_eur": row.player.market_value,
            "mv_change_24h_eur": row.player.mv_change_24h,
            "max_bid_eur": decision.max_bid_eur,
            "target_bid_eur": prices[pid],
            "purpose": purpose,
            "reasons": reasons,
            "team_plan": team_plan,
            "swap_sale": next(
                (
                    {key: value for key, value in swap_row.items() if key != "for_player_id"}
                    for swap_row in swap_rows
                    if swap_row["for_player_id"] == pid
                ),
                None,
            ),
            "bid_scenarios": decision.scenarios,
            "probability_status": decision.probability_status,
            # Gebotsregel V3: Zielgebot und seine Herleitung; None bei stehendem Gebot,
            # Manager-Listing oder ohne beobachtete 24-h-Änderung (dann Mindestpreis).
            "bid_premium": _premium_record(pid, premiums, commitments, prices[pid]),
            "observed_at": observed.isoformat(),
            "valid_until": expiry.isoformat(),
            "alternative_id": next(iter(fallback.purchase_ids), None),
        }
        reviews[pid] = review
        if pid in commitments:
            continue
        action = PlannedAction(
            "bid",
            lg,
            player_id=pid,
            amount=prices[pid],
            squad_need=completion,
            purchase_review=review,
        )
        buys.append(
            Proposal(
                action,
                "team_package_buy",
                " ".join(reasons),
                candidates[pid].total_score,
                prices[pid] > DEFAULT_GUARDRAILS.approval_threshold,
                player_name=candidates[pid].name,
            )
        )
    swaps: list[SwapPair] = []
    fallers = {c.player_id: c for c in squad_cands}
    displaced = [
        c
        for c in squad_cands
        if c.player_id in baseline.lineup_ids
        and c.player_id not in result.lineup_ids
        and c.player_id not in DEFAULT_GUARDRAILS.all_protected_ids
        and c.player_id not in protected
        and c.player_id not in swap_pairs.values()
        and c.player_id not in held  # Tauschbremse: frisch Gekaufte gehen nicht per Tausch
        and not (
            custom_rules_active
            and agent_rules.allow_sells == "bench_only"
            and is_verified_starter(
                c.player_id,
                squad_by_id[c.player_id].lineup_prob,
                li_status,
                squad_by_id[c.player_id].fitness_status,
            )
        )
    ]
    if deadline is not None and not (
        custom_rules_active and agent_rules.allow_sells == "none"
    ):
        for proposal in list(buys):
            pid = str(proposal.action.player_id)
            if pid in new_swaps:
                # Tausch auf Dispo: Gebot + wartender Sofortverkauf genau dieses Spielers.
                sold = new_swaps[pid]
                swaps.append(
                    SwapPair(fallers[sold], proposal, int(squad_by_id[sold].market_value))
                )
                buys.remove(proposal)
                continue
            if market_by_id[pid].seller_id:
                continue
            target = market_by_id[pid].player
            old = next(
                (c for c in displaced if c.position == int(target.position)),
                next(
                    (
                        c for c in displaced
                        if not is_verified_starter(
                            c.player_id,
                            squad_by_id[c.player_id].lineup_prob,
                            li_status,
                            squad_by_id[c.player_id].fitness_status,
                        )
                    ),
                    None,
                ),
            )
            if old is not None and not completion:
                swaps.append(SwapPair(old, proposal, old.current_market_value))
                displaced.remove(old)
                buys.remove(proposal)
    protected_lineup = set(result.lineup_ids) | set(baseline.lineup_ids)
    showcase_excluded = (
        protected_lineup
        | set(trade_ids)
        | set(new_swaps.values())
        | set(swap_links.values())
        | set(protected)
    )
    sell_guardrails = replace(DEFAULT_GUARDRAILS, protected_player_ids=frozenset(protected))
    sells = (
        []
        if (
            trades_blocked(trades_used, trade_limit, count_saturated=usage.count_saturated)
            or (custom_rules_active and agent_rules.allow_sells == "none")
        )
        else [
            proposal
            for proposal in propose_sells(
                squad_cands,
                lg,
                sell_guardrails,
                list_slots=DEFAULT_GUARDRAILS.daily_list_cap,
                bench_ids=li_bench_ids,
                showcase_bench=True,
                showcase_exclude_ids=showcase_excluded,
            )
            if str(proposal.action.player_id) not in protected_lineup
            and str(proposal.action.player_id) not in protected
            and not (
                proposal.rule_id == "sell_low_score"
                and str(proposal.action.player_id) in squad_by_id
                and is_verified_starter(
                    str(proposal.action.player_id),
                    squad_by_id[str(proposal.action.player_id)].lineup_prob,
                    li_status,
                    squad_by_id[str(proposal.action.player_id)].fitness_status,
                )
                and float(
                    projections.get(str(proposal.action.player_id), {}).get("horizon_points")
                    or 0.0
                )
                >= 160.0
            )
            and not (
                custom_rules_active
                and agent_rules.allow_sells == "bench_only"
                and str(proposal.action.player_id) in squad_by_id
                and is_verified_starter(
                    str(proposal.action.player_id),
                    squad_by_id[str(proposal.action.player_id)].lineup_prob,
                    li_status,
                    squad_by_id[str(proposal.action.player_id)].fitness_status,
                )
            )
        ]
    )
    # Konto-Deckung aus Nicht-Startern (Hebel C): Wenn das freie Geld unter dem
    # Anpfiff-Puffer liegt und Kader > 11 fitte Nicht-Starter (KB 3–5 / LI-Bank)
    # außerhalb verifizierter Stammspieler enthält, baut die Engine genau so viele
    # Nicht-Starter per Sofortverkauf ab, bis das Defizit gedeckt ist und weiterhin
    # eine vollständige 11er-Formation besetzbar bleibt.
    if (
        not trades_blocked(trades_used, trade_limit, count_saturated=usage.count_saturated)
        and not (
            custom_rules_active
            and (agent_rules.allow_sells == "none" or not agent_rules.allow_friday_rescue)
        )
        and cash_frei - KICKOFF_MIN_BALANCE < 0
    ):
        deficit_left = -(cash_frei - KICKOFF_MIN_BALANCE)
        existing_sell_ids = (
            {str(p.action.player_id) for p in sells if p.action.action_type == "sell"}
            | set(new_swaps.values())
            | set(swap_links.values())
        )
        kept_ids = {
            p.id for p in squad_players
            if p.id not in trade_ids and p.id not in existing_sell_ids
        }
        deficit_pool = [
            p for p in squad_players
            if p.id in kept_ids
            and p.id not in DEFAULT_GUARDRAILS.all_protected_ids
            and p.id not in protected
            and int(p.market_value) > 0
            and p.fitness_status == 0
            and li_status.get(p.id) is not Status.OUT
            and not is_verified_starter(p.id, p.lineup_prob, li_status, p.fitness_status)
        ]
        deficit_pool.sort(
            key=lambda p: (
                0 if li_status.get(p.id) is Status.BENCH else 1,
                -int(p.lineup_prob or 5),
                float(projections.get(p.id, {}).get("horizon_points") or 0.0),
                -int(p.market_value),
            )
        )
        from itertools import combinations

        max_sells = max(0, len(kept_ids) - 11)
        best_combo: tuple = ()
        best_key: tuple | None = None
        for r in range(1, min(max_sells, len(deficit_pool)) + 1):
            for combo in combinations(deficit_pool, r):
                if r > 1 and any(
                    sum(int(x.market_value) for x in combo if x is not dropped) >= deficit_left
                    for dropped in combo
                ):
                    continue
                drop_ids = {p.id for p in combo}
                rem_options = [
                    o for o in options
                    if o.owned and o.player_id in kept_ids and o.player_id not in drop_ids
                ]
                check_plan = optimize_team(
                    rem_options, cash_available=0, club_counts=club_counts, max_per_club=max_club
                )
                if check_plan.filled_slots < min(11, baseline.filled_slots):
                    continue
                total_mv = sum(int(p.market_value) for p in combo)
                covers = total_mv >= deficit_left
                key = (
                    0 if covers else 1,
                    0 if covers else -total_mv,
                    len(combo),
                    sum(0 if li_status.get(p.id) is Status.BENCH else 1 for p in combo),
                    sum(
                        float(projections.get(p.id, {}).get("horizon_points") or 0.0)
                        for p in combo
                    ),
                    -sum(int(p.lineup_prob or 5) for p in combo),
                    sum(int(p.mv_change_24h or 0) for p in combo),
                    -total_mv,
                )
                if best_key is None or key < best_key:
                    best_key = key
                    best_combo = combo
        for p in best_combo:
            cand = fallers.get(p.id)
            score = cand.total_score if cand is not None else 0.0
            p_name = cand.name if cand is not None else f"{p.first_name} {p.last_name}".strip()
            mv = int(p.market_value)
            kept_ids.remove(p.id)
            deficit_left -= mv
            sells = [
                sp for sp in sells
                if not (sp.action.action_type == "list" and str(sp.action.player_id) == p.id)
            ]
            sells.append(
                Proposal(
                    PlannedAction("sell", lg, player_id=p.id, amount=mv),
                    DEFICIT_COVER_RULE,
                    (
                        f"Konto-Deckung ({f'{cash_frei:,}'.replace(',', '.')} €): "
                        f"Nicht-Starter {p_name} (KB-Stufe {p.lineup_prob}, "
                        f"MW {f'{mv:,}'.replace(',', '.')} €) abbauen, um Kaufkraft "
                        "und Anpfiff-Deckung ohne Starter-Notverkauf herzustellen."
                    ),
                    score,
                    mv >= DEFAULT_GUARDRAILS.approval_threshold,
                    player_name=p_name,
                )
            )
    for target, sold in sorted(new_swaps.items()):
        notes.append(
            f"Tausch auf Planungs-Dispo bis {PLANNING_OVERDRAFT_UNTIL:%d.%m.%Y}: "
            f"{candidates[target].name} für {fallers[sold].name}; Verkauf wartet auf den "
            f"Zuschlag, angerechnet {new_swap_credit[target]:,} €.".replace(",", ".")
        )
    curation_notes = [*notes, *result.notes, *recovery["notes"]]
    if attempts >= 16:
        curation_notes.append(
            "Detailabruf-Budget erreicht; ungeprüfte neue Ziele nicht vorgeschlagen."
        )
    roster_value_notes: list[str] = []
    need_positions = set(baseline.missing_positions)
    purchase_reviews = reviews
    market_cands = [candidates[c.player_id] for c in market_cands]
    for review in reviews.values():
        if not review.get("selected") and not review["reasons"]:
            review["eligible"] = False
            review["reasons"] = ["Keine Verbesserung des besten finanzierbaren Gesamtpakets."]
    league_name = next((lgue.name for lgue in auth.leagues if lgue.id == lg), "")
    return Plan(
        league_id=lg,
        budget=budget,
        buys=buys,
        sells=sells,
        squad=squad,
        user_id=auth.user_id,
        league_name=league_name,
        profiteurs=profiteurs,
        trades_used=trades_used,
        trades_buys=usage.buys,
        trades_sells=usage.sells,
        trade_limit=trade_limit,
        trade_count_saturated=usage.count_saturated,
        market_candidates=market_cands,
        need_positions=need_positions,
        plan_base=plan_base,
        momentum_reserve_eur=reserve,
        matchday_deadline_at=deadline_pair[1] if deadline_pair else None,
        risk_budget_eur=risk_budget,
        pledge_dropped=pledge_dropped,
        li_bench_ids=frozenset(li_bench_ids) if li_bench_ids is not None else None,
        swaps=swaps,
        roster_value_notes=roster_value_notes,
        purchase_reviews=purchase_reviews,
        curation_notes=curation_notes,
        team_plan=team_plan,
        projections=projections,
        trade_player_ids=trade_ids,
        trade_bound=dict(firm_trade_bids),
    )


def _line(p: Proposal) -> str:
    flag = "  🔐 Approval" if p.requires_approval else ""
    return f"  • [{p.rule_id}] {p.rationale}{flag}"


def plan_command(
    league_id: str | None = typer.Option(None, "--league", "-l", help="Liga-ID"),
) -> None:
    plan = asyncio.run(build_plan(league_id))
    typer.echo(f"📋 Plan für Liga {plan.league_id} — Budget {plan.budget / 1_000_000:.1f}M €\n")
    typer.echo(f"KÄUFE ({len(plan.buys)}):")
    for p in plan.buys:
        typer.echo(_line(p))
    if not plan.buys:
        typer.echo("  (keine)")
    typer.echo(f"\nVERKÄUFE ({len(plan.sells)}):")
    for p in plan.sells:
        typer.echo(_line(p))
    if not plan.sells:
        typer.echo("  (keine)")
    typer.echo("\n(Dry-Run — es wurde nichts ausgeführt.)")
