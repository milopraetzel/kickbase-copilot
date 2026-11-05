"""Zentrales KI-Regelwerk & Onboarding-Profil (Web-App + KI-Chat synchron).

Speichert in ``data/agent_rules.json`` genau, welche Autonomie-Stufe und welche
Einzelbefugnisse der Nutzer dem Kickbase-Agenten erlaubt hat. Sowohl die Web-App
(Tab ``Regelwerk`` ueber ``/api/rules``) als auch CLI (``kba rules`` / ``kba onboarding``)
und der KI-Chat lesen und schreiben dieselbe Datei.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kickbase_agent.config import get_settings

RULES_ENV_VAR = "AGENT_RULES_FILE"
VALID_MODES = ("dry_run", "veto_copilot", "full_autopilot")
VALID_SELL_POLICIES = ("none", "bench_only", "swap_allowed")


@dataclass
class AgentRules:
    """Vom Nutzer konfigurierbares Regelwerk fuer den autonomen Agenten."""

    mode: str = "full_autopilot"
    allow_bids: bool = True
    max_bid_million: float = 50.0
    max_overpay_pct: float = 8.0
    allow_sells: str = "swap_allowed"
    allow_mw_trading: bool = True
    trade_budget_cap_million: float = 50.0
    friday_buffer_haircut_pct: float = 0.0
    allow_lineup: bool = True
    allow_friday_rescue: bool = True
    veto_minutes: int = 30
    protected_players: list[str] = field(default_factory=list)
    onboarding_completed: bool = False
    updated_at: str = ""
    updated_by: str = "default"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_custom_active(self) -> bool:
        """True, sobald der Nutzer das Regelwerk aktiv gespeichert hat."""
        return bool(self.onboarding_completed and self.updated_by != "env_default")

    def is_player_protected(self, player_id: str, player_name: str = "") -> bool:
        """Prueft, ob ein Spieler auf der persoenlichen Schutzliste steht."""
        pid = str(player_id).strip().lower()
        pname = str(player_name).strip().lower()
        for entry in self.protected_players:
            token = entry.strip().lower()
            if not token:
                continue
            if (
                token == pid
                or (len(token) >= 3 and token in pid)
                or (pname and token in pname)
            ):
                return True
        return False


PRESETS: dict[str, dict[str, Any]] = {
    "dry_run": {
        "mode": "dry_run",
        "allow_bids": False,
        "max_bid_million": 10.0,
        "max_overpay_pct": 4.0,
        "allow_sells": "none",
        "allow_mw_trading": False,
        "trade_budget_cap_million": 0.0,
        "friday_buffer_haircut_pct": 5.0,
        "allow_lineup": False,
        "allow_friday_rescue": False,
        "veto_minutes": 60,
    },
    "veto_copilot": {
        "mode": "veto_copilot",
        "allow_bids": True,
        "max_bid_million": 18.0,
        "max_overpay_pct": 5.5,
        "allow_sells": "bench_only",
        "allow_mw_trading": True,
        "trade_budget_cap_million": 20.0,
        "friday_buffer_haircut_pct": 4.0,
        "allow_lineup": True,
        "allow_friday_rescue": True,
        "veto_minutes": 60,
    },
    "full_autopilot": {
        "mode": "full_autopilot",
        "allow_bids": True,
        "max_bid_million": 50.0,
        "max_overpay_pct": 8.0,
        "allow_sells": "swap_allowed",
        "allow_mw_trading": True,
        "trade_budget_cap_million": 50.0,
        "friday_buffer_haircut_pct": 0.0,
        "allow_lineup": True,
        "allow_friday_rescue": True,
        "veto_minutes": 30,
    },
}


def rules_file_path() -> Path:
    """Pfad zur gemeinsamen ``agent_rules.json`` im Datenverzeichnis."""
    env_override = os.environ.get(RULES_ENV_VAR, "").strip()
    if env_override:
        override_path = Path(env_override)
        override_path.parent.mkdir(parents=True, exist_ok=True)
        return override_path
    try:
        settings = get_settings()
        data_dir = Path(settings.agent_data_dir)
    except Exception:
        data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "agent_rules.json"


def load_agent_rules(path: Path | None = None) -> AgentRules:
    """Laedt das aktuelle Regelwerk oder initialisiert es aus den ``.env``-Defaults."""
    target = path or rules_file_path()
    if not target.exists():
        try:
            settings = get_settings()
            dry_run = bool(settings.agent_dry_run)
            raw_prot = getattr(settings, "protected_player_ids", "")
        except Exception:
            dry_run = True
            raw_prot = "8329"
        base = dict(PRESETS["full_autopilot"])
        base["mode"] = "dry_run" if dry_run else "full_autopilot"
        protected = [p.strip() for p in str(raw_prot).split(",") if p.strip()]
        base["protected_players"] = protected
        base["onboarding_completed"] = False
        base["updated_at"] = datetime.now(UTC).isoformat()
        base["updated_by"] = "env_default"
        return _sanitize_rules(base)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return AgentRules()
    return _sanitize_rules(raw)


def _sanitize_rules(raw: dict[str, Any]) -> AgentRules:
    mode = str(raw.get("mode", "full_autopilot"))
    if mode not in VALID_MODES:
        mode = "dry_run"
    allow_sells = str(raw.get("allow_sells", "swap_allowed"))
    if allow_sells not in VALID_SELL_POLICIES:
        allow_sells = "bench_only"
    protected_raw = raw.get("protected_players", [])
    if isinstance(protected_raw, str):
        protected = [p.strip() for p in protected_raw.split(",") if p.strip()]
    elif isinstance(protected_raw, list):
        protected = [str(p).strip() for p in protected_raw if str(p).strip()]
    else:
        protected = []
    return AgentRules(
        mode=mode,
        allow_bids=bool(raw.get("allow_bids", mode != "dry_run")),
        max_bid_million=max(0.5, min(95.0, float(raw.get("max_bid_million", 25.0)))),
        max_overpay_pct=max(0.0, min(25.0, float(raw.get("max_overpay_pct", 6.0)))),
        allow_sells=allow_sells,
        allow_mw_trading=bool(raw.get("allow_mw_trading", True)),
        trade_budget_cap_million=max(
            0.0, min(80.0, float(raw.get("trade_budget_cap_million", 30.0)))
        ),
        friday_buffer_haircut_pct=max(
            0.0, min(15.0, float(raw.get("friday_buffer_haircut_pct", 3.5)))
        ),
        allow_lineup=bool(raw.get("allow_lineup", True)),
        allow_friday_rescue=bool(raw.get("allow_friday_rescue", True)),
        veto_minutes=max(5, min(240, int(raw.get("veto_minutes", 60)))),
        protected_players=protected,
        onboarding_completed=bool(raw.get("onboarding_completed", True)),
        updated_at=str(raw.get("updated_at") or datetime.now(UTC).isoformat()),
        updated_by=str(raw.get("updated_by") or "user"),
    )


def save_agent_rules(
    updates: dict[str, Any],
    *,
    updated_by: str = "web_ui",
    path: Path | None = None,
) -> AgentRules:
    """Aktualisiert das Regelwerk atomar auf Platte (synchron fuer Web-App & KI-Chat)."""
    target = path or rules_file_path()
    current = load_agent_rules(target).to_dict()
    preset = updates.get("preset")
    if isinstance(preset, str) and preset in PRESETS:
        current.update(PRESETS[preset])
    current.update({k: v for k, v in updates.items() if k != "preset"})
    current["onboarding_completed"] = True
    current["updated_at"] = datetime.now(UTC).isoformat()
    current["updated_by"] = updated_by
    rules = _sanitize_rules(current)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(rules.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(target)
    return rules
