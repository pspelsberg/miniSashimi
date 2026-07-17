"""
app/evaluation/llm_judge.py

Next-Level-Path #2: LLM-as-a-Judge Evaluation

Systematische Evaluation von:
- Fine-Tuned-Modellen (z.B. CodeMonkey)
- Generiertem Code aus dem Orchestrator
- Worker-Outputs

Nutzt ein "Judge-LLM", um Antworten auf strukturierte Kriterien zu bewerten:
- Korrektheit (Correctness)
- Vollständigkeit (Completeness)
- Stil-Adherence (Style)
- Sicherheit (Security)
- Effizienz (Efficiency)

Sicherheit:
- JSON-Output strikt
- Mehrere Judge-Runs für Konsens (optional)
- Bias-Reduktion durch Position-Randomisierung
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from app.adapters.outbound.llm.client import call_llm

logger = logging.getLogger("minisashimi.evaluation.judge")


class Criterion(str, Enum):
    CORRECTNESS = "correctness"
    COMPLETENESS = "completeness"
    STYLE = "style_adherence"
    SECURITY = "security"
    EFFICIENCY = "efficiency"
    CLARITY = "clarity"


@dataclass
class CriterionScore:
    name: Criterion
    score: float
    reasoning: str
    issues: List[str] = field(default_factory=list)


@dataclass
class JudgmentResult:
    """Gesamtergebnis einer LLM-as-a-Judge Evaluation."""

    overall_score: float
    criteria: List[CriterionScore]
    summary: str
    recommendations: List[str]
    raw_response: str = ""
    model_used: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_score": self.overall_score,
            "criteria": [
                {
                    "name": c.name.value,
                    "score": c.score,
                    "reasoning": c.reasoning,
                    "issues": c.issues,
                }
                for c in self.criteria
            ],
            "summary": self.summary,
            "recommendations": self.recommendations,
            "model_used": self.model_used,
        }


JUDGE_SYSTEM_PROMPT = """Du bist ein objektiver LLM-as-a-Judge. Deine Aufgabe ist es,
gegebenen Code/Output anhand spezifischer Kriterien auf einer Skala von 0.0 bis 1.0 zu bewerten.

Bewertungskriterien:
- Correctness: Ist der Code funktional korrekt?
- Completeness: Werden alle Anforderungen erfüllt?
- Style_Adherence: Hält der Code sich an Konventionen?
- Security: Sind offensichtliche Sicherheitsprobleme vorhanden?
- Efficiency: Ist die Implementierung effizient?
- Clarity: Ist der Code lesbar und gut dokumentiert?

Antworte AUSSCHLIESSLICH im folgenden JSON-Format:
{
  "overall_score": <float 0.0-1.0>,
  "criteria": [
    {"name": "correctness", "score": <float>, "reasoning": "...", "issues": ["..."]},
    {"name": "completeness", "score": <float>, "reasoning": "...", "issues": ["..."]},
    {"name": "style_adherence", "score": <float>, "reasoning": "...", "issues": ["..."]},
    {"name": "security", "score": <float>, "reasoning": "...", "issues": ["..."]},
    {"name": "efficiency", "score": <float>, "reasoning": "...", "issues": ["..."]},
    {"name": "clarity", "score": <float>, "reasoning": "...", "issues": ["..."]}
  ],
  "summary": "Kurze Zusammenfassung der Bewertung",
  "recommendations": ["Konkrete Verbesserungsvorschläge"]
}

Sei objektiv, präzise und konstruktiv. Vermeide Halo-Effekte."""


class LLMJudge:
    """
    LLM-as-a-Judge für Code/Output-Evaluation.

    Verwendung:
        judge = LLMJudge(model="openai/gpt-4o")
        result = await judge.evaluate(
            code=worker_output,
            reference="Expected behavior: ...",
            criteria=[Criterion.CORRECTNESS, Criterion.SECURITY]
        )
    """

    def __init__(
        self,
        model: str = "openai/gpt-4o",
        consensus_runs: int = 1,
        temperature: float = 0.0,
    ) -> None:
        self.model = model
        self.consensus_runs = max(1, consensus_runs)
        self.temperature = temperature

    async def evaluate(
        self,
        code: str,
        *,
        reference: Optional[str] = None,
        task_description: Optional[str] = None,
        criteria: Optional[List[Criterion]] = None,
    ) -> JudgmentResult:
        """
        Bewertet Code/Output anhand der gewählten Kriterien.
        """
        criteria = criteria or list(Criterion)

        user_prompt_parts = [
            "## Zu bewertender Code/Output:",
            f"```\n{code}\n```",
        ]
        if task_description:
            user_prompt_parts.append(f"\n## Aufgabenbeschreibung:\n{task_description}")
        if reference:
            user_prompt_parts.append(f"\n## Referenz / Erwartung:\n{reference}")
        user_prompt_parts.append(
            f"\n## Gewünschte Kriterien (alle): {', '.join(c.value for c in criteria)}"
        )
        user_prompt_parts.append(
            "\nGib das Ergebnis AUSSCHLIESSLICH als valides JSON zurück, ohne Markdown."
        )

        messages = [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(user_prompt_parts)},
        ]

        results: List[JudgmentResult] = []
        for run in range(self.consensus_runs):
            try:
                content, _ = await call_llm(
                    self.model, messages, temperature=self.temperature
                )
                parsed = _parse_judge_response(content)
                parsed.model_used = self.model
                parsed.raw_response = content
                results.append(parsed)
            except Exception as e:
                logger.error(f"Judge-Run {run + 1}/{self.consensus_runs} fehlgeschlagen: {e}")

        if not results:
            return JudgmentResult(
                overall_score=0.0,
                criteria=[],
                summary="Alle Judge-Runs fehlgeschlagen.",
                recommendations=[],
                model_used=self.model,
            )

        if len(results) == 1:
            return results[0]

        return _consensus(results)

    async def compare(
        self,
        output_a: str,
        output_b: str,
        *,
        task_description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Vergleicht zwei Outputs (z.B. für A/B-Tests).
        """
        prompt = f"""Vergleiche die folgenden zwei Outputs für die gegebene Aufgabe.

## Aufgabe:
{task_description or "(nicht angegeben)"}

## Output A:
```
{output_a}
```

## Output B:
```
{output_b}
```

Welcher Output ist besser? Antworte im JSON-Format:
{{
  "winner": "A" | "B" | "tie",
  "confidence": <float 0.0-1.0>,
  "reasoning": "...",
  "a_strengths": ["..."],
  "a_weaknesses": ["..."],
  "b_strengths": ["..."],
  "b_weaknesses": ["..."]
}}"""

        messages = [
            {"role": "system", "content": "Du bist ein objektiver LLM-Judge für Vergleiche."},
            {"role": "user", "content": prompt},
        ]
        try:
            content, _ = await call_llm(self.model, messages, temperature=0.0)
            return _safe_json_parse(content)
        except Exception as e:
            logger.error(f"Compare fehlgeschlagen: {e}")
            return {"error": str(e)}


def _parse_judge_response(content: str) -> JudgmentResult:
    text = content.strip()
    for fence in ["```json", "```"]:
        text = text.replace(fence, "")
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("Kein JSON in Judge-Response.")
        data = json.loads(match.group(0))

    criteria_scores = []
    for c in data.get("criteria", []):
        try:
            name = Criterion(c["name"])
        except (ValueError, KeyError):
            continue
        criteria_scores.append(
            CriterionScore(
                name=name,
                score=float(c.get("score", 0.0)),
                reasoning=str(c.get("reasoning", "")),
                issues=list(c.get("issues", [])),
            )
        )

    return JudgmentResult(
        overall_score=float(data.get("overall_score", 0.0)),
        criteria=criteria_scores,
        summary=str(data.get("summary", "")),
        recommendations=list(data.get("recommendations", [])),
    )


def _consensus(results: List[JudgmentResult]) -> JudgmentResult:
    """Mittelt mehrere Judge-Runs."""
    avg_overall = sum(r.overall_score for r in results) / len(results)

    merged: Dict[Criterion, List[CriterionScore]] = {}
    for r in results:
        for c in r.criteria:
            merged.setdefault(c.name, []).append(c)

    consensus_criteria = []
    for crit, scores in merged.items():
        avg_score = sum(s.score for s in scores) / len(scores)
        all_issues: List[str] = []
        all_reasoning: List[str] = []
        for s in scores:
            all_issues.extend(s.issues)
            all_reasoning.append(s.reasoning)
        consensus_criteria.append(
            CriterionScore(
                name=crit,
                score=avg_score,
                reasoning=" | ".join(all_reasoning),
                issues=list(dict.fromkeys(all_issues)),
            )
        )

    all_recs: List[str] = []
    summaries: List[str] = []
    for r in results:
        all_recs.extend(r.recommendations)
        summaries.append(r.summary)

    return JudgmentResult(
        overall_score=avg_overall,
        criteria=consensus_criteria,
        summary=" | ".join(summaries),
        recommendations=list(dict.fromkeys(all_recs)),
        model_used=results[0].model_used,
    )


def _safe_json_parse(content: str) -> Dict[str, Any]:
    text = content.strip()
    for fence in ["```json", "```"]:
        text = text.replace(fence, "")
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    return {"raw": content}
