"""Schreib-Methoden des KickbaseClient (Gebote, Verkauf, Aufstellung, Bonus)."""

import json

import pytest

from kickbase_agent.kickbase.client import KickbaseClient

L = "https://api.kickbase.com/v4/leagues/42"


@pytest.mark.asyncio
async def test_place_bid_posts_price(httpx_mock):
    httpx_mock.add_response(
        method="POST", url=f"{L}/market/99/offers", json={"ok": True}
    )
    client = KickbaseClient(token="t")
    result = await client.place_bid("42", "99", 5_500_000)

    assert result == {"ok": True}
    req = httpx_mock.get_request()
    assert json.loads(req.content) == {"price": 5_500_000}


@pytest.mark.asyncio
async def test_cancel_bid_deletes_offer(httpx_mock):
    httpx_mock.add_response(method="DELETE", url=f"{L}/market/99/offers/7", json={})
    client = KickbaseClient(token="t")
    await client.cancel_bid("42", "99", "7")

    assert httpx_mock.get_request().method == "DELETE"


@pytest.mark.asyncio
async def test_accept_offer_posts(httpx_mock):
    httpx_mock.add_response(
        method="POST", url=f"{L}/market/99/offers/7/accept", json={"ok": 1}
    )
    client = KickbaseClient(token="t")
    await client.accept_offer("42", "99", "7")

    assert httpx_mock.get_request().url.path.endswith("/accept")


@pytest.mark.asyncio
async def test_decline_offer_posts(httpx_mock):
    httpx_mock.add_response(
        method="POST", url=f"{L}/market/99/offers/7/decline", json={"ok": 1}
    )
    client = KickbaseClient(token="t")
    await client.decline_offer("42", "99", "7")

    assert httpx_mock.get_request().url.path.endswith("/decline")


@pytest.mark.asyncio
async def test_list_player_posts_pi_and_prc(httpx_mock):
    httpx_mock.add_response(method="POST", url=f"{L}/market", json={"ok": 1})
    client = KickbaseClient(token="t")
    await client.list_player("42", "99", 6_000_000)

    assert json.loads(httpx_mock.get_request().content) == {"pi": "99", "prc": 6_000_000}


@pytest.mark.asyncio
async def test_delist_player_deletes(httpx_mock):
    httpx_mock.add_response(method="DELETE", url=f"{L}/market/99", json={})
    client = KickbaseClient(token="t")
    await client.delist_player("42", "99")

    assert httpx_mock.get_request().method == "DELETE"


@pytest.mark.asyncio
async def test_sell_to_kickbase_posts(httpx_mock):
    httpx_mock.add_response(method="POST", url=f"{L}/market/99/sell", json={"ok": 1})
    client = KickbaseClient(token="t")
    await client.sell_to_kickbase("42", "99")

    assert httpx_mock.get_request().url.path.endswith("/sell")


@pytest.mark.asyncio
async def test_set_lineup_posts_formation_and_players(httpx_mock):
    httpx_mock.add_response(method="POST", url=f"{L}/lineup", json={"ok": 1})
    client = KickbaseClient(token="t")
    await client.set_lineup("42", "4-4-2", ["1", "2", "3"])

    assert json.loads(httpx_mock.get_request().content) == {
        "type": "4-4-2",
        "players": ["1", "2", "3"],
    }


@pytest.mark.asyncio
async def test_get_lineup_reads(httpx_mock):
    httpx_mock.add_response(method="GET", url=f"{L}/lineup", json={"lp": []})
    client = KickbaseClient(token="t")
    result = await client.get_lineup("42")

    assert result == {"lp": []}


@pytest.mark.asyncio
async def test_collect_bonus_gets(httpx_mock):
    httpx_mock.add_response(
        method="GET", url="https://api.kickbase.com/v4/bonus/collect", json={"amount": 100}
    )
    client = KickbaseClient(token="t")
    result = await client.collect_bonus()

    assert result == {"amount": 100}
