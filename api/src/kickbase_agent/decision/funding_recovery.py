"""Finanzierung durch belegte Gebotsaufgabe reparieren, ohne künftige Erlöse."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import combinations
from typing import Any


def _stamp(value) -> datetime | None:
    try:
        value = datetime.fromisoformat(value) if isinstance(value, str) else value
        if isinstance(value, datetime) and value.tzinfo is not None:
            return value.astimezone(UTC)
    except ValueError:
        pass
    return None


def _positive(value) -> bool:
    return type(value) is int and value > 0


def _live_rows(bids) -> list[dict] | None:
    if not isinstance(bids, (list, tuple)):
        return None
    rows, seen = [], set()
    for bid in bids:
        if not isinstance(bid, dict):
            return None
        pid = str(bid.get("player_id") or "")
        amount = bid.get("amount", bid.get("amount_eur"))
        if not pid or pid in seen or not _positive(amount):
            return None
        seen.add(pid)
        rows.append({"player_id": pid, "offer_id": str(bid.get("offer_id") or ""),
                     "amount_eur": amount})
    return sorted(rows, key=lambda row: row["player_id"])


def _exposure(live: list[dict], pending: list[dict]) -> int:
    amounts = {row["player_id"]: row["amount_eur"] for row in live}
    for row in pending:
        pid = str(row.get("player_id") or "")
        amount = row.get("amount")
        if pid and _positive(amount):
            amounts[pid] = max(amounts.get(pid, 0), amount)
    return sum(amounts.values())


def build_funding_recovery(
    *, league_id: str, plan_id: str, budget: int, owned_player_ids, live_bids,
    pending_bids, now: datetime, market_by_id: dict, protected_ids=frozenset(),
    manual_live_ids=frozenset(), points_by_id: dict | None = None,
) -> dict:
    """Erst lokale Reservierungen freigeben, dann wenige echte Gebote aufgeben.

    Die Aufgabe beendet diese Auktion für uns. Sie ist keine Preisänderung und
    plant weder einen Verkauf aus der Elf noch einen davon finanzierten Neukauf.
    """
    stamp = _stamp(now)
    if stamp is None or type(budget) is not int:
        raise ValueError("Finanzierungsplan benötigt datierten echten Kontostand.")
    now = stamp
    result: dict[str, Any] = {
        "status": "funded", "observed_at": now.isoformat(), "deficit_eur": 0,
        "released_pending_ids": [], "actions": [], "notes": [],
    }
    live = _live_rows(live_bids)
    if live is None:
        result.update(status="blocked", notes=["Offene Gebote sind nicht eindeutig beziffert."])
        return result
    result["deficit_eur"] = max(0, _exposure(live, pending_bids) - budget)
    if not result["deficit_eur"]:
        return result
    if budget < 0:
        result.update(status="cash_deficit", cash_deficit_eur=-budget,
                      notes=["Das echte Konto ist negativ. Rückzüge erzeugen kein Geld; "
                             "Verkäufe brauchen tatsächliche Gutschrift. Notbremsenplan nötig."])
        return result
    pending = list(pending_bids)
    removable = [row for row in pending if row.get("status") == "planned"
                 and not row.get("manual") and type(row.get("intent_id")) is int]
    # Größte reine Reservierung zuerst; tatsächliche Livegebote bleiben separat.
    for row in sorted(removable, key=lambda r: int(r.get("amount") or 0), reverse=True):
        if _exposure(live, pending) <= budget:
            break
        pending.remove(row)
        result["released_pending_ids"].append(row["intent_id"])
    live_deficit = sum(row["amount_eur"] for row in live) - budget
    if live_deficit <= 0:
        result.update(status="release_pending" if _exposure(live, pending) <= budget else "blocked")
        result["notes"] = ["Automatische Kaufreservierungen zuerst freigeben; danach neu planen."
                           if result["status"] == "release_pending" else
                           "Manuelle/laufende Reservierungen verhindern weitere Käufe."]
        return result
    raw = {str(row.get("player_id")): row for row in live_bids}
    protected_pending_ids = {
        str(row.get("player_id")) for row in pending_bids
        if row.get("manual") or row.get("status") == "executing"
    }
    manual_pending_ids = {str(row.get("player_id")) for row in pending_bids if row.get("manual")}
    eligible = []
    for row in live:
        pid = row["player_id"]
        if (pid in protected_ids or pid in manual_live_ids or pid in protected_pending_ids
                or pid in set(map(str, owned_player_ids))
                or not row["offer_id"]):
            continue
        listing = market_by_id.get(pid)
        expires = _stamp(getattr(listing, "expires_at", None))
        seconds = raw[pid].get("expires_in_s")
        if expires is None and _positive(seconds) and not raw[pid].get("seller"):
            expires = now + timedelta(seconds=seconds)
        # Ohne belegten Endzeitpunkt kann der Verzicht nicht sicher begrenzt werden.
        if expires is None or expires <= now or raw[pid].get("seller"):
            continue
        provenance = raw[pid].get("funding_provenance")
        if not isinstance(provenance, dict):
            continue
        original_end = _stamp(provenance.get("original_listing_expires_at"))
        if (not _positive(provenance.get("original_bid_action_id")) or original_end is None
                or now >= original_end or abs((original_end - expires).total_seconds()) > 120):
            continue
        eligible.append({**row, "expires_at": expires.isoformat(),
                         "original_bid_action_id": provenance["original_bid_action_id"],
                         "original_listing_expires_at": original_end.isoformat()})
    selected = None
    if len(eligible) <= 16:
        for count in range(1, len(eligible) + 1):
            choices = [subset for subset in combinations(eligible, count)
                       if sum(row["amount_eur"] for row in subset) >= live_deficit]
            if choices:
                selected = min(choices, key=lambda subset: (
                    sum((points_by_id or {}).get(row["player_id"], 0) for row in subset),
                    sum(row["amount_eur"] for row in subset),
                    tuple(row["player_id"] for row in subset),
                ))
                break
    if selected is None:
        result.update(status="blocked", notes=[
            "Unterdeckung bleibt. Kein ausreichend belegter, ungeschützter Gebotsrückzug "
            "mit bekanntem Auktionsende; bestehende Anpfiff-Notbremse bleibt zuständig."
        ])
        result["blocked_cancellations"] = [
            {
                **row, "requires_manual_decision":
                    row["player_id"] in manual_live_ids | manual_pending_ids,
                "reason": "Manuell gesetztes oder verändertes Gebot bleibt geschützt."
                if row["player_id"] in manual_live_ids | manual_pending_ids else
                "Ein Kaufversuch läuft; erst seinen tatsächlichen Ausgang abwarten.",
                "cash_after_commitments_eur": budget - _exposure(
                    [other for other in live if other["player_id"] != row["player_id"]],
                    [other for other in pending
                     if str(other.get("player_id")) != row["player_id"]],
                ),
            }
            for row in live
            if row["player_id"] in manual_live_ids or row["player_id"] in protected_pending_ids
        ]
        if result["blocked_cancellations"]:
            result["notes"].append(
                "Eine manuelle Entscheidung oder laufende Ausführung blockiert die Finanzierung. "
                "Die Alternative und ihr Freigabe-/Wartebedarf sind ausgewiesen."
            )
        return result
    result["status"] = "abandon_bids"
    result["notes"] = ["Gebote vollständig aufgeben; kein Wiedereinstieg in dieselbe Auktion. "
                       "Erst den bestätigten Rückzug abwarten, dann die Mannschaft neu planen."]
    abandoned_ids = {row["player_id"] for row in selected}
    remaining = _exposure([row for row in live if row["player_id"] not in abandoned_ids], pending)
    result["remaining_deficit_eur"] = max(0, remaining - budget)
    if result["remaining_deficit_eur"]:
        result["status"] = "abandon_bids_partial"
        result["notes"].append(
            "Die Live-Unterdeckung wird reduziert. Unantastbare manuelle/laufende "
            f"Reservierungen lassen weiter {result['remaining_deficit_eur']:,} € ungedeckt; "
            "es gibt weiterhin kein finanziertes Kaufpaket."
        )
    for row in selected:
        expiry = _stamp(row["expires_at"])
        assert expiry is not None  # Alle ausgewählten Endzeiten wurden oben geprüft.
        review = {
            "schema_version": 1, "purpose": "funding_abandon_bid", "plan_id": plan_id,
            "league_id": league_id, "observed_at": now.isoformat(),
            "valid_until": min(now + timedelta(hours=3), expiry).isoformat(),
            "budget_eur": budget, "owned_player_ids": sorted(map(str, owned_player_ids)),
            "live_bids": live, "selected_cancellations": list(selected),
            "player_id": row["player_id"], "offer_id": row["offer_id"],
            "amount_eur": row["amount_eur"], "abandon_until": row["expires_at"],
            "original_bid_action_id": row["original_bid_action_id"],
            "original_listing_expires_at": row["original_listing_expires_at"],
        }
        result["actions"].append({**row, "funding_review": review})
    return result


def funding_review_veto(
    review, *, league_id, player_id, offer_id, amount, now,
    budget=None, live_bids=None, owned_player_ids=None,
) -> str | None:
    """Frischer Zustand muss den konkreten Rückzug weiterhin erforderlich machen."""
    invalid = "Gebotsaufgabe ohne vollständigen, frischen Finanzierungsnachweis."
    if not isinstance(review, dict):
        return invalid
    at, start, end, abandon = (_stamp(v) for v in (
        now, review.get("observed_at"), review.get("valid_until"), review.get("abandon_until")))
    if (type(review.get("schema_version")) is not int or review["schema_version"] != 1
            or review.get("purpose") != "funding_abandon_bid"
            or not review.get("plan_id") or not all((at, start, end, abandon))):
        return invalid
    assert at is not None and start is not None and end is not None and abandon is not None
    original_end = _stamp(review.get("original_listing_expires_at"))
    if (not _positive(review.get("original_bid_action_id")) or original_end is None
            or at >= original_end or abs((original_end - abandon).total_seconds()) > 120):
        return "Ursprünglicher Gebots-/Auktionsbeleg fehlt oder passt nicht zum Listing."
    if not start <= at < end <= start + timedelta(hours=3) or end > abandon:
        return "Finanzierungsnachweis ist abgelaufen oder zeitlich widersprüchlich."
    if (str(review.get("league_id")) != str(league_id)
            or str(review.get("player_id")) != str(player_id)
            or not offer_id or str(review.get("offer_id")) != str(offer_id)
            or not _positive(amount) or review.get("amount_eur") != amount):
        return "Gebotsaufgabe passt nicht zur geprüften Auktion und ihrem Betrag."
    if (type(budget) is not int or type(review.get("budget_eur")) is not int
            or budget != review["budget_eur"] or budget < 0
            or not isinstance(owned_player_ids, (list, set, frozenset, tuple))
            or not isinstance(review.get("owned_player_ids"), list)
            or any(not isinstance(pid, str) for pid in review["owned_player_ids"])
            or set(map(str, owned_player_ids)) != set(review["owned_player_ids"])):
        return "Kontostand/Besitz fehlt oder ist verändert; Finanzierungsplan neu berechnen."
    expected, current = _live_rows(review.get("live_bids")), _live_rows(live_bids)
    cancellations = _live_rows(review.get("selected_cancellations"))
    if expected is None or current is None or not cancellations:
        return invalid
    if str(player_id) in set(map(str, owned_player_ids)):
        return "Spieler bereits im Besitz; ein veraltetes Gebot darf nicht zurückgezogen werden."
    expected_map = {row["player_id"]: row for row in expected}
    current_map = {row["player_id"]: row for row in current}
    selected_map = {row["player_id"]: row for row in cancellations}
    target = {"player_id": str(player_id), "offer_id": str(offer_id), "amount_eur": amount}
    if (selected_map.get(str(player_id)) != target or current_map.get(str(player_id)) != target
            or any(expected_map.get(pid) != row for pid, row in selected_map.items())
            or any(expected_map.get(pid) != row for pid, row in current_map.items())
            or (expected_map.keys() - current_map.keys()) - selected_map.keys()):
        return "Offene Gebote sind verändert; Finanzierungsplan neu berechnen."
    if sum(row["amount_eur"] for row in current) <= budget:
        return "Alle echten offenen Gebote sind gedeckt; Rückzug nicht mehr erforderlich."
    still_selected = sum(row["amount_eur"] for pid, row in current_map.items()
                         if pid in selected_map)
    if sum(row["amount_eur"] for row in current) - still_selected > budget:
        return "Die ausgewählten Rückzüge reichen nicht zur Deckung der offenen Gebote."
    return None
