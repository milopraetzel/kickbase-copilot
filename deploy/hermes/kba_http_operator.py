#!/usr/bin/env python3
"""Hermes reader for the Kickbase WebApp HTTP API (read-only since 2026-09-21).

Reads Basic Auth from ~/.hermes/.env and never prints credentials. The former
write commands (create, adjust, execute-now) and the standing default approval
were removed with the turnaround study (one writer; Kickbase writes only on
Emil's named order per player). The API refuses via=hermes with 403 anyway.
A veto stays possible: it only stops an action.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

LEAGUE_ID = "1234567"
HERMES_BASE = Path(__file__).resolve().parents[1]
READ_PATHS = {
    "me": "me",
    "squad": f"leagues/{LEAGUE_ID}/squad",
    "market": f"leagues/{LEAGUE_ID}/market",
    "bids": "bids",
    "pathways": "pathways",
    "intents": "intents?status=all&limit=100",
    "protocol": "protocol?limit=100",
    "jobs": "jobs",
    "learning": "learning",
    "strategy": "strategy",
    "purchase-plan": "purchase-plan",
    "audit": "audit?limit=100",
    "tips": "tips",
    "reports": "reports?limit=30",
    "news": "news?limit=30",
    "refresh-status": "refresh/status",
}


def env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    path = HERMES_BASE / ".env"
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("KBA_API_URL", "KBA_API_AUTH"):
        if not values.get(key):
            raise SystemExit(f"Störfall: {key} fehlt oder ist leer")
    if not values["KBA_API_AUTH"].startswith("emil:"):
        raise SystemExit("Störfall: KBA_API_AUTH hat nicht die Form emil:<passwort>")
    return values


class API:
    def __init__(self) -> None:
        values = env_values()
        self.base = values["KBA_API_URL"].rstrip("/")
        token = base64.b64encode(values["KBA_API_AUTH"].encode()).decode()
        self.headers = {
            "Authorization": f"Basic {token}",
            "X-Requested-With": "kba",
            "Accept": "application/json",
            "User-Agent": "Hermes-KBA-Stufe-C/1.0",
        }

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None if payload is None else json.dumps(payload).encode()
        headers = dict(self.headers)
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.base}/{path.lstrip('/')}", data=data, headers=headers, method=method
        )
        try:
            # Der Mannschaftsplan liest frische Daten und löst alle Formationen.
            plan_request = path == "purchase-plan" or (method == "POST" and path == "intents")
            timeout = 180 if plan_request else 45
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                body = json.load(exc)
            except Exception:
                body = {"detail": str(exc)}
            raise SystemExit(
                f"Störfall: HTTP {exc.code} {method} /api/{path}: "
                f"{body.get('detail', body)}"
            ) from exc
        except urllib.error.URLError as exc:
            raise SystemExit(f"Störfall: API nicht erreichbar: {exc.reason}") from exc


def compact_snapshot(api: API) -> dict[str, Any]:
    me = api.request("GET", READ_PATHS["me"])
    squad = api.request("GET", READ_PATHS["squad"])
    market = api.request("GET", READ_PATHS["market"])
    bids = api.request("GET", READ_PATHS["bids"])
    pathways = api.request("GET", READ_PATHS["pathways"])
    intents = api.request("GET", READ_PATHS["intents"])
    protocol = api.request("GET", "protocol?limit=40")
    reports = api.request("GET", READ_PATHS["reports"])
    try:
        news = api.request("GET", READ_PATHS["news"])
    except SystemExit:
        news = []
    jobs = api.request("GET", READ_PATHS["jobs"])
    try:
        raw_strategy = api.request("GET", READ_PATHS["strategy"])
        strategy = {key: raw_strategy.get(key) for key in (
            "generated_at", "status", "summary", "portfolio", "deadline",
            "planning_notes", "coverage", "source_refresh", "limitations",
        )}
        strategy["player_count"] = len(raw_strategy.get("players") or [])
    except SystemExit as exc:
        strategy = {"status": "unavailable", "error": str(exc)}

    squad_rows = []
    for row in squad.get("players", []):
        p = row["player"]
        squad_rows.append({
            "id": p["id"],
            "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
            "position": p.get("position_label"),
            "market_value": p.get("market_value"),
            "trend": p.get("market_value_trend"),
            "points": p.get("points"),
            "kickbase_starter": row.get("is_starter"),
            "protected": str(p["id"]) == "8329",
        })

    market_rows = []
    for row in market.get("players", []):
        p = row["player"]
        market_rows.append({
            "id": p["id"],
            "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
            "position": p.get("position_label"),
            "market_value": p.get("market_value"),
            "trend": p.get("market_value_trend"),
            "points": p.get("points"),
            "price": row.get("price"),
            "expires_at": row.get("expires_at"),
        })

    return {
        "strategy": strategy,
        "league": next((x for x in me.get("leagues", []) if x.get("id") == LEAGUE_ID), None),
        "squad_count": len(squad_rows),
        "squad": squad_rows,
        "market": market_rows,
        "open_bids": bids.get("bids", []),
        "pathways": pathways.get("pathways", []),
        "planned_intents": [
            x for x in intents.get("intents", []) if x.get("status") == "planned"
        ],
        "recent_failed_intents": [
            x for x in intents.get("intents", []) if x.get("status") in {"failed", "refused"}
        ][:20],
        "protocol": protocol.get("entries", [])[:40],
        "reports": reports.get("reports", [])[:30],
        "news": news[:30] if isinstance(news, list) else [],
        "jobs_failed_window_total": jobs.get("failed_window_total"),
        "jobs": jobs.get("jobs", []),
    }


def verify_write(api: API, result: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"write_response": result}
    intent = result.get("intent") if isinstance(result, dict) else None
    intent_id = intent.get("id") if isinstance(intent, dict) else None
    if intent_id:
        out["verified_intent"] = api.request("GET", f"intents/{intent_id}")
    out["protocol_after_write"] = api.request("GET", "protocol?limit=20")
    return out


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Kickbase WebApp HTTP operator")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    get = sub.add_parser("get")
    get.add_argument("resource", choices=sorted(READ_PATHS))

    veto = sub.add_parser("veto")
    veto.add_argument("intent_id", type=int)
    veto.add_argument("--note", default="[HERMES] Veto nach Live-Prüfung")

    sub.add_parser("refresh")

    evidence = sub.add_parser("evidence")
    evidence.add_argument("learning_id")
    evidence.add_argument("verdict", choices=["confirm", "refute"])
    evidence.add_argument("--note", required=True)
    evidence.add_argument("--ref", required=True)
    return p


def main() -> None:
    args = parser().parse_args()
    api = API()
    if args.command == "snapshot":
        result = compact_snapshot(api)
    elif args.command == "get":
        result = api.request("GET", READ_PATHS[args.resource])
    elif args.command == "veto":
        result = verify_write(
            api, api.request("POST", f"intents/{args.intent_id}/veto", {"note": args.note})
        )
    elif args.command == "refresh":
        result = api.request("POST", "refresh", {})
        result = {"write_response": result, "verified": api.request("GET", "refresh/status")}
    elif args.command == "evidence":
        payload = {
            "learning_id": args.learning_id,
            "verdict": args.verdict,
            "note": args.note,
            "ref": args.ref,
        }
        result = api.request("POST", "learning/evidence", payload)
    else:
        raise AssertionError(args.command)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
