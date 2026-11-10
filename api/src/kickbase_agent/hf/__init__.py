"""Neuro-Symbolisches Hugging-Face-Paket fuer Kickbase-Agent / Kickbase-Copilot."""

from kickbase_agent.hf.context import (
    FRIDAY_MIN_BUFFER_EUR,
    STALE_MANAGER_BID_HOURS,
    AnonymizedBundle,
    ManagerContext,
    OpenBidContext,
    PlayerSnapshot,
    anonymize_context,
    deanonymize_text,
)
from kickbase_agent.hf.dataset_export import (
    HindsightOutcome,
    build_dpo_record,
    build_sft_record,
    compute_hindsight_reward,
    export_jsonl,
    validate_hub_privacy,
)
from kickbase_agent.hf.neuro_symbolic import (
    DEFAULT_HF_MODEL_ID,
    AiDecisionEnvelope,
    AiPlayerValuation,
    EventSnapshot,
    HuggingFaceJudgeClient,
    NeuroSymbolicResult,
    VerifiedBidPlan,
    deterministic_fallback_envelope,
    should_invoke_ai,
    verify_neuro_plan,
)

__all__ = [
    "DEFAULT_HF_MODEL_ID",
    "FRIDAY_MIN_BUFFER_EUR",
    "STALE_MANAGER_BID_HOURS",
    "AiDecisionEnvelope",
    "AiPlayerValuation",
    "AnonymizedBundle",
    "EventSnapshot",
    "HindsightOutcome",
    "HuggingFaceJudgeClient",
    "ManagerContext",
    "NeuroSymbolicResult",
    "OpenBidContext",
    "PlayerSnapshot",
    "VerifiedBidPlan",
    "anonymize_context",
    "build_dpo_record",
    "build_sft_record",
    "compute_hindsight_reward",
    "deanonymize_text",
    "deterministic_fallback_envelope",
    "export_jsonl",
    "should_invoke_ai",
    "validate_hub_privacy",
    "verify_neuro_plan",
]
