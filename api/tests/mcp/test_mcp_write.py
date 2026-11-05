"""Write-Gates: ohne confirm → Dry-Run; ≥5 M ohne emil_go → refused; NIE der Client direkt.

Jeder Write geht durch ActionExecutor.execute — sichtbar an der transfer_actions-
Zeile (rule_id mcp-*), und der FakeClient sieht Writes nur, wenn der Executor sie
live freigibt (Test-Liga + AGENT_DRY_RUN=0).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from kickbase_agent.db.models import TransferAction
from kickbase_agent.mcp import tools_write


def _rows(engine) -> list[TransferAction]:
    with Session(engine, future=True) as s:
        return list(s.execute(select(TransferAction)).scalars())


async def test_place_bid_without_confirm_is_dry_run_with_audit_row(world, env):
    out = await tools_write.place_bid("99", 2_000_000, "Starter, 93 % MW")
    assert out["status"] == "skipped_dry_run"
    assert out["executor_called"] is True and out["live_requested"] is False
    assert world.client.writes == []  # kein Kickbase-Write
    rows = _rows(world.engine)
    assert len(rows) == 1
    row = rows[0]
    assert (row.action_type, row.rule_id, row.status, row.dry_run) == (
        "bid", "mcp-bid", "skipped_dry_run", True,
    )
    assert row.player_id == "99" and row.amount == 2_000_000 and row.league_id == "L1"
    assert out["audit"]["id"] == row.id and out["amount_fmt"] == "2,00 M"
    assert out["emil_go_required"] is False


async def test_place_bid_live_over_5m_without_emil_go_is_refused_before_executor(world, env):
    env(dry="0", test_league="L1")  # Umgebung wäre geärmt — die Gate greift trotzdem zuerst
    out = await tools_write.place_bid("99", 5_120_000, "Tier-1-Kauf", confirm=True)
    assert out["status"] == "refused" and out["executor_called"] is False
    assert "Emils GO" in out["reason"] and "emil_go=" in out["hint"]
    assert world.client.writes == []
    assert _rows(world.engine) == []  # Executor nie gerufen → keine Audit-Zeile


async def test_place_bid_dry_run_over_5m_needs_no_emil_go_but_flags_it(world, env):
    out = await tools_write.place_bid("99", 5_120_000, "Vorschau", confirm=False)
    assert out["status"] == "skipped_dry_run" and out["emil_go_required"] is True
    assert world.client.writes == []


async def test_place_bid_confirm_in_unarmed_env_is_honest_skipped_dry_run(world, env):
    env(dry="1")  # Kill-Switch AGENT_DRY_RUN=1 gewinnt immer — kein stilles „executed"
    out = await tools_write.place_bid(
        "99", 5_120_000, "Starter", confirm=True, emil_go="Emil 23.08. 10:42: mach den Trade"
    )
    assert out["status"] == "skipped_dry_run" and out["live_requested"] is True
    assert out["flags"]["agent_dry_run"] is True and out["flags"]["agent_allow_live"] is False
    assert world.client.writes == []
    row = _rows(world.engine)[0]
    assert row.dry_run is True and "Emil-GO: Emil 23.08. 10:42" in (row.rationale or "")


async def test_place_bid_confirm_with_dry0_but_no_allow_live_is_refused_by_interlock(world, env):
    env(dry="0", allow_live="0")
    out = await tools_write.place_bid("99", 2_000_000, "Starter", confirm=True)
    assert out["status"] == "refused" and out["executor_called"] is True
    assert "AGENT_ALLOW_LIVE=0" in out["message"]
    assert world.client.writes == []
    assert _rows(world.engine)[0].status == "refused"


async def test_local_bid_without_engine_review_is_refused_by_executor(world, env):
    env(dry="0", test_league="L1")  # Test-Liga ärmt alle Aktionstypen
    out = await tools_write.place_bid(
        "99", 5_120_000, "Starter", confirm=True, emil_go="Emil 23.08. 10:42: mach den Trade"
    )
    assert out["status"] == "refused" and "Mannschaftsprüfung" in out["message"]
    assert world.client.writes == []
    row = _rows(world.engine)[0]
    assert row.status == "refused" and row.dry_run is False and row.rule_id == "mcp-bid"


async def test_place_bid_validates_inputs_without_touching_executor(world, env):
    assert (await tools_write.place_bid("99", 0, "x"))["error"].startswith("ToolError")
    assert (await tools_write.place_bid("99", 100, "  "))["error"].startswith("ToolError")
    assert (await tools_write.place_bid("", 100, "x"))["error"].startswith("ToolError")
    assert _rows(world.engine) == [] and world.client.writes == []


async def test_list_player_dry_run_and_live(world, env):
    out = await tools_write.list_player("3", 26_000_000, "Verkaufs-Tranche 103 %")
    assert out["status"] == "skipped_dry_run" and out["price_fmt"] == "26,00 M"
    assert _rows(world.engine)[0].rule_id == "mcp-list"
    env(dry="0", test_league="L1")
    live = await tools_write.list_player(
        "3", 26_000_000, "Verkaufs-Tranche", confirm=True, emil_go="Emil 16.09.: listen",
    )
    assert live["status"] == "executed"
    assert world.client.writes == [("list", "L1", "3", 26_000_000)]


async def test_list_player_needs_emil_go_above_the_threshold(world, env):
    """Ab 5 M gilt dieselbe Schwelle wie beim Gebot (16.09.2026).

    Vorher kannte das Werkzeug `emil_go` gar nicht: Die Box-API verlangt den
    Wortlaut ab 5 M und antwortete mit HTTP 400 — eine teure Listung war über
    MCP unmöglich, ohne dass der Grund im Werkzeug sichtbar wurde.
    """
    env(dry="0", test_league="L1")
    refused = await tools_write.list_player("3", 26_000_000, "ohne Wortlaut", confirm=True)
    assert refused["status"] == "refused" and refused["executor_called"] is False
    assert world.client.writes == [] and _rows(world.engine) == []
    # Mit Wortlaut geht dieselbe Listung durch (Gegenprobe zum Tor).
    ok = await tools_write.list_player(
        "3", 26_000_000, "mit Wortlaut", confirm=True, emil_go="Emil 16.09.: listen",
    )
    assert ok["status"] == "executed"
    assert world.client.writes == [("list", "L1", "3", 26_000_000)]


async def test_sell_player_always_needs_emil_go_for_live(world, env):
    env(dry="0", test_league="L1")
    out = await tools_write.sell_player("2", "Langzeit-Verletzt", confirm=True)
    assert out["status"] == "refused" and out["executor_called"] is False
    assert world.client.writes == [] and _rows(world.engine) == []


async def test_sell_player_takes_market_value_from_squad_and_executes_with_go(world, env):
    env(dry="0", test_league="L1")
    out = await tools_write.sell_player(
        "2", "Achilles — Langzeit", confirm=True, emil_go="Emil 23.08.: verkaufen"
    )
    assert out["status"] == "executed" and out["player_name"] == "Joshua Kimmich"
    assert out["market_value_eur"] == 19_000_000
    assert world.client.writes == [("sell", "L1", "2")]
    row = _rows(world.engine)[0]
    assert (row.action_type, row.rule_id, row.amount) == ("sell", "mcp-sell", 19_000_000)


async def test_sell_player_dry_run_without_go_and_unknown_player(world, env):
    out = await tools_write.sell_player("2", "Vorschau")
    assert out["status"] == "skipped_dry_run" and world.client.writes == []
    missing = await tools_write.sell_player("404", "x")
    assert missing["error"].startswith("ToolError: Spieler 404 ist nicht im eigenen Kader")


async def test_collect_bonus_unarmed_is_dry_run_with_message(world, env):
    out = await tools_write.collect_bonus()
    assert out["status"] == "skipped_dry_run"
    assert any("DRY-RUN" in m for m in out["messages"])
    assert out["audit"]["type"] == "bonus" and out["audit"]["rule_id"] == "auto-bonus"
    assert world.client.writes == []


async def test_collect_bonus_armed_measures_credit_via_executor(world, env):
    env(dry="0", auto_bonus="1")
    world.client.bonus_credit = 50_000
    out = await tools_write.collect_bonus()
    assert out["status"] == "executed"
    assert world.client.writes == [("bonus",)]
    assert any("+50 k" in m or "50.000" in m for m in out["messages"])
    assert out["audit"]["status"] == "executed"


# ------------------------------------------------------------ delist_player (22.09.2026)


async def test_delist_player_without_confirm_is_dry_run_without_client_write(world, env):
    out = await tools_write.delist_player("3", "Olise halten — Listing raus")
    assert out["status"] == "skipped_dry_run"
    assert out["executor_called"] is True and out["live_requested"] is False
    rows = _rows(world.engine)
    assert len(rows) == 1 and rows[0].rule_id == "mcp-delist"
    assert rows[0].action_type == "delist" and rows[0].player_id == "3"
    assert world.client.writes == []


async def test_delist_player_live_needs_no_emil_go_and_reaches_client(world, env):
    """Ein Delist baut Risiko ab (wie cancel_bid): kein emil_go-Zwang. Live nur
    über den Executor — der FakeClient sieht genau einen delist-Write."""
    env(dry="0", test_league="L1")
    out = await tools_write.delist_player("3", "Listing zurück", confirm=True)
    assert out["status"] == "executed", out
    assert world.client.writes == [("delist", "L1", "3")]
    assert _rows(world.engine)[0].rule_id == "mcp-delist"


async def test_delist_player_validates_inputs_and_squad_membership(world, env):
    assert (await tools_write.delist_player("", "x"))["error"].startswith("ToolError")
    assert (await tools_write.delist_player("3", "  "))["error"].startswith("ToolError")
    missing = await tools_write.delist_player("404", "nicht im Kader")
    assert missing["error"].startswith("ToolError: Spieler 404 ist nicht im eigenen Kader")
    assert _rows(world.engine) == [] and world.client.writes == []
