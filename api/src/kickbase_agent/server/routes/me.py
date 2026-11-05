"""GET /api/me — current user + leagues (mit echtem Kontostand)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from kickbase_agent.kickbase.client import KickbaseClient
from kickbase_agent.server.auth import TokenCache, get_token_cache
from kickbase_agent.server.deps import kickbase_client
from kickbase_agent.server.schemas import LeagueOut, MeOut

router = APIRouter()


@router.get("/me", response_model=MeOut)
async def get_me(
    cache: TokenCache = Depends(get_token_cache),
    client: KickbaseClient = Depends(kickbase_client),
) -> MeOut:
    user_id = await cache.get_user_id()
    leagues = await cache.get_leagues()
    out: list[LeagueOut] = []
    for lg in leagues:
        try:
            budget = await client.get_budget(lg.id)
        except Exception:
            budget = lg.budget  # Fallback: selection-Wert, falls Budget-Endpoint hakt
        out.append(LeagueOut(id=lg.id, name=lg.name, budget=budget))
    return MeOut(user_id=user_id, leagues=out)
