"""FastAPI dependency providers."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends
from sqlalchemy.orm import Session

from kickbase_agent.db.session import SessionLocal
from kickbase_agent.kickbase.client import KickbaseClient
from kickbase_agent.server.auth import TokenCache, get_token_cache


def db_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


async def kickbase_client(
    cache: TokenCache = Depends(get_token_cache),
) -> KickbaseClient:
    token = await cache.get_token()
    return KickbaseClient(token=token, on_auth_failure=cache.refresh_token)
