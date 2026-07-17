"""
app/finetuning/dpo_trainer.py

Next-Level-Path #2: DPO (Direct Preference Optimization) Trainer

DPO-Training für CodeMonkey-Modell (oder andere lokale LLMs).
DPO aligniert Modell-Antworten anhand von Präferenz-Paaren (chosen, rejected)
ohne separates Reward-Model (im Gegensatz zu RLHF/PPO).

Vorteile:
- Stabiler als PPO
- Einfacher zu trainieren
- Direkte Optimierung auf Präferenzen

Sicherheit:
- Daten-PII-Filter vor Training
- Path-Traversal-Schutz
- GPU-Ressourcen-Limits
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.paths import configured_root, ensure_regular_file, resolve_under_root, UnsafePathError

logger = logging.getLogger("minisashimi.finetuning.dpo")


@dataclass
class PreferencePair:
    """Ein einzelnes Präferenz-Paar für DPO."""

    prompt: str
    chosen: str
    rejected: str
    score_chosen: Optional[float] = None
    score_rejected: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DPOConfig:
    """DPO-Hyperparameter."""

    beta: float = 0.1
    learning_rate: float = 5e-7
    batch_size: int = 4
    gradient_accumulation_steps: int = 4
    num_epochs: int = 3
    max_length: int = 1024
    max_prompt_length: int = 512
    warmup_steps: int = 100
    logging_steps: int = 10
    save_steps: int = 500
    eval_steps: int = 100
    output_dir: str = "./dpo_output"
    fp16: bool = True
    gradient_checkpointing: bool = True


@dataclass
class DPOTrainingResult:
    """Ergebnis eines DPO-Trainingslaufs."""

    success: bool
    output_dir: str
    final_loss: Optional[float] = None
    training_time_s: float = 0.0
    steps_completed: int = 0
    error: Optional[str] = None
    metrics: Dict[str, float] = field(default_factory=dict)


class DPOTrainer:
    """
    DPO-Trainer für CodeMonkey-Model.

    Verwendung:
        trainer = DPOTrainer(model_name="codemonify/codemonkey-base")
        pairs = trainer.load_preferences("./preferences.jsonl")
        result = await trainer.train(pairs, config=DPOConfig(num_epochs=2))
    """

    SAFE_BASE_DIRS = [
        os.path.expanduser("~"), "/tmp", "/var/tmp", ".",
    ]
    MAX_PAIRS = 100_000
    MAX_PAIR_TEXT_LENGTH = 100_000
    DATASET_ROOT = configured_root("SASHIMI_DATASET_ROOT", "./datasets")
    ARTIFACT_ROOT = configured_root("SASHIMI_ARTIFACT_ROOT", "./artifacts")

    def __init__(
        self,
        model_name: str,
        tokenizer_name: Optional[str] = None,
    ) -> None:
        self.model_name = model_name
        self.tokenizer_name = tokenizer_name or model_name
        self._validate_model_name(model_name)

    @staticmethod
    def _validate_model_name(name: str) -> None:
        """Reject path traversal, URLs and unsafe model identifiers."""
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?", name)
            or ".." in name
            or name.startswith("/")
        ):
            raise ValueError(f"Ungültiger Modell-Name: {name}")

    def _validate_path(self, path: str, *, root: Optional[Path] = None, must_exist: bool = False) -> Path:
        try:
            return resolve_under_root(path, root or self.DATASET_ROOT, must_exist=must_exist)
        except (UnsafePathError, FileNotFoundError) as exc:
            raise ValueError(f"Pfad außerhalb des erlaubten Datenbereichs: {path}") from exc

    def load_preferences(self, jsonl_path: str) -> List[PreferencePair]:
        """Lädt Präferenz-Paare aus JSONL."""
        path = self._validate_path(jsonl_path, must_exist=True)
        ensure_regular_file(path, max_bytes=50 * 1024 * 1024)

        pairs = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                if any(len(str(data.get(key, ""))) > self.MAX_PAIR_TEXT_LENGTH for key in ("prompt", "chosen", "rejected")):
                    raise ValueError("Präferenz-Text ist zu lang")
                pairs.append(
                    PreferencePair(
                        prompt=data.get("prompt", ""),
                        chosen=data.get("chosen", ""),
                        rejected=data.get("rejected", ""),
                        score_chosen=data.get("score_chosen"),
                        score_rejected=data.get("score_rejected"),
                        metadata=data.get("metadata", {}),
                    )
                )
                if len(pairs) > self.MAX_PAIRS:
                    raise ValueError(f"Zu viele Präferenz-Paare (>{self.MAX_PAIRS}).")

        logger.info(f"DPO: {len(pairs)} Präferenz-Paare geladen.")
        return pairs

    def build_synthetic_preferences(
        self,
        worker_outputs_good: List[Dict[str, str]],
        worker_outputs_bad: List[Dict[str, str]],
    ) -> List[PreferencePair]:
        """
        Baut synthetische Präferenz-Paare aus Worker-Outputs.
        Worker-Outputs mit 'quality_score' >= 0.7 werden als 'chosen' verwendet.
        """
        if len(worker_outputs_good) != len(worker_outputs_bad):
            raise ValueError(
                "worker_outputs_good und worker_outputs_bad müssen gleich lang sein."
            )

        pairs = []
        for good, bad in zip(worker_outputs_good, worker_outputs_bad):
            prompt = good.get("prompt", bad.get("prompt", ""))
            if not prompt or not good.get("output") or not bad.get("output"):
                continue
            pairs.append(
                PreferencePair(
                    prompt=prompt,
                    chosen=good["output"],
                    rejected=bad["output"],
                    score_chosen=good.get("quality_score"),
                    score_rejected=bad.get("quality_score"),
                    metadata={"source": "synthetic_from_workers"},
                )
            )

        logger.info(f"DPO: {len(pairs)} synthetische Präferenz-Paare erzeugt.")
        return pairs

    async def train(
        self,
        preferences: List[PreferencePair],
        config: Optional[DPOConfig] = None,
        eval_split: float = 0.1,
    ) -> DPOTrainingResult:
        """Startet DPO-Training."""
        if not preferences:
            return DPOTrainingResult(
                success=False,
                output_dir="",
                error="Keine Präferenz-Paare übergeben.",
            )

        if len(preferences) > self.MAX_PAIRS:
            raise ValueError(f"Zu viele Präferenz-Paare (>{self.MAX_PAIRS}).")
        config = config or DPOConfig(output_dir="./artifacts/dpo_output")
        if not 1 <= config.num_epochs <= 20 or not 1 <= config.batch_size <= 128:
            raise ValueError("Ungültige DPO-Ressourcenparameter")
        if not 1e-9 <= config.learning_rate <= 1.0:
            raise ValueError("Ungültige Lernrate")
        config.output_dir = str(self._validate_path(config.output_dir, root=self.ARTIFACT_ROOT))
        Path(config.output_dir).mkdir(parents=True, exist_ok=True)

        logger.info(
            f"DPO-Training gestartet: model={self.model_name}, "
            f"pairs={len(preferences)}, epochs={config.num_epochs}"
        )

        try:
            from datasets import Dataset  # type: ignore
            from transformers import (  # type: ignore
                AutoModelForCausalLM,
                AutoTokenizer,
            )
            from trl import DPOConfig as TRLDPOConfig  # type: ignore
            from trl import DPOTrainer as TRLDPOTrainer  # type: ignore
        except ImportError as e:
            return DPOTrainingResult(
                success=False,
                output_dir=config.output_dir,
                error=f"Fehlende Dependencies: {e}. Installation: pip install trl transformers datasets",
            )

        n_eval = max(1, int(len(preferences) * eval_split))
        train_pairs = preferences[:-n_eval] if n_eval > 0 else preferences
        eval_pairs = preferences[-n_eval:] if n_eval > 0 else []

        train_data = {
            "prompt": [p.prompt for p in train_pairs],
            "chosen": [p.chosen for p in train_pairs],
            "rejected": [p.rejected for p in train_pairs],
        }
        train_dataset = Dataset.from_dict(train_data)

        eval_dataset = None
        if eval_pairs:
            eval_dataset = Dataset.from_dict(
                {
                    "prompt": [p.prompt for p in eval_pairs],
                    "chosen": [p.chosen for p in eval_pairs],
                    "rejected": [p.rejected for p in eval_pairs],
                }
            )

        try:
            model = AutoModelForCausalLM.from_pretrained(
                self.model_name,
                local_files_only=os.getenv("SASHIMI_ALLOW_REMOTE_MODELS", "false").lower() != "true",
                trust_remote_code=False,
            )
            tokenizer = AutoTokenizer.from_pretrained(
                self.tokenizer_name,
                local_files_only=os.getenv("SASHIMI_ALLOW_REMOTE_MODELS", "false").lower() != "true",
                trust_remote_code=False,
            )
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token

            trl_config = TRLDPOConfig(
                beta=config.beta,
                learning_rate=config.learning_rate,
                per_device_train_batch_size=config.batch_size,
                gradient_accumulation_steps=config.gradient_accumulation_steps,
                num_train_epochs=config.num_epochs,
                max_length=config.max_length,
                max_prompt_length=config.max_prompt_length,
                warmup_steps=config.warmup_steps,
                logging_steps=config.logging_steps,
                save_steps=config.save_steps,
                eval_steps=config.eval_steps,
                output_dir=config.output_dir,
                fp16=config.fp16,
                gradient_checkpointing=config.gradient_checkpointing,
            )

            trainer = TRLDPOTrainer(
                model=model,
                ref_model=None,
                args=trl_config,
                train_dataset=train_dataset,
                eval_dataset=eval_dataset,
                tokenizer=tokenizer,
            )

            import time
            start = time.time()
            train_output = trainer.train()
            elapsed = time.time() - start

            trainer.save_model(config.output_dir)

            metrics = {}
            if hasattr(train_output, "metrics"):
                metrics = dict(train_output.metrics)
            final_loss = metrics.get("train_loss")

            logger.info(
                f"DPO-Training abgeschlossen: loss={final_loss}, "
                f"steps={metrics.get('train_steps', '?')}, time={elapsed:.1f}s"
            )

            return DPOTrainingResult(
                success=True,
                output_dir=config.output_dir,
                final_loss=final_loss,
                training_time_s=elapsed,
                steps_completed=metrics.get("train_steps", 0),
                metrics=metrics,
            )
        except Exception as e:
            logger.error(f"DPO-Training fehlgeschlagen: {e}")
            return DPOTrainingResult(
                success=False,
                output_dir=config.output_dir,
                error=str(e),
            )

    def export_gguf_after_dpo(
        self,
        dpo_output_dir: str,
        gguf_output_dir: str,
        quant_type: str = "q4_k_m",
    ) -> Dict[str, Any]:
        """
        Convenience: DPO → Quantize Pipeline.
        Nutzt den QuantizationHelper.
        """
        from app.finetuning.quantization import QuantizationHelper

        async def _run():
            helper = QuantizationHelper()
            return await helper.quantize_gguf(
                model_path=dpo_output_dir,
                output_path=gguf_output_dir,
                quant_type=quant_type,
            )

        import asyncio
        result = asyncio.run(_run())
        return {
            "success": result.success,
            "gguf_path": result.output_path,
            "file_size_mb": result.file_size_mb,
            "error": result.error,
        }
