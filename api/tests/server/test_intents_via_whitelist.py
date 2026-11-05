"""Paket 1, P0-8 (Studie 19.09.2026): Absender-Stolperdraht auf POST /api/intents.

`via` ist Selbstauskunft — alle Schreiber nutzen denselben Basic-Auth-Zugang. Der
Stolperdraht lässt das alte Hermes-Skript (sendet fest `via=hermes`) geschlossen
scheitern; der echte Riegel bleibt Emils Passwort-/Token-Wechsel (E10). Veto,
Anpassen und „Jetzt ausführen" bleiben offen: sie sind Emils Eingriffswege.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from kickbase_agent.db.models import Intent

from . import test_intents_create as base

# Fixtures und Payload des bestehenden Moduls: derselbe Transport, derselbe Plan.
_payload = base._payload
approved_engine_plan = base.approved_engine_plan
client = base.client
db = base.db


@pytest.mark.parametrize("via", ["hermes", "codex", "orchestrator", "irgendwer", "HERMES"])
def test_foreign_or_unknown_sender_is_refused_and_nothing_is_stored(client, db, via):
    r = client.post("/api/intents", json=_payload(via=via))
    assert r.status_code == 403, r.text
    assert "via" in r.json()["detail"]
    assert db.execute(select(Intent)).scalars().first() is None


@pytest.mark.parametrize("via", ["web", "mcp", "claude-session", "owner", " Web "])
def test_whitelisted_sender_is_accepted_and_recorded_normalised(client, db, via):
    r = client.post("/api/intents", json=_payload(via=via))
    assert r.status_code == 200, r.text
    row = db.execute(select(Intent)).scalars().one()
    assert row.sources["via"] == via.strip().lower()
    assert row.sources["via_declared"] is True


@pytest.mark.parametrize("missing", ["absent", "empty"])
def test_missing_sender_stays_mcp_but_is_countable(client, db, missing):
    payload = _payload()
    if missing == "absent":
        del payload["via"]
    else:
        payload["via"] = "  "
    r = client.post("/api/intents", json=payload)
    assert r.status_code == 200, r.text
    row = db.execute(select(Intent)).scalars().one()
    assert row.sources["via"] == "mcp"
    assert row.sources["via_declared"] is False


def test_todays_mcp_remote_payload_still_works(client, db):
    """Der MCP-Remote-Weg (tools_write) sendet heute genau dieses Feld."""
    from kickbase_agent.mcp import tools_write

    assert '"via": "mcp"' in open(tools_write.__file__, encoding="utf-8").read()
    assert client.post("/api/intents", json=_payload(via="mcp")).status_code == 200


def _planned(db) -> Intent:
    now = datetime.now(UTC)
    intent = Intent(
        created_at=now, updated_at=now, kind="bid", player_id="1", player_name="X",
        team_id="1", position=2, amount=1_000_000, rationale="Test",
        sources={"via": "hermes"}, listing_type="kickbase",
        execute_at=now + timedelta(hours=1), status="planned",
    )
    db.add(intent)
    db.commit()
    return intent


def test_veto_without_note_stays_open_even_for_a_foreign_intent(client, db):
    intent = _planned(db)
    r = client.post(f"/api/intents/{intent.id}/veto")
    assert r.status_code == 200, r.text
    assert r.json()["intent"]["status"] == "vetoed"


def test_adjust_and_execute_now_stay_open(client, db):
    intent = _planned(db)
    assert client.post(
        f"/api/intents/{intent.id}/adjust", json={"amount": 1_100_000}
    ).status_code == 200
    assert client.post(f"/api/intents/{intent.id}/execute-now").status_code == 200
