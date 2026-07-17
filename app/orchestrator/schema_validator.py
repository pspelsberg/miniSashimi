"""
app/orchestrator/schema_validator.py

Next-Level-Path #1: Strict Schema Validation für Agent Handoffs

Strikte Pydantic-Validierung zwischen Phasen, um Parsing-Fehler zu vermeiden.

Handoff-Kontrakte:
- ThinkerOutput → ConductorInput
- ConductorOutput → WorkerInput (für jeden Worker)
- WorkerOutput → PreVerifierInput
- PreVerifierOutput → SecurityAuditorInput
- SecurityAuditorOutput → SynthesisInput
- SynthesisOutput → ClientResponse

Jeder Kontrakt prüft Felder, Typen, Pflichtwerte und Wertebereiche.
"""
from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger("minisashimi.orchestrator.validator")


class WorkerRole(str, Enum):
    BACKEND = "worker_backend"
    FRONTEND = "worker_frontend"
    TEST = "test_architect"
    INTEGRATION = "worker_integration"


class Severity(str, Enum):
    CRITICAL = "KRITISCH"
    WARNING = "WARNUNG"
    OPTIMIZATION = "OPTIMIERUNG"
    GOOD = "Guter Standard"


# ----- THINKER → CONDUCTOR -----


class ThinkerOutput(BaseModel):
    """Kontrakt: Thinker liefert Architektur-Plan."""

    architecture: str = Field(min_length=10, description="Modul-/Komponentenstruktur")
    module_breakdown: List[str] = Field(min_length=1, description="Liste der Module/Teilschritte")
    interfaces: List[str] = Field(default_factory=list, description="Geplante Schnittstellen")
    rationale: Optional[str] = Field(default=None, description="Begründung des Plans")

    @field_validator("module_breakdown")
    @classmethod
    def validate_modules(cls, v: List[str]) -> List[str]:
        if not all(isinstance(m, str) and m.strip() for m in v):
            raise ValueError("module_breakdown darf nur nicht-leere Strings enthalten.")
        return [m.strip() for m in v]


# ----- CONDUCTOR → WORKER -----


class ConductorTask(BaseModel):
    """Kontrakt: Ein einzelner Conductor-Task (Worker-Auftrag)."""

    title: str = Field(min_length=3, max_length=200)
    assigned_model: WorkerRole
    prompt_payload: str = Field(min_length=10)
    priority: int = Field(default=5, ge=1, le=10)
    depends_on: List[int] = Field(default_factory=list, description="Indizes vorheriger Tasks")
    expected_artifacts: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def coerce(cls, data: Any) -> Any:
        if isinstance(data, dict):
            payload_keys = [
                "prompt_payload", "prompt", "payload", "instruction",
                "instructions", "task", "description", "details",
            ]
            for pk in payload_keys:
                if pk in data and data[pk] is not None:
                    val = data[pk]
                    if not isinstance(val, str):
                        try:
                            val = json.dumps(val, ensure_ascii=False)
                        except Exception:
                            val = str(val)
                    data["prompt_payload"] = val
                    break

            if "assigned_model" in data:
                model_val = str(data["assigned_model"]).strip().lower()
                role_map = {
                    "backend": WorkerRole.BACKEND,
                    "frontend": WorkerRole.FRONTEND,
                    "test": WorkerRole.TEST,
                    "architect": WorkerRole.TEST,
                    "integration": WorkerRole.INTEGRATION,
                }
                for key, role in role_map.items():
                    if key in model_val:
                        data["assigned_model"] = role
                        break
                else:
                    try:
                        data["assigned_model"] = WorkerRole(model_val)
                    except ValueError:
                        data["assigned_model"] = WorkerRole.BACKEND
        return data


class ConductorOutput(BaseModel):
    """Kontrakt: Conductor liefert Task-Liste."""

    tasks: List[ConductorTask] = Field(min_length=1, max_length=50)


# ----- WORKER → VERIFIER -----


class WorkerOutput(BaseModel):
    """Kontrakt: Worker liefert Code + Metadaten."""

    code: str = Field(min_length=1, description="Generierter Quellcode")
    language: str = Field(default="python", description="Programmiersprache")
    files: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Liste von {path, content} Dateien",
    )
    notes: Optional[str] = None
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        allowed = {
            "python", "javascript", "typescript", "java", "go",
            "rust", "cpp", "csharp", "ruby", "php", "html", "css",
            "shell", "sql", "yaml", "json", "markdown",
        }
        v_lower = v.lower().strip()
        if v_lower not in allowed:
            logger.debug(f"Exotische Sprache: {v}")
        return v_lower


# ----- VERIFIER → SECURITY -----


class VerifierFinding(BaseModel):
    severity: Severity
    title: str
    description: str
    line_hint: Optional[str] = None
    cwe_id: Optional[str] = None
    cvss_score: Optional[float] = Field(default=None, ge=0.0, le=10.0)


class PreVerifierOutput(BaseModel):
    """Kontrakt: Pre-Verifier (Syntax-Check)."""

    status: Literal["OK", "FAIL"] = "OK"
    issues: List[VerifierFinding] = Field(default_factory=list)
    summary: str = Field(default="", max_length=2000)


# ----- SECURITY → SYNTHESIS -----


class SecurityAuditorOutput(BaseModel):
    """Kontrakt: Security Auditor (OWASP / CWE / CVSS)."""

    findings: List[VerifierFinding] = Field(default_factory=list)
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "LOW"
    summary: str = Field(default="", max_length=2000)
    compliance: Dict[str, bool] = Field(
        default_factory=dict,
        description="DSGVO/GDPR, OWASP, EU-AI-Act Compliance-Flags",
    )


# ----- SYNTHESIS → CLIENT -----


class FileBlock(BaseModel):
    path: str = Field(min_length=1)
    language: str = "python"
    content: str = Field(min_length=1)


class SynthesisOutput(BaseModel):
    """Kontrakt: Finale Synthese für den Client."""

    summary: str = Field(min_length=1, max_length=10000)
    files: List[FileBlock] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)


# =================================================================
# VALIDATOR HARNESS
# =================================================================


class HandoffValidator:
    """
    Validiert Handoffs zwischen Agent-Phasen mit strikten Pydantic-Kontrakten.

    Verwendung:
        validator = HandoffValidator()
        try:
            valid = validator.validate_conductor(raw_data)
        except ValidationError as e:
            ...
    """

    def __init__(self, strict: bool = True) -> None:
        self.strict = strict
        self._metrics: Dict[str, int] = {
            "thinker_ok": 0, "thinker_fail": 0,
            "conductor_ok": 0, "conductor_fail": 0,
            "worker_ok": 0, "worker_fail": 0,
            "verifier_ok": 0, "verifier_fail": 0,
            "security_ok": 0, "security_fail": 0,
            "synthesis_ok": 0, "synthesis_fail": 0,
        }

    def _record(self, phase: str, ok: bool) -> None:
        key = f"{phase}_{'ok' if ok else 'fail'}"
        self._metrics[key] = self._metrics.get(key, 0) + 1

    def validate_thinker(self, data: Union[str, Dict[str, Any]]) -> ThinkerOutput:
        if isinstance(data, str):
            data = _parse_json_lenient(data)
        result = ThinkerOutput.model_validate(data)
        self._record("thinker", True)
        return result

    def validate_conductor(
        self, data: Union[str, Dict[str, Any], List[Any]]
    ) -> ConductorOutput:
        if isinstance(data, str):
            data = _parse_json_lenient(data)
        if isinstance(data, list):
            data = {"tasks": data}
        elif isinstance(data, dict) and "tasks" not in data:
            if any(k in data for k in ["title", "assigned_model", "prompt_payload"]):
                data = {"tasks": [data]}
            else:
                for val in data.values():
                    if isinstance(val, list):
                        data = {"tasks": val}
                        break
        result = ConductorOutput.model_validate(data)
        self._record("conductor", True)
        return result

    def validate_worker(self, data: Union[str, Dict[str, Any]]) -> WorkerOutput:
        if isinstance(data, str):
            data = _parse_json_lenient(data)
        result = WorkerOutput.model_validate(data)
        self._record("worker", True)
        return result

    def validate_pre_verifier(self, data: Union[str, Dict[str, Any]]) -> PreVerifierOutput:
        if isinstance(data, str):
            data = _parse_json_lenient(data)
        result = PreVerifierOutput.model_validate(data)
        self._record("verifier", True)
        return result

    def validate_security(self, data: Union[str, Dict[str, Any]]) -> SecurityAuditorOutput:
        if isinstance(data, str):
            data = _parse_json_lenient(data)
        result = SecurityAuditorOutput.model_validate(data)
        self._record("security", True)
        return result

    def validate_synthesis(self, data: Union[str, Dict[str, Any]]) -> SynthesisOutput:
        if isinstance(data, str):
            data = _parse_json_lenient(data)
        result = SynthesisOutput.model_validate(data)
        self._record("synthesis", True)
        return result

    def metrics(self) -> Dict[str, int]:
        return dict(self._metrics)


def _parse_json_lenient(s: str) -> Dict[str, Any]:
    """Robustes JSON-Parsing (kompakt; siehe orchestrator/engine.py für Vollversion)."""
    import json
    import re
    s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    cleaned = re.sub(r"```(?:json)?", "", s).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for sc, ec in [("[", "]"), ("{", "}")]:
        si = s.find(sc)
        ei = s.rfind(ec)
        if si != -1 and ei > si:
            try:
                return json.loads(s[si:ei + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("Kein valides JSON extrahierbar.")
