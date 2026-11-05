"""Tests fuer das zentrale KI-Regelwerk (Web-App + KI-Chat synchron)."""

from __future__ import annotations

from pathlib import Path

from kickbase_agent.agent.rules import load_agent_rules, save_agent_rules
from kickbase_agent.cli.cockpit.workspace import render_workspace


def test_rules_presets_and_custom_overrides(tmp_path: Path) -> None:
    rules_file = tmp_path / "agent_rules.json"
    initial = load_agent_rules(rules_file)
    assert initial.onboarding_completed is False

    saved = save_agent_rules(
        {
            "preset": "veto_copilot",
            "max_bid_million": 12.5,
            "trade_budget_cap_million": 22.0,
            "friday_buffer_haircut_pct": 4.0,
            "allow_sells": "bench_only",
            "protected_players": "Olise, Musiala",
        },
        updated_by="test_chat",
        path=rules_file,
    )
    assert saved.mode == "veto_copilot"
    assert saved.allow_bids is True
    assert saved.max_bid_million == 12.5
    assert saved.trade_budget_cap_million == 22.0
    assert saved.friday_buffer_haircut_pct == 4.0
    assert saved.allow_sells == "bench_only"
    assert saved.protected_players == ["Olise", "Musiala"]
    assert saved.is_player_protected("999", "Michael Olise") is True
    assert saved.is_player_protected("999", "Harry Kane") is False
    assert saved.onboarding_completed is True

    reloaded = load_agent_rules(rules_file)
    assert reloaded.to_dict() == saved.to_dict()


def test_workspace_includes_regeln_tab() -> None:
    html = render_workspace({"stand": "05.11.2026 08:45", "strategy": {}})
    assert 'data-view="regeln"' in html
    assert "Regelwerk</button>" in html
