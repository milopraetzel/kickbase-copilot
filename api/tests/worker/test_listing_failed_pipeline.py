"""Worker-Seite von Doktrin-Satz 3: Roh-Parser + Vorhaben-Entwürfe (01.09.2026).

Neue Datei (L-087). Markt-Items in der echten v4-Form (Muster
tests/worker/test_intents.py: i/fn/n/pos/mv/prc/exs/tid/u/ofs/dt) — nie
erfundene Felder (Regel 6).
"""

from datetime import UTC, datetime, timedelta

from kickbase_agent.agent.executor import PlannedAction
from kickbase_agent.decision.models import Proposal
from kickbase_agent.worker.intents import (
    ACCEPT_VETO_WINDOW,
    VETO_WINDOW,
    failed_listing_drafts,
    failed_listings_from_raw,
)

_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
_USER = "2663242"


def _item(
    pid: str,
    *,
    seller: str = _USER,
    listed_hours_ago: float = 72.0,
    ofs: list | None = None,
    exs: int = 0,
    mv: int = 900_000,
    dt_missing: bool = False,
) -> dict:
    it = {
        "i": pid,
        "fn": "Testo",
        "n": f"Spieler{pid}",
        "pos": 3,
        "mv": mv,
        "prc": mv,
        "exs": exs,
        "tid": "7",
        "u": {"i": seller, "n": "emil" if seller == _USER else "Rivale"},
        "ofs": ofs or [],
    }
    if not dt_missing:
        it["dt"] = (_NOW - timedelta(hours=listed_hours_ago)).isoformat().replace("+00:00", "Z")
    return it


def test_parser_findet_altes_eigenes_listing_ohne_angebot() -> None:
    raw = {"it": [_item("p1", listed_hours_ago=72)]}
    failed = failed_listings_from_raw(raw, _USER, _NOW)
    assert len(failed) == 1
    f = failed[0]
    assert f.player_id == "p1"
    assert f.market_value == 900_000
    assert 71.9 < f.age_hours < 72.1


def test_parser_ignoriert_listing_mit_angebot() -> None:
    """Ein `ofs`-Eintrag gehört dem Accept-Pfad — nie parallel Sofortverkauf."""
    ofs = [{"u": "U9", "uop": 950_000, "uoid": "O1"}]
    raw = {"it": [_item("p1", ofs=ofs, listed_hours_ago=100)]}
    assert failed_listings_from_raw(raw, _USER, _NOW) == []


def test_parser_ignoriert_fremde_und_frische_listings() -> None:
    raw = {
        "it": [
            _item("fremd", seller="U7", listed_hours_ago=100),
            _item("frisch", listed_hours_ago=47.5),
        ]
    }
    assert failed_listings_from_raw(raw, _USER, _NOW) == []


def test_parser_ignoriert_kickbase_countdown_und_fehlendes_dt() -> None:
    """exs > 0 = Kickbase-Listing (rollt selbst aus); ohne `dt` kein Alters-Urteil."""
    raw = {
        "it": [
            _item("countdown", exs=3600, listed_hours_ago=100),
            _item("ohne_dt", dt_missing=True),
        ]
    }
    assert failed_listings_from_raw(raw, _USER, _NOW) == []


def test_parser_ueberlebt_kaputtes_dt() -> None:
    it = _item("p1", listed_hours_ago=100)
    it["dt"] = "kein-datum"
    assert failed_listings_from_raw({"it": [it]}, _USER, _NOW) == []


def _prop(pid: str, amount: int) -> Proposal:
    return Proposal(
        action=PlannedAction(
            action_type="sell",
            league_id="L1",
            player_id=pid,
            amount=amount,
            rule_id="listing_failed",
        ),
        rule_id="listing_failed",
        rationale="Testo: Listing gescheitert.",
        score=0.0,
        requires_approval=amount > 5_000_000,
        player_name=f"Testo Spieler{pid}",
    )


def test_drafts_tragen_fenster_und_quellen() -> None:
    failed = failed_listings_from_raw({"it": [_item("p1", listed_hours_ago=72)]}, _USER, _NOW)
    drafts = failed_listing_drafts([_prop("p1", 900_000)], {f.player_id: f for f in failed}, _NOW)
    assert len(drafts) == 1
    d = drafts[0]
    assert d.kind == "sell"
    assert d.listing_type == "manager"
    assert d.deadline is None
    assert d.execute_at == _NOW + ACCEPT_VETO_WINDOW
    assert d.sources["rule_id"] == "listing_failed"
    assert d.sources["listing_age_h"] == 72.0
    assert d.team_id == "7" and d.position == 3


def test_grosse_drafts_bekommen_emils_zwei_stunden_fenster() -> None:
    failed = failed_listings_from_raw(
        {"it": [_item("p1", listed_hours_ago=72, mv=6_000_000)]}, _USER, _NOW
    )
    drafts = failed_listing_drafts([_prop("p1", 6_000_000)], {f.player_id: f for f in failed}, _NOW)
    assert drafts[0].execute_at == _NOW + VETO_WINDOW


def test_fremde_proposals_werden_ignoriert() -> None:
    """Nur sell/listing_failed-Proposals werden zu Entwürfen — nichts anderes."""
    andere = Proposal(
        action=PlannedAction(action_type="sell", league_id="L1", player_id="p9", amount=1),
        rule_id="take_profit",
        rationale="anderer Pass",
        score=0.9,
        requires_approval=False,
        player_name="Anders",
    )
    assert failed_listing_drafts([andere], {}, _NOW) == []


def test_parser_ignoriert_schaufenster_anker_listing() -> None:
    """Ein Schaufenster-Listing über 105 % MW (z. B. 125 % MW oder Emils Wunschpreis)
    wird nach 48 h ohne Angebot nicht per Sofortverkauf an den Computer verramscht."""
    it = _item("p1", listed_hours_ago=72, mv=1_000_000)
    it["prc"] = 1_250_000
    assert failed_listings_from_raw({"it": [it]}, _USER, _NOW) == []

