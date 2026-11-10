"""Tests fuer das Neuro-Symbolische Hugging-Face-Paket (`kickbase_agent.hf`)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from kickbase_agent.hf import (
    AiDecisionEnvelope,
    AiPlayerValuation,
    EventSnapshot,
    HindsightOutcome,
    HuggingFaceJudgeClient,
    ManagerContext,
    OpenBidContext,
    PlayerSnapshot,
    anonymize_context,
    build_dpo_record,
    build_sft_record,
    compute_hindsight_reward,
    deanonymize_text,
    export_jsonl,
    should_invoke_ai,
    verify_neuro_plan,
)


def _sample_context() -> ManagerContext:
    squad = tuple(
        PlayerSnapshot(
            player_id=f"s{i}",
            name=f"Stammspieler {i}",
            club_id=f"C{i % 5}",
            position=(i % 3) + 2,
            market_value_eur=12_000_000,
            avg_points=98.0,
            recent_minutes=(90, 90),
            lineup_prob=1,
        )
        for i in range(11)
    ) + (
        PlayerSnapshot(
            player_id="flip_held",
            name="Jeffrey Gouweleeuw",
            club_id="C9",
            position=2,
            market_value_eur=8_517_716,
            change_24h_eur=210_000,
            change_3d_pct=0.09,
            is_trade_holding=True,
        ),
    )
    market = (
        PlayerSnapshot(
            player_id="riser_1",
            name="Robin Hack",
            club_id="C7",
            position=3,
            market_value_eur=6_500_000,
            change_24h_eur=290_000,
            change_3d_pct=0.17,
            avg_points=88.0,
            recent_minutes=(32, 74),
            lineup_prob=2,
            listing_price_eur=6_500_000,
            is_manager_listing=False,
            expires_in_hours=8.5,
            news_summary="Robin Hack meldet sich nach Erkaeltung stark im Training zurueck.",
        ),
        PlayerSnapshot(
            player_id="club_blocked",
            name="Vierter Klubspieler",
            club_id="C0",
            position=2,
            market_value_eur=4_000_000,
            change_24h_eur=120_000,
            change_3d_pct=0.11,
            listing_price_eur=4_000_000,
            is_manager_listing=False,
            expires_in_hours=5.0,
        ),
    )
    open_bids = (
        OpenBidContext(
            player_id="swap_target",
            amount_eur=12_000_000,
            is_manager_listing=True,
            bid_age_hours=24.0,
            coupled_swap_out_id="s1",
            coupled_swap_proceeds_eur=4_188_604,
        ),
    )
    return ManagerContext(
        budget_eur=1_320_297,
        team_value_eur=166_610_460,
        overdraft_floor_eur=-55_536_820,
        trade_cap_eur=50_000_000,
        friday_hours_left=92.0,
        squad=squad + (
            PlayerSnapshot(
                player_id="s_extra_c0",
                name="Drittspieler C0",
                club_id="C0",
                position=2,
                market_value_eur=2_000_000,
            ),
        ),
        market=market,
        open_bids=open_bids,
        protected_ids=frozenset({"s0"}),
    )


def test_anonymize_hides_real_names_and_flags_stale_manager_bids():
    ctx = _sample_context()
    bundle = anonymize_context(ctx)
    serialized = json.dumps(bundle.prompt_payload, ensure_ascii=False)
    assert "Robin Hack" not in serialized
    assert "Jeffrey Gouweleeuw" not in serialized
    assert bundle.prompt_payload["open_bids"][0]["capital_lockup_warning"] is True
    assert bundle.prompt_payload["constraints"]["friday_coverage_eur"] == 2_026_617

    riser_alias = bundle.id_to_alias["riser_1"]
    restored = deanonymize_text(f"Kaufe {riser_alias} fuer den Dispo-Hebel.", bundle)
    assert restored == "Kaufe Robin Hack fuer den Dispo-Hebel."


def test_event_gate_prevents_redundant_invocations():
    s1 = EventSnapshot(
        market_ids=frozenset({"a", "b"}),
        news_digest="h1",
        mv_epoch_day="2026-11-10",
        nearest_expiry_hours=4.0,
    )
    assert should_invoke_ai(None, s1) == (True, "initial_run")
    assert should_invoke_ai(s1, s1) == (False, "no_material_change")

    s_new_listing = EventSnapshot(
        market_ids=frozenset({"a", "b", "c"}),
        news_digest="h1",
        mv_epoch_day="2026-11-10",
        nearest_expiry_hours=4.0,
    )
    assert should_invoke_ai(s1, s_new_listing) == (True, "new_market_listings:1")

    s_closing = EventSnapshot(
        market_ids=frozenset({"a", "b"}),
        news_digest="h1",
        mv_epoch_day="2026-11-10",
        nearest_expiry_hours=0.3,
    )
    assert should_invoke_ai(s1, s_closing) == (True, "auction_closing_window")


def test_solver_physics_guard_enforces_club_limit_and_friday_coverage():
    ctx = _sample_context()
    bundle = anonymize_context(ctx)
    riser_alias = bundle.id_to_alias["riser_1"]
    blocked_alias = bundle.id_to_alias["club_blocked"]

    # C0 hat bereits s0, s5, s10 und s_extra_c0 (>= 3 Spieler) -> muss am Klub-Limit scheitern.
    envelope = AiDecisionEnvelope(
        valuations=(
            AiPlayerValuation(
                alias=blocked_alias,
                expected_points=130.0,
                risk_discount=0.05,
                max_overpay_pct=5.0,
                action_hint="buy_flip",
                rationale_de=f"{blocked_alias} sieht billig aus.",
            ),
            AiPlayerValuation(
                alias=riser_alias,
                expected_points=115.0,
                risk_discount=0.10,
                max_overpay_pct=10.0,
                action_hint="buy_flip",
                rationale_de=f"{riser_alias} steigt stark und dreht den Dispo positiv.",
            ),
        ),
        briefing_de=f"Fokus auf {riser_alias}.",
    )
    res = verify_neuro_plan(ctx, bundle, envelope)
    assert "club_blocked" in res.rejected_reasons
    assert res.rejected_reasons["club_blocked"] == "klub_limit_3_erreicht"
    assert len(res.accepted_bids) == 1
    bid = res.accepted_bids[0]
    assert bid.player_id == "riser_1"
    assert bid.bid_eur == 7_150_000
    # Aufschlag 650k EUR reduziert +2,03 M EUR Freitagsdeckung auf +1,38 M EUR (>= +10k EUR):
    assert bid.friday_coverage_after_eur == 1_376_617
    assert "Robin Hack" in bid.rationale_de
    assert res.briefing_de == "Fokus auf Robin Hack."


async def test_hf_judge_client_parses_mock_router_and_falls_back_cleanly(monkeypatch):
    ctx = _sample_context()
    bundle = anonymize_context(ctx)
    riser_alias = bundle.id_to_alias["riser_1"]

    mock_json = json.dumps(
        {
            "valuations": [
                {
                    "alias": riser_alias,
                    "expected_points": 118.0,
                    "risk_discount": 0.08,
                    "max_overpay_pct": 8.0,
                    "action_hint": "buy_flip",
                    "rationale_de": f"{riser_alias} nutzt den zinsfreien Dispo ideal.",
                }
            ],
            "stale_bid_aliases_to_flag": [],
            "briefing_de": f"Empfehlung: {riser_alias} bieten.",
        }
    )

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": mock_json}}]}

    async def _fake_post(self, url, *, headers=None, json=None):  # noqa: ANN001, A002
        return _FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    client = HuggingFaceJudgeClient(api_token="hf_test_dummy_token")
    res = await client.evaluate(ctx)
    assert res.used_fallback is False
    assert [b.player_id for b in res.accepted_bids] == ["riser_1"]
    assert res.accepted_bids[0].bid_eur == 7_020_000

    # Ohne Token greift deterministischer Sicherheits-Fallback ohne Crash:
    offline_client = HuggingFaceJudgeClient(api_token="")
    fallback_res = await offline_client.evaluate(ctx)
    assert fallback_res.used_fallback is True
    assert [b.player_id for b in fallback_res.accepted_bids] == ["riser_1"]


def test_dataset_export_blocks_public_upload_when_news_snippets_present(tmp_path: Path):
    ctx = _sample_context()
    bundle = anonymize_context(ctx)
    riser_alias = bundle.id_to_alias["riser_1"]
    chosen = AiDecisionEnvelope(
        valuations=(
            AiPlayerValuation(
                alias=riser_alias,
                expected_points=110.0,
                risk_discount=0.1,
                max_overpay_pct=9.0,
                action_hint="buy_flip",
                rationale_de=f"{riser_alias} kaufen.",
            ),
        ),
        briefing_de="Plan A",
    )
    rejected = AiDecisionEnvelope(valuations=(), briefing_de="Nichts tun")
    outcome = HindsightOutcome(matchday_points=1142.0, net_mv_gain_eur=1_800_000)
    sft = build_sft_record(ctx, chosen, outcome)
    dpo = build_dpo_record(ctx, chosen, rejected)
    assert sft["reward"] == compute_hindsight_reward(outcome)
    assert "Robin Hack" not in json.dumps(sft)
    assert "Robin Hack" not in json.dumps(dpo)

    with pytest.raises(ValueError, match="private=True"):
        export_jsonl([sft], tmp_path / "public.jsonl", private=False)

    saved = export_jsonl([sft], tmp_path / "private.jsonl", private=True)
    assert saved.exists()
