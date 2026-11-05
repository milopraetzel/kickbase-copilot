"""Zwei-DB-Fix: Remote-Modus der MCP-Write-Tools (KBA_REMOTE_API_URL gesetzt).

Live-Writes werden NICHT mehr lokal ausgeführt (lokale DB = falsches Audit),
sondern als manuelles Vorhaben an die Box-API übergeben (POST /api/intents,
CSRF-Header, Basic-Auth). confirm=False erzeugt im Remote-Modus GAR NICHTS.
"""

from __future__ import annotations

import json

import pytest

from kickbase_agent.mcp import tools_write

pytestmark = pytest.mark.asyncio


@pytest.fixture
def remote_env(monkeypatch):
    monkeypatch.setenv("KBA_REMOTE_API_URL", "https://box.example/api")
    monkeypatch.setenv("KBA_REMOTE_API_AUTH", "emil:geheim")


async def test_place_bid_remote_creates_intent(world, env, remote_env, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="https://box.example/api/intents",
        json={"intent": {"id": 7, "kind": "bid", "player_id": "99", "status": "planned"}},
    )
    out = await tools_write.place_bid(
        "99", 6_000_000, "Starter 100% MW", confirm=True, emil_go="mach so (24.08.)"
    )
    assert out["status"] == "intent_planned" and out["remote"] is True
    assert out["intent"]["id"] == 7
    req = httpx_mock.get_requests()[0]
    assert req.headers["X-Requested-With"] == "kba"
    assert req.headers["Authorization"].startswith("Basic ")
    body = json.loads(req.content)
    assert body["kind"] == "bid" and body["amount"] == 6_000_000
    assert "mach so" in body["emil_go"] and body["via"] == "mcp"


async def test_place_bid_remote_still_gates_emil_go(world, env, remote_env, httpx_mock):
    out = await tools_write.place_bid("99", 6_000_000, "Tier-1", confirm=True)
    assert out["status"] == "refused"  # Gate greift VOR dem Remote-Call
    assert not httpx_mock.get_requests()


async def test_place_bid_remote_preview_touches_nothing(world, env, remote_env, httpx_mock):
    out = await tools_write.place_bid("99", 2_000_000, "Vorschau", confirm=False)
    assert out["status"] == "preview_only" and out["remote"] is True
    assert not httpx_mock.get_requests()


async def test_remote_duplicate_intent_is_reported(world, env, remote_env, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="https://box.example/api/intents",
        status_code=409,
        json={"detail": {"error": "Offenes Vorhaben existiert bereits",
                          "intent": {"id": 3, "player_id": "99"}}},
    )
    out = await tools_write.place_bid(
        "99", 6_000_000, "nochmal", confirm=True, emil_go="go"
    )
    assert out["status"] == "duplicate_intent"
    assert out["intent"]["id"] == 3


async def test_list_player_remote_creates_intent(world, env, remote_env, httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="https://box.example/api/intents",
        json={"intent": {"id": 8, "kind": "list", "player_id": "9", "status": "planned"}},
    )
    out = await tools_write.list_player("9", 2_000_000, "Bank listen", confirm=True)
    assert out["status"] == "intent_planned"
    assert json.loads(httpx_mock.get_requests()[0].content)["kind"] == "list"


async def test_rejected_engine_plan_is_not_reported_as_duplicate(
    world, env, remote_env, httpx_mock,
):
    httpx_mock.add_response(
        method="POST", url="https://box.example/api/intents", status_code=409,
        json={"detail": {"error": "Keine finanzierbare Elf mit diesem Gebot.",
                         "selected": [], "plan_id": "plan-checked"}},
    )
    out = await tools_write.place_bid("99", 3_000_000, "Kauf prüfen", confirm=True)
    assert out["status"] == "refused"
    assert "finanzierbare Elf" in out["reason"]
    assert out["purchase_plan"]["plan_id"] == "plan-checked"
    assert len(httpx_mock.get_requests()) == 1

async def test_collect_bonus_remote_is_refused(world, env, remote_env):
    """P1 (Codex-Folge-Review 24.08.): Im Remote-Modus sammelt die Box die Prämie
    selbst (Slots 08:00/13:03/18:03) — der Mac macht KEINEN lokalen Live-Write
    (Ein-Schreiber-Prinzip); kein HTTP-Call, keine lokale Audit-Zeile."""
    out = await tools_write.collect_bonus()
    assert out["status"] == "refused_remote"
    assert "Box-Worker" in out["reason"]


async def test_remote_intent_sends_write_token_header(
    world, env, remote_env, monkeypatch, httpx_mock
):
    """P1-Gegenstück: Ist KBA_API_WRITE_TOKEN lokal gesetzt, geht er als
    X-KBA-Write-Token mit — die Box-Route verlangt ihn, sobald IHR Setting steht."""
    monkeypatch.setenv("KBA_API_WRITE_TOKEN", "s3cret")
    httpx_mock.add_response(
        method="POST",
        url="https://box.example/api/intents",
        json={"intent": {"id": 8, "kind": "bid", "player_id": "99", "status": "planned"}},
    )
    out = await tools_write.place_bid("99", 3_000_000, "Testgebot", confirm=True)
    assert out["status"] == "intent_planned"
    req = httpx_mock.get_requests()[0]
    assert req.headers["X-KBA-Write-Token"] == "s3cret"


async def test_delist_player_remote_creates_intent(world, env, remote_env, httpx_mock):
    """Delist-Weg (22.09.2026): confirm=True legt ein manuelles Vorhaben kind=delist
    auf der Box an (via=mcp, amount 0, kein emil_go nötig) — kein lokaler Write."""
    httpx_mock.add_response(
        method="POST",
        url="https://box.example/api/intents",
        json={"intent": {"id": 9, "kind": "delist", "player_id": "3", "status": "planned"}},
    )
    out = await tools_write.delist_player("3", "Olise halten — Listing raus", confirm=True)
    assert out["status"] == "intent_planned" and out["remote"] is True
    assert out["intent"]["kind"] == "delist"
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["kind"] == "delist" and body["player_id"] == "3"
    assert body["amount"] == 0 and body["via"] == "mcp"
    assert world.client.writes == []


async def test_delist_player_remote_preview_touches_nothing(world, env, remote_env, httpx_mock):
    out = await tools_write.delist_player("3", "Vorschau", confirm=False)
    assert out["status"] == "preview_only" and out["remote"] is True
    assert not httpx_mock.get_requests()
