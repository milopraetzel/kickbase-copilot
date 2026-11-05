"""Minimalistische Arbeitsoberfläche mit KI-Entscheidungsfaktor und Live-Backend-Steuerung."""

from __future__ import annotations

import html
import json
import math
from typing import Any

from kickbase_agent.agent.rules import load_agent_rules
from kickbase_agent.cli.cockpit.workspace_theme import CSS, JS
from kickbase_agent.scouts.ligainsider.teams import TEAMS


def _safe_data(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _safe_data(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_data(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _compact_fair(fair: Any) -> dict | None:
    if not isinstance(fair, dict):
        return None
    pos_code = {"TW": 1, "ABW": 2, "MIT": 3, "STU": 4}
    cloud = []
    for c in fair.get("cloud") or []:
        if not isinstance(c, dict):
            continue
        tid = str(c.get("team_id") or "")
        team_obj = TEAMS.get(tid)
        team_name = str(c.get("team") or getattr(team_obj, "kb_name", "") or "")
        pos_str = str(c.get("pos") or "")
        entry: dict[str, Any] = {
            "pid": str(c.get("player_id") or c.get("pid") or ""),
            "name": c.get("name"),
            "ap": c.get("ap"),
            "mv": c.get("mv"),
            "pos": pos_str,
            "position": c.get("position") or pos_code.get(pos_str),
            "team_id": tid,
            "team": team_name,
            "img": c.get("img"),
        }
        if "punkte" in c or "points" in c:
            entry["punkte"] = c.get("punkte") if "punkte" in c else c.get("points")
        cloud.append(entry)
    return {
        "curve": fair.get("curve"),
        "cloud": cloud,
    }


def _compact_intents(rows: Any) -> list[dict] | None:
    if not isinstance(rows, (list, tuple)):
        return None
    keep = (
        "id", "kind", "player_id", "player_name", "team_id", "club", "pos", "position",
        "amount", "amount_fmt", "big", "status", "rationale", "sources", "listing_type",
        "execute_at", "deadline", "created_at", "updated_at", "adjusted_from",
        "emil_note", "result", "veto_requested_at", "seconds_left", "deadline_seconds_left",
    )
    return [{k: r.get(k) for k in keep if k in r} for r in rows if isinstance(r, dict)]


def _compact_protocol(rows: Any) -> list[dict] | None:
    if not isinstance(rows, (list, tuple)):
        return None
    keep = (
        "id", "kind", "ts", "title", "why", "type", "action_type", "status",
        "rule_id", "rationale", "amount", "dry_run", "player_id", "player",
        "player_name", "outcome",
    )
    return [
        {k: r.get(k) for k in keep if k in r}
        for r in list(rows)[:25]
        if isinstance(r, dict)
    ]


def _compact_jev(jev: Any) -> dict | None:
    if not isinstance(jev, dict):
        return None
    drop_entry = {"raw", "text", "rule"}
    feed = [{k: v for k, v in e.items() if k not in drop_entry}
            for e in (jev.get("feed") or []) if isinstance(e, dict)]
    spieler = {
        str(pid): [{k: v for k, v in e.items() if k not in drop_entry}
                   for e in entries if isinstance(e, dict)]
        for pid, entries in (jev.get("spieler") or {}).items()
        if isinstance(entries, (list, tuple))
    }
    return {
        "status": jev.get("status"),
        "zahlen": jev.get("zahlen"),
        "bilanz": jev.get("bilanz"),
        "feed": feed,
        "spieler": spieler,
    }


def _payload(d: dict) -> str:
    fields = (
        "stand", "kickoff", "budget", "teamwert", "mine", "ranking", "manager_squads",
        "kader", "market", "news", "bids", "formation", "detail_coverage", "strategy",
    )
    selected = {key: d.get(key) for key in fields}
    selected["rules"] = (
        d.get("rules")
        if isinstance(d.get("rules"), dict)
        else load_agent_rules().to_dict()
    )
    selected["fair"] = _compact_fair(d.get("fair"))
    selected["jev"] = _compact_jev(d.get("jev"))
    selected["intents"] = _compact_intents(d.get("intents"))
    selected["protocol"] = _compact_protocol(d.get("protocol"))
    raw = json.dumps(
        _safe_data(selected),
        ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    )
    return (raw.replace("&", "\\u0026").replace("<", "\\u003c")
            .replace(">", "\\u003e").replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def render_workspace(d: dict) -> str:
    """Vollständiges HTML aus dem bereits gesammelten Cockpit-Datenvertrag."""
    stand = html.escape(str(d.get("stand") or "Zeitpunkt unbekannt"))
    return f'''<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark"><title>Kickbase · KI-Cockpit</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&display=swap"
rel="stylesheet">
<style>{CSS}</style></head><body>
<a class="skip" href="#main">Zum Inhalt</a><div class="workspace"><aside class="sidebar">
<a class="brand" href="#heute"><span class="brand-mark" aria-hidden="true">k.</span>
Kickbase<span class="brand-caption">KI-MANAGER</span></a>
<div class="league-id"><span class="eyebrow">Liga</span>
<strong>BASE <span id="manager-count"></span></strong>
<small>KI-Steuerung &amp; Belege</small></div>
<nav class="main-nav" aria-label="Hauptnavigation">
<button data-view="heute" class="active" aria-current="page">
<span class="nav-icon" aria-hidden="true">◫</span>Heute</button>
<button data-view="uebersicht">
<span class="nav-icon" aria-hidden="true">◉</span>Übersicht</button>
<button data-view="spieler">
<span class="nav-icon" aria-hidden="true">⌕</span>Alle Spieler</button>
<button data-view="liga">
<span class="nav-icon" aria-hidden="true">▥</span>Liga</button>
<button data-view="jev">
<span class="nav-icon" aria-hidden="true">✦</span>Jev-Analyse</button>
<button data-view="regeln">
<span class="nav-icon" aria-hidden="true">⚙</span>Regelwerk</button></nav>
<div class="sidebar-bottom">
<div class="manager-avatar"><span>e</span>
<div><strong id="manager-name">Mein Manager</strong>
<small>Autopilot &amp; Veto</small></div></div></div></aside>
<div class="main-wrap"><header class="topbar"><span id="breadcrumb"></span>
<div class="topbar-right">
<span class="snapshot" id="live-status" data-stand="{stand}"><i></i> Stand {stand}</span>
<button type="button" class="button secondary btn-sm" id="kba-sync"
title="Vorhaben, offene Gebote und Protokoll sofort live vom Backend abrufen">Live-Sync</button>
<button type="button" class="button btn-sm" id="kba-refresh"
aria-label="Daten auf dem Server neu berechnen"
title="Neuen Cockpit-Datenlauf auf dem Server starten (~2–3 Min.)">↻ Neu berechnen</button>
</div></header>
<main id="main" tabindex="-1"><div id="content"></div>
<noscript><h1>Mannschaft &amp; Strategie</h1>
<p>Für interaktive Steuerung bitte JavaScript aktivieren.</p></noscript>
</main>
<footer>Kickbase · KI-Entscheidungen mit Faktor &amp; Beleg
<span>Alle Schreibaktionen laufen gesichert über den ActionExecutor.</span></footer></div></div>
<div id="tooltip" role="tooltip" hidden></div>
<div id="kba-toast" class="toast" role="status" aria-live="polite" hidden></div>
<dialog id="drawer" closedby="any" aria-labelledby="drawer-title">
<div class="drawer-top"><span>KI-Dossier · Entscheidungs-Faktor &amp; Steuerung</span>
<button class="icon-button" id="drawer-close"
aria-label="Spielerdetails schließen">×</button></div><div id="drawer-content"></div></dialog>
<script id="workspace-data" type="application/json">{_payload(d)}</script><script>{JS}</script>
</body></html>'''
