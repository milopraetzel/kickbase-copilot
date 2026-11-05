"""Pydantic models for news items."""

from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NewsArticle(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    url: str
    title: str
    body: str
    published_at: datetime

    def content_hash(self) -> str:
        blob = f"{self.source}|{self.title}|{self.url}".encode()
        return hashlib.sha256(blob).hexdigest()[:32]


class Match(BaseModel):
    model_config = ConfigDict(frozen=True)

    match_id: int
    matchday: int
    home_team: str
    away_team: str
    kickoff: datetime
    home_score: int | None = None
    away_score: int | None = None
    is_finished: bool = False
