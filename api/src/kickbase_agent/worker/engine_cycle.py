"""Ereignisabhängiger Planungszyklus über den bestehenden Vorhaben-Executor."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from kickbase_agent.db.models import CommunityTip, Intent, NewsItem, Report
from kickbase_agent.db.session import SessionLocal
from kickbase_agent.kickbase.factory import make_kickbase_client

_cycle_lock = asyncio.Lock()
MAX_PLAN_AGE = timedelta(hours=1)


def record_team_plan(session, plan, now: datetime) -> None:
    """Den tatsächlich verwendeten Zustand zur Entscheidung aufbewahren."""
    team_plan = getattr(plan, "team_plan", None)
    if not team_plan:
        return
    # JSON roundtrip normalisiert Tupel/Zeiten, ohne Geheimnisse aus Settings zu lesen.
    payload = json.loads(json.dumps({
        "league_id": plan.league_id, "team_plan": team_plan,
        "purchase_reviews": plan.purchase_reviews, "budget": plan.budget,
        "owned_ids": [sp.player.id for sp in plan.squad],
        "deadline": plan.matchday_deadline_at,
    }, default=lambda value: value.isoformat()))
    session.add(Report(
        kind="engine_decision", created_at=now, source="season-engine-v1",
        title="Finanzierter Mannschaftsplan",
        body="\n".join(team_plan.get("notes") or ["Datierter Entscheidungszustand gespeichert."]),
        meta=payload,
    ))


def state_fingerprint(*, league_id, budget, squad, market, bids, news, tips, now,
                      intents=()) -> str:
    """Relevante Zustandswechsel; verstrichene Zeit nur als Auktionsphase."""
    data = {
        "league": league_id, "budget": budget,
        "squad": sorted((sp.player.id, sp.player.market_value, sp.player.fitness_status,
                         sp.player.lineup_prob, sp.lineup_order) for sp in squad),
        "market": sorted((mp.player.id, mp.price, mp.player.market_value,
                          mp.player.fitness_status, mp.player.lineup_prob, mp.seller_id,
                          int(mp.expires_at.timestamp()) // 300,
                          "closing" if mp.expires_at - now <= timedelta(hours=2) else "open")
                         for mp in market),
        "bids": sorted((str(b.get("player_id")), int(b.get("amount") or 0)) for b in bids),
        "news": sorted(news), "tips": sorted(tips),
        "intents": sorted(intents),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


async def run_engine_cycle(channel=None) -> str:
    """Bei Zuschlag, Verlust, Preis-/Rollensignal oder Stundenwechsel neu planen.

    Liest Zustand, legt über den bestehenden Planner Vorhaben an, führt selbst
    keine Trades aus. Fehlgeschlagene Planung erhält keinen erfolgreichen Checkpoint.
    """
    if _cycle_lock.locked():
        return "already_running"
    async with _cycle_lock:
        now = datetime.now(UTC)
        client, auth = await make_kickbase_client()
        lg = auth.league_id
        if not lg:
            return "no_league"
        squad = await client.get_squad(lg)
        market = await client.get_market(lg)
        budget = await client.get_budget(lg)
        bids = await client.get_own_bids(lg, auth.user_id)
        with SessionLocal() as session:
            news = list(session.scalars(select(NewsItem.id).order_by(
                NewsItem.published_at.desc()).limit(20)))
            tips = [(row.id, row.weight, row.note) for row in session.scalars(
                select(CommunityTip).order_by(CommunityTip.created_at.desc()).limit(20))]
            # Lokale Entscheidungen sind Ereignisse, auch bevor Kickbase sich
            # ändert. Erneuerte Quellen/Review-TTL erzeugen dagegen keinen Loop.
            intent_states = [
                (row.id, row.kind, row.player_id, row.status, row.amount, row.adjusted_from,
                 row.veto_requested_at is not None)
                for row in session.scalars(select(Intent).where(
                    (Intent.status.in_(("planned", "executing")))
                    | (Intent.updated_at >= now - timedelta(days=2))
                ))
                if str((row.sources or {}).get("league_id", lg)) == str(lg)
            ]
            prior_rows = session.scalars(select(Report).where(Report.kind == "engine_state")
                                         .order_by(Report.created_at.desc()).limit(20)).all()
            prior = next((r for r in prior_rows if (r.meta or {}).get("league_id") == lg), None)
            fingerprint = state_fingerprint(
                league_id=lg, budget=budget, squad=squad, market=market, bids=bids,
                news=news, tips=tips, now=now, intents=intent_states,
            )
            prior_at = prior.created_at if prior else None
            if prior_at is not None and prior_at.tzinfo is None:
                prior_at = prior_at.replace(tzinfo=UTC)
            if (prior is not None and prior_at is not None and now - prior_at < MAX_PLAN_AGE
                    and prior.meta.get("fingerprint") == fingerprint):
                return "unchanged"
        from kickbase_agent.worker.intents import run_intent_planner

        result = await run_intent_planner(channel)
        with SessionLocal() as session:
            session.add(Report(
                kind="engine_state", created_at=now, source="season-engine-v1",
                title="Mannschaft neu geplant", body=result,
                meta={"league_id": lg, "fingerprint": fingerprint},
            ))
            session.commit()
        return result
