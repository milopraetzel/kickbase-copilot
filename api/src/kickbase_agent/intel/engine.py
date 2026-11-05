"""Rule engine combining signals into a total score and ranking candidates."""

from __future__ import annotations

from kickbase_agent.intel.models import ExplosionCandidate, Signal


class RuleEngine:
    def score(self, signals: list[Signal]) -> float:
        if not signals:
            return 0.5
        total_weight = sum(s.weight for s in signals)
        if total_weight == 0:
            return 0.5
        return sum(s.score * s.weight for s in signals) / total_weight

    def rank(self, candidates: list[ExplosionCandidate]) -> list[ExplosionCandidate]:
        return sorted(candidates, key=lambda c: c.total_score, reverse=True)
