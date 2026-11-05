"""GET /api/leagues/{league_id}/squad."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from kickbase_agent.kickbase.client import KickbaseClient
from kickbase_agent.server.deps import kickbase_client
from kickbase_agent.server.schemas import PlayerOut, SquadOut, SquadPlayerOut

router = APIRouter()

POSITION_LABELS = {1: "GK", 2: "DEF", 3: "MID", 4: "STR"}


@router.get("/leagues/{league_id}/squad", response_model=SquadOut)
async def get_squad(
    league_id: str,
    client: KickbaseClient = Depends(kickbase_client),
) -> SquadOut:
    squad = await client.get_squad(league_id=league_id)
    return SquadOut(
        league_id=league_id,
        players=[
            SquadPlayerOut(
                player=PlayerOut(
                    id=sp.player.id,
                    first_name=sp.player.first_name,
                    last_name=sp.player.last_name,
                    position=int(sp.player.position),
                    position_label=POSITION_LABELS[int(sp.player.position)],
                    team_id=sp.player.team_id,
                    market_value=sp.player.market_value,
                    market_value_trend=sp.player.market_value_trend,
                    points=sp.player.points,
                ),
                is_starter=sp.is_starter,
            )
            for sp in squad
        ],
    )
