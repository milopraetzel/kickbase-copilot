"""HTTP-Endpunkte fuer das interaktive KI-Regelwerk & Onboarding (/api/rules)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from kickbase_agent.agent.rules import PRESETS, load_agent_rules, save_agent_rules

router = APIRouter(tags=["rules"])


class RulesUpdatePayload(BaseModel):
    preset: str | None = None
    mode: str | None = None
    allow_bids: bool | None = None
    max_bid_million: float | None = None
    max_overpay_pct: float | None = None
    allow_sells: str | None = None
    allow_mw_trading: bool | None = None
    trade_budget_cap_million: float | None = None
    friday_buffer_haircut_pct: float | None = None
    allow_lineup: bool | None = None
    allow_friday_rescue: bool | None = None
    veto_minutes: int | None = None
    protected_players: list[str] | str | None = None
    onboarding_completed: bool | None = None


@router.get("/rules")
def get_rules() -> dict[str, Any]:
    """Liefert das aktuelle KI-Regelwerk inkl. verfuegbarer Presets."""
    rules = load_agent_rules()
    return {
        "rules": rules.to_dict(),
        "presets": PRESETS,
    }


@router.put("/rules")
def update_rules(payload: RulesUpdatePayload) -> dict[str, Any]:
    """Speichert Aenderungen aus dem Web-App-Regelwerk oder Onboarding-Assistenten."""
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    rules = save_agent_rules(updates, updated_by="web_cockpit")
    return {
        "ok": True,
        "rules": rules.to_dict(),
    }
