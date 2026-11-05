"""FastMCP-Server „kickbase" — stdio-Transport, Console-Script `kba-mcp`.

Registrierung für Claude Code: `.mcp.json` im Repo-Root
(`uv run --directory api kba-mcp`; der Server läuft lokal mit api/.env, Box-Ops
gehen via ssh). Logging AUSSCHLIESSLICH nach stderr — stdout ist das Protokoll.
"""

from __future__ import annotations

import logging
import sys

try:  # mcp >= 2.0: FastMCP heißt dort MCPServer (gleiche API)
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # pragma: no cover — mcp 1.x
    from mcp.server.fastmcp import FastMCP  # type: ignore[no-redef]
from mcp.types import ToolAnnotations

from kickbase_agent.mcp import tools_ops, tools_read, tools_strategy, tools_write
from kickbase_agent.mcp._util import log

SERVER_NAME = "kickbase"
INSTRUCTIONS = """Kickbase-Agent (Liga BASE) als Tools. Gruppen:
- Read (ohne Nebenwirkung): league_state, squad, market, own_bids, player, feed, audit_log,
  active_tips, research_data (~1 min), recommend_lineup (Empfehlungs-Elf, ~20–60 s),
  team_profile (alle Spieler eines Clubs: Ø-Punkte, 7-Tage-Trend, KB-Startelf-Stufe),
  evaluation (Treffer-Bilanz, Erkenntnisse, Parameter-Historie),
  strategy_insights (bestätigte Transfers, Finanzierung, Kurven, Quellen, Frische).
  Remote-Konfiguration KBA_REMOTE_API_URL/AUTH (Alias KBA_API_URL/AUTH): audit_log,
  active_tips und evaluation lesen die Box-DB. Andere Read-Tools lesen Kickbase direkt;
  market, research_data und recommend_lineup verwenden weiterhin die LOKALE Signal-DB.
  Deren Scores sind deshalb keine verifizierte Wiedergabe der Box-Entscheidungen.
- Ops (Nebenwirkung, kein Kickbase-Write): box_status, deploy_verify (Sperrfenster
  21:25–22:25 Berlin wird erzwungen; Minuten), cockpit_build (Minuten), discord_post
  (≤1900 Zeichen), tip_add (lokal + Box), learning_evidence (Beleg zu einer
  Erkenntnis; source=agent, Zwei-DB-Regel: mit KBA_REMOTE_API_URL an die Box).
- Write (NUR über den ActionExecutor, nie Client direkt): collect_bonus, place_bid,
  list_player, sell_player, delist_player — ohne confirm=True nur Dry-Run-Vormerkung; live
  nur, wenn die Umgebung geärmt ist (AGENT_DRY_RUN=0 + AGENT_ALLOW_LIVE=1), sonst ehrlich
  refused/skipped_dry_run. Ab 5 M € / Tier-1 und bei jedem Sofortverkauf ist für live
  emil_go (Emils Wortlaut + Zeit) Pflicht; delist_player (Listing zurückziehen) braucht
  keins. Seit 21.09.2026 löst eine Session Writes NUR auf Emils namentlichen Auftrag für
  genau diesen Spieler aus (CLAUDE.md §5.12).
Geld: *_eur (int) + *_fmt („7,41 M"). Fehler: {"error", "hint"}. Keine Secrets in Antworten.
"""

READ_TOOLS = (
    tools_read.league_state,
    tools_read.squad,
    tools_read.market,
    tools_read.own_bids,
    tools_read.player,
    tools_read.feed,
    tools_read.audit_log,
    tools_read.active_tips,
    tools_read.research_data,
    tools_read.recommend_lineup,
    tools_read.team_profile,
    tools_read.evaluation,
    tools_strategy.strategy_insights,
)
OPS_TOOLS = (
    tools_ops.box_status,
    tools_ops.deploy_verify,
    tools_ops.cockpit_build,
    tools_ops.discord_post,
    tools_ops.tip_add,
    tools_ops.learning_evidence,
)
WRITE_TOOLS = (
    tools_write.collect_bonus,
    tools_write.place_bid,
    tools_write.list_player,
    tools_write.sell_player,
    tools_write.delist_player,
)
TOOL_NAMES: tuple[str, ...] = tuple(
    fn.__name__ for fn in (*READ_TOOLS, *OPS_TOOLS, *WRITE_TOOLS)
)

mcp = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)


def _register() -> None:
    for fn in READ_TOOLS:
        mcp.add_tool(
            fn, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True)
        )
    for fn in OPS_TOOLS:
        mcp.add_tool(
            fn,
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                openWorldHint=True,
            ),
        )
    for fn in WRITE_TOOLS:
        mcp.add_tool(
            fn,
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=True, idempotentHint=False,
                openWorldHint=True,
            ),
        )


_register()


def main() -> None:
    """Einstieg `kba-mcp`: stdio-Server; Logs nach stderr (stdout = MCP-Protokoll)."""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("kickbase-mcp startet (stdio, %d Tools): %s", len(TOOL_NAMES), ", ".join(TOOL_NAMES))
    mcp.run("stdio")


if __name__ == "__main__":
    main()
