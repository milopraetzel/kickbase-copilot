"""Claude-powered narrative for explosion candidates."""

from __future__ import annotations

import json

from anthropic import AsyncAnthropic

from kickbase_agent.intel.models import ExplosionCandidate, PlayerStory

SYSTEM_PROMPT = (
    "Du bist ein deutscher Fantasy-Football-Experte für Kickbase. "
    "Gib für jeden Spieler eine knackige Einschätzung in 2-3 Sätzen. "
    "Antwort AUSSCHLIESSLICH als JSON-Objekt mit den Feldern: "
    "headline (string, max 60 Zeichen), narrative (string, 2-3 Sätze), "
    "confidence (string: 'hoch', 'mittel', oder 'niedrig'). "
    "Keine Markdown-Formatierung, nur JSON."
)

MODEL = "claude-sonnet-4-6"


class StoryWriter:
    def __init__(self, api_key: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)

    async def for_candidate(self, candidate: ExplosionCandidate) -> PlayerStory:
        user_prompt = (
            f"Spieler: {candidate.name} (Position {candidate.position}, "
            f"Marktwert {candidate.current_market_value / 1_000_000:.1f}M €).\n"
            f"Signale:\n"
            + "\n".join(
                f"- {s.name.upper()}: score={s.score:.2f} | {s.reason}"
                for s in candidate.signals
            )
            + f"\n\nGesamt-Score: {candidate.total_score:.2f}"
        )

        response = await self._client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw = response.content[0].text.strip()
        data = json.loads(raw)
        return PlayerStory(
            player_id=candidate.player_id,
            headline=data["headline"],
            narrative=data["narrative"],
            confidence=data["confidence"],
        )
