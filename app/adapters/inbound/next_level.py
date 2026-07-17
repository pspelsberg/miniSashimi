"""
app/adapters/inbound/next_level.py

REST-API für die Skill Builder Next-Level-Path Features:

- POST /api/next-level/judge          — LLM-as-a-Judge Evaluation
- POST /api/next-level/humaneval/run  — HumanEval Benchmark (async)
- GET  /api/next-level/stats          — Memory + Cache Stats
- POST /api/next-level/quantize       — AWQ/GGUF Quantisierung triggern
- POST /api/next-level/dpo            — DPO Training triggern
- POST /api/next-level/dpo/preferences/synthetic — Synth. Präferenzen erzeugen
- GET  /api/next-level/graph/topology — Aktuelle Graph-Topologie
- POST /api/next-level/acp/send       — ACP-Message manuell senden
- GET  /api/next-level/acp/history    — ACP-Message-History
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.adapters.inbound.rest import verify_token
from app.middleware.rate_limiter import rate_limit

JOB_SEMAPHORE = asyncio.Semaphore(2)
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.:/-]+)?$")
MAX_TEXT_LEN = 100_000


def validate_model_name(value: str) -> str:
    if not MODEL_PATTERN.fullmatch(value) or len(value) > 256:
        raise ValueError("Invalid model identifier")
    return value


router = APIRouter(
    prefix="/api/next-level",
    tags=["next-level"],
    dependencies=[Depends(verify_token), Depends(rate_limit)],
)


# ----- LLM-as-a-Judge -----


class JudgeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=100_000)
    reference: Optional[str] = Field(default=None, max_length=100_000)
    task_description: Optional[str] = Field(default=None, max_length=100_000)
    model: str = "openai/gpt-4o"
    consensus_runs: int = 1

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        return validate_model_name(value)


@router.post("/judge")
async def llm_judge(req: JudgeRequest, http_request: Request) -> Dict[str, Any]:
    judge = http_request.app.state.llm_judge
    judge.model = req.model
    judge.consensus_runs = max(1, min(req.consensus_runs, 5))
    async with JOB_SEMAPHORE:
        from app.evaluation.llm_judge import Criterion
        result = await judge.evaluate(
            code=req.code,
            reference=req.reference,
            task_description=req.task_description,
            criteria=list(Criterion),
        )
    return result.to_dict()


# ----- HumanEval -----

class HumanEvalRunRequest(BaseModel):
    model: str = "lmstudio/codemonkey"
    jsonl_path: Optional[str] = Field(default=None, max_length=4096)
    sample: bool = False
    temperature: float = 0.2

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        return validate_model_name(value)


@router.post("/humaneval/run")
async def humaneval_run(req: HumanEvalRunRequest, http_request: Request) -> Dict[str, Any]:
    from app.evaluation.humaneval_runner import HumanEvalRunner

    runner = HumanEvalRunner(model=req.model, temperature=req.temperature)
    if req.sample:
        problems = runner.load_sample_problems()
    elif req.jsonl_path:
        async with JOB_SEMAPHORE:
            problems = runner.load_problems(req.jsonl_path)
    else:
        raise HTTPException(
            status_code=400,
            detail="Entweder 'jsonl_path' oder 'sample=true' angeben.",
        )

    if req.jsonl_path and not req.sample:
        raise HTTPException(
            status_code=403,
            detail="HumanEval-Datasets sind deaktiviert. Verwende 'sample=true' oder konfiguriere SASHIMI_ENABLE_CODE_EXECUTION zusammen mit SASHIMI_SANDBOX_COMMAND.",
        )

    async with JOB_SEMAPHORE:
        summary = await runner.run(problems)
    return summary.to_dict()


# ----- Stats -----

@router.get("/stats")
async def stats(http_request: Request) -> Dict[str, Any]:
    mem = http_request.app.state.hybrid_memory
    cache = http_request.app.state.semantic_cache
    validator = http_request.app.state.handoff_validator
    return {
        "memory": await mem.stats(),
        "cache": await cache.stats(),
        "validator": validator.metrics(),
    }


# ----- Quantization -----

class QuantizeRequest(BaseModel):
    model_path: str = Field(min_length=1, max_length=4096)
    output_path: str = Field(min_length=1, max_length=4096)
    format: str = "gguf"
    quant_type: str = "q4_k_m"
    bits: int = 4
    group_size: int = 128

    @field_validator("bits", "group_size")
    @classmethod
    def _validate_positive(cls, value: int) -> int:
        if value <= 0 or value > 4096:
            raise ValueError("numeric value out of range")
        return value


@router.post("/quantize")
async def quantize(req: QuantizeRequest) -> Dict[str, Any]:
    async with JOB_SEMAPHORE:
        from app.finetuning.quantization import QuantizationHelper, QuantFormat

        helper = QuantizationHelper()
        try:
            fmt = QuantFormat(req.format.lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unbekanntes Format: {req.format}")

        if fmt == QuantFormat.AWQ:
            result = await asyncio.to_thread(
                helper.quantize_awq,
                req.model_path, req.output_path, req.bits, req.group_size,
            )
        elif fmt == QuantFormat.GGUF:
            result = await asyncio.to_thread(
                helper.quantize_gguf,
                req.model_path, req.output_path, req.quant_type,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="GPTQ aktuell nur manuell via external tooling.",
            )

    return {
        "success": result.success,
        "output_path": result.output_path,
        "format": result.format.value,
        "file_size_mb": result.file_size_mb,
        "error": result.error,
        "metadata": result.metadata,
    }


# ----- DPO -----

class DPOPairInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_TEXT_LEN)
    chosen: str = Field(min_length=1, max_length=MAX_TEXT_LEN)
    rejected: str = Field(min_length=1, max_length=MAX_TEXT_LEN)
    score_chosen: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    score_rejected: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class DPORequest(BaseModel):
    model_name: str = Field(min_length=1, max_length=256)
    pairs: List[DPOPairInput] = Field(min_length=1, max_length=1000)
    output_dir: str = Field(default="./artifacts/dpo_output", max_length=4096)
    num_epochs: int = Field(default=3, ge=1, le=20)
    learning_rate: float = Field(default=5e-7, gt=0.0, le=1.0)
    batch_size: int = Field(default=4, ge=1, le=128)

    @field_validator("model_name")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        return validate_model_name(value)


class SyntheticPreferencesRequest(BaseModel):
    good_outputs: List[Dict[str, Any]] = Field(min_length=1, max_length=1000)
    bad_outputs: List[Dict[str, Any]] = Field(min_length=1, max_length=1000)


@router.post("/dpo")
async def dpo_train(req: DPORequest) -> Dict[str, Any]:
    async with JOB_SEMAPHORE:
        from app.finetuning.dpo_trainer import DPOTrainer, DPOConfig, PreferencePair

        trainer = DPOTrainer(model_name=req.model_name)
        pairs = [
            PreferencePair(
                prompt=p.prompt,
                chosen=p.chosen,
                rejected=p.rejected,
                score_chosen=p.score_chosen,
                score_rejected=p.score_rejected,
            )
            for p in req.pairs
        ]
        config = DPOConfig(
            output_dir=req.output_dir,
            num_epochs=req.num_epochs,
            learning_rate=req.learning_rate,
            batch_size=req.batch_size,
        )
        result = await asyncio.to_thread(trainer.train, pairs, config)
    return {
        "success": result.success,
        "output_dir": result.output_dir,
        "final_loss": result.final_loss,
        "training_time_s": result.training_time_s,
        "steps_completed": result.steps_completed,
        "error": result.error,
        "metrics": result.metrics,
    }


@router.post("/dpo/preferences/synthetic")
async def synthetic_preferences(req: SyntheticPreferencesRequest) -> Dict[str, Any]:
    from app.finetuning.dpo_trainer import DPOTrainer

    trainer = DPOTrainer(model_name="placeholder")
    pairs = trainer.build_synthetic_preferences(req.good_outputs, req.bad_outputs)
    return {"count": len(pairs), "pairs": [p.__dict__ for p in pairs[:200]]}


# ----- Graph -----

@router.get("/graph/topology")
async def graph_topology(http_request: Request) -> Dict[str, Any]:
    engine = getattr(http_request.app.state, "graph_engine", None)
    if engine is None:
        return {
            "entry": None,
            "nodes": [],
            "edges": [],
            "mermaid": "```mermaid\nstateDiagram-v2\n    [*] --> idle\n```",
        }
    return engine.topology()


# ----- ACP -----

class ACPSendRequest(BaseModel):
    sender: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    recipient: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    payload: Dict[str, Any] = Field(default_factory=dict, max_length=64)
    intent: str = "event"
    workflow_id: Optional[str] = Field(default=None, max_length=128)
    priority: str = "normal"


@router.post("/acp/send")
async def acp_send(req: ACPSendRequest, http_request: Request) -> Dict[str, Any]:
    from app.orchestrator.acp import ACPMessage, ACPIntent, ACPPriority

    bus = http_request.app.state.acp_bus

    try:
        intent = ACPIntent(req.intent.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown intent: {req.intent}")
    try:
        priority = ACPPriority(req.priority.lower())
    except ValueError:
        priority = ACPPriority.NORMAL

    msg = ACPMessage(
        sender=req.sender,
        recipient=req.recipient,
        intent=intent,
        payload=req.payload,
        workflow_id=req.workflow_id,
        priority=priority,
    )
    delivered = await bus.publish(msg)
    return {"message_id": msg.message_id, "delivered": delivered}


@router.get("/acp/history")
async def acp_history(
    http_request: Request,
    workflow_id: Optional[str] = None,
    recipient: Optional[str] = None,
    sender: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    bus = http_request.app.state.acp_bus
    history = bus.history(
        workflow_id=workflow_id,
        recipient=recipient,
        sender=sender,
        limit=min(max(limit, 1), 200),
    )
    return {
        "count": len(history),
        "messages": [
            {
                "sender": m.sender,
                "recipient": m.recipient,
                "intent": m.intent.value,
                "payload": m.payload,
                "workflow_id": m.workflow_id,
                "priority": m.priority.value,
                "timestamp": m.timestamp,
            }
            for m in history
        ],
    }


# ----- Handoff Validator -----

class ValidateRequest(BaseModel):
    phase: str = Field(min_length=1, max_length=64, pattern=r"^[a-z_]+$")
    data: Any = None


@router.post("/validate")
async def validate_handoff(req: ValidateRequest, http_request: Request) -> Dict[str, Any]:
    validator = http_request.app.state.handoff_validator
    method = getattr(validator, f"validate_{req.phase}", None)
    if not method:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown phase '{req.phase}'. "
            f"Allowed: thinker, conductor, worker, pre_verifier, security, synthesis",
        )
    try:
        result = method(req.data)
        return {"valid": True, "data": result.model_dump()}
    except Exception as exc:
        logger.warning("Handoff validation failed for phase %s: %s", req.phase, exc)
        return {"valid": False, "error": str(exc)}
