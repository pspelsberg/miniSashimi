from typing import List, Dict
import asyncio
from fastapi import WebSocket

# Liste der aktiven WebSocket-Verbindungen (Dashboard Clients)
ws_clients: List[WebSocket] = []

# Registrierung der aktiv laufenden Workflow-Tasks
active_tasks: Dict[str, asyncio.Task] = {}

# Speicher fuer kurzlebige WebSocket-Tickets (CWE-598/F-002)
active_tickets: Dict[str, float] = {}
