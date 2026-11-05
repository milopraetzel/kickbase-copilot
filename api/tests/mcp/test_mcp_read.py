"""Read-Tools gegen den FakeClient: kompakte dicts, Geld als *_eur + *_fmt, Fehler als dict."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from kickbase_agent.db.models import CommunityTip, TransferAction
from kickbase_agent.mcp import _util, tools_read
from kickbase_agent.mcp._util import fmt_money

PLAYER_LAST_5 = [{"hp": True, "p": 47}, {"hp": False}, {"hp": True, "p": 60},
                 {"hp": True, "p": 71}, {"hp": True, "p": 12}]


def test_fmt_money_matches_spec_examples():
    assert fmt_money(7_410_000) == "7,41 M"
    assert fmt_money(850_000) == "850 k"
    assert fmt_money(120) == "120 €"
    assert fmt_money(-1_500_000) == "-1,50 M"
    assert fmt_money(None) == "—"


async def test_league_state_reports_budget_phase_trade_budget_and_kickoff(world):
    state = await tools_read.league_state()
    assert "error" not in state
    assert state["league_id"] == "L1"
    assert (state["budget_eur"], state["budget_fmt"]) == (7_410_000, "7,41 M")
    assert state["team_value_eur"] == 8_500_000 + 19_000_000 + 25_000_000 + 9_000_000
    assert state["squad_count"] == 12
    # Offene Gebote (ofs[] mit unserer User-ID) zählen als Exposure
    assert state["open_bids"] == 1 and state["open_bid_exposure_eur"] == 5_120_000
    # 33-%-Regel: max_single_bid(7,41 M, 52,5 M) = 7,41 M + 17,5 M
    assert state["kaufkraft_eur"] == 7_410_000 + 61_500_000 // 3
    assert state["overdraft_floor_eur"] == -(61_500_000 // 3)
    # pbaa (Konto nach offenen Geboten) kommt von Kickbase: 7,41 M − 5,12 M
    assert state["budget_after_bids_eur"] == 7_410_000 - 5_120_000
    assert state["kaufkraft_after_bids_eur"] == state["kaufkraft_eur"] - 5_120_000
    assert state["phase"] in ("invest", "purge", "hold")
    tb = state["trade_budget"]
    assert (tb["used"], tb["limit"], tb["saturated"], tb["my_name"]) == (1, 200, False, "emil")
    assert tb["line"].startswith("🟢 Trade-Budget: 1/200")
    assert state["matchday_deadline"]["matchday"] == 1
    assert state["next_kickoff"] is not None
    assert "duration_s" in state


async def test_squad_marks_lineup_listed_and_member_fields(world):
    out = await tools_read.squad()
    assert out["count"] == 12 and out["team_value_fmt"] == "61,50 M"
    by_id = {p["id"]: p for p in out["players"]}
    neuer, kimmich, musiala = by_id["1"], by_id["2"], by_id["3"]
    assert neuer["pos"] == "TW" and neuer["club"] == "Bayern"
    assert neuer["in_lineup"] is True and neuer["lineup_order"] == 0
    assert neuer["lineup_prob"] == 1 and neuer["fitness_status"] == 0
    assert neuer["img"] == "https://kickbase.b-cdn.net/content/file/neuer.png"
    assert (neuer["mv24_eur"], neuer["mv24_fmt"]) == (120_000, "120 k")
    assert kimmich["fitness_status"] == 2 and kimmich["in_lineup"] is True
    # Musiala ist gelistet (steht auch im Markt-Payload) und nicht in der Aufstellung
    assert musiala["listed"] is True and musiala["in_lineup"] is False
    assert out["listed"] == 1 and out["in_lineup"] == 2
    # Sortierung nach Marktwert absteigend
    assert [p["id"] for p in out["players"]][:3] == ["3", "2", "1"]


async def test_market_exposes_seller_expiry_own_bid_and_sorts(world):
    out = await tools_read.market(limit=10, sort="mv")
    assert out["count"] == 3 and out["sort"] == "mv"
    rows = {p["id"]: p for p in out["players"]}
    gem, striker, musiala = rows["99"], rows["88"], rows["3"]
    assert gem["seller"] == "" and gem["expires_in_s"] == 3600
    assert gem["listing_type"] == "kickbase" and striker["listing_type"] == "manager"
    assert (gem["bid_ours_eur"], gem["bid_ours_fmt"]) == (5_120_000, "5,12 M")
    assert gem["price_eur"] == 5_500_000 and gem["price_vs_mv_pct"] == 110.0
    assert striker["seller"] == "Felix K." and striker["bid_ours_eur"] is None
    assert striker["club"] == "Freiburg" and striker["pos"] == "STU"
    assert musiala["in_squad"] is True and musiala["seller"] == "emil"
    assert [p["id"] for p in out["players"]] == ["3", "99", "88"]
    # expires: Kickbase-Countdowns zuerst, Manager-Listings (exs 0) zuletzt
    soon = await tools_read.market(limit=2, sort="expires")
    assert [p["id"] for p in soon["players"]] == ["99", "88"]
    assert soon["returned"] == 2 and soon["count"] == 3


async def test_market_score_sort_uses_engine_scores_without_ligainsider(world):
    out = await tools_read.market(sort="score")
    assert "error" not in out
    assert all(isinstance(p["score"], float) for p in out["players"])
    assert [p["score"] for p in out["players"]] == sorted(
        (p["score"] for p in out["players"]), reverse=True
    )
    assert "OHNE LigaInsider" in out["score_note"]


async def test_market_rejects_unknown_sort_as_error_dict(world):
    out = await tools_read.market(sort="hype")
    assert out["error"].startswith("ToolError: Unbekannte Sortierung")
    assert "score" in out["hint"]


async def test_own_bids_lists_amount_seller_listing_price_and_expiry(world):
    out = await tools_read.own_bids()
    assert out["count"] == 1 and out["exposure_fmt"] == "5,12 M"
    bid = out["bids"][0]
    assert bid["player_id"] == "99" and bid["name"] == "Hidden Gem"
    assert bid["seller"] == "" and bid["listing_price_eur"] == 5_500_000
    assert bid["expires_in_s"] == 3600 and bid["offer_id"] == "u1"
    assert bid["amount_vs_listing_pct"] == 93.1


async def test_player_detail_maps_real_payload_including_ln_instead_of_n(world):
    out = await tools_read.player("2")
    assert "error" not in out
    # Detail-Endpoint liefert `ln` statt `n` (Live-Fund 23.08.) — kein KeyError
    assert out["name"] == "Bernardo" and out["club"] == "Hoffenheim" and out["pos"] == "ABW"
    assert (out["mv_eur"], out["mv24_eur"], out["mv_trend"]) == (7_811_721, -204_124, 2)
    assert out["lineup_prob"] == 3
    assert out["fitness_status"] == 2 and out["fitness_text"] == "Achilles tendon problems"
    assert out["status_list"] == [2]
    assert (out["total_points"], out["avg_points"]) == (2381, 82)
    assert out["last_matches"] == PLAYER_LAST_5 and len(out["last_matches"]) == 5
    assert out["plpt"] == "Ligainsider" and out["plpurl"].startswith("https://www.ligainsider.de/")
    assert (out["shirt_number"], out["yellow"], out["seconds_played"]) == (13, 9, 152_866)
    assert out["img"].endswith("/content/file/bernardo.png")
    assert "stxt" in out["raw_keys"] and "ln" in out["raw_keys"]


async def test_player_detail_tolerates_missing_optional_fields(world):
    out = await tools_read.player("1")  # fitter Spieler: kein stxt/tp/ap
    assert out["name"] == "Marcel Hoffmeier" and out["fitness_status"] == 0
    assert out["fitness_text"] == "" and out["status_list"] == []
    assert out["total_points"] is None and out["avg_points"] is None
    assert out["last_matches"] == [{"hp": False}] * 5


async def test_feed_filters_types_and_summarises_known_events(world):
    out = await tools_read.feed(limit=10)
    assert out["count"] == 4
    first = out["items"][0]
    assert first["t"] == 15 and first["summary"] == "emil kauft Hoffmeier für 8,70 M"
    assert out["items"][1]["summary"] == "Prämie kassiert: +50 k (Tag 6)"
    assert out["items"][2]["summary"] == "Stefan verkauft Vozar für 1,28 M"
    assert out["items"][3]["summary"].startswith("Markt-Event: Listing")
    only_bonus = await tools_read.feed(limit=10, types=[22])
    assert only_bonus["count"] == 1 and only_bonus["items"][0]["data"] == {"bn": 50000, "day": 6}
    assert only_bonus["types"] == [22]


async def test_audit_log_reads_transfer_actions_newest_first(world):
    with Session(world.engine, future=True) as s:
        s.add(TransferAction(
            league_id="L1", action_type="bid", player_id="99", amount=5_120_000,
            status="executed", idempotency_key="k1", rule_id="mcp-bid",
            rationale="Starter, unterbewertet", dry_run=False,
            created_at=datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
        ))
        s.add(TransferAction(
            league_id="L1", action_type="bonus", status="noop", idempotency_key="k2",
            rule_id="auto-bonus", dry_run=False,
            created_at=datetime(2026, 8, 23, 6, 3, tzinfo=UTC),
        ))
        s.commit()
    out = await tools_read.audit_log(limit=10)
    assert out["count"] == 2
    assert [a["type"] for a in out["actions"]] == ["bonus", "bid"]
    bid = out["actions"][1]
    assert (bid["amount_eur"], bid["amount_fmt"], bid["dry_run"]) == (5_120_000, "5,12 M", False)
    assert bid["created_at"].startswith("2026-08-22T12:00:00")  # Berliner Zeit
    only = await tools_read.audit_log(status="noop")
    assert [a["status"] for a in only["actions"]] == ["noop"]


async def test_active_tips_lists_recent_tips(world):
    with Session(world.engine, future=True) as s:
        s.add(CommunityTip(player_name="Hoffmeier", weight=0.6, source="youtube",
                           note="Stamm-Innenverteidiger", created_at=datetime.now(UTC)))
        s.commit()
    out = await tools_read.active_tips()
    assert out["count"] == 1 and out["ttl_days"] == 4
    assert out["tips"][0]["player_name"] == "Hoffmeier" and out["tips"][0]["weight"] == 0.6


async def test_research_data_collects_cli_text_instead_of_stdout(world, monkeypatch, capsys):
    async def fake_dump(echo):
        echo("BUDGET: 7.4M € | PHASE: invest")
        echo("\n== KADER ==")
        echo("Kimmich | Bayern | Pos3 | MW 19.0M | Score 0.61 | LI:starter | KB:St.2")

    monkeypatch.setattr(tools_read, "_dump_data", fake_dump)
    out = await tools_read.research_data()
    assert out["lines"] == 3
    assert out["text"].startswith("BUDGET: 7.4M €") and "KB:St.2" in out["text"]
    assert capsys.readouterr().out == ""  # nichts auf stdout (MCP-Protokoll!)


async def test_tool_guard_turns_exceptions_into_error_dict(world, monkeypatch):
    async def boom():
        raise RuntimeError("Kickbase down")

    monkeypatch.setattr(tools_read, "make_kickbase_client", boom)
    out = await tools_read.squad()
    assert out == {"error": "RuntimeError: Kickbase down", "hint": _util.hint_for(RuntimeError())}
    assert "kba-mcp" in out["hint"]


async def test_missing_league_is_an_error_with_hint(world, monkeypatch):
    async def no_league():
        from types import SimpleNamespace

        return world.client, SimpleNamespace(user_id="u1", leagues=[], league_id=None)

    monkeypatch.setattr(tools_read, "make_kickbase_client", no_league)
    out = await tools_read.own_bids()
    assert out["error"] == "ToolError: Keine Liga konfiguriert."
    assert "KICKBASE_LEAGUE_ID" in out["hint"]
