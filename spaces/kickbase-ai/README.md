---
title: Kickbase-AI Neuro-Symbolic Copilot
emoji: ⚽
colorFrom: green
colorTo: blue
sdk: gradio
sdk_version: 5.12.0
app_file: app.py
python_version: "3.12"
pinned: true
license: mit
short_description: Neuro-Symbolischer KI-Co-Manager fuer die Fantasy-Bundesliga
---

# Kickbase-AI — Neuro-Symbolischer Bundesliga-Co-Manager

**Open-Source-Motor:** [`github.com/milopraetzel/kickbase-copilot`](https://github.com/milopraetzel/kickbase-copilot)

Dieses Projekt verbindet ein kontextbasiertes **Hugging-Face-Sprachmodell** (`Qwen/Qwen2.5-7B-Instruct` bzw. eigener LoRA-Fine-Tune via `train_qwen_lora.py`) mit einem **deterministischen Physik- & Dispo-Pruefer**:

1. **Spieler-Anonymisierung (`Player_01`, `Club_A`):** Verhindert Markenname-Overfitting aus alten Trainingsdaten — das Modell urteilt ausschliesslich nach aktuellen Minuten, 3-Tage-Marktwert-Wellen, Quoten und News-Kontext.
2. **Zinsfreier -33-%-Dispo-Hebel mit harter Freitagsdeckung:** Das Modell schaetzt Punktepotenzial, Risiko-Abschlag und sinnvollen Aufschlag (`max_overpay_pct`), waehrend der Solver-Wrapper garantiert, dass das Konto bis Freitag 20:30 Uhr wieder im Plus (`>= +10.000 EUR`) landet.
3. **Kapitalbindungs-Detektor:** Erkennt automatisch Manager-Gebote, die seit Stunden ohne Auktionsende Dispo binden, waehrend frische Marktwert-Steiger verfuegbar sind.

## Eigene Fine-Tuning-Pipeline starten

```bash
pip install trl peft transformers datasets accelerate bitsandbytes
python train_qwen_lora.py --dataset path/to/private_traces.jsonl --output-dir ./qwen-kickbase-lora
```
