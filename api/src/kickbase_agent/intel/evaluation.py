"""Treffer-Bilanz der Engine — Lern-Schicht der Steuerzentrale (Messung, keine Meinung).

Emils Frage (23.08.): „Verbessert sich die KI anhand der Ergebnisse?" Ehrliche Lage:
regelbasiertes Scoring mit festen Gewichten; gelernt wird bisher manuell (LEARNINGS,
Parameter-Änderungen). Dieses Modul liefert die MESSUNG dazu — aus der lokalen DB,
ohne Netz, ohne Writes, robust bei leeren Tabellen:

- ``buy_hits`` / ``shadow`` / ``executed``: Kaufvorschläge der Engine (bestätigte UND
  Schatten-Gebote aus ``transfer_actions`` + Vorhaben ``kind="bid"``) gegen den
  Marktwert-Verlauf in ``player_history``: MW am Vorschlagstag vs. MW 1 bzw. 7
  Tageswerte später; Treffer = MW gestiegen. ``shadow`` = nicht erworbene
  Vorschläge („hätte gekauft"), ``executed`` = nur bestätigte Erwerbe.
- ``tips``: Community-/Recherche-Tipps gegen den MW 7 Tageswerte später
  (Kauf-Tipp: Treffer = gestiegen; Meide-Tipp mit negativem Gewicht: gefallen).
- ``flips``: bestätigte Erwerbe (Reconcile-Ergebnis ``won``) — Kaufpreis vs. letzter MW.
- ``lineup``: ehrlicher Platzhalter (ab SP1 messbar).
- ``coverage``: wie viele Vorschläge überhaupt Folgedaten haben.

Zeitanker: Kickbase aktualisiert Marktwerte täglich um 22:00 Berlin; der Snapshot-Job
(22:05) schreibt je Tag EINE player_history-Zeile (recorded_at = Mitternacht UTC) mit dem
Wert NACH dem Update. Der „Vorschlagstag" eines Zeitstempels ist deshalb der Tag des
letzten 22:00-Updates davor (vor 22:00 Berlin => Vortag): Basis = dieser Tageswert,
„1d" = der nächste Tageswert, „7d" = der siebte. Fehlt der Basiswert des Vorschlagstags
oder der Folgewert (nächster verfügbarer Tageswert, höchstens ``FOLLOWUP_GRACE_DAYS`` später),
zählt der Vorschlag nicht — lieber kleines n als geschönte Zahlen.

Dedupe: derselbe Spieler am selben Vorschlagstag zählt EINMAL, auch wenn ihn mehrere
Pfade (Schatten-Scan, Auto-Trading, Planner, MCP) vorgeschlagen haben — eine Idee,
eine Messung. Ein echtes Gebot schlägt dabei die Schatten-Einträge desselben Tages.
"""

from __future__ import annotations

import bisect
import statistics
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import CommunityTip, Intent, Player, PlayerHistory, TransferAction
from kickbase_agent.scouts.ligainsider.matcher import normalize

BERLIN = ZoneInfo("Europe/Berlin")

#: Stunde (Berlin) des täglichen Kickbase-Marktwert-Updates.
MV_UPDATE_HOUR = 22
#: Mess-Horizonte in Tageswerten.
HORIZONS: tuple[int, ...] = (1, 7)
#: Basiswert: NUR der Tageswert des Vorschlagstags selbst (0 = keine Toleranz). Ein älterer
#: Wert wäre der MW VOR dem 22:00-Update, das die Engine schon gesehen hat — er würde die
#: Deltas genau bei „Explosions"-Kandidaten systematisch schönen. Fehlt der Tag => zählt nicht.
BASELINE_GRACE_DAYS = 0
#: Folgewert: der nächste verfügbare Tageswert, höchstens n Tage nach dem Zielhorizont.
FOLLOWUP_GRACE_DAYS = 2

#: rule_id-Präfixe, die als Kaufvorschlag der Engine gelten (alle Pfade).
BUY_RULE_PREFIXES: tuple[str, ...] = (
    "buy_explosion", "auto-trading-buy", "intent-bid", "mcp-bid", "trading_buy",
)
#: transfer_actions-Status, die „nicht ausgeführt" bedeuten (Schatten).
SHADOW_ACTION_STATUSES: frozenset[str] = frozenset(
    {"proposed", "skipped_dry_run", "refused", "expired", "rejected", "failed", "noop"}
)
#: Intent-Status, die „nicht ausgeführt" bedeuten (Schatten).
SHADOW_INTENT_STATUSES: frozenset[str] = frozenset({"vetoed", "expired", "failed", "superseded"})

#: Höchstens so viele (jüngste) Tipps werden bewertet.
TIPS_LIMIT = 200
#: Ab so vielen Spielern wird die Historie komplett statt per IN-Liste geladen.
_IN_LIST_MAX = 500

LINEUP_PLACEHOLDER: dict[str, Any] = {"status": "ab SP1 (28.08.) messbar", "n": 0}

FLIPS_NOTE = (
    "Nur ein durch Kader-Reconcile bestätigter Zuschlag (outcome.result = won); "
    "unbestätigte, verlorene oder stornierte Gebote zählen nicht als Erwerb."
)


# ------------------------------------------------------------------ Zeit & Serien


def as_utc(dt: datetime) -> datetime:
    """SQLite liefert naive Zeitstempel zurück — als UTC interpretieren."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def anchor_day(stamp: datetime) -> date:
    """Vorschlagstag = Tag des letzten 22:00-Berlin-Marktwert-Updates vor ``stamp``.

    Vor 22:00 Berlin gilt noch der Wert des Vortags (Snapshot 22:05 = Wert NACH dem
    Update des jeweiligen Tages), ab 22:00 der des heutigen Tages.
    """
    local = as_utc(stamp).astimezone(BERLIN)
    if local.hour >= MV_UPDATE_HOUR:
        return local.date()
    return local.date() - timedelta(days=1)


@dataclass
class MvSeries:
    """Tageswerte eines Spielers, aufsteigend nach Tag (ein Wert je Tag)."""

    days: list[date] = field(default_factory=list)
    values: list[int] = field(default_factory=list)

    @classmethod
    def from_rows(cls, rows: list[tuple[date, int]]) -> MvSeries:
        by_day: dict[date, int] = {}
        for day, value in rows:  # spätere Zeilen überschreiben (Reihenfolge = id)
            by_day[day] = int(value)
        days = sorted(by_day)
        return cls(days=days, values=[by_day[d] for d in days])

    def __len__(self) -> int:
        return len(self.days)

    def on_or_before(self, day: date, grace_days: int = BASELINE_GRACE_DAYS) -> int | None:
        """Letzter Tageswert mit ``day - grace <= Tag <= day`` — sonst None."""
        idx = bisect.bisect_right(self.days, day) - 1
        if idx < 0:
            return None
        if (day - self.days[idx]).days > grace_days:
            return None
        return self.values[idx]

    def on_or_after(self, day: date, grace_days: int = FOLLOWUP_GRACE_DAYS) -> int | None:
        """Erster Tageswert mit ``day <= Tag <= day + grace`` — sonst None."""
        idx = bisect.bisect_left(self.days, day)
        if idx >= len(self.days):
            return None
        if (self.days[idx] - day).days > grace_days:
            return None
        return self.values[idx]

    def latest(self) -> int | None:
        return self.values[-1] if self.values else None


def delta_pct(base: int | None, later: int | None) -> float | None:
    """Prozentuale Veränderung base -> later (2 Nachkommastellen); None wenn nicht messbar."""
    if base is None or later is None or base <= 0:
        return None
    return round((later - base) / base * 100.0, 2)


def summarize(deltas: list[float]) -> dict[str, Any]:
    """{"n", "hit_rate" (0..1), "avg_delta_pct", "median_delta_pct"} — leer => None.
    Treffer = MW gestiegen (delta > 0)."""
    n = len(deltas)
    if n == 0:
        return {"n": 0, "hit_rate": None, "avg_delta_pct": None, "median_delta_pct": None}
    hits = sum(1 for d in deltas if d > 0)
    return {
        "n": n,
        "hit_rate": round(hits / n, 3),
        "avg_delta_pct": round(statistics.fmean(deltas), 2),
        "median_delta_pct": round(statistics.median(deltas), 2),
    }


# ------------------------------------------------------------------ Vorschläge


@dataclass
class BuyProposal:
    """Ein Kaufvorschlag (dedupliziert je Spieler + Vorschlagstag)."""

    player_id: str
    anchor: date
    bucket: str  # executed | shadow | open
    sources: set[str] = field(default_factory=set)
    rows: int = 0


_BUCKET_RANK = {"executed": 2, "shadow": 1, "open": 0}


def is_buy_rule(rule_id: str | None) -> bool:
    rid = rule_id or ""
    return any(rid.startswith(p) for p in BUY_RULE_PREFIXES)


def outcome_result(action: TransferAction | None) -> str:
    """Persistiertes Ergebnis des Kader-Reconciles, falls vorhanden."""
    if action is None or not isinstance(action.payload, dict):
        return ""
    expectation = action.payload.get("expectation")
    outcome = action.payload.get("outcome")
    if not isinstance(expectation, dict) or not expectation or not isinstance(outcome, dict):
        return ""
    at = outcome.get("at")
    if not isinstance(at, str):
        return ""
    try:
        datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return str(outcome.get("result") or "")


def verified_acquisition(action: TransferAction | None) -> bool:
    """Nur ein strukturierter ``won``-Read-back beweist einen Erwerb."""
    return bool(
        action is not None
        and action.action_type == "bid"
        and action.status == "executed"
        and not bool(action.dry_run)
        and outcome_result(action) == "won"
    )


def action_offer_id(action: TransferAction | None) -> str:
    """Stabile Auktions-ID einer Aktion; ohne sie wird kein Storno zugeordnet."""
    if action is None or not isinstance(action.payload, dict):
        return ""
    return str(action.payload.get("offer_id") or "").strip()


def is_exact_cancellation(bid: TransferAction, cancel: TransferAction) -> bool:
    """Ordnet ein echtes Storno ausschließlich derselben Kickbase-Auktion zu."""
    offer_id = action_offer_id(bid)
    return bool(
        offer_id
        and cancel.action_type == "cancel_bid"
        and cancel.status == "executed"
        and not bool(cancel.dry_run)
        and str(cancel.league_id or "") == str(bid.league_id or "")
        and str(cancel.player_id or "") == str(bid.player_id or "")
        and action_offer_id(cancel) == offer_id
        and as_utc(cancel.created_at) >= as_utc(bid.created_at)
    )


def action_bucket(action: TransferAction, *, cancelled: bool = False) -> str:
    """executed | shadow | open (pending/unknown = noch offen, zählt nur in buy_hits)."""
    if bool(action.dry_run):
        return "shadow"
    if cancelled:
        return "shadow"
    if verified_acquisition(action):
        return "executed"
    if action.status == "executed":
        if outcome_result(action) in {"lost", "cancelled"}:
            return "shadow"
        return "open"
    if action.status in SHADOW_ACTION_STATUSES:
        return "shadow"
    return "open"


def intent_bucket(
    intent: Intent,
    action: TransferAction | None = None,
    *,
    cancelled: bool = False,
) -> str:
    if bool(action and action.dry_run):
        return "shadow"
    if cancelled or outcome_result(action) in {"lost", "cancelled"}:
        return "shadow"
    if verified_acquisition(action):
        return "executed"
    if intent.status == "executed":
        return "open"
    if intent.status in SHADOW_INTENT_STATUSES:
        return "shadow"
    return "open"  # planned / executing


def _merge(proposals: dict[tuple[str, date], BuyProposal], key: tuple[str, date],
           bucket: str, source: str) -> None:
    p = proposals.get(key)
    if p is None:
        proposals[key] = BuyProposal(
            player_id=key[0], anchor=key[1], bucket=bucket, sources={source}, rows=1
        )
        return
    p.rows += 1
    p.sources.add(source)
    if _BUCKET_RANK[bucket] > _BUCKET_RANK[p.bucket]:
        p.bucket = bucket


def collect_proposals(session: Session) -> tuple[list[BuyProposal], int]:
    """Alle Kaufvorschläge (transfer_actions bid mit Engine-rule_id + Intents kind=bid),
    dedupliziert je (Spieler, Vorschlagstag). Gibt (Vorschläge, rohe Zeilenzahl) zurück.

    Aktionen, die ein Vorhaben per ``executed_action_id`` referenziert, stecken im
    Vorhaben und zählen nicht doppelt.
    """
    intents = list(
        session.execute(select(Intent).where(Intent.kind == "bid")).scalars().all()
    )
    all_actions = list(
        session.execute(
            select(TransferAction).where(
                TransferAction.action_type.in_(("bid", "cancel_bid"))
            )
        ).scalars().all()
    )
    actions = [a for a in all_actions if a.action_type == "bid"]
    by_id = {a.id: a for a in actions}
    linked_ids = {
        a.id
        for i in intents
        if i.executed_action_id is not None
        if (a := by_id.get(i.executed_action_id)) is not None
        and str(a.player_id or "") == str(i.player_id or "")
    }
    cancellations = [
        a
        for a in all_actions
        if a.action_type == "cancel_bid" and a.status == "executed" and not bool(a.dry_run)
    ]

    def cancelled_after(action: TransferAction | None) -> bool:
        if action is None:
            return False
        return any(is_exact_cancellation(action, c) for c in cancellations)
    proposals: dict[tuple[str, date], BuyProposal] = {}
    rows = 0
    for a in actions:
        if a.id in linked_ids or not a.player_id or not is_buy_rule(a.rule_id):
            continue
        rows += 1
        _merge(proposals, (str(a.player_id), anchor_day(a.created_at)),
               action_bucket(a, cancelled=cancelled_after(a)), a.rule_id or "bid")
    for i in intents:
        if not i.player_id:
            continue
        rows += 1
        action = by_id.get(i.executed_action_id) if i.executed_action_id is not None else None
        if action is not None and str(action.player_id or "") != str(i.player_id):
            action = None
        proposal_at = action.created_at if action is not None else i.created_at
        _merge(proposals, (str(i.player_id), anchor_day(proposal_at)),
               intent_bucket(i, action, cancelled=cancelled_after(action)), "intent-bid")
    ordered = sorted(proposals.values(), key=lambda p: (p.anchor, p.player_id))
    return ordered, rows


# ------------------------------------------------------------------ Historie & Namen


def load_series(session: Session, player_ids: set[str]) -> dict[str, MvSeries]:
    """player_id -> MvSeries (Tag = UTC-Datum von recorded_at)."""
    if not player_ids:
        return {}
    stmt = select(
        PlayerHistory.player_id, PlayerHistory.recorded_at, PlayerHistory.market_value
    ).order_by(PlayerHistory.player_id, PlayerHistory.recorded_at, PlayerHistory.id)
    if len(player_ids) <= _IN_LIST_MAX:
        stmt = stmt.where(PlayerHistory.player_id.in_(sorted(player_ids)))
    raw: dict[str, list[tuple[date, int]]] = {}
    for pid, recorded_at, mv in session.execute(stmt).all():
        if pid not in player_ids or recorded_at is None or mv is None:
            continue
        raw.setdefault(str(pid), []).append((as_utc(recorded_at).date(), int(mv)))
    return {pid: MvSeries.from_rows(rows) for pid, rows in raw.items()}


def load_player_names(session: Session, player_ids: set[str]) -> dict[str, str]:
    if not player_ids:
        return {}
    stmt = select(Player.id, Player.name)
    if len(player_ids) <= _IN_LIST_MAX:
        stmt = stmt.where(Player.id.in_(sorted(player_ids)))
    return {str(pid): name for pid, name in session.execute(stmt).all() if pid in player_ids}


def match_player(tip_name: str, players: list[tuple[str, str]]) -> tuple[str, str] | None:
    """Tipp-Name -> (player_id, name) aus ``players`` (id, name): ohne Groß/Klein und
    Akzente; exakter Name vor Token-Teilmenge („Füllkrug" in „Niclas Füllkrug") vor
    Teilstring; bei Gleichstand der erste (Liste ist nach Name sortiert). None = kein Treffer.
    """
    needle = normalize(tip_name)
    if not needle:
        return None
    needle_tokens = set(needle.split())
    best: tuple[int, str, str] | None = None
    for pid, name in players:
        hay = normalize(name)
        if not hay:
            continue
        if hay == needle:
            rank = 3
        elif needle_tokens <= set(hay.split()):
            rank = 2
        elif needle in hay or hay in needle:
            rank = 1
        else:
            continue
        if best is None or rank > best[0]:
            best = (rank, pid, name)
    return None if best is None else (best[1], best[2])


# ------------------------------------------------------------------ Auswertung


def _bucket_stats(outcomes: list[dict[str, Any]], bucket: str | None) -> dict[str, Any]:
    picked = outcomes if bucket is None else [o for o in outcomes if o["bucket"] == bucket]
    return {
        f"{h}d": summarize([o["deltas"][h] for o in picked if o["deltas"][h] is not None])
        for h in HORIZONS
    }


def _proposal_outcomes(
    proposals: list[BuyProposal], series: dict[str, MvSeries]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in proposals:
        s = series.get(p.player_id)
        base = s.on_or_before(p.anchor) if s is not None else None
        deltas: dict[int, float | None] = {}
        for h in HORIZONS:
            later = s.on_or_after(p.anchor + timedelta(days=h)) if s is not None else None
            deltas[h] = delta_pct(base, later)
        out.append({"proposal": p, "bucket": p.bucket, "base": base, "deltas": deltas})
    return out


def _evaluate_tips(session: Session, series_cache: dict[str, MvSeries]) -> dict[str, Any]:
    tips = list(
        session.execute(
            select(CommunityTip).order_by(CommunityTip.created_at.desc(), CommunityTip.id.desc())
            .limit(TIPS_LIMIT)
        ).scalars().all()
    )
    if not tips:
        return {"n": 0, "hit_rate_7d": None, "avg_delta_pct_7d": None, "items": []}
    players = [
        (str(pid), name)
        for pid, name in session.execute(select(Player.id, Player.name).order_by(Player.name))
    ]
    matched: dict[int, tuple[str, str] | None] = {
        t.id: match_player(t.player_name, players) for t in tips
    }
    need = {m[0] for m in matched.values() if m is not None} - set(series_cache)
    series_cache.update(load_series(session, need))

    items: list[dict[str, Any]] = []
    scored: list[tuple[float, float]] = []  # (delta, weight)
    for t in tips:
        m = matched.get(t.id)
        delta: float | None = None
        hit: bool | None = None
        if m is not None:
            s = series_cache.get(m[0])
            if s is not None:
                anchor = anchor_day(t.created_at)
                base = s.on_or_before(anchor)
                later = s.on_or_after(anchor + timedelta(days=7))
                delta = delta_pct(base, later)
        weight = float(t.weight or 0.0)
        if delta is not None:
            hit = delta > 0 if weight >= 0 else delta < 0
            scored.append((delta, weight))
        items.append(
            {
                "player_name": t.player_name,
                "weight": weight,
                "source": t.source,
                "created_at": as_utc(t.created_at).isoformat(),
                "delta_pct_7d": delta,
                # additiv: aufgelöster Spieler + Treffer-Flag
                "player_id": m[0] if m is not None else None,
                "matched_name": m[1] if m is not None else None,
                "hit_7d": hit,
            }
        )
    hits = sum(1 for d, w in scored if (d > 0 if w >= 0 else d < 0))
    n = len(scored)
    return {
        "n": n,
        "hit_rate_7d": round(hits / n, 3) if n else None,
        "avg_delta_pct_7d": round(statistics.fmean(d for d, _ in scored), 2) if n else None,
        "items": items,
    }


def _evaluate_flips(
    session: Session, series_cache: dict[str, MvSeries]
) -> tuple[list[dict[str, Any]], int]:
    rows = list(
        session.execute(
            select(TransferAction)
            .where(
                TransferAction.action_type == "bid",
                TransferAction.status == "executed",
                TransferAction.dry_run.is_(False),
            )
            .order_by(TransferAction.created_at.desc(), TransferAction.id.desc())
        ).scalars().all()
    )
    cancellations = list(
        session.execute(
            select(TransferAction).where(
                TransferAction.action_type == "cancel_bid",
                TransferAction.status == "executed",
                TransferAction.dry_run.is_(False),
            )
        ).scalars().all()
    )
    rows = [
        r
        for r in rows
        if r.player_id
        and verified_acquisition(r)
        and not any(is_exact_cancellation(r, c) for c in cancellations)
    ]
    ids = {str(r.player_id) for r in rows}
    series_cache.update(load_series(session, ids - set(series_cache)))
    names = load_player_names(session, ids)
    flips: list[dict[str, Any]] = []
    total = 0
    for r in rows:
        pid = str(r.player_id)
        s = series_cache.get(pid)
        mv_now = s.latest() if s is not None else None
        buy_price = int(r.amount) if r.amount is not None else None
        d_eur = None if mv_now is None or buy_price is None else int(mv_now - buy_price)
        if d_eur is not None:
            total += d_eur
        flips.append(
            {
                "player_id": pid,
                "name": names.get(pid, pid),
                "buy_price": buy_price,
                "bought_at": as_utc(r.resolved_at or r.created_at).isoformat(),
                "mv_now": mv_now,
                "delta_eur": d_eur,
                "delta_pct": delta_pct(buy_price, mv_now),
                # additiv
                "rule_id": r.rule_id,
                "action_id": r.id,
            }
        )
    return flips, total


def evaluate(session: Session, now: datetime | None = None) -> dict[str, Any]:
    """Treffer-Bilanz (siehe Modul-Docstring). Reine Lese-Logik, keine Exceptions bei
    leeren Tabellen (dann n=0 / None überall)."""
    stamp = as_utc(now) if now is not None else datetime.now(UTC)
    proposals, rows_total = collect_proposals(session)
    series: dict[str, MvSeries] = load_series(session, {p.player_id for p in proposals})
    outcomes = _proposal_outcomes(proposals, series)
    tips = _evaluate_tips(session, series)
    flips, flips_total = _evaluate_flips(session, series)
    return {
        "generated_at": stamp.isoformat(),
        "buy_hits": _bucket_stats(outcomes, None),
        "shadow": _bucket_stats(outcomes, "shadow"),
        "executed": _bucket_stats(outcomes, "executed"),
        "tips": tips,
        "flips": flips,
        "flips_total_delta_eur": int(flips_total),
        "flips_note": FLIPS_NOTE,
        "lineup": dict(LINEUP_PLACEHOLDER),
        "coverage": {
            "proposals_total": len(proposals),
            "with_followup_1d": sum(1 for o in outcomes if o["deltas"][1] is not None),
            "with_followup_7d": sum(1 for o in outcomes if o["deltas"][7] is not None),
            # additiv: rohe Zeilen vor der Dedupe je (Spieler, Vorschlagstag)
            "rows_total": rows_total,
            "executed_total": sum(1 for p in proposals if p.bucket == "executed"),
            "shadow_total": sum(1 for p in proposals if p.bucket == "shadow"),
            "open_total": sum(1 for p in proposals if p.bucket == "open"),
        },
        "method": {
            "anchor": "Vorschlagstag = Tag des letzten 22:00-Berlin-MW-Updates vor dem "
                      "Zeitstempel; 1d/7d = 1./7. Tageswert danach",
            "hit": "MW gestiegen (delta_pct > 0); Meide-Tipps: MW gefallen",
            "baseline_grace_days": BASELINE_GRACE_DAYS,
            "followup_grace_days": FOLLOWUP_GRACE_DAYS,
            "buy_rules": list(BUY_RULE_PREFIXES),
            "dedupe": "je Spieler + Vorschlagstag einmal; executed schlägt shadow",
        },
    }
