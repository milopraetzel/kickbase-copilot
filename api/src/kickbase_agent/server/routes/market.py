"""GET /api/leagues/{league_id}/market — offene Transfermarkt-Angebote."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from kickbase_agent.kickbase.client import KickbaseClient
from kickbase_agent.server.deps import kickbase_client
from kickbase_agent.server.routes.squad import POSITION_LABELS
from kickbase_agent.server.schemas import MarketOut, MarketPlayerOut, PlayerOut

router = APIRouter()


@router.get("/leagues/{league_id}/market", response_model=MarketOut)
async def get_market(
    league_id: str,
    client: KickbaseClient = Depends(kickbase_client),
) -> MarketOut:
    market = await client.get_market(league_id=league_id)
    return MarketOut(
        league_id=league_id,
        players=[
            MarketPlayerOut(
                player=PlayerOut(
                    id=mp.player.id,
                    first_name=mp.player.first_name,
                    last_name=mp.player.last_name,
                    position=int(mp.player.position),
                    position_label=POSITION_LABELS[int(mp.player.position)],
                    team_id=mp.player.team_id,
                    market_value=mp.player.market_value,
                    market_value_trend=mp.player.market_value_trend,
                    points=mp.player.points,
                ),
                price=mp.price,
                expires_at=mp.expires_at,
            )
            for mp in sorted(market, key=lambda m: m.expires_at)
        ],
    )
