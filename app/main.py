from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, Header, Query, Request, WebSocketException
from contextlib import asynccontextmanager
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session
from typing import List, Dict, Any, Optional
import json
import logging
import asyncio
import os
import secrets
import time

from app.core.database import init_db, engine
from app.core.security import log_audit_event, SecretRedactingFilter
from app.orchestrator.engine import OrchestratorEngine, active_workflows, register_dashboard_callback
from app.core.state import ws_clients, active_tasks, active_tickets
from app.middleware.rate_limiter import RateLimiter, api_rate_limiter, rate_limit

# Import Routers & Re-export names for test cases to maintain backwards-compatibility
from app.adapters.inbound.rest import (
    router as rest_router,
    chat_completions,
    delete_workflow_history,
    abort_workflow,
    extract_text_content,
    parse_files_from_markdown,
    ALLOWED_PROVIDERS,
    verify_token,
    get_or_create_dashboard_token,
    verify_and_consume_ticket
)
from app.adapters.inbound.websocket import router as ws_router

# Logging konfigurieren mit Zero-Leak Filter (2.6 Log-Masking-Middleware)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logging.getLogger().addFilter(SecretRedactingFilter())
logger = logging.getLogger("minisashimi.main")

is_prod = os.getenv("SASHIMI_ENV", "production").lower() == "production"

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    
    ws_log_handler = WebSocketLogHandler()
    ws_log_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    ws_log_handler.addFilter(SecretRedactingFilter())
    
    loggers_to_attach = [
        logging.getLogger(),
        logging.getLogger("minisashimi"),
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.access"),
        logging.getLogger("uvicorn.error")
    ]
    for l in loggers_to_attach:
        l.addHandler(ws_log_handler)
        
    logger.info("Datenbank initialisiert und Standardeinstellungen geladen.")
    
    try:
        from app.domain.models import WorkflowsTable, WorkflowState
        from sqlmodel import Session, select
        with Session(engine) as session:
            statement = select(WorkflowsTable)
            rows = session.exec(statement).all()
            for row in rows:
                try:
                    state = WorkflowState.model_validate_json(row.state_json)
                    active_workflows[state.workflow_id] = state
                except Exception as parse_err:
                    logger.error(f"Fehler beim Parsen des historischen Workflows {row.workflow_id}: {str(parse_err)}")
        logger.info(f"{len(active_workflows)} historische/aktive Workflows im Speicher initialisiert.")
    except Exception as db_err:
        logger.error(f"Fehler beim Laden historischer Workflows: {str(db_err)}")
        
    get_or_create_dashboard_token()
    api_rate_limiter.start_cleanup()
    
    async def ticket_cleanup_loop():
        while True:
            try:
                await asyncio.sleep(60)
                now = time.time()
                expired = [t for t, exp in list(active_tickets.items()) if exp < now]
                for t in expired:
                    active_tickets.pop(t, None)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in ticket cleanup loop: {str(e)}")

    ticket_task = asyncio.create_task(ticket_cleanup_loop())
    
    async def workflow_cleanup_loop():
        from datetime import datetime, timezone
        from app.domain.models import TaskStatus
        while True:
            try:
                await asyncio.sleep(300)  # alle 5 Minuten
                now = datetime.now(timezone.utc)
                to_remove = []
                for wid, state in list(active_workflows.items()):
                    if state.completed_at:
                        age = (now - state.completed_at).total_seconds()
                        if age > 3600:  # 1 Stunde
                            to_remove.append(wid)
                    else:
                        if state.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.ABORTED):
                            state.completed_at = now
                        else:
                            age = (now - state.started_at).total_seconds()
                            if age > 14400:  # 4 Stunden
                                to_remove.append(wid)
                for wid in to_remove:
                    active_workflows.pop(wid, None)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in workflow cleanup loop: {str(e)}")

    workflow_task = asyncio.create_task(workflow_cleanup_loop())
    
    async def ws_update_callback(message: Dict[str, Any]):
        dead_clients = []
        for client in list(ws_clients):
            try:
                await client.send_text(json.dumps(message))
            except Exception:
                dead_clients.append(client)
        for client in dead_clients:
            if client in ws_clients:
                ws_clients.remove(client)
                
    register_dashboard_callback(ws_update_callback)
    
    yield
    
    ticket_task.cancel()
    workflow_task.cancel()
    for l in loggers_to_attach:
        l.removeHandler(ws_log_handler)

app = FastAPI(
    title="miniSashimi Multi-Agent Proxy",
    docs_url=None if is_prod else "/docs",
    redoc_url=None if is_prod else "/redoc",
    openapi_url=None if is_prod else "/openapi.json",
    lifespan=lifespan
)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    try:
        body = await request.body()
        body_str = body.decode("utf-8", errors="ignore")
    except Exception:
        body_str = "<could not read body>"
    safe_errors = [{"loc": e["loc"], "type": e["type"]} for e in exc.errors()]
    logger.error(f"Validation error: {safe_errors}")
    from app.core.security import sanitize_value
    logger.error(f"Request body was: {sanitize_value(body_str)}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()}
    )

# HTTP Security Header Middleware (F-009 / F-014)
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' ws: wss:;"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    return response

# CORS konfigurieren, damit das React Frontend zugreifen kann
allowed_origins_str = os.getenv("SASHIMI_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
allowed_origins = [origin.strip() for origin in allowed_origins_str.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
)

class WebSocketLogHandler(logging.Handler):
    def emit(self, record):
        if getattr(record, "_from_ws_handler", False):
            return
        try:
            record._from_ws_handler = True
            log_entry = self.format(record)
            message = {
                "type": "log_event",
                "data": {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(record.created)),
                    "logger": record.name,
                    "level": record.levelname,
                    "message": log_entry
                }
            }
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    loop.call_soon_threadsafe(
                        lambda: loop.create_task(broadcast_log_to_ws(message))
                    )
            except RuntimeError:
                pass
        except Exception:
            pass

async def broadcast_log_to_ws(message: Dict[str, Any]):
    dead_clients = []
    for client in list(ws_clients):
        try:
            await client.send_text(json.dumps(message))
        except Exception:
            dead_clients.append(client)
    for client in dead_clients:
        if client in ws_clients:
            ws_clients.remove(client)



@app.get("/health", dependencies=[Depends(rate_limit)])
def health():
    return {"status": "ok"}

# Include Routers
app.include_router(rest_router)
app.include_router(ws_router)
