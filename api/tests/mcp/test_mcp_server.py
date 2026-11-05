"""Server-Smoke: FastMCP-Instanz „kickbase" listet alle Spec-Tools; Tool-Calls liefern dicts.

Drei Ebenen: (1) direkt am Server-Objekt, (2) In-Memory-MCP-Client (echtes Protokoll,
kein Subprozess), (3) echter stdio-Subprozess via `python -m kickbase_agent.mcp.server`
(der Weg, den `kba-mcp`/.mcp.json gehen).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mcp.client import Client
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from kickbase_agent.mcp import server as srv

SPEC_TOOLS = {
    # Read
    "league_state", "squad", "market", "own_bids", "player", "feed", "audit_log",
    "active_tips", "research_data", "recommend_lineup", "team_profile", "evaluation",
    "strategy_insights",
    # Ops
    "box_status", "deploy_verify", "cockpit_build", "discord_post", "tip_add",
    "learning_evidence",
    # Write
    "collect_bonus", "place_bid", "list_player", "sell_player", "delist_player",
}


def test_tool_names_match_spec_catalog():
    assert set(srv.TOOL_NAMES) == SPEC_TOOLS
    assert len(srv.TOOL_NAMES) == len(SPEC_TOOLS) == 24
    assert srv.mcp.name == "kickbase"


async def test_server_lists_all_tools_with_annotations_and_schemas():
    tools = {t.name: t for t in await srv.mcp.list_tools()}
    assert set(tools) == SPEC_TOOLS
    # Read-Tools sind als read-only annotiert, Writes als destruktiv
    ro = tools["squad"].annotations
    assert getattr(ro, "read_only_hint", getattr(ro, "readOnlyHint", None)) is True
    wr = tools["place_bid"].annotations
    assert getattr(wr, "read_only_hint", getattr(wr, "readOnlyHint", None)) is False
    assert getattr(wr, "destructive_hint", getattr(wr, "destructiveHint", None)) is True
    props = tools["place_bid"].input_schema["properties"]
    assert set(props) == {
        "player_id", "amount_eur", "rationale", "confirm", "emil_go",
        # 26.08.: Kader-Bedarf-Marker (Overpay-Kappe 135 % statt 110 % MW)
        "squad_need",
    }
    assert props["confirm"]["default"] is False and props["emil_go"]["default"] == ""
    assert props["squad_need"]["default"] is False
    assert tools["deploy_verify"].input_schema["required"] == ["markers"]
    assert "Sperrfenster" in (tools["deploy_verify"].description or "")
    assert "ActionExecutor" in (tools["place_bid"].description or "")


async def test_in_memory_client_calls_tool_and_gets_structured_dict(world):
    async with Client(srv.mcp) as client:
        listed = await client.list_tools()
        assert {t.name for t in listed.tools} == SPEC_TOOLS
        res = await client.call_tool("audit_log", {"limit": 5})
        assert res.is_error is False
        assert res.structured_content == {"count": 0, "status_filter": None, "actions": []}
        assert json.loads(res.content[0].text)["count"] == 0
        # Fehler kommen als Daten ({"error","hint"}), nicht als Protokollfehler
        bad = await client.call_tool("market", {"sort": "hype"})
        assert bad.is_error is False
        assert bad.structured_content["error"].startswith("ToolError: Unbekannte Sortierung")
        # Write-Gate über das echte Protokoll: ≥5 M live ohne emil_go → refused
        gate = await client.call_tool(
            "place_bid",
            {"player_id": "99", "amount_eur": 6_000_000, "rationale": "x", "confirm": True},
        )
        assert gate.structured_content["status"] == "refused"
        assert world.client.writes == []


async def test_evaluation_tool_returns_learning_contract_over_protocol(world):
    """Read-Tool `evaluation` = Inhalt von GET /api/learning (leere DB: Nullwerte, kein Fehler)."""
    async with Client(srv.mcp) as client:
        res = await client.call_tool("evaluation", {})
        assert res.is_error is False
        body = res.structured_content
        assert set(body) == {
            "now", "evaluation", "learnings", "parameters", "open_calibrations",
            # seit 26.08. (Lern-Bilanz): Wochen-Kopf, Korrektur-Liste und
            # Emils Tabelle der Selbstverbesserungen
            "bilanz", "to_fix", "selbstverbesserungen",
        }
        ev = body["evaluation"]
        assert ev["buy_hits"]["1d"]["n"] == 0 and ev["buy_hits"]["7d"]["hit_rate"] is None
        assert ev["shadow"]["1d"]["n"] == 0 and ev["executed"]["1d"]["n"] == 0
        assert ev["tips"]["n"] == 0 and ev["flips"] == [] and ev["flips_total_delta_eur"] == 0
        assert ev["lineup"]["n"] == 0 and ev["coverage"]["proposals_total"] == 0
        assert isinstance(body["learnings"], list)
        assert body["to_fix"] == [] and body["bilanz"]["refuted_week"] == 0
        assert body["parameters"]["history"] and "daily_buy_cap" in body["parameters"]["current"]
        assert body["open_calibrations"]
        assert world.client.writes == []
    tools = {t.name: t for t in await srv.mcp.list_tools()}
    ro = tools["evaluation"].annotations
    assert getattr(ro, "read_only_hint", getattr(ro, "readOnlyHint", None)) is True
    assert "Treffer-Bilanz" in (tools["evaluation"].description or "")


async def test_stdio_subprocess_starts_and_lists_tools(tmp_path: Path):
    """Der echte Einstieg (`python -m kickbase_agent.mcp.server` ≙ kba-mcp) über stdio:
    initialize + tools/list — ohne Kickbase-/Box-Zugriff. stdout gehört dem Protokoll."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "kickbase_agent.mcp.server"],
        cwd=str(tmp_path),  # keine lokale .env — Settings kommen aus der Test-Umgebung
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write, read_timeout_seconds=30) as session,
    ):
        init = await session.initialize()
        assert init.server_info.name == "kickbase"
        assert "ActionExecutor" in (init.instructions or "")
        tools = await session.list_tools()
        assert {t.name for t in tools.tools} == SPEC_TOOLS
