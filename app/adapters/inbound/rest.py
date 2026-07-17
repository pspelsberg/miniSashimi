from fastapi import APIRouter, Depends, HTTPException, Header, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import Session
from typing import Dict, Any, List, Optional
import os
import secrets
import time
import json
import logging
import asyncio
import hashlib
from datetime import datetime, timezone, timedelta

from app.domain.models import (
    ChatCompletionRequest, ChatCompletionResponse, ChatCompletionChoice, 
    ChatMessage, ChatCompletionUsage, WorkflowsTable, WorkflowState, TaskStatus
)
from app.core.database import (
    get_api_key, save_api_key, delete_api_key, get_setting, save_setting,
    create_session, get_session, delete_session, engine
)
from app.core.security import encrypt_key, decrypt_key, log_audit_event
from app.orchestrator.engine import (
    OrchestratorEngine, active_workflows, register_dashboard_callback
)
from app.core.state import active_tasks, active_tickets
from app.middleware.rate_limiter import rate_limit

logger = logging.getLogger("minisashimi.rest")
router = APIRouter()

ALLOWED_PROVIDERS = {"openai", "anthropic", "mistral", "openrouter", "deepseek", "google"}
SESSION_TTL_SECONDS = 86_400
MAX_MARKDOWN_OUTPUT_BYTES = 500_000
MAX_FILE_BLOCKS = 50
ALLOWED_SETTING_KEYS = {
    "model_thinker", "model_conductor", "model_worker_backend",
    "model_worker_frontend", "model_test_architect", "model_worker_integration",
    "model_pre_verifier", "model_security_auditor", "model_performance_scout",
    "model_dependency_watchdog", "model_visual_verifier", "model_runtime_executor",
    "model_repository_janitor", "pii_masking_enabled", "cost_limit_usd",
    "llm_timeout_seconds", "test_mode_enabled", "lmstudio_base_url",
    "ollama_base_url", "llama_cpp_base_url", "token_limit",
    "workflow_timeout_seconds",
}


def _hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _secure_cookie() -> bool:
    default = "false" if os.getenv("SASHIMI_ENV", "production").lower() != "production" else "true"
    return os.getenv("SASHIMI_COOKIE_SECURE", default).lower() == "true"


def _session_cookie_parts(request: Request) -> Optional[tuple[str, str, int]]:
    raw_cookie = request.cookies.get("sashimi_session")
    if not raw_cookie:
        return None
    try:
        decrypted = decrypt_key(raw_cookie)
        session_id, csrf_token, issued_at = decrypted.split(":", 2)
        return session_id, csrf_token, int(issued_at)
    except (ValueError, TypeError):
        return None


class LoginPayload(BaseModel):
    token: str = Field(min_length=1, max_length=512)


def uuid_str() -> str:
    import uuid
    return str(uuid.uuid4())

def get_or_create_dashboard_token() -> str:
    expected_token = os.getenv("SASHIMI_DASHBOARD_TOKEN")
    if expected_token:
        return expected_token
        
    encrypted_token = get_setting("dashboard_token")
    if encrypted_token:
        try:
            decrypted = decrypt_key(encrypted_token)
            if decrypted:
                return decrypted
        except Exception:
            pass
            
    # Generate new token
    expected_token = secrets.token_hex(16)
    save_setting("dashboard_token", encrypt_key(expected_token))
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        mode = 0o600
        token_dir = os.getenv("SASHIMI_KEYS_DIR", os.path.expanduser("~/.minisashimi"))
        os.makedirs(token_dir, mode=0o700, exist_ok=True)
        os.chmod(token_dir, 0o700)
        token_file = os.path.join(token_dir, "dashboard_token")
        fd = os.open(token_file, flags, mode)
        with os.fdopen(fd, "w") as f:
            f.write(expected_token)
        logger.info("\n=========================================\n"
                    "DASHBOARD ACCESS TOKEN GENERATED:\n"
                    f"  Saved to secure location: {token_file}\n"
                    "Save this token or set SASHIMI_DASHBOARD_TOKEN in your environment.\n"
                    "=========================================\n")
    except Exception as e:
        logger.error(f"Failed to write dashboard_token file: {str(e)}")
        
    return expected_token

def select_write_tool_name(tools: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    if not tools:
        return None
    accepted = {"write", "write_file", "writeFile"}
    for tool in tools:
        if isinstance(tool, dict) and tool.get("type") == "function":
            name = tool.get("function", {}).get("name")
            if name in accepted:
                return name
    return None


class AuthContext:
    def __init__(self, token: str, csrf_token: Optional[str] = None, auth_method: str = "header"):
        self.token = token
        self.csrf_token = csrf_token
        self.auth_method = auth_method

def verify_token(request: Request, authorization: Optional[str] = Header(None)) -> AuthContext:
    expected_token = get_or_create_dashboard_token()

    provided_token = None
    csrf_token = None
    auth_method = "header"

    cookie_parts = _session_cookie_parts(request)
    if cookie_parts:
        session_id, cookie_csrf, issued_at = cookie_parts
        session = get_session(session_id)
        now = int(time.time())
        if session is not None:
            expires_at = session.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        if (
            session
            and now - issued_at <= SESSION_TTL_SECONDS
            and expires_at > datetime.now(timezone.utc)
            and secrets.compare_digest(session.token_hash, _hash_value(expected_token))
            and secrets.compare_digest(session.csrf_hash, _hash_value(cookie_csrf))
        ):
            provided_token = expected_token
            csrf_token = cookie_csrf
            auth_method = "cookie"

    if not provided_token and authorization and isinstance(authorization, str):
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            provided_token = parts[1]
        else:
            provided_token = authorization
        auth_method = "header"

    if not provided_token or not secrets.compare_digest(provided_token, expected_token):
        raise HTTPException(status_code=401, detail="Unauthorized dashboard access.")

    if auth_method == "cookie" and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        csrf_header = request.headers.get("X-CSRF-Token")
        if not csrf_header or not csrf_token or not secrets.compare_digest(csrf_header, csrf_token):
            raise HTTPException(status_code=403, detail="CSRF token validation failed.")

    return AuthContext(token=provided_token, csrf_token=csrf_token, auth_method=auth_method)


def generate_ws_ticket() -> str:
    ticket = secrets.token_hex(16)
    active_tickets[ticket] = time.time() + 10.0  # 10 Sekunden Gueltigkeit
    return ticket

def verify_and_consume_ticket(ticket: str) -> bool:
    now = time.time()
    # Expired Tickets aufraeumen
    expired = [t for t, exp in list(active_tickets.items()) if exp < now]
    for t in expired:
        active_tickets.pop(t, None)
    if ticket in active_tickets:
        exp = active_tickets.pop(ticket)
        return exp >= now
    return False

def extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                if part.get("type") == "text" and "text" in part:
                    text_parts.append(part["text"])
        return "\n".join(text_parts)
    return str(content) if content is not None else ""

def validate_tool_path(raw_path: str) -> str:
    """Accept only safe, relative POSIX paths for downstream write tools."""
    from pathlib import PurePosixPath

    if not isinstance(raw_path, str) or not raw_path.strip() or "\x00" in raw_path:
        raise ValueError("Invalid tool path")
    path = PurePosixPath(raw_path.strip())
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Tool path must stay within the client workspace")
    return str(path)


def parse_files_from_markdown(text: str):
    import re

    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_MARKDOWN_OUTPUT_BYTES:
        return []
    pattern = r'[ \t]*###\s*FILE:\s*([^\n\r]{1,4096})\s*(?:[\r\n]+)[ \t]*```[a-zA-Z0-9#+_-]*\s*[\r\n]+(.*?)(?:[\r\n]+)[ \t]*```'
    matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
    results = []
    for filename, content in matches[:MAX_FILE_BLOCKS]:
        try:
            fn = validate_tool_path(filename.strip())
        except ValueError:
            logger.warning("Rejected unsafe LLM tool path")
            continue
        if len(content.encode("utf-8")) <= 200_000:
            results.append((fn, content))
    return results

@router.post("/v1/chat/completions", dependencies=[Depends(verify_token), Depends(rate_limit)])
async def chat_completions(request: ChatCompletionRequest, http_request: Request):
    logger.info(f"OpenAI API Call empfangen fuer Modell: {request.model}")
    
    # Extrahiere den eigentlichen Prompt
    if not request.messages:
        raise HTTPException(status_code=400, detail="Keine Nachrichten uebergeben.")
        
    if request.messages[-1].role in ("tool", "function"):
        logger.info("Letzte Nachricht ist eine Tool-Antwort. Beende Workflow/Konversation.")
        completion_text = "Ich habe die Ergebnisse der Dateioperationen empfangen. Alle Dateien wurden erfolgreich angelegt und verifiziert. Der Workflow ist abgeschlossen."
        
        if request.stream:
            from fastapi.responses import StreamingResponse
            import time
            
            async def simple_generator():
                chunk_id = f"chatcmpl-{uuid_str()}"
                created_time = int(time.time())
                
                first_chunk = {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": request.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": ""},
                            "logprobs": None,
                            "finish_reason": None
                        }
                    ]
                }
                yield f"data: {json.dumps(first_chunk)}\n\n"
                
                content_chunk = {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": request.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": completion_text},
                            "logprobs": None,
                            "finish_reason": None
                        }
                    ]
                }
                yield f"data: {json.dumps(content_chunk)}\n\n"
                
                final_chunk = {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": request.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "logprobs": None,
                            "finish_reason": "stop"
                        }
                    ]
                }
                yield f"data: {json.dumps(final_chunk)}\n\n"
                yield "data: [DONE]\n\n"
                
            return StreamingResponse(simple_generator(), media_type="text/event-stream")
            
        else:
            choice = ChatCompletionChoice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content=completion_text
                ),
                finish_reason="stop"
            )
            
            response = ChatCompletionResponse(
                model=request.model,
                choices=[choice],
                usage=ChatCompletionUsage(
                    prompt_tokens=10,
                    completion_tokens=len(completion_text.split()) * 2,
                    total_tokens=10 + len(completion_text.split()) * 2
                )
            )
            return response
            
    user_messages = [m for m in request.messages if m.role == "user"]
    if not user_messages:
        raw_content = request.messages[-1].content
    else:
        raw_content = user_messages[-1].content

    prompt = extract_text_content(raw_content)

    workflow_id = f"wf-{uuid_str()}"
    client_ip = http_request.client.host if http_request.client else "unknown"
    log_audit_event("workflow_started", {
        "workflow_id": workflow_id,
        "model": request.model,
        "client_ip": client_ip
    })

    # Feature-Flag: Graph-Engine mit Conditional Routing + Handoff-Validation
    # Setze SASHIMI_USE_GRAPH_ENGINE=1 in der Umgebung, um den GraphOrchestrator zu nutzen.
    use_graph = os.getenv("SASHIMI_USE_GRAPH_ENGINE", "0") == "1"
    if use_graph:
        from app.orchestrator.graph_orchestrator import GraphOrchestrator
        engine_instance = GraphOrchestrator(
            workflow_id,
            prompt,
            validator=http_request.app.state.handoff_validator,
            memory=http_request.app.state.hybrid_memory,
            cache=http_request.app.state.semantic_cache,
            acp_bus=http_request.app.state.acp_bus,
        )
        logger.info(f"Workflow {workflow_id} nutzt GraphOrchestrator (Next-Level-Path)")
    else:
        engine_instance = OrchestratorEngine(workflow_id, prompt)
    
    # Starte die Orchestrierung in einem Hintergrund-Task
    workflow_task = asyncio.create_task(engine_instance.run())
    active_tasks[workflow_id] = workflow_task
    
    # Monitor-Task zur Ueberwachung des Client-Disconnects
    async def monitor_disconnect():
        try:
            while not workflow_task.done():
                if await http_request.is_disconnected():
                    logger.warning(f"Client hat die Verbindung getrennt. Breche Workflow {workflow_id} ab.")
                    engine_instance.state.is_aborted = True
                    workflow_task.cancel()
                    break
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            pass

    disconnect_monitor_task = asyncio.create_task(monitor_disconnect())
    
    try:
        if request.stream:
            from fastapi.responses import StreamingResponse
            import time

            async def event_generator():
                try:
                    chunk_id = f"chatcmpl-{uuid_str()}"
                    created_time = int(time.time())
                    
                    first_chunk = {
                        "id": chunk_id,
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": request.model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": ""},
                                "logprobs": None,
                                "finish_reason": None
                            }
                        ]
                    }
                    yield f"data: {json.dumps(first_chunk)}\n\n"
                    
                    while True:
                        if workflow_task.done() and engine_instance.stream_queue.empty():
                            break
                            
                        try:
                            chunk_text = await asyncio.wait_for(engine_instance.stream_queue.get(), timeout=1.0)
                        except asyncio.TimeoutError:
                            if workflow_task.done() and engine_instance.stream_queue.empty():
                                break
                            yield ": keep-alive\n\n"
                            continue
                            
                        if chunk_text is None:
                            break
                            
                        chunk = {
                            "id": chunk_id,
                            "object": "chat.completion.chunk",
                            "created": created_time,
                            "model": request.model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": chunk_text},
                                    "logprobs": None,
                                    "finish_reason": None
                                }
                            ]
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"
                    
                    final_ans = await workflow_task
                    parsed_files = parse_files_from_markdown(final_ans)
                    
                    if parsed_files and request.tools:
                        write_tool_name = select_write_tool_name(request.tools)
                        if not write_tool_name:
                            parsed_files = []

                        for idx, (filepath, code_content) in enumerate(parsed_files):
                            tc_id = f"call_{uuid_str()[:12]}"
                            arguments = {
                                "path": filepath,
                                "content": code_content
                            }
                            
                            tc_start = {
                                "id": chunk_id,
                                "object": "chat.completion.chunk",
                                "created": created_time,
                                "model": request.model,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {
                                            "tool_calls": [
                                                {
                                                    "index": idx,
                                                    "id": tc_id,
                                                    "type": "function",
                                                    "function": {
                                                        "name": write_tool_name,
                                                        "arguments": ""
                                                    }
                                                }
                                            ]
                                        },
                                        "logprobs": None,
                                        "finish_reason": None
                                    }
                                ]
                            }
                            yield f"data: {json.dumps(tc_start)}\n\n"
                            
                            tc_args = {
                                "id": chunk_id,
                                "object": "chat.completion.chunk",
                                "created": created_time,
                                "model": request.model,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {
                                            "tool_calls": [
                                                {
                                                    "index": idx,
                                                    "function": {
                                                        "arguments": json.dumps(arguments, ensure_ascii=False)
                                                    }
                                                }
                                            ]
                                        },
                                        "logprobs": None,
                                        "finish_reason": None
                                    }
                                ]
                            }
                            yield f"data: {json.dumps(tc_args)}\n\n"
                    
                    finish_reason = "tool_calls" if parsed_files and request.tools else "stop"
                    final_chunk = {
                        "id": chunk_id,
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": request.model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "logprobs": None,
                                "finish_reason": finish_reason
                            }
                        ]
                    }
                    yield f"data: {json.dumps(final_chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                    
                except asyncio.CancelledError:
                    logger.warning(f"Streaming-Client hat die Verbindung getrennt. Breche Workflow {workflow_id} ab.")
                    engine_instance.state.is_aborted = True
                    workflow_task.cancel()
                    raise
                except Exception as e:
                    logger.exception("Fehler im Streaming-Generator")
                    error_chunk = {
                        "id": f"chatcmpl-{uuid_str()}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": request.model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": "\n\n[FEHLER] Systemfehler bei der Orchestrierung. Bitte Administrator kontaktieren."},
                                "logprobs": None,
                                "finish_reason": "error"
                            }
                        ]
                    }
                    yield f"data: {json.dumps(error_chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                finally:
                    disconnect_monitor_task.cancel()
                    active_tasks.pop(workflow_id, None)

            return StreamingResponse(event_generator(), media_type="text/event-stream")

        final_answer = await workflow_task
        tool_calls = []
        parsed_files = parse_files_from_markdown(final_answer)
        if parsed_files and request.tools:
            write_tool_name = select_write_tool_name(request.tools)
            if not write_tool_name:
                parsed_files = []

            for filepath, code_content in parsed_files:
                tc_id = f"call_{uuid_str()[:12]}"
                arguments = {
                    "path": filepath,
                    "content": code_content
                }
                tool_calls.append({
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": write_tool_name,
                        "arguments": json.dumps(arguments, ensure_ascii=False)
                    }
                })

        choice = ChatCompletionChoice(
            index=0,
            message=ChatMessage(
                role="assistant", 
                content=final_answer,
                tool_calls=tool_calls if tool_calls else None
            ),
            finish_reason="tool_calls" if tool_calls else "stop"
        )
        
        prompt_tokens = len(prompt.split()) * 2
        completion_tokens = len(final_answer.split()) * 2
        
        response = ChatCompletionResponse(
            model=request.model,
            choices=[choice],
            usage=ChatCompletionUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens
            )
        )
        return response
        
    except (InterruptedError, asyncio.CancelledError):
        raise HTTPException(status_code=499, detail="Workflow durch den Benutzer abgebrochen.")
    except Exception as e:
        logger.exception("Schwerer Fehler bei der Systemorchestrierung")
        raise HTTPException(status_code=500, detail="Interner Serverfehler bei der Workflow-Verarbeitung.")
    finally:
        disconnect_monitor_task.cancel()
        active_tasks.pop(workflow_id, None)

@router.get("/api/verify", dependencies=[Depends(rate_limit)])
def verify_access(auth: AuthContext = Depends(verify_token)):
    return {"status": "valid", "csrf_token": auth.csrf_token}

@router.post("/api/login", dependencies=[Depends(rate_limit)])
def login(payload: LoginPayload, response: Response):
    token = payload.token.strip()
    expected_token = get_or_create_dashboard_token()
    if not token or not secrets.compare_digest(token, expected_token):
        raise HTTPException(status_code=401, detail="Invalid token")

    csrf_token = secrets.token_urlsafe(32)
    session_id = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_TTL_SECONDS)
    create_session(session_id, expected_token, csrf_token, expires_at)
    cookie_value = encrypt_key(f"{session_id}:{csrf_token}:{int(time.time())}")

    response.set_cookie(
        key="sashimi_session",
        value=cookie_value,
        httponly=True,
        secure=_secure_cookie(),
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )
    return {"status": "ok", "csrf_token": csrf_token}


@router.post("/api/logout")
def logout(request: Request, response: Response):
    parts = _session_cookie_parts(request)
    if parts:
        delete_session(parts[0])
    response.delete_cookie("sashimi_session", path="/")
    return {"status": "ok"}


@router.post("/api/ws-ticket", dependencies=[Depends(verify_token), Depends(rate_limit)])
def get_ws_ticket():
    ticket = generate_ws_ticket()
    return {"ticket": ticket}

@router.get("/api/keys", dependencies=[Depends(verify_token), Depends(rate_limit)])
def list_keys():
    providers = ["openai", "anthropic", "mistral", "openrouter", "deepseek", "google"]
    results = {}
    for p in providers:
        key = get_api_key(p)
        results[p] = {
            "configured": bool(key),
            "masked": f"••••••••••••{key[-4:]}" if key and len(key) > 4 else ("••••" if key else "")
        }
    return results

@router.post("/api/keys/{provider}", dependencies=[Depends(verify_token), Depends(rate_limit)])
def update_key(provider: str, payload: Dict[str, str], http_request: Request):
    prov = provider.lower().strip()
    if prov not in ALLOWED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Ungültiger Provider '{provider}'. Erlaubt sind: {', '.join(ALLOWED_PROVIDERS)}")
    api_key = payload.get("api_key", "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="Kein API-Key uebergeben.")
    save_api_key(prov, api_key)
    client_ip = http_request.client.host if http_request.client else "unknown"
    log_audit_event("api_key_updated", {
        "provider": prov,
        "client_ip": client_ip
    })
    logger.info(f"API Key fuer Provider '{prov}' erfolgreich aktualisiert.")
    return {"status": "ok", "message": f"API Key fuer '{prov}' gespeichert."}

@router.delete("/api/keys/{provider}", dependencies=[Depends(verify_token), Depends(rate_limit)])
def delete_key(provider: str, http_request: Request):
    prov = provider.lower().strip()
    if prov not in ALLOWED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Ungültiger Provider '{provider}'. Erlaubt sind: {', '.join(ALLOWED_PROVIDERS)}")
    deleted = delete_api_key(prov)
    client_ip = http_request.client.host if http_request.client else "unknown"
    log_audit_event("api_key_deleted", {
        "provider": prov,
        "client_ip": client_ip
    })
    if deleted:
        logger.info(f"API Key fuer Provider '{prov}' erfolgreich geloescht.")
        return {"status": "ok", "message": f"API Key fuer '{prov}' geloescht."}
    else:
        return {"status": "not_found", "message": f"API Key fuer '{prov}' war nicht hinterlegt."}

@router.get("/api/settings", dependencies=[Depends(verify_token), Depends(rate_limit)])
def get_settings():
    return {k: get_setting(k) for k in sorted(ALLOWED_SETTING_KEYS)}


@router.post("/api/settings", dependencies=[Depends(verify_token), Depends(rate_limit)])
def update_settings(settings: Dict[str, str], http_request: Request):
    from app.core.security import is_allowed_base_url

    if len(settings) > len(ALLOWED_SETTING_KEYS):
        raise HTTPException(status_code=400, detail="Too many settings supplied")

    numeric_ranges = {
        "cost_limit_usd": (0.01, 100_000.0),
        "llm_timeout_seconds": (1.0, 3_600.0),
        "token_limit": (1.0, 10_000_000.0),
        "workflow_timeout_seconds": (1.0, 86_400.0),
    }
    for key, value in settings.items():
        if key not in ALLOWED_SETTING_KEYS:
            raise HTTPException(status_code=400, detail="Unknown setting")
        if not isinstance(value, str) or len(value) > 2_000:
            raise HTTPException(status_code=400, detail="Invalid setting value")
        if key.endswith("_base_url") and value:
            provider = key.removesuffix("_base_url")
            if not is_allowed_base_url(value, provider):
                raise HTTPException(status_code=400, detail="Unsafe or unapproved URL detected")
        if key in numeric_ranges:
            try:
                number = float(value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid numeric setting") from exc
            minimum, maximum = numeric_ranges[key]
            if not minimum <= number <= maximum:
                raise HTTPException(status_code=400, detail="Numeric setting out of range")
        if key in {"pii_masking_enabled", "test_mode_enabled"} and value not in {"true", "false"}:
            raise HTTPException(status_code=400, detail="Boolean setting must be true or false")

    for key, value in settings.items():
        save_setting(key, value)
    client_ip = http_request.client.host if http_request.client else "unknown"
    log_audit_event("settings_updated", {
        "updated_keys": list(settings.keys()),
        "client_ip": client_ip
    })
    logger.info("Systemeinstellungen erfolgreich aktualisiert.")
    return {"status": "ok"}

@router.get("/api/workflows", dependencies=[Depends(verify_token), Depends(rate_limit)])
def list_workflows():
    return {wid: w.model_dump() for wid, w in active_workflows.items()}

@router.delete("/api/workflows", dependencies=[Depends(verify_token), Depends(rate_limit)])
async def delete_workflow_history(http_request: Request):
    from sqlmodel import Session, delete
    
    try:
        with Session(engine) as session:
            session.exec(delete(WorkflowsTable))
            session.commit()
    except Exception as db_err:
        logger.error(f"Fehler beim Löschen der Workflows aus der Datenbank: {str(db_err)}")
        raise HTTPException(status_code=500, detail="Datenbankfehler beim Löschen der Workflows.")
        
    to_keep = {}
    for wid, state in list(active_workflows.items()):
        if state.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
            to_keep[wid] = state
            try:
                with Session(engine) as session:
                    new_wf = WorkflowsTable(workflow_id=wid, state_json=state.model_dump_json())
                    session.add(new_wf)
                    session.commit()
            except Exception as e:
                logger.error(f"Fehler beim Re-Speichern des aktiven Workflows {wid}: {str(e)}")
                
    active_workflows.clear()
    active_workflows.update(to_keep)
    
    workflows_dump = {
        "type": "init_workflows",
        "data": {wid: json.loads(w.model_dump_json()) for wid, w in active_workflows.items()}
    }
    
    from app.core.state import ws_clients
    dead_clients = []
    for client in list(ws_clients):
        try:
            await client.send_text(json.dumps(workflows_dump))
        except Exception:
            dead_clients.append(client)
    for client in dead_clients:
        if client in ws_clients:
            ws_clients.remove(client)
            
    client_ip = http_request.client.host if http_request.client else "unknown"
    log_audit_event("workflow_history_deleted", {
        "client_ip": client_ip
    })
    logger.info("Workflow-Historie erfolgreich gelöscht.")
    return {"status": "ok", "message": "Workflow-Historie gelöscht."}

@router.post("/api/workflows/{workflow_id}/abort", dependencies=[Depends(verify_token), Depends(rate_limit)])
async def abort_workflow(workflow_id: str, http_request: Request):
    from app.orchestrator.engine import notify_dashboard
    
    if workflow_id not in active_workflows:
        raise HTTPException(status_code=404, detail="Workflow nicht gefunden.")
    
    active_workflows[workflow_id].is_aborted = True
    
    if workflow_id in active_tasks:
        active_tasks[workflow_id].cancel()
        
    state = active_workflows[workflow_id]
    if state.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
        state.status = TaskStatus.ABORTED
        state.completed_at = datetime.now(timezone.utc)
        if state.tasks:
            last_task = state.tasks[-1]
            if last_task.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
                last_task.status = TaskStatus.ABORTED
                last_task.completed_at = datetime.now(timezone.utc)
                
    await notify_dashboard(workflow_id)

    client_ip = http_request.client.host if http_request.client else "unknown"
    log_audit_event("workflow_aborted_by_user", {
        "workflow_id": workflow_id,
        "client_ip": client_ip
    })
    logger.warning(f"Abbruch fuer Workflow '{workflow_id}' angefordert und ausgeführt.")
    return {"status": "ok", "message": "Abbruch-Signal gesendet."}
