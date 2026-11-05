"""GET /api/leagues/{league_id}/intel/candidates — ranked explosion candidates."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from kickbase_agent.config import get_settings
from kickbase_agent.intel.collector import collect_candidates
from kickbase_agent.intel.history import load_history, load_performance
from kickbase_agent.intel.stories import StoryWriter
from kickbase_agent.kickbase.client import KickbaseClient
from kickbase_agent.server.deps import db_session, kickbase_client
from kickbase_agent.server.routes.squad import POSITION_LABELS
from kickbase_agent.server.schemas import (
    ExplosionCandidateOut,
    PlayerStoryOut,
    SignalOut,
)

router = APIRouter()


@router.get(
    "/leagues/{league_id}/intel/candidates",
    response_model=list[ExplosionCandidateOut],
)
async def get_candidates(
    league_id: str,
    client: KickbaseClient = Depends(kickbase_client),
    session: Session = Depends(db_session),
) -> list[ExplosionCandidateOut]:
    squad = await client.get_squad(league_id=league_id)
    fixtures = await client.get_matchdays()
    team_names = await client.get_team_names()

    player_ids = [sp.player.id for sp in squad]
    mv_history_by_player = load_history(session, player_ids)
    perf_by_player = load_performance(session, player_ids)

    candidates = await collect_candidates(
        squad_players=squad,
        fixtures=fixtures,
        team_names=team_names,
        mv_history_by_player=mv_history_by_player,
        perf_by_player=perf_by_player,
    )

    return [
        ExplosionCandidateOut(
            player_id=c.player_id,
            name=c.name,
            team_id=c.team_id,
            position=c.position,
            position_label=POSITION_LABELS[c.position],
            current_market_value=c.current_market_value,
            total_score=c.total_score,
            signals=[
                SignalOut(name=s.name, score=s.score, weight=s.weight, reason=s.reason)
                for s in c.signals
            ],
        )
        for c in candidates
    ]


@router.get(
    "/leagues/{league_id}/players/{player_id}/story",
    response_model=PlayerStoryOut,
)
async def get_player_story(
    league_id: str,
    player_id: str,
    client: KickbaseClient = Depends(kickbase_client),
    session: Session = Depends(db_session),
) -> PlayerStoryOut:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="Claude API key not configured")

    candidates = await get_candidates(
        league_id=league_id, client=client, session=session
    )
    target = next((c for c in candidates if c.player_id == player_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Player not found in this league")

    # Rebuild the domain object for StoryWriter
    from kickbase_agent.intel.models import ExplosionCandidate, Signal

    domain = ExplosionCandidate(
        player_id=target.player_id,
        name=target.name,
        team_id=target.team_id,
        position=target.position,
        current_market_value=target.current_market_value,
        total_score=target.total_score,
        signals=[
            Signal(name=s.name, score=s.score, weight=s.weight, reason=s.reason)
            for s in target.signals
        ],
    )

    writer = StoryWriter(api_key=settings.anthropic_api_key)
    story = await writer.for_candidate(domain)
    return PlayerStoryOut(
        player_id=story.player_id,
        headline=story.headline,
        narrative=story.narrative,
        confidence=story.confidence,
    )
