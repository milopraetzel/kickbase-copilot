"""cancel_bid-Vorhaben im Worker (02.09.2026): PlannedAction trägt die Offer-ID.

Neue Datei (L-087). Der Executor-Dispatch ruft client.cancel_bid(lg, pid,
offer_id) — die ID muss aus den Quellen in die Aktion; der manuelle Weg
trägt das '-manual'-Suffix (kein autonomer Pfad).
"""

from types import SimpleNamespace

from kickbase_agent.worker.intents import _kind_label, action_for


def test_action_for_traegt_offer_id_und_manuellen_rule_id() -> None:
    it = SimpleNamespace(
        kind="cancel_bid", player_id="3350", amount=0, rationale="Moritz W. hängt",
        adjusted_from=None, emil_note=None,
        sources={"manual": True, "offer_id": "2663242", "via": "hermes"},
    )
    a = action_for(it, "L1")
    assert a.action_type == "cancel_bid" and a.offer_id == "2663242"
    assert a.rule_id == "intent-cancel_bid-manual" and a.amount is None
    assert a.swap is False


def test_label_fuer_karten_und_berichte() -> None:
    assert _kind_label("cancel_bid") == "Gebots-Rückzug"
    assert _kind_label("bid") == "Gebot"
