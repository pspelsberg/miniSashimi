"""
app/finetuning/quantization.py

Next-Level-Path #2: AWQ/GGUF Quantization Helper

Hilfsfunktionen zur Quantisierung von Fine-Tuned-Modellen für lokale Inferenz:
- AWQ (Activation-aware Weight Quantization, 4-bit)
- GGUF (GPT-Generated Unified Format, llama.cpp)
- GPTQ als Bonus

Sicherheit:
- Pfad-Validierung gegen Path-Traversal (CWE-22)
- Dateigröße-Limits
- Keine Code-Execution auf User-Input
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional

from app.core.paths import configured_root, ensure_directory_size, resolve_under_root, UnsafePathError

logger = logging.getLogger("minisashimi.finetuning.quantization")


class QuantFormat(str, Enum):
    AWQ = "awq"
    GGUF = "gguf"
    GPTQ = "gptq"


QUANT_RECIPES = {
    QuantFormat.AWQ: {
        "bits": 4,
        "group_size": 128,
        "zero_point": True,
        "version": "gemv",
    },
    QuantFormat.GGUF: {
        "quants": ["q4_k_m", "q5_k_m", "q8_0", "q2_k"],
        "default": "q4_k_m",
    },
    QuantFormat.GPTQ: {
        "bits": 4,
        "group_size": 128,
        "desc_act": True,
    },
}


@dataclass
class QuantizationResult:
    success: bool
    output_path: str
    format: QuantFormat
    file_size_mb: float = 0.0
    error: Optional[str] = None
    metadata: Optional[dict] = None


class QuantizationHelper:
    """
    Helper für AWQ/GGUF/GPTQ-Quantisierung.

    Verwendung:
        helper = QuantizationHelper()
        result = await helper.quantize_awq(
            model_path="./codemonkey-merged",
            output_path="./codemonkey-awq",
        )
    """

    MODEL_ROOT = configured_root("SASHIMI_MODEL_ROOT", "./models")
    LLAMA_CPP_ROOT = configured_root("SASHIMI_LLAMA_CPP_ROOT", "./llama.cpp")
    try:
        MAX_OUTPUT_SIZE_GB = max(1, min(int(os.getenv("SASHIMI_MAX_OUTPUT_GB", "8")), 50))
    except ValueError:
        MAX_OUTPUT_SIZE_GB = 8
    PROCESS_TIMEOUT_SECONDS = 3_600

    def _validate_paths(self, *paths: str) -> List[Path]:
        """Resolve all paths below the configured model root."""
        resolved = []
        for raw_path in paths:
            try:
                resolved.append(resolve_under_root(raw_path, self.MODEL_ROOT))
            except UnsafePathError as exc:
                raise ValueError(f"Pfad außerhalb des Modellverzeichnisses: {raw_path}") from exc
        return resolved

    def _check_unsafe_files(self, model_path: str) -> Optional[str]:
        """Prüft auf unsichere Modell-Dateien zur Vermeidung von Unsafe Deserialization (CWE-502)."""
        if os.getenv("SASHIMI_ALLOW_UNSAFE_MODELS", "false").lower() == "true":
            return None
        unsafe_extensions = {".bin", ".pt", ".pth", ".pkl", ".ckpt"}
        unsafe_found = []
        for root_dir, _, files in os.walk(model_path):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in unsafe_extensions:
                    unsafe_found.append(f)
        if unsafe_found:
            return (
                f"Unsafe model files found: {', '.join(unsafe_found[:5])}. "
                "Quantization rejected to prevent Unsafe Deserialization (CWE-502). "
                "Set SASHIMI_ALLOW_UNSAFE_MODELS=true to bypass this check."
            )
        return None

    def _run_bounded_process(self, command: List[str], output_path: Path) -> tuple[int, str, str]:
        """Run trusted conversion tooling with timeout and live disk bounds."""
        proc = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=str(output_path),
        )
        deadline = time.monotonic() + self.PROCESS_TIMEOUT_SECONDS
        try:
            while proc.poll() is None:
                if time.monotonic() > deadline:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    return -1, stdout or "", "conversion timeout"
                try:
                    ensure_directory_size(
                        output_path,
                        max_bytes=self.MAX_OUTPUT_SIZE_GB * 1024 * 1024 * 1024,
                    )
                except UnsafePathError:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    return -1, stdout or "", "conversion output exceeds the configured size limit"
                time.sleep(0.25)
            stdout, stderr = proc.communicate()
            return proc.returncode or 0, stdout or "", stderr or ""
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    async def quantize_awq(
        self,
        model_path: str,
        output_path: str,
        bits: int = 4,
        group_size: int = 128,
    ) -> QuantizationResult:
        """AWQ-Quantisierung (4-bit, hardware-beschleunigt)."""
        model_path_obj, output_path_obj = self._validate_paths(model_path, output_path)
        if not 2 <= bits <= 8 or not 1 <= group_size <= 4096:
            raise ValueError("Ungültige AWQ-Parameter")
        model_path = str(model_path_obj)
        output_path = str(output_path_obj)

        if not Path(model_path).exists():
            return QuantizationResult(
                success=False,
                output_path=output_path,
                format=QuantFormat.AWQ,
                error=f"Modell-Pfad existiert nicht: {model_path}",
            )

        unsafe_err = self._check_unsafe_files(model_path)
        if unsafe_err:
            return QuantizationResult(
                success=False,
                output_path=output_path,
                format=QuantFormat.AWQ,
                error=unsafe_err,
            )

        try:
            from awq import AutoAWQForCausalLM  # type: ignore
            from transformers import AutoTokenizer  # type: ignore
        except ImportError:
            return QuantizationResult(
                success=False,
                output_path=output_path,
                format=QuantFormat.AWQ,
                error="autoawq nicht installiert. Installation: pip install autoawq",
            )

        logger.info(f"AWQ-Quantisierung: {model_path} → {output_path}")

        try:
            model = AutoAWQForCausalLM.from_pretrained(model_path)
            tokenizer = AutoTokenizer.from_pretrained(model_path)

            quant_config = {
                "zero_point": True,
                "q_group_size": group_size,
                "w_bit": bits,
                "version": "GEMV",
            }

            Path(output_path).mkdir(parents=True, exist_ok=True)
            model.quantize(tokenizer, quant_config=quant_config)
            model.save_quantized(output_path)
            tokenizer.save_pretrained(output_path)

            size_mb = ensure_directory_size(
                Path(output_path), max_bytes=self.MAX_OUTPUT_SIZE_GB * 1024 * 1024 * 1024
            ) / (1024 * 1024)

            metadata = {
                "format": "awq",
                "bits": bits,
                "group_size": group_size,
                "source_model": model_path,
            }
            (Path(output_path) / "quantize_config.json").write_text(
                json.dumps(metadata, indent=2)
            )

            logger.info(f"AWQ-Quantisierung erfolgreich: {size_mb:.1f} MB")
            return QuantizationResult(
                success=True,
                output_path=output_path,
                format=QuantFormat.AWQ,
                file_size_mb=size_mb,
                metadata=metadata,
            )
        except Exception as e:
            logger.error(f"AWQ-Quantisierung fehlgeschlagen: {e}")
            return QuantizationResult(
                success=False,
                output_path=output_path,
                format=QuantFormat.AWQ,
                error="Quantization failed",
            )

    async def quantize_gguf(
        self,
        model_path: str,
        output_path: str,
        quant_type: str = "q4_k_m",
    ) -> QuantizationResult:
        """GGUF-Quantisierung via llama.cpp convert.py."""
        model_path_obj, output_path_obj = self._validate_paths(model_path, output_path)
        model_path = str(model_path_obj)
        output_path = str(output_path_obj)

        if quant_type not in QUANT_RECIPES[QuantFormat.GGUF]["quants"]:
            return QuantizationResult(
                success=False,
                output_path=output_path,
                format=QuantFormat.GGUF,
                error=f"Unbekannter Quant-Type: {quant_type}. "
                f"Erlaubt: {QUANT_RECIPES[QuantFormat.GGUF]['quants']}",
            )

        if not Path(model_path).exists():
            return QuantizationResult(
                success=False,
                output_path=output_path,
                format=QuantFormat.GGUF,
                error=f"Modell-Pfad existiert nicht: {model_path}",
            )

        unsafe_err = self._check_unsafe_files(model_path)
        if unsafe_err:
            return QuantizationResult(
                success=False,
                output_path=output_path,
                format=QuantFormat.GGUF,
                error=unsafe_err,
            )

        logger.info(
            f"GGUF-Quantisierung: {model_path} → {output_path} (quant={quant_type})"
        )

        Path(output_path).mkdir(parents=True, exist_ok=True)

        try:
            # Resolve tools from an operator-configured, trusted directory;
            # never execute a relative path supplied by the request's cwd.
            convert_tool = self.LLAMA_CPP_ROOT / "convert.py"
            quantize_tool = self.LLAMA_CPP_ROOT / "quantize"
            if not convert_tool.is_file() or not quantize_tool.is_file():
                return QuantizationResult(
                    success=False,
                    output_path=output_path,
                    format=QuantFormat.GGUF,
                    error="llama.cpp tools are not configured",
                )
            cmd_convert = [
                sys.executable, str(convert_tool),
                model_path,
                "--outfile", str(Path(output_path) / "model.f16.gguf"),
                "--outtype", "f16",
            ]
            proc1_returncode, _, proc1_stderr = self._run_bounded_process(cmd_convert, Path(output_path))
            if proc1_returncode != 0:
                return QuantizationResult(
                    success=False,
                    output_path=output_path,
                    format=QuantFormat.GGUF,
                    error=f"convert.py fehlgeschlagen: {proc1_stderr[:500]}",
                )

            cmd_quant = [
                str(quantize_tool),
                str(Path(output_path) / "model.f16.gguf"),
                str(Path(output_path) / f"model.{quant_type}.gguf"),
                quant_type,
            ]
            proc2_returncode, _, proc2_stderr = self._run_bounded_process(cmd_quant, Path(output_path))
            if proc2_returncode != 0:
                return QuantizationResult(
                    success=False,
                    output_path=output_path,
                    format=QuantFormat.GGUF,
                    error=f"quantize fehlgeschlagen: {proc2_stderr[:500]}",
                )

            try:
                (Path(output_path) / "model.f16.gguf").unlink()
            except Exception:
                pass

            size_mb = ensure_directory_size(
                Path(output_path), max_bytes=self.MAX_OUTPUT_SIZE_GB * 1024 * 1024 * 1024
            ) / (1024 * 1024)

            metadata = {
                "format": "gguf",
                "quant_type": quant_type,
                "source_model": model_path,
            }
            (Path(output_path) / "quantize_config.json").write_text(
                json.dumps(metadata, indent=2)
            )

            logger.info(f"GGUF-Quantisierung erfolgreich: {size_mb:.1f} MB")
            return QuantizationResult(
                success=True,
                output_path=output_path,
                format=QuantFormat.GGUF,
                file_size_mb=size_mb,
                metadata=metadata,
            )
        except Exception as e:
            logger.error(f"GGUF-Quantisierung fehlgeschlagen: {e}")
            return QuantizationResult(
                success=False,
                output_path=output_path,
                format=QuantFormat.GGUF,
                error="Quantization failed",
            )

    def recommend_format(
        self,
        *,
        target_hardware: str = "cpu",
        target_speed_priority: bool = True,
        max_size_mb: Optional[int] = None,
    ) -> QuantFormat:
        """
        Empfiehlt das beste Quant-Format basierend auf Hardware/Ziel.
        """
        if target_hardware == "gpu":
            return QuantFormat.AWQ
        if target_hardware == "apple_silicon":
            return QuantFormat.GGUF
        if target_speed_priority:
            return QuantFormat.AWQ
        if max_size_mb and max_size_mb < 2000:
            return QuantFormat.GGUF
        return QuantFormat.GPTQ
