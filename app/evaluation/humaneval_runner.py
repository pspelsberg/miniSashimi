"""
app/evaluation/humaneval_runner.py

Next-Level-Path #2: HumanEval Benchmark Runner

Führt das HumanEval-Benchmark auf einem Fine-Tuned-Modell (z.B. CodeMonkey) aus.

HumanEval:
- 164 Programmieraufgaben
- Funktionale Korrektheit (pass@k Metric)
- Definiert von OpenAI

Sicherheit:
- Sandboxed execution (subprocess mit timeout)
- Keine Netzwerk-Calls
- Read-only workspace
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.adapters.outbound.llm.client import call_llm
from app.core.paths import configured_root, ensure_regular_file, resolve_under_root, UnsafePathError

logger = logging.getLogger("minisashimi.evaluation.humaneval")


HUMANEVAL_PROMPT_TEMPLATE = """You are an expert Python programmer. Complete the following function:

```python
{prompt}
```

Your response should contain ONLY the completed function in a Python code block.
Do not include any explanations, examples, or test code.

Example response format:
```python
{complete_example}
```"""


@dataclass
class HumanEvalProblem:
    task_id: str
    prompt: str
    canonical_solution: str = ""
    test: str = ""
    entry_point: str = ""
    description: str = ""


@dataclass
class HumanEvalResult:
    task_id: str
    passed: bool
    completion: str
    error: Optional[str] = None
    execution_time_ms: int = 0
    attempts: int = 0


@dataclass
class BenchmarkSummary:
    model: str
    total: int
    passed: int
    failed: int
    pass_at_1: float
    avg_execution_time_ms: float
    results: List[HumanEvalResult] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_at_1": self.pass_at_1,
            "avg_execution_time_ms": self.avg_execution_time_ms,
            "timestamp": self.timestamp,
            "results": [
                {
                    "task_id": r.task_id,
                    "passed": r.passed,
                    "error": r.error,
                    "execution_time_ms": r.execution_time_ms,
                    "attempts": r.attempts,
                }
                for r in self.results
            ],
        }


class HumanEvalRunner:
    """
    HumanEval-Benchmark-Runner für miniSashimi-Code-Modelle.

    Verwendung:
        runner = HumanEvalRunner(model="lmstudio/codemonkey-lora")
        problems = runner.load_problems("./humaneval/HumanEval.jsonl")
        summary = await runner.run(problems[:10])  # 10 Probleme
        print(f"pass@1: {summary.pass_at_1:.2%}")
    """

    EXECUTION_TIMEOUT_S = 10
    COMPLETION_TIMEOUT_S = 60
    MAX_PROBLEMS = 20
    MAX_DATASET_BYTES = 50 * 1024 * 1024
    DATASET_ROOT = configured_root("SASHIMI_HUMANEVAL_ROOT", "./datasets/humaneval")

    def __init__(
        self,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def load_problems(self, jsonl_path: str) -> List[HumanEvalProblem]:
        """Load only bounded JSONL datasets below the configured root."""
        try:
            path = resolve_under_root(jsonl_path, self.DATASET_ROOT, must_exist=True)
            ensure_regular_file(path, max_bytes=self.MAX_DATASET_BYTES)
        except (UnsafePathError, FileNotFoundError) as exc:
            raise ValueError("HumanEval dataset is outside the permitted dataset root") from exc

        problems = []
        with path.open("r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                if line_number > self.MAX_PROBLEMS + 1:
                    raise ValueError(f"Too many HumanEval problems (>{self.MAX_PROBLEMS})")
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                values = {
                    key: str(data.get(key, ""))
                    for key in ("prompt", "canonical_solution", "test", "description")
                }
                if any(len(value) > 100_000 for value in values.values()):
                    raise ValueError("HumanEval problem field is too large")
                problems.append(
                    HumanEvalProblem(
                        task_id=str(data.get("task_id", ""))[:256],
                        prompt=values["prompt"],
                        canonical_solution=values["canonical_solution"],
                        test=values["test"],
                        entry_point=str(data.get("entry_point", ""))[:256],
                        description=values["description"],
                    )
                )
        logger.info("HumanEval: %d problems loaded from approved dataset root", len(problems))
        return problems

    def load_sample_problems(self) -> List[HumanEvalProblem]:
        """Liefert eine kleine Sample-Liste für schnelles Testen."""
        return [
            HumanEvalProblem(
                task_id="HumanEval/0",
                prompt='from typing import List\n\n\ndef has_close_elements(numbers: List[float], threshold: float) -> bool:\n    """ Check if in given list of numbers, are any two numbers closer to each other than\n    given threshold.\n    >>> has_close_elements([1.0, 2.0, 3.0], 0.5)\n    False\n    >>> has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3)\n    True\n    """\n',
                canonical_solution="    for idx, elem in enumerate(numbers):\n        for idx2, elem2 in enumerate(numbers):\n            if idx != idx2:\n                distance = abs(elem - elem2)\n                if distance < threshold:\n                    return True\n    return False\n",
                test='\n\nMETADATA = {\n    \'author\': \'jt\',\n    \'dataset\': \'test\'\n}\n\n\ndef check(candidate):\n    assert candidate([1.0, 2.0, 3.0], 0.5) == False\n    assert candidate([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3) == True',
                entry_point="has_close_elements",
            ),
        ]

    async def solve_problem(self, problem: HumanEvalProblem) -> HumanEvalResult:
        """Löst ein einzelnes Problem mit dem Fine-Tuned-Modell."""
        prompt = HUMANEVAL_PROMPT_TEMPLATE.format(
            prompt=problem.prompt,
            complete_example="def example_function(x: int) -> int:\n    return x + 1",
        )
        messages = [
            {"role": "system", "content": "You are an expert Python programmer."},
            {"role": "user", "content": prompt},
        ]

        try:
            content, _ = await asyncio.wait_for(
                call_llm(self.model, messages, temperature=self.temperature),
                timeout=self.COMPLETION_TIMEOUT_S,
            )
            completion = _extract_code_block(content)
            passed, error = await asyncio.to_thread(self._execute_test, problem, completion)
            return HumanEvalResult(
                task_id=problem.task_id,
                passed=passed,
                completion=completion,
                error=error,
                attempts=1,
            )
        except asyncio.TimeoutError:
            return HumanEvalResult(
                task_id=problem.task_id,
                passed=False,
                completion="",
                error="completion_timeout",
            )
        except Exception as e:
            return HumanEvalResult(
                task_id=problem.task_id,
                passed=False,
                completion="",
                error=str(e),
            )

    async def run(self, problems: List[HumanEvalProblem]) -> BenchmarkSummary:
        """Führt das Benchmark auf einer begrenzten Problemliste aus."""
        if len(problems) > self.MAX_PROBLEMS:
            raise ValueError(f"Too many HumanEval problems (>{self.MAX_PROBLEMS})")
        logger.info(
            f"HumanEval: starte {len(problems)} Probleme mit Modell {self.model}"
        )

        results: List[HumanEvalResult] = []
        for problem in problems:
            start = time.time()
            result = await self.solve_problem(problem)
            result.execution_time_ms = int((time.time() - start) * 1000)
            results.append(result)

        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        avg_time = (
            sum(r.execution_time_ms for r in results) / len(results)
            if results
            else 0.0
        )

        summary = BenchmarkSummary(
            model=self.model,
            total=len(results),
            passed=passed,
            failed=failed,
            pass_at_1=passed / len(results) if results else 0.0,
            avg_execution_time_ms=avg_time,
            results=results,
        )
        logger.info(
            f"HumanEval beendet: pass@1={summary.pass_at_1:.2%} "
            f"({passed}/{len(results)}) in {avg_time:.0f}ms avg"
        )
        return summary

    def _execute_test(self, problem: HumanEvalProblem, completion: str) -> tuple[bool, Optional[str]]:
        """Execute only through an explicitly configured external sandbox."""
        if not completion.strip():
            return False, "empty_completion"
        if os.getenv("SASHIMI_ENABLE_CODE_EXECUTION", "false").lower() != "true":
            return False, "code_execution_disabled"

        import shutil
        sandbox_raw = os.getenv("SASHIMI_SANDBOX_COMMAND", "").strip()
        sandbox_command = shlex.split(sandbox_raw) if sandbox_raw else []
        if not sandbox_command:
            return False, "external_sandbox_not_configured"
            
        resolved_bin = shutil.which(sandbox_command[0])
        if not resolved_bin:
            return False, "external_sandbox_binary_not_found"
            
        resolved_path = Path(resolved_bin)
        if resolved_path.name not in {"bwrap", "firejail", "nsjail", "sandbox-exec"}:
            return False, "external_sandbox_not_configured"
            
        safe_dirs = {"/bin", "/usr/bin", "/usr/local/bin", "/usr/sbin", "/sbin"}
        if str(resolved_path.parent) not in safe_dirs:
            if os.getenv("SASHIMI_ALLOW_CUSTOM_SANDBOX_PATH", "false").lower() != "true":
                return False, "unsafe_sandbox_binary_location"

        full_code = f"{problem.prompt}{completion}\n{problem.test}\ncheck({problem.entry_point})\n"
        with tempfile.TemporaryDirectory(prefix="minisashimi-eval-") as workdir:
            script_path = Path(workdir) / "candidate.py"
            script_path.write_text(full_code, encoding="utf-8")
            command = sandbox_command + [str(script_path)]
            try:
                proc = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.EXECUTION_TIMEOUT_S,
                    check=False,
                    cwd=workdir,
                    env={"PATH": os.getenv("PATH", "/usr/bin:/bin"), "PYTHONNOUSERSITE": "1"},
                    start_new_session=True,
                )
                if proc.returncode == 0:
                    return True, None
                return False, proc.stderr[:500] if proc.stderr else "non_zero_exit"
            except subprocess.TimeoutExpired as exc:
                pid = getattr(exc, "pid", None)
                if pid:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except OSError:
                        pass
                return False, "execution_timeout"
            except Exception:
                return False, "sandbox_execution_failed"


def _extract_code_block(content: str) -> str:
    """Extrahiert den ersten Python-Codeblock aus dem LLM-Output."""
    import re

    match = re.search(r"```(?:python)?\s*\n(.*?)\n```", content, re.DOTALL)
    if match:
        return match.group(1)
    return content.strip()


import asyncio
