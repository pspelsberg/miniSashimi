from fastapi import APIRouter, WebSocket, WebSocketDisconnect, WebSocketException, Query
from typing import Optional
import json
import logging

from app.core.security import log_audit_event
from app.orchestrator.engine import active_workflows
from app.core.state import ws_clients, ws_clients_lock
from app.adapters.inbound.rest import verify_and_consume_ticket

logger = logging.getLogger("minisashimi.websocket")
router = APIRouter()

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, ticket: Optional[str] = Query(None)):
    # Ticket validieren vor dem Akzeptieren der Verbindung (F-018)
    if not ticket or not verify_and_consume_ticket(ticket):
        raise WebSocketException(code=1008, reason="Unauthorized")

    # Rate Limiting: Max 10 total active connections, max 3 per IP (F-029).
    client_ip = websocket.client.host if websocket.client else "unknown"
    async with ws_clients_lock:
        if len(ws_clients) >= 10:
            raise WebSocketException(code=1008, reason="Server busy. Max active connections reached.")
        ip_connections = sum(1 for c in ws_clients if c.client and c.client.host == client_ip)
        if ip_connections >= 3:
            raise WebSocketException(code=1008, reason="Too many active connections from this IP.")
        await websocket.accept()
        ws_clients.append(websocket)
    log_audit_event("ws_connected", {
        "client_ip": client_ip,
        "active_clients_count": len(ws_clients)
    })
    logger.info(f"Dashboard-Client verbunden via WebSocket. Aktive Clients: {len(ws_clients)}")
    
    try:
        # Sende direkt alle aktuell bekannten Workflows
        try:
            workflows_dump = {
                "type": "init_workflows",
                "data": {wid: json.loads(w.model_dump_json()) for wid, w in active_workflows.items()}
            }
            await websocket.send_text(json.dumps(workflows_dump))
        except Exception as e:
            logger.error(f"Fehler beim Senden des Init-Dumps via WebSocket: {str(e)}")

        while True:
            # Halte Verbindung offen und lausche auf Pings
            data = await websocket.receive_text()
            # Client input is untrusted; malformed frames are rejected
            # without raising an avoidable server-side exception.
            try:
                msg = json.loads(data)
            except (TypeError, json.JSONDecodeError):
                await websocket.send_text(json.dumps({"type": "error", "message": "Invalid message"}))
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        logger.info("Dashboard-Client getrennt.")
    except Exception as e:
        logger.error(f"WebSocket Fehler: {str(e)}")
    finally:
        async with ws_clients_lock:
            if websocket in ws_clients:
                ws_clients.remove(websocket)
        logger.info(f"WebSocket Verbindung bereinigt. Aktive Clients: {len(ws_clients)}")
