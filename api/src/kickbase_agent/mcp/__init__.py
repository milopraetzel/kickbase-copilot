"""`kickbase-mcp` — der Kickbase-Agent als MCP-Server (stdio, FastMCP).

Spec: docs/superpowers/specs/2026-08-23-kickbase-mcp.md. Die Tools sind reine
async-Funktionen (tools_read / tools_ops / tools_write), die server.py an der
FastMCP-Instanz „kickbase" registriert — direkt testbar ohne laufenden Server.

Eiserne Regeln (hier technisch erzwungen):
- Kein Kickbase-Write außerhalb des ActionExecutors (tools_write geht NUR durch
  ActionExecutor.execute; das Arming bleibt bei den bestehenden Umgebungs-Flags).
- Keine Secrets in Rückgaben; Logging ausschließlich nach stderr (stdout ist
  das MCP-Protokoll).
- Fehler als {"error", "hint"} statt Traceback.
"""
