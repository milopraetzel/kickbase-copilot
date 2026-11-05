"""Tatsächliche Gebote und noch offene Kaufvorhaben gemeinsam reservieren."""

from __future__ import annotations

from kickbase_agent.kickbase.models import MarketPlayer


def merge_commitments(
    live_bids: list[dict], pending: list[dict], market: dict[str, MarketPlayer]
) -> dict[str, dict]:
    """Je Spieler das höchste echte Engagement, auch bei Live-/Intent-Doppelung.

    Unbekannte Positionen bleiben 0, belegen aber trotzdem Geld und einen Platz.
    Ein laufendes Gebot ist eine Reservierung, niemals bereits Kaderbesitz.
    """
    result: dict[str, dict] = {}
    for row in [*pending, *live_bids]:
        pid = str(row.get("player_id") or "")
        if not pid:
            continue
        listing = market.get(pid)
        old = result.get(pid, {})
        result[pid] = {
            **old,
            **row,
            "player_id": pid,
            "amount": max(int(old.get("amount") or 0), int(row.get("amount") or 0)),
            "position": int(listing.player.position) if listing else int(row.get("position") or 0),
            "team_id": listing.player.team_id if listing else str(row.get("team_id") or ""),
            "is_manager": bool(
                (listing and listing.seller_id)
                or row.get("seller")
                or row.get("listing_type") == "manager"
                or old.get("is_manager")
            ),
        }
    return result

