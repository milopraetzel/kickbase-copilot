"""Anonymisierter SFT- und DPO-Dataset-Exporter fuer das Training auf Hugging Face (`trl`).

Schuetzt Urheberrecht und Privatsphaere:
1. Alle Spielernamen und Klubs werden ueber `anonymize_context` in `Player_XX` / `Club_X`
   ueberfuehrt.
2. Enthaelt ein Export Dritt-Textausschnitte (`contains_news_snippets=True`), erzwingt
   `validate_hub_privacy` zwingend `private=True` fuer den Hugging-Face-Dataset-Upload.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from kickbase_agent.hf.context import ManagerContext, anonymize_context
from kickbase_agent.hf.neuro_symbolic import SYSTEM_PROMPT_DE, AiDecisionEnvelope


@dataclass(frozen=True)
class HindsightOutcome:
    """Echtes Rueckblick-Ergebnis nach Spieltag und 3-Tage-Marktwertverlauf fuer Rewards."""

    matchday_points: float
    net_mv_gain_eur: int
    empty_lineup_slots: int = 0
    kickoff_negative: bool = False
    user_preference_delta: float = 0.0


def compute_hindsight_reward(
    outcome: HindsightOutcome,
    *,
    alpha_mv_per_million: float = 18.0,
    beta_penalty: float = 250.0,
    gamma_pref: float = 35.0,
) -> float:
    """Belohnungsfunktion R = Punkte + alpha * MW-Gewinn(M) - beta * Strafe + gamma * Praeferenz."""
    mv_m = outcome.net_mv_gain_eur / 1_000_000.0
    penalty = float(outcome.empty_lineup_slots) + (3.0 if outcome.kickoff_negative else 0.0)
    reward = (
        outcome.matchday_points
        + alpha_mv_per_million * mv_m
        - beta_penalty * penalty
        + gamma_pref * outcome.user_preference_delta
    )
    return round(reward, 2)


def envelope_to_json_str(envelope: AiDecisionEnvelope) -> str:
    """Serialisiert ein `AiDecisionEnvelope` deterministisch als Trainings-Zielantwort."""
    data = {
        "valuations": [asdict(v) for v in envelope.valuations],
        "stale_bid_aliases_to_flag": list(envelope.stale_bid_aliases_to_flag),
        "briefing_de": envelope.briefing_de,
    }
    return json.dumps(data, ensure_ascii=False)


def build_sft_record(
    ctx: ManagerContext,
    target: AiDecisionEnvelope,
    outcome: HindsightOutcome | None = None,
) -> dict[str, Any]:
    """Erzeugt einen `trl`-kompatiblen ChatML-Datensatz (`messages` + optional `reward`)."""
    bundle = anonymize_context(ctx)
    has_news = any(
        bool(p.get("news"))
        for group in ("squad", "market")
        for p in bundle.prompt_payload.get(group, [])
    )
    rec: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_DE},
            {"role": "user", "content": json.dumps(bundle.prompt_payload, ensure_ascii=False)},
            {"role": "assistant", "content": envelope_to_json_str(target)},
        ],
        "contains_news_snippets": has_news,
    }
    if outcome is not None:
        rec["reward"] = compute_hindsight_reward(outcome)
    return rec


def build_dpo_record(
    ctx: ManagerContext,
    chosen: AiDecisionEnvelope,
    rejected: AiDecisionEnvelope,
) -> dict[str, Any]:
    """Erzeugt ein Praeferenz-Paar (`prompt`, `chosen`, `rejected`) aus Nutzer-Veto/-Anpassung."""
    bundle = anonymize_context(ctx)
    has_news = any(
        bool(p.get("news"))
        for group in ("squad", "market")
        for p in bundle.prompt_payload.get(group, [])
    )
    return {
        "prompt": [
            {"role": "system", "content": SYSTEM_PROMPT_DE},
            {"role": "user", "content": json.dumps(bundle.prompt_payload, ensure_ascii=False)},
        ],
        "chosen": [{"role": "assistant", "content": envelope_to_json_str(chosen)}],
        "rejected": [{"role": "assistant", "content": envelope_to_json_str(rejected)}],
        "contains_news_snippets": has_news,
    }


def validate_hub_privacy(*, records: list[dict[str, Any]], private: bool) -> None:
    """Verhindert versehentliche oeffentliche Uploads von fremden News-Texten auf den Hub."""
    if not private and any(r.get("contains_news_snippets") for r in records):
        msg = (
            "Sicherheits-Sperre: Datensaetze mit News-Textausschnitten duerfen auf Hugging Face "
            "nur mit `private=True` hochgeladen werden."
        )
        raise ValueError(msg)


def export_jsonl(records: list[dict[str, Any]], out_path: Path, *, private: bool = True) -> Path:
    """Schreibt SFT- oder DPO-Datensaetze nach Pruefung der Sichtbarkeitsregel als JSONL."""
    validate_hub_privacy(records=records, private=private)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    out_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return out_path
