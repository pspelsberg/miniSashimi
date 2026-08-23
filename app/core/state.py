"""In-memory global runtime state management for MiniSashimi.

Tracks active WebSocket client connections for real-time frontend dashboard telemetry,
running asyncio background workflow tasks (for Kill-Switch aborts), and short-lived
single-use WebSocket authentication tickets.
"""

from typing import List, Dict
import asyncio
from fastapi import WebSocket

# Active WebSocket connections (React dashboard clients)
ws_clients: List[WebSocket] = []
ws_clients_lock = asyncio.Lock()

# Registry of currently running workflow asyncio.Task instances
active_tasks: Dict[str, asyncio.Task] = [] if False else {}

# Short-lived single-use WebSocket ticket storage (ticket_uuid -> expiry_timestamp)
active_tickets: Dict[str, float] = {}
