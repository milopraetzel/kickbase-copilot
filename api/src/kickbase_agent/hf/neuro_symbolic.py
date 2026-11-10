"""Neuro-Symbolische Entscheidungsbruecke zwischen Hugging-Face-Modell und Kickbase-Physik.

Das Sprachmodell (`Qwen2.5-7B-Instruct` oder Fine-Tune auf dem Hugging Face Hub) bewertet den
Kontext fliessend (`expected_points`, `risk_discount`, `max_overpay_pct`, `action_hint`) und
formuliert das deutsche Co-Manager-Briefing. Der deterministische `verify_neuro_plan`-Pruefer
setzt danach kompromisslos -33-%-Dispo, 50-M-Handelsrahmen, Klub-Limit (<= 3), geschuetzte
Spieler und Freitagsdeckung (>= +10.000 EUR) durch.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from kickbase_agent.hf.context import (
    FRIDAY_MIN_BUFFER_EUR,
    STALE_MANAGER_BID_HOURS,
    AnonymizedBundle,
    ManagerContext,
    PlayerSnapshot,
    anonymize_context,
    deanonymize_text,
)

log = logging.getLogger(__name__)

DEFAULT_HF_MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"
HF_ROUTER_URL = "https://router.huggingface.co/v1/chat/completions"
MAX_PLAYERS_PER_CLUB = 3
MAX_OVERPAY_CAP_PCT = 25.0


@dataclass(frozen=True)
class EventSnapshot:
    """Zustandsfingerabdruck fuer das ereignisgesteuerte KI-Aufruf-Gate."""

    market_ids: frozenset[str]
    news_digest: str
    mv_epoch_day: str
    nearest_expiry_hours: float | None = None


def should_invoke_ai(prev: EventSnapshot | None, curr: EventSnapshot) -> tuple[bool, str]:
    """Verhindert 144 identische Aufrufe/Tag: Feuert nur bei echtem neuem Informationsstand."""
    if prev is None:
        return True, "initial_run"
    new_listings = curr.market_ids - prev.market_ids
    if new_listings:
        return True, f"new_market_listings:{len(new_listings)}"
    if curr.mv_epoch_day != prev.mv_epoch_day:
        return True, f"mv_update:{curr.mv_epoch_day}"
    if curr.news_digest != prev.news_digest:
        return True, "fresh_news_digest"
    if curr.nearest_expiry_hours is not None and curr.nearest_expiry_hours <= 0.5:
        return True, "auction_closing_window"
    return False, "no_material_change"


@dataclass(frozen=True)
class AiPlayerValuation:
    """Kontext-Bewertung eines Markt-Kandidaten durch das Hugging-Face-Modell."""

    alias: str
    expected_points: float
    risk_discount: float
    max_overpay_pct: float
    action_hint: str
    rationale_de: str


@dataclass(frozen=True)
class AiDecisionEnvelope:
    """Strukturierte Antwort des Modells vor der harten Physik-Pruefung."""

    valuations: tuple[AiPlayerValuation, ...]
    stale_bid_aliases_to_flag: tuple[str, ...] = ()
    briefing_de: str = ""


@dataclass(frozen=True)
class VerifiedBidPlan:
    """Physikalisch geprueftes Gebot mit garantierter Freitags- und Dispo-Deckung."""

    player_id: str
    player_name: str
    bid_eur: int
    kind: str
    expected_points: float
    after_bids_eur: int
    friday_coverage_after_eur: int
    rationale_de: str


@dataclass(frozen=True)
class NeuroSymbolicResult:
    """Endergebnis aus KI-Abwaegung + deterministischer Solver-/Physik-Pruefung."""

    accepted_bids: tuple[VerifiedBidPlan, ...]
    rejected_reasons: dict[str, str] = field(default_factory=dict)
    flagged_stale_bids: tuple[str, ...] = ()
    briefing_de: str = ""
    used_fallback: bool = False


def _round_thousand(amount: float) -> int:
    return int(round(amount / 1000.0)) * 1000


def verify_neuro_plan(
    ctx: ManagerContext,
    bundle: AnonymizedBundle,
    envelope: AiDecisionEnvelope,
    *,
    used_fallback: bool = False,
) -> NeuroSymbolicResult:
    """Prueft und skaliert KI-Vorschlaege gegen alle eisernen Kickbase-Gesetze."""
    market_by_id: dict[str, PlayerSnapshot] = {p.player_id: p for p in ctx.market}
    club_counts: dict[str, int] = {}
    for sp in ctx.squad:
        club_counts[sp.club_id] = club_counts.get(sp.club_id, 0) + 1
    squad_ids = {sp.player_id for sp in ctx.squad}
    open_bid_ids = {b.player_id for b in ctx.open_bids}

    running_after_bids = ctx.after_bids_eur()
    running_friday_cov = ctx.friday_coverage_eur()
    running_trade_exp = ctx.trade_exposure_eur()

    ranked = sorted(
        envelope.valuations,
        key=lambda v: v.expected_points * max(0.0, 1.0 - v.risk_discount),
        reverse=True,
    )

    accepted: list[VerifiedBidPlan] = []
    rejected: dict[str, str] = {}

    for val in ranked:
        if val.action_hint not in {"buy_starter", "buy_flip"}:
            continue
        pid = bundle.alias_to_id.get(val.alias)
        if pid is None or pid not in market_by_id:
            rejected[val.alias] = "unbekannter_marktspieler"
            continue
        if pid in squad_ids or pid in open_bid_ids:
            rejected[pid] = "bereits_im_kader_oder_gebot_offen"
            continue
        mp = market_by_id[pid]
        if val.action_hint == "buy_flip" and mp.is_manager_listing:
            rejected[pid] = "flip_nur_auf_kickbase_auktionen"
            continue
        if club_counts.get(mp.club_id, 0) >= MAX_PLAYERS_PER_CLUB:
            rejected[pid] = "klub_limit_3_erreicht"
            continue

        clamped_pct = max(0.0, min(val.max_overpay_pct, MAX_OVERPAY_CAP_PCT))
        ask = mp.listing_price_eur or mp.market_value_eur
        raw_bid = _round_thousand(mp.market_value_eur * (1.0 + clamped_pct / 100.0))
        bid_eur = max(ask, raw_bid)

        next_after_bids = running_after_bids - bid_eur
        if next_after_bids < ctx.overdraft_floor_eur:
            rejected[pid] = "dispo_33_prozent_ueberschritten"
            continue

        if val.action_hint == "buy_flip":
            if running_trade_exp + bid_eur > ctx.trade_cap_eur:
                rejected[pid] = "handelsrahmen_ueberschritten"
                continue
            # Kurzfrist-Flip wird vor Freitag zum vollen Marktwert wieder an Kickbase verkauft;
            # nur der Aufschlag ueber Marktwert belastet den Freitagspuffer.
            friday_delta = mp.market_value_eur - bid_eur
        else:
            friday_delta = -bid_eur

        next_friday_cov = running_friday_cov + friday_delta
        if next_friday_cov < FRIDAY_MIN_BUFFER_EUR:
            rejected[pid] = "freitagsdeckung_unter_10k_eur"
            continue

        running_after_bids = next_after_bids
        running_friday_cov = next_friday_cov
        if val.action_hint == "buy_flip":
            running_trade_exp += bid_eur
        club_counts[mp.club_id] = club_counts.get(mp.club_id, 0) + 1

        accepted.append(
            VerifiedBidPlan(
                player_id=pid,
                player_name=mp.name,
                bid_eur=bid_eur,
                kind=val.action_hint,
                expected_points=round(val.expected_points, 1),
                after_bids_eur=running_after_bids,
                friday_coverage_after_eur=running_friday_cov,
                rationale_de=deanonymize_text(val.rationale_de, bundle),
            )
        )

    flagged_ids: list[str] = []
    for alias in envelope.stale_bid_aliases_to_flag:
        pid = bundle.alias_to_id.get(alias)
        if pid:
            flagged_ids.append(bundle.id_to_name.get(pid, pid))

    return NeuroSymbolicResult(
        accepted_bids=tuple(accepted),
        rejected_reasons=rejected,
        flagged_stale_bids=tuple(flagged_ids),
        briefing_de=deanonymize_text(envelope.briefing_de, bundle),
        used_fallback=used_fallback,
    )


def deterministic_fallback_envelope(bundle: AnonymizedBundle) -> AiDecisionEnvelope:
    """Deterministischer Sicherheits-Rueckfall, wenn HF-Token fehlt oder Router nicht antwortet."""
    payload = bundle.prompt_payload
    vals: list[AiPlayerValuation] = []
    for mp in payload.get("market", []):
        alias = str(mp["alias"])
        rise_3d = float(mp.get("rise_3d_pct") or 0.0)
        mv_24h = int(mp.get("mv_24h_eur") or 0)
        is_kb = mp.get("listing_kind") == "kickbase"
        avg_pts = float(mp.get("avg_pts") or 0.0)
        kb_prob = mp.get("kb_prob")
        if is_kb and rise_3d >= 5.0 and mv_24h > 0:
            overpay = min(15.0, round(rise_3d * 0.65, 1))
            vals.append(
                AiPlayerValuation(
                    alias=alias,
                    expected_points=max(60.0, avg_pts + rise_3d * 3.0),
                    risk_discount=0.10 if kb_prob in (1, 2) else 0.22,
                    max_overpay_pct=overpay,
                    action_hint="buy_flip",
                    rationale_de=(
                        f"{alias} steigt seit 3 Tagen um +{rise_3d:.1f} % "
                        f"(+{mv_24h // 1000}k EUR/Tag) — Dispo-Flip bis +{overpay:.1f} %."
                    ),
                )
            )
        elif avg_pts >= 95.0 and kb_prob in (1, 2):
            vals.append(
                AiPlayerValuation(
                    alias=alias,
                    expected_points=avg_pts,
                    risk_discount=0.08 if kb_prob == 1 else 0.15,
                    max_overpay_pct=6.0,
                    action_hint="buy_starter",
                    rationale_de=(
                        f"{alias} bringt stabil {avg_pts:.0f} Punkte im Schnitt "
                        "bei hoher Startelf-Sicherheit."
                    ),
                )
            )

    stale = [
        str(b["alias"])
        for b in payload.get("open_bids", [])
        if b.get("capital_lockup_warning")
        or (
            b.get("listing_kind") == "manager"
            and float(b.get("bid_age_hours", 0)) >= STALE_MANAGER_BID_HOURS
        )
    ]
    free_m = int(payload["constraints"]["free_interest_free_dispo_eur"]) / 1_000_000
    cov_m = int(payload["constraints"]["friday_coverage_eur"]) / 1_000_000
    briefing = (
        f"Freier Dispo: {free_m:.2f} M EUR (Freitagsdeckung aktuell {cov_m:+.2f} M EUR). "
        f"{len(vals)} Markt-Chancen identifiziert"
        + (f"; Achtung Kapitalbindung bei {', '.join(stale)}." if stale else ".")
    )
    return AiDecisionEnvelope(
        valuations=tuple(vals),
        stale_bid_aliases_to_flag=tuple(stale),
        briefing_de=briefing,
    )


SYSTEM_PROMPT_DE = """Du bist Kickbase-AI, ein neuro-symbolischer Bundesliga-Co-Manager.
Du erhaeltst anonymisierte Spieler (`Player_01`..) und vorab berechnete Euro-Grenzen.
Rechne niemals eigene Dispo-Grenzen aus, sondern bewerte die Kandidaten im Kontext (Form, Minuten,
3-Tage-Marktwert-Welle, News, Kapitalbindungs-Alter offener Manager-Gebote) und antworte NUR mit
validem JSON im Schema:
{
  "valuations": [
    {
      "alias": "Player_05",
      "expected_points": 112.5,
      "risk_discount": 0.12,
      "max_overpay_pct": 8.5,
      "action_hint": "buy_flip" | "buy_starter" | "skip",
      "rationale_de": "Kurze Begruendung mit Player_XX"
    }
  ],
  "stale_bid_aliases_to_flag": ["Player_02"],
  "briefing_de": "Knappe Zusammenfassung fuer den Manager auf Deutsch."
}"""


def _parse_envelope(raw_content: str) -> AiDecisionEnvelope:
    cleaned = raw_content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    data: dict[str, Any] = json.loads(cleaned)
    vals = [
        AiPlayerValuation(
            alias=str(item["alias"]),
            expected_points=float(item.get("expected_points", 0.0)),
            risk_discount=float(item.get("risk_discount", 0.2)),
            max_overpay_pct=float(item.get("max_overpay_pct", 0.0)),
            action_hint=str(item.get("action_hint", "skip")),
            rationale_de=str(item.get("rationale_de", "")),
        )
        for item in data.get("valuations", [])
        if isinstance(item, dict) and item.get("alias")
    ]
    stale = [str(a) for a in data.get("stale_bid_aliases_to_flag", [])]
    return AiDecisionEnvelope(
        valuations=tuple(vals),
        stale_bid_aliases_to_flag=tuple(stale),
        briefing_de=str(data.get("briefing_de", "")),
    )


class HuggingFaceJudgeClient:
    """Asynchroner Client fuer den Hugging Face Inference Router mit deterministischem Fallback."""

    def __init__(
        self,
        *,
        model_id: str | None = None,
        api_token: str | None = None,
        base_url: str = HF_ROUTER_URL,
        timeout_s: float = 20.0,
    ) -> None:
        self.model_id = model_id or os.environ.get("HF_MODEL_ID", DEFAULT_HF_MODEL_ID)
        self.api_token = (
            api_token if api_token is not None else os.environ.get("HF_TOKEN", "")
        )
        self.base_url = base_url
        self.timeout_s = timeout_s

    async def evaluate(self, ctx: ManagerContext) -> NeuroSymbolicResult:
        """Fuehrt Anonymisierung, HF-Modell-Abwaegung und Physik-Verifikation in einem Zug aus."""
        bundle = anonymize_context(ctx)
        if not self.api_token:
            env = deterministic_fallback_envelope(bundle)
            return verify_neuro_plan(ctx, bundle, env, used_fallback=True)

        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model_id,
            "temperature": 0.0,
            "max_tokens": 1200,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT_DE},
                {"role": "user", "content": json.dumps(bundle.prompt_payload, ensure_ascii=False)},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(self.base_url, headers=headers, json=body)
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
            env = _parse_envelope(content)
            return verify_neuro_plan(ctx, bundle, env, used_fallback=False)
        except Exception as exc:  # noqa: BLE001 — sauberer Fallback auf Regel-Heuristik
            log.warning(
                "HF-Inferenz fehlgeschlagen (%s) — nutze Sicherheits-Fallback",
                type(exc).__name__,
            )
            env = deterministic_fallback_envelope(bundle)
            return verify_neuro_plan(ctx, bundle, env, used_fallback=True)
