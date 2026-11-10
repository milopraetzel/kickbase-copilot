"""Interaktive Hugging-Face-Space-App fuer Kickbase-AI (Neuro-Symbolische Arena & Dispo-Rechner).

Zeigt den Live-Vergleich zwischen starrer Schwellen-Logik und dem kontextbasierten
Hugging-Face-Modell (`Qwen/Qwen2.5-7B-Instruct` oder eigener `HF_MODEL_ID`) inklusive
Spieler-Anonymisierung (`Player_01`..) und hartem -33-%-Dispo-/Freitagspuffer-Pruefer.
"""

from __future__ import annotations

import json
import os
from typing import Any

import gradio as gr
import httpx

DEFAULT_MODEL = os.environ.get("HF_MODEL_ID", "Qwen/Qwen2.5-7B-Instruct")
HF_ROUTER_URL = "https://router.huggingface.co/v1/chat/completions"
FRIDAY_MIN_BUFFER_EUR = 10_000

DEMO_MARKET = [
    {
        "alias": "Player_01",
        "real_name": "Offensiv-Comebacker (Beispiel A)",
        "club": "Club_A",
        "mv_m": 6.50,
        "mv_24h_k": 290,
        "rise_3d_pct": 17.0,
        "avg_pts": 91.0,
        "recent_min": [32, 76],
        "kb_prob": 2,
        "listing_kind": "kickbase",
        "news": "Kam nach Erkaeltung in Minute 32 rein, sofort 95 Rohpunkte, volle Trainingswoche.",
    },
    {
        "alias": "Player_02",
        "real_name": "Rotations-Falle (Beispiel B)",
        "club": "Club_B",
        "mv_m": 11.20,
        "mv_24h_k": 45,
        "rise_3d_pct": 2.1,
        "avg_pts": 84.0,
        "recent_min": [61, 60],
        "kb_prob": 3,
        "listing_kind": "kickbase",
        "news": (
            "Trainer deutet auf der PK Belastungssteuerung nach Pokalspiel ueber 120 Minuten an."
        ),
    },
    {
        "alias": "Player_03",
        "real_name": "Wellen-Steiger (Beispiel C)",
        "club": "Club_C",
        "mv_m": 8.40,
        "mv_24h_k": 240,
        "rise_3d_pct": 12.8,
        "avg_pts": 79.0,
        "recent_min": [90, 90],
        "kb_prob": 1,
        "listing_kind": "kickbase",
        "news": "Gesetzter Stammspieler vor zwei Heimspielen gegen Aufsteiger.",
    },
]


def _classic_rule_verdict(p: dict[str, Any]) -> str:
    min_ok = len(p["recent_min"]) >= 2 and all(m >= 60 for m in p["recent_min"])
    if p["mv_m"] >= 5.0 and not min_ok and p["rise_3d_pct"] < 18.0:
        return "ABGELEHNT (Starre Schwelle: < 2x60 Min bei >= 5,0 M EUR)"
    if p["kb_prob"] == 3 and p["mv_24h_k"] > 0 and min_ok:
        return f"KAUF bis {p['mv_m'] * 1.08:.2f} M EUR (Blind fuer PK-Rotationswarnung!)"
    if p["rise_3d_pct"] >= 5.0:
        return f"FLIP bis {p['mv_m'] * 1.10:.2f} M EUR (Standard-Stufe)"
    return "UEBERSPRUNGEN"


def _call_hf_or_fallback(payload: dict[str, Any], model_id: str) -> tuple[dict[str, Any], str]:
    token = os.environ.get("HF_TOKEN", "").strip()
    if token:
        system_prompt = (
            "Du bist Kickbase-AI. Bewerte jeden anonymisierten Markt-Kandidaten im Kontext "
            "(Minutenverlauf, 3-Tage-Welle, PK-News) und antworte NUR als JSON mit "
            '{"valuations":[{"alias":"Player_01","max_overpay_pct":10.5,'
            '"action_hint":"buy_flip","rationale_de":"..."}],"briefing_de":"..."}.'
        )
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(
                    HF_ROUTER_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "model": model_id,
                        "temperature": 0.0,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                        ],
                    },
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"]
                return json.loads(raw), f"Live Hugging Face Router ({model_id})"
        except Exception as exc:  # noqa: BLE001
            mode = f"Deterministischer Neuro-Symbolischer Fallback ({type(exc).__name__})"
    else:
        mode = "Deterministischer Neuro-Symbolischer Showcase (HF_TOKEN optional setzbar)"

    vals = []
    for p in payload["market"]:
        if "Belastungssteuerung" in p.get("news", ""):
            vals.append(
                {
                    "alias": p["alias"],
                    "max_overpay_pct": 0.0,
                    "action_hint": "skip",
                    "rationale_de": (
                        f"{p['alias']} hat zwar 2x60 Min, aber die PK warnt vor Rotation nach "
                        "120 Pokal-Minuten — Kapital nicht binden."
                    ),
                }
            )
        elif p["rise_3d_pct"] >= 10.0:
            overpay = round(min(14.0, p["rise_3d_pct"] * 0.65), 1)
            vals.append(
                {
                    "alias": p["alias"],
                    "max_overpay_pct": overpay,
                    "action_hint": "buy_flip",
                    "rationale_de": (
                        f"{p['alias']} steigt +{p['rise_3d_pct']:.1f} % in 3 Tagen bei starkem "
                        f"News-/Form-Kontext — zinsfreien Dispo bis +{overpay:.1f} % nutzen."
                    ),
                }
            )
    return {
        "valuations": vals,
        "briefing_de": (
            "Kontext schlaegt starre Minuten-Klippen: Comebacker und Wellen-Steiger kaufen, "
            "PK-Rotationsfalle trotz 2x60 Minuten meiden."
        ),
    }, mode


def run_arena(
    budget_m: float,
    team_value_m: float,
    open_swap_bid_m: float,
    coupled_swap_sale_m: float,
    held_trade_mv_m: float,
    bid_age_h: float,
    model_id: str,
) -> tuple[str, str, str]:
    """Berechnet Dispo-Physik und stellt Regel-Engine vs. Neuro-Symbolische HF-KI gegenueber."""
    budget = int(budget_m * 1_000_000)
    team_val = int(team_value_m * 1_000_000)
    floor = -int((team_val + budget) * 0.33)
    swap_bid = int(open_swap_bid_m * 1_000_000)
    swap_sale_95 = int(coupled_swap_sale_m * 1_000_000 * 0.95)
    held_mv = int(held_trade_mv_m * 1_000_000)

    after_bids = budget - swap_bid
    free_dispo = max(0, after_bids - floor)
    friday_cov = after_bids + held_mv + swap_sale_95

    anon_market = [{k: v for k, v in p.items() if k != "real_name"} for p in DEMO_MARKET]
    payload = {
        "constraints": {
            "after_bids_eur": after_bids,
            "overdraft_floor_eur": floor,
            "free_dispo_eur": free_dispo,
            "friday_coverage_eur": friday_cov,
            "open_manager_bid_age_h": bid_age_h,
        },
        "market": anon_market,
    }
    ai_out, engine_mode = _call_hf_or_fallback(payload, model_id)
    alias_map = {p["alias"]: p["real_name"] for p in DEMO_MARKET}
    val_by_alias = {v["alias"]: v for v in ai_out.get("valuations", [])}

    rows_md = [
        f"**Modus:** `{engine_mode}`\n",
        "| Kandidat | Starre Regel-Engine | Neuro-Symbolische HF-KI + Physik-Wrapper |",
        "|---|---|---|",
    ]
    running_after = after_bids
    running_cov = friday_cov
    for p in DEMO_MARKET:
        classic = _classic_rule_verdict(p)
        v = val_by_alias.get(p["alias"], {"action_hint": "skip", "max_overpay_pct": 0.0})
        if v.get("action_hint") in {"buy_flip", "buy_starter"}:
            factor = 1.0 + float(v["max_overpay_pct"]) / 100.0
            bid_eur = int(round(p["mv_m"] * 1_000_000 * factor, -3))
            friday_delta = int(p["mv_m"] * 1_000_000) - bid_eur
            if running_after - bid_eur < floor:
                ai_cell = "BLOCKIERT durch Physik-Wrapper (-33-%-Dispo erreicht)"
            elif running_cov + friday_delta < FRIDAY_MIN_BUFFER_EUR:
                ai_cell = "BLOCKIERT durch Physik-Wrapper (Freitagsdeckung < +10k EUR)"
            else:
                running_after -= bid_eur
                running_cov += friday_delta
                reason = str(v.get("rationale_de", "")).replace(p["alias"], p["real_name"])
                ai_cell = (
                    f"**GEBOT {bid_eur / 1e6:.2f} M EUR** "
                    f"(Konto danach `{running_after / 1e6:+.2f} M`, "
                    f"Freitag `{running_cov / 1e6:+.2f} M`) — {reason}"
                )
        else:
            reason = str(v.get("rationale_de", "Ausgelassen")).replace(p["alias"], p["real_name"])
            ai_cell = f"**SKIP** — {reason}"
        rows_md.append(f"| **{p['real_name']}** (`{p['mv_m']:.2f} M`) | {classic} | {ai_cell} |")

    lockup_warn = (
        f"\n- **Kapitalbindungs-Warnung:** Manager-Gebot steht seit `{bid_age_h:.0f} h` offen!"
        if bid_age_h >= 18.0 and open_swap_bid_m > 0
        else ""
    )
    finance_md = (
        f"### Zinsfreier Dispo- & Freitags-Check\n"
        f"- **33-%-Dispo-Untergrenze:** `{floor / 1e6:+.2f} M EUR`\n"
        f"- **Freie zinsfreie Kaufkraft vor neuen Flips:** `{free_dispo / 1e6:.2f} M EUR`\n"
        f"- **Echte Freitagsdeckung (inkl. 95 % Swap-Verkauf):** `{friday_cov / 1e6:+.2f} M EUR`\n"
        f"- **Kontostand nach freigegebenen KI-Flips:** `{running_after / 1e6:+.2f} M EUR` "
        f"(Freitagspuffer danach: `{running_cov / 1e6:+.2f} M EUR`){lockup_warn}"
    )
    briefing = str(ai_out.get("briefing_de", ""))
    for alias, real in alias_map.items():
        briefing = briefing.replace(alias, real)
    return finance_md, "\n".join(rows_md), json.dumps(payload, indent=2, ensure_ascii=False)


with gr.Blocks(title="Kickbase-AI Neuro-Symbolic Arena") as demo:
    gr.Markdown(
        "# Kickbase-AI — Neuro-Symbolischer Bundesliga-Co-Manager\n"
        "Vergleiche live, wie ein kontextbasiertes Hugging-Face-Modell mit vorgeschalteter "
        "Spieler-Anonymisierung (`Player_01`..) und nachgeschaltetem `-33 %`-Dispo-Solver "
        "starre Schwellen-Regeln schlaegt."
    )
    with gr.Row():
        budget_in = gr.Slider(-20.0, 20.0, value=1.32, step=0.1, label="Aktuelles Konto (M EUR)")
        tv_in = gr.Slider(80.0, 250.0, value=166.61, step=0.5, label="Teamwert (M EUR)")
        model_in = gr.Textbox(value=DEFAULT_MODEL, label="Hugging Face Model ID")
    with gr.Row():
        swap_bid_in = gr.Slider(
            0.0, 30.0, value=12.0, step=0.25, label="Laufendes Swap-Gebot (M EUR)"
        )
        swap_sale_in = gr.Slider(
            0.0, 25.0, value=4.41, step=0.1, label="Gekoppelter Swap-Abgang MW (M EUR)"
        )
        held_in = gr.Slider(
            0.0, 30.0, value=8.52, step=0.1, label="Gehaltene Riser-Positionen MW (M EUR)"
        )
        age_in = gr.Slider(
            0.0, 48.0, value=6.0, step=1.0, label="Alter des Manager-Gebots (Stunden)"
        )

    run_btn = gr.Button("Neuro-Symbolische Bewertung berechnen", variant="primary")
    finance_out = gr.Markdown()
    arena_out = gr.Markdown()
    with gr.Accordion("Anonymisierter Modell-Prompt (Zero Brand-Bias)", open=False):
        prompt_out = gr.Code(language="json")

    inputs = [budget_in, tv_in, swap_bid_in, swap_sale_in, held_in, age_in, model_in]
    outputs = [finance_out, arena_out, prompt_out]
    run_btn.click(run_arena, inputs=inputs, outputs=outputs)
    demo.load(run_arena, inputs=inputs, outputs=outputs)

if __name__ == "__main__":
    demo.launch()
