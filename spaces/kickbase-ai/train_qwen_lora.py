"""Fine-Tuning-Skript (SFT + optional DPO via `trl` & `peft`) fuer `Qwen/Qwen2.5-7B-Instruct`.

Trainiert das eigene Kickbase-AI-Entscheidungsmodell auf anonymisierten Entscheidungs-Traces
(`build_sft_record` / `build_dpo_record` aus `kickbase_agent.hf.dataset_export`) und laedt den
LoRA-Adapter optional direkt auf den Hugging Face Hub hoch.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trainiere Kickbase-AI Qwen-2.5 LoRA Adapter")
    parser.add_argument(
        "--base-model",
        default="Qwen/Qwen2.5-7B-Instruct",
        help="Basis-Modell vom Hugging Face Hub",
    )
    parser.add_argument(
        "--dataset",
        required=True,
        type=Path,
        help="Pfad zur exportierten SFT- oder DPO-JSONL-Datei",
    )
    parser.add_argument(
        "--mode",
        choices=("sft", "dpo"),
        default="sft",
        help="Trainingsstufe: Stufe 1 `sft` oder Stufe 2 `dpo` (Praeferenz-Paare)",
    )
    parser.add_argument(
        "--output-dir",
        default=Path("./kickbase-qwen25-lora"),
        type=Path,
        help="Zielverzeichnis fuer Adapter-Checkpoints",
    )
    parser.add_argument(
        "--hub-model-id",
        default="",
        help="Optionales Ziel-Repo auf Hugging Face (z. B. user/kickbase-qwen25-7b-lora)",
    )
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--lr", type=float, default=2e-4)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    # Optionale Trainings-Abhaengigkeiten erst beim Skriptstart laden, damit der Kern leicht bleibt.
    from datasets import load_dataset  # type: ignore[import-not-found]
    from peft import LoraConfig  # type: ignore[import-not-found]
    from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore[import-not-found]
    from trl import (  # type: ignore[import-not-found]
        DPOConfig,
        DPOTrainer,
        SFTConfig,
        SFTTrainer,
    )

    ds = load_dataset("json", data_files=str(args.dataset), split="train")
    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    peft_cfg = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
    )
    model = AutoModelForCausalLM.from_pretrained(args.base_model, device_map="auto")

    if args.mode == "sft":
        cfg = SFTConfig(
            output_dir=str(args.output_dir),
            num_train_epochs=args.epochs,
            learning_rate=args.lr,
            per_device_train_batch_size=2,
            gradient_accumulation_steps=4,
            logging_steps=10,
            push_to_hub=bool(args.hub_model_id),
            hub_model_id=args.hub_model_id or None,
            hub_private_repo=False,
        )
        trainer = SFTTrainer(
            model=model,
            args=cfg,
            train_dataset=ds,
            processing_class=tokenizer,
            peft_config=peft_cfg,
        )
    else:
        dpo_cfg = DPOConfig(
            output_dir=str(args.output_dir),
            num_train_epochs=args.epochs,
            learning_rate=5e-6,
            per_device_train_batch_size=1,
            gradient_accumulation_steps=8,
            logging_steps=10,
            push_to_hub=bool(args.hub_model_id),
            hub_model_id=args.hub_model_id or None,
        )
        trainer = DPOTrainer(
            model=model,
            args=dpo_cfg,
            train_dataset=ds,
            processing_class=tokenizer,
            peft_config=peft_cfg,
        )

    trainer.train()
    trainer.save_model(str(args.output_dir))
    if args.hub_model_id:
        trainer.push_to_hub()


if __name__ == "__main__":
    main()
