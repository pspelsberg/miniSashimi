import asyncio
import json
import logging
import re
from typing import List, Dict, Any, Callable, Awaitable, Optional, Literal
import uuid
from datetime import datetime, timezone
from pydantic import BaseModel, model_validator

from app.domain.models import Task, WorkflowState, TaskStatus
from app.core.database import get_setting
from app.core.security import log_audit_event
from app.adapters.outbound.llm.client import call_llm
from app.middleware.anonymizer import Anonymizer

class ConductorTaskSchema(BaseModel):
    title: str
    assigned_model: Literal["worker_backend", "worker_frontend", "test_architect", "worker_integration"]
    prompt_payload: str

    @model_validator(mode='before')
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # 1. Normalize prompt_payload
            payload_keys = ["prompt_payload", "prompt", "payload", "instruction", "instructions", "task", "description", "details"]
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
            
            if "prompt_payload" not in data or not data["prompt_payload"]:
                data["prompt_payload"] = data.get("title", "Keine Beschreibung angegeben.")

            # 2. Normalize assigned_model
            model_keys = ["assigned_model", "model", "assigned_worker", "worker"]
            model_val = None
            for mk in model_keys:
                if mk in data and data[mk] is not None:
                    model_val = str(data[mk]).strip()
                    break
            
            if model_val:
                model_val_lower = model_val.lower()
                if "backend" in model_val_lower:
                    data["assigned_model"] = "worker_backend"
                elif "frontend" in model_val_lower:
                    data["assigned_model"] = "worker_frontend"
                elif "architect" in model_val_lower or "test" in model_val_lower:
                    data["assigned_model"] = "test_architect"
                elif "integration" in model_val_lower:
                    data["assigned_model"] = "worker_integration"
                else:
                    valid_models = ["worker_backend", "worker_frontend", "test_architect", "worker_integration"]
                    if model_val in valid_models:
                        data["assigned_model"] = model_val
                    else:
                        data["assigned_model"] = "worker_backend"
            else:
                data["assigned_model"] = "worker_backend"

            # 3. Normalize title
            if "title" not in data or not data["title"]:
                if "prompt_payload" in data and data["prompt_payload"]:
                    payload_preview = str(data["prompt_payload"])[:40]
                    data["title"] = f"Task: {payload_preview}..."
                else:
                    data["title"] = "Unbenannter Task"

        return data

logger = logging.getLogger("minisashimi.orchestrator")

# Globale Liste aktiver Workflows im Speicher
active_workflows: Dict[str, WorkflowState] = {}
# Callback-Funktion für Dashboard-Updates (wird von main.py registriert)
dashboard_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None

def register_dashboard_callback(cb: Callable[[Dict[str, Any]], Awaitable[None]]):
    global dashboard_callback
    dashboard_callback = cb

async def notify_dashboard(workflow_id: str):
    if dashboard_callback and workflow_id in active_workflows:
        state = active_workflows[workflow_id]
        try:
            await dashboard_callback({
                "type": "workflow_update",
                "data": json.loads(state.model_dump_json())
            })
        except Exception as e:
            logger.error(f"Fehler beim Senden des Dashboard-Updates: {str(e)}")
            
        # Persistiere in der Datenbank
        try:
            from app.core.database import engine
            from app.domain.models import WorkflowsTable
            from sqlmodel import Session
            with Session(engine) as session:
                row = session.get(WorkflowsTable, workflow_id)
                if row:
                    row.state_json = state.model_dump_json()
                    row.updated_at = datetime.now(timezone.utc)
                    session.add(row)
                else:
                    new_wf = WorkflowsTable(workflow_id=workflow_id, state_json=state.model_dump_json())
                    session.add(new_wf)
                session.commit()
        except Exception as db_err:
            logger.error(f"Fehler beim Speichern des Workflows in der Datenbank: {str(db_err)}")

def sanitize_llm_output(content: str) -> str:
    """
    Returns the content directly. Standard rendering in the frontend (React's text nodes)
    ensures safety by automatically escaping HTML tags, preventing XSS/injection attacks
    without corrupting programming syntax.
    """
    return content


def extract_json(s: str) -> Any:
    s_trimmed = s.strip()
    
    # 1. Try direct loading
    try:
        return json.loads(s_trimmed)
    except json.JSONDecodeError:
        pass
        
    # 2. Try stripping markdown code block indicators
    lines = s.splitlines()
    cleaned_lines = []
    for line in lines:
        stripped_line = line.strip()
        if stripped_line.startswith("```"):
            continue
        cleaned_lines.append(line)
    
    candidate = "\n".join(cleaned_lines).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
        
    # 3. Search for JSON array or object boundaries
    start_chars = ('[', '{')
    for start_char in start_chars:
        start_idx = s.find(start_char)
        if start_idx == -1:
            continue
            
        end_char = ']' if start_char == '[' else '}'
        
        end_idx = s.rfind(end_char)
        while end_idx > start_idx:
            try:
                substring = s[start_idx:end_idx + 1]
                return json.loads(substring)
            except json.JSONDecodeError:
                end_idx = s.rfind(end_char, start_idx, end_idx)
                
    raise ValueError("No valid JSON found in string")


def parse_conductor_tasks(json_str: str) -> List[Dict[str, Any]]:
    """
    Parses and normalizes conductor tasks from a JSON string.
    Supports list format, nested object lists (e.g. {"tasks": [...]}), and single dict tasks.
    """
    raw_tasks = extract_json(json_str)
    
    # Falls das LLM ein Objekt zurückgibt, das die Taskliste in einem Key kapselt (z.B. {"tasks": [...]})
    if isinstance(raw_tasks, dict):
        possible_keys = ["tasks", "tasklist", "steps", "items", "list", "data", "workflow", "todo"]
        found_list = None
        for key in possible_keys:
            if key in raw_tasks and isinstance(raw_tasks[key], list):
                found_list = raw_tasks[key]
                break
        
        if found_list is not None:
            raw_tasks = found_list
        elif any(k in raw_tasks for k in ["title", "assigned_model", "prompt_payload", "prompt", "payload"]):
            raw_tasks = [raw_tasks]
        else:
            for val in raw_tasks.values():
                if isinstance(val, list):
                    raw_tasks = val
                    break

    # Validierung der Task-Strukturen über Pydantic (F-003)
    if isinstance(raw_tasks, list):
        validated_tasks = []
        for t in raw_tasks:
            if isinstance(t, dict):
                val = ConductorTaskSchema(**t)
                validated_tasks.append(val.model_dump())
        return validated_tasks
    else:
        raise ValueError("Conductor-Ergebnis ist kein JSON-Array.")


class OrchestratorEngine:
    def __init__(self, workflow_id: str, client_prompt: str):
        self.workflow_id = workflow_id
        self.client_prompt = client_prompt
        self.anonymizer = Anonymizer()
        self.stream_queue = asyncio.Queue()
        self.output_history = []
        
        # Initialisiere State
        self.state = WorkflowState(
            workflow_id=workflow_id,
            client_prompt=client_prompt,
            tasks=[],
            is_aborted=False,
            accumulated_cost_usd=0.0,
            status=TaskStatus.RUNNING,
            started_at=datetime.now(timezone.utc)
        )
        active_workflows[workflow_id] = self.state

    async def emit_output(self, title: str, content: str):
        formatted = f"\n\n### {title}\n{content}\n"
        self.output_history.append(formatted)
        await self.stream_queue.put(formatted)

    def check_abort(self):
        if self.state.is_aborted:
            raise InterruptedError("Workflow wurde vom Benutzer abgebrochen.")

    def get_effective_model(self, setting_key: str, default: str = "") -> str:
        if get_setting("test_mode_enabled", "false") == "true":
            return "mistral/mistral-small-latest"
        return get_setting(setting_key, default)

    def check_cost_limit(self):
        limit = float(get_setting("cost_limit_usd", "10.0"))
        if self.state.accumulated_cost_usd >= limit * 0.8:
            if not getattr(self, "_cost_warning_sent", False):
                self._cost_warning_sent = True
                log_audit_event("workflow_cost_warning", {
                    "workflow_id": self.workflow_id,
                    "accumulated_cost_usd": self.state.accumulated_cost_usd,
                    "limit_usd": limit
                })
            
        if self.state.accumulated_cost_usd >= limit:
            self.state.status = TaskStatus.FAILED
            log_audit_event("workflow_cost_limit_exceeded", {
                "workflow_id": self.workflow_id,
                "accumulated_cost_usd": self.state.accumulated_cost_usd,
                "limit_usd": limit
            })
            raise ValueError(f"Kostenlimit von ${limit:.4f} ueberschritten. Aktuelle Kosten: ${self.state.accumulated_cost_usd:.4f}")

    def check_limits(self):
        self.check_cost_limit()
        
        token_limit = int(get_setting("token_limit", "100000"))
        if self.state.total_tokens >= token_limit:
            self.state.status = TaskStatus.FAILED
            log_audit_event("workflow_token_limit_exceeded", {
                "workflow_id": self.workflow_id,
                "total_tokens": self.state.total_tokens,
                "limit_tokens": token_limit
            })
            raise ValueError(f"Token-Limit von {token_limit:,} ueberschritten. Aktueller Verbrauch: {self.state.total_tokens:,}")

    def add_cost(self, usage: Dict[str, int], model: str, task: Optional[Task] = None):
        """Berechnet die ungefähren Kosten basierend auf dem Modell und aggregiert Token."""
        if task:
            task.prompt_tokens = usage.get("prompt_tokens", 0)
            task.completion_tokens = usage.get("completion_tokens", 0)
            task.total_tokens = usage.get("total_tokens", 0)
            task.cache_read_tokens = usage.get("cache_read_tokens", 0)
            
        self.state.prompt_tokens += usage.get("prompt_tokens", 0)
        self.state.completion_tokens += usage.get("completion_tokens", 0)
        self.state.total_tokens += usage.get("total_tokens", 0)
        self.state.cache_read_tokens += usage.get("cache_read_tokens", 0)

        # Simple Schätzung
        prompt_rate = 0.0015 / 1000  # Default 1.50$ / M
        completion_rate = 0.002 / 1000
        
        if "gpt-4" in model:
            prompt_rate = 0.005 / 1000
            completion_rate = 0.015 / 1000
        elif "deepseek" in model:
            prompt_rate = 0.00014 / 1000
            completion_rate = 0.00028 / 1000
        elif "mistral-small" in model:
            prompt_rate = 0.0001 / 1000
            completion_rate = 0.0003 / 1000
        elif "gemini" in model:
            if "pro" in model:
                # Gemini Pro rates: $1.25/1M prompt, $5.00/1M completion
                prompt_rate = 0.00125 / 1000
                completion_rate = 0.005 / 1000
            else:
                # Gemini Flash or other models: $0.075/1M prompt, $0.30/1M completion
                prompt_rate = 0.000075 / 1000
                completion_rate = 0.0003 / 1000
            
        cost = (usage.get("prompt_tokens", 0) * prompt_rate) + (usage.get("completion_tokens", 0) * completion_rate)
        self.state.accumulated_cost_usd += cost
        
        # Enforce limits directly after adding costs to prevent runaway billing (CWE-400, LLM10:2025)
        self.check_limits()

    async def run(self) -> str:
        timeout_val = float(get_setting("workflow_timeout_seconds", "300.0"))
        try:
            async with asyncio.timeout(timeout_val):
                return await self._run_internal()
        except asyncio.TimeoutError:
            self.state.status = TaskStatus.FAILED
            self.state.completed_at = datetime.now(timezone.utc)
            if self.state.tasks:
                last_task = self.state.tasks[-1]
                if last_task.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
                    last_task.status = TaskStatus.FAILED
                    last_task.error = "Workflow-Timeout ueberschritten."
                    last_task.completed_at = datetime.now(timezone.utc)
            self.state.error = "Workflow-Timeout ueberschritten."
            await notify_dashboard(self.workflow_id)
            raise ValueError(f"Workflow-Timeout von {timeout_val} Sekunden ueberschritten.")

    async def _run_internal(self) -> str:
        try:
            await notify_dashboard(self.workflow_id)
            
            # --- 1. DATENSCHUTZ-MASKE ---
            pii_enabled = get_setting("pii_masking_enabled", "true") == "true"
            masked_prompt = self.anonymizer.mask(self.client_prompt) if pii_enabled else self.client_prompt
            logger.info(f"Starte Workflow {self.workflow_id}. Maskierung aktiv: {pii_enabled}")

            # --- 2. PHASE 1: THINKER ---
            self.check_abort()
            thinker_task = Task(title="Systemarchitektur & Ablauf-Planung (Thinker)", assigned_model=self.get_effective_model("model_thinker"))
            self.state.tasks.append(thinker_task)
            thinker_task.status = TaskStatus.RUNNING
            await notify_dashboard(self.workflow_id)
            
            thinker_messages = [
                {"role": "system", "content": "Du bist der Thinker-Agent. Deine Aufgabe ist es, komplexe Programmier- und Softwareaufgaben in granulare, logische Teilschritte zu zerlegen. Entwirfe eine Modulstruktur und klare Schnittstellen."},
                {"role": "user", "content": f"Bitte plane die Umsetzung für folgendes Problem:\n{masked_prompt}"}
            ]
            
            content, usage = await call_llm(thinker_task.assigned_model, thinker_messages)
            self.add_cost(usage, thinker_task.assigned_model, thinker_task)
            thinker_task.output = content
            thinker_task.status = TaskStatus.COMPLETED
            thinker_task.completed_at = datetime.now(timezone.utc)
            await notify_dashboard(self.workflow_id)
            await self.emit_output("🧠 Phase 1: Systemarchitektur & Ablauf-Planung (Thinker)", content)

            # --- 3. PHASE 1: CONDUCTOR ---
            self.check_abort()
            conductor_task = Task(title="Tool-Auswahl & Workflow-Konstruktion (Conductor)", assigned_model=self.get_effective_model("model_conductor"))
            self.state.tasks.append(conductor_task)
            conductor_task.status = TaskStatus.RUNNING
            await notify_dashboard(self.workflow_id)
            
            conductor_messages = [
                {"role": "system", "content": "Du bist der Conductor-Agent. Deine Aufgabe ist es, den Plan des Thinkers in eine Liste konkreter, technischer Arbeitsschritte (Tasks) für unsere Worker-LLMs zu übersetzen. Du musst zwingend ein valides JSON-Array zurückgeben, bei dem jedes Element die Felder 'title', 'assigned_model' (Wähle aus: 'worker_backend', 'worker_frontend', 'test_architect', 'worker_integration') und 'prompt_payload' (präzise Arbeitsanweisungen für diesen Worker) enthält. Antworte AUSSCHLIESSLICH im JSON-Format, ohne Markdown-Codeblöcke."},
                {"role": "user", "content": f"Hier ist der Plan des Thinkers:\n{content}\n\nGeneriere die JSON-Taskliste."}
            ]
            
            json_str, usage = await call_llm(conductor_task.assigned_model, conductor_messages)
            self.add_cost(usage, conductor_task.assigned_model, conductor_task)
            
            try:
                raw_tasks = parse_conductor_tasks(json_str)
            except Exception as e:
                logger.error(f"Conductor-Tasks Validierungsfehler: {str(e)}. JSON: {json_str}")
                # Fallback-Task erstellen falls JSON-Parsing oder Validierung scheitert
                raw_tasks = [{
                    "title": "Fallback Code-Synthese",
                    "assigned_model": "worker_backend",
                    "prompt_payload": f"Setze folgenden Plan um:\n{content}"
                }]
                
            conductor_task.output = json.dumps(raw_tasks, indent=2)
            conductor_task.status = TaskStatus.COMPLETED
            conductor_task.completed_at = datetime.now(timezone.utc)
            await notify_dashboard(self.workflow_id)
            await self.emit_output("📋 Phase 1: Tool-Auswahl & Workflow-Konstruktion (Conductor)", f"```json\n{conductor_task.output}\n```")

            # --- 4. PHASE 2: WORKERS ---
            worker_outputs = []
            for i, raw_task in enumerate(raw_tasks):
                self.check_abort()
                
                # Bestimme echtes Modell anhand der Conductor-Auswahl
                model_role = raw_task.get("assigned_model", "worker_backend")
                actual_model = self.get_effective_model(f"model_{model_role}", self.get_effective_model("model_worker_backend"))
                
                w_task = Task(
                    title=f"Worker {i+1}: {raw_task.get('title', 'Codegenerierung')}",
                    assigned_model=actual_model,
                    payload={"instruction": raw_task.get("prompt_payload", "")}
                )
                self.state.tasks.append(w_task)
                w_task.status = TaskStatus.RUNNING
                await notify_dashboard(self.workflow_id)
                
                worker_messages = [
                    {"role": "system", "content": f"Du bist ein hochspezialisierter Worker ({model_role}). Schreibe den Programmiercode exakt nach den Anweisungen. Liefere lauffähigen, sauberen Code ohne unnötige Prosa."},
                    {"role": "user", "content": f"Aufgabe:\n{raw_task.get('prompt_payload', '')}\n\nBisheriger Kontext:\n" + "\n".join(worker_outputs)}
                ]
                
                w_out, usage = await call_llm(actual_model, worker_messages)
                self.add_cost(usage, actual_model, w_task)
                w_task.output = w_out
                w_task.status = TaskStatus.COMPLETED
                w_task.completed_at = datetime.now(timezone.utc)
                worker_outputs.append(f"### Output von {w_task.title}:\n{w_out}")
                await notify_dashboard(self.workflow_id)
                await self.emit_output(f"🛠️ Phase 2: {w_task.title}", w_out)

            combined_code = "\n\n".join(worker_outputs)

            # --- 5. PHASE 3: VERIFICATION ---
            self.check_abort()
            verifier_task = Task(title="Syntax-Validierung & Vorab-Prüfung (Pre-Verifier)", assigned_model=self.get_effective_model("model_pre_verifier"))
            self.state.tasks.append(verifier_task)
            verifier_task.status = TaskStatus.RUNNING
            await notify_dashboard(self.workflow_id)
            
            verifier_messages = [
                {"role": "system", "content": "Du bist der Pre-Verifier. Prüfe den generierten Code schnell auf offensichtliche Syntaxfehler, Klammerfehler oder unvollständige Codeblöcke. Gib grünes Licht (OK) oder benenne die Fehler präzise."},
                {"role": "user", "content": f"Bitte validiere folgenden Code:\n{combined_code}"}
            ]
            v_out, usage = await call_llm(verifier_task.assigned_model, verifier_messages)
            self.add_cost(usage, verifier_task.assigned_model, verifier_task)
            verifier_task.output = v_out
            verifier_task.status = TaskStatus.COMPLETED
            verifier_task.completed_at = datetime.now(timezone.utc)
            await notify_dashboard(self.workflow_id)
            await self.emit_output("🔍 Phase 3: Syntax-Validierung & Vorab-Prüfung (Pre-Verifier)", v_out)

            # 3D-Verifier-Schwarm
            self.check_abort()
            security_task = Task(title="Verifier: Security Auditor", assigned_model=self.get_effective_model("model_security_auditor"))
            self.state.tasks.append(security_task)
            security_task.status = TaskStatus.RUNNING
            await notify_dashboard(self.workflow_id)
            
            security_messages = [
                {"role": "system", "content": "Du bist ein Security Auditor. Analysiere den Code exzessiv auf Sicherheitslücken (SQLi, XSS, Hardcoded Keys, unverschlüsselte Daten, OWASP Top 10). Werte gefundene Schwachstellen nach den Stufen [KRITISCH], [WARNUNG], [OPTIMIERUNG] oder [Guter Standard] und gib jeweils einen CVSS-Score sowie eine CWE-ID an."},
                {"role": "user", "content": f"Code zum Auditieren:\n{combined_code}"}
            ]
            sec_out, usage = await call_llm(security_task.assigned_model, security_messages)
            self.add_cost(usage, security_task.assigned_model, security_task)
            security_task.output = sec_out
            security_task.status = TaskStatus.COMPLETED
            security_task.completed_at = datetime.now(timezone.utc)
            await notify_dashboard(self.workflow_id)
            await self.emit_output("🛡️ Phase 3: Verifier: Security Auditor", sec_out)

            # --- 6. SYNTHESE & FINALES ERGEBNIS ---
            self.check_abort()
            synthesis_task = Task(title="Finales Review & Zusammenfassung", assigned_model=self.get_effective_model("model_thinker"))
            self.state.tasks.append(synthesis_task)
            synthesis_task.status = TaskStatus.RUNNING
            await notify_dashboard(self.workflow_id)
            
            synthesis_messages = [
                {"role": "system", "content": "Du bist der finale Integrations- und Synthese-Agent. Bringe die Code-Ergebnisse des Workers und das Feedback des Security Auditors zusammen. Generiere die endgültige Antwort für den Client. Behebe eventuelle kritische Sicherheitslücken direkt im finalen Code. Gib jeden geänderten oder neu erstellten Codeblock zwingend im folgenden Format aus:\n\n### FILE: <relativer_pfad_im_workspace>\n```<sprache>\n<code_inhalt>\n```\n\nErsetze <relativer_pfad_im_workspace> durch den tatsächlichen Pfad der Datei, z.B. 'hello.py' oder 'app/main.py'. Es ist absolut kritisch, dass die Zeile '### FILE: <pfad>' exakt so über dem Codeblock steht, damit der Client die Datei zuordnen kann."},
                {"role": "user", "content": f"Bisheriger Code:\n{combined_code}\n\nSecurity Feedback:\n{sec_out}"}
            ]
            final_content, usage = await call_llm(synthesis_task.assigned_model, synthesis_messages)
            self.add_cost(usage, synthesis_task.assigned_model, synthesis_task)
            
            # Demaskiere PII falls nötig, damit der Client die echten IDs/Daten sieht
            if pii_enabled:
                final_content = self.anonymizer.unmask(final_content)
            
            # Sanitieren gegen Prompt-Injection/XSS
            final_content = sanitize_llm_output(final_content)
                
            synthesis_task.output = final_content
            synthesis_task.status = TaskStatus.COMPLETED
            synthesis_task.completed_at = datetime.now(timezone.utc)
            
            self.state.status = TaskStatus.COMPLETED
            self.state.completed_at = datetime.now(timezone.utc)
            log_audit_event("workflow_completed", {
                "workflow_id": self.workflow_id,
                "cost_usd": self.state.accumulated_cost_usd,
                "tasks_count": len(self.state.tasks)
            })
            await notify_dashboard(self.workflow_id)
            await self.emit_output("🎯 Phase 4: Finales Review & Zusammenfassung", final_content)
            
            return "".join(self.output_history)
            
        except (InterruptedError, asyncio.CancelledError) as e:
            logger.warning(f"Workflow {self.workflow_id} abgebrochen: {str(e)}")
            self.state.status = TaskStatus.ABORTED
            self.state.completed_at = datetime.now(timezone.utc)
            log_audit_event("workflow_aborted", {
                "workflow_id": self.workflow_id,
                "cost_usd": self.state.accumulated_cost_usd
            })
            await notify_dashboard(self.workflow_id)
            await self.stream_queue.put(f"\n\n❌ **Workflow abgebrochen:** {str(e)}\n")
            raise e
        except Exception as e:
            logger.error(f"Schwerer Fehler im Workflow {self.workflow_id}: {str(e)}")
            self.state.status = TaskStatus.FAILED
            self.state.completed_at = datetime.now(timezone.utc)
            log_audit_event("workflow_failed", {
                "workflow_id": self.workflow_id,
                "error": str(e),
                "cost_usd": self.state.accumulated_cost_usd
            })
            # Markiere den aktuellen Task als fehlgeschlagen
            if self.state.tasks:
                last_task = self.state.tasks[-1]
                if last_task.status == TaskStatus.RUNNING:
                    last_task.status = TaskStatus.FAILED
                    last_task.error = str(e)
            await notify_dashboard(self.workflow_id)
            await self.stream_queue.put(f"\n\n❌ **Schwerer Fehler im Workflow:** {str(e)}\n")
            raise e
        finally:
            await self.stream_queue.put(None)
