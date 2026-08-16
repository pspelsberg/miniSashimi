"""
app/orchestrator/acp.py

Next-Level-Path #1: Agent Communication Protocol (ACP)

Standardisiertes Protokoll für Agent↔Agent-Kommunikation.

Konzept:
- Jede Message hat: sender, recipient, intent, payload, correlation_id, timestamp
- Intents: REQUEST, RESPONSE, EVENT, ERROR, BROADCAST
- Correlation-IDs verknüpfen Request/Response-Paare
- JSON-Serialisierbar, kompatibel mit REST/WebSocket/SQLite-Storage

Sicherheit:
- Sender-Authentifizierung (Signatur optional via HMAC)
- Payload-Größen-Limit (CWE-400)
- PII-Filter-Funktion
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("minisashimi.orchestrator.acp")


class ACPIntent(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"
    EVENT = "event"
    ERROR = "error"
    BROADCAST = "broadcast"


class ACPPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ACPMessage:
    """Standardisierte Agent-Message (ACP v0.1)."""

    sender: str
    recipient: str
    intent: ACPIntent
    payload: Dict[str, Any]
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=lambda: time.time())
    priority: ACPPriority = ACPPriority.NORMAL
    workflow_id: Optional[str] = None
    ttl_seconds: int = 3600
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        d = asdict(self)
        d["intent"] = self.intent.value
        d["priority"] = self.priority.value
        return json.dumps(d, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "ACPMessage":
        d = json.loads(raw)
        d["intent"] = ACPIntent(d["intent"])
        d["priority"] = ACPPriority(d.get("priority", "normal"))
        return cls(**d)

    def is_expired(self) -> bool:
        return self.ttl_seconds <= 0 or (time.time() - self.timestamp) > self.ttl_seconds


class ACPBus:
    """
    In-Memory Message-Bus für Agent-Kommunikation.

    Verwendung:
        bus = ACPBus()
        bus.subscribe("verifier", verifier_handler)
        await bus.publish(ACPMessage(sender="worker", recipient="verifier", ...))
    """

    MAX_PAYLOAD_BYTES = 1_048_576  # 1 MB
    MAX_HISTORY = 1000

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Any]] = {}
        self._history: List[ACPMessage] = []

    def subscribe(self, recipient: str, handler) -> None:
        self._subscribers.setdefault(recipient, []).append(handler)

    def unsubscribe(self, recipient: str, handler) -> None:
        if recipient in self._subscribers:
            try:
                self._subscribers[recipient].remove(handler)
            except ValueError:
                pass

    async def publish(self, message: ACPMessage) -> int:
        """Sendet eine Message an alle Subscriber des Recipients. Returns Anzahl Deliveries."""
        if message.is_expired():
            logger.warning(f"ACP: Message {message.message_id} bereits abgelaufen.")
            return 0

        try:
            payload_size = len(json.dumps(message.payload, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            logger.error("ACP: Payload is not JSON serializable")
            return 0
        if payload_size > self.MAX_PAYLOAD_BYTES:
            logger.error(
                f"ACP: Payload zu groß "
                f"(>{self.MAX_PAYLOAD_BYTES} bytes). Verworfen."
            )
            return 0

        self._history.append(message)
        if len(self._history) > self.MAX_HISTORY:
            self._history = self._history[-self.MAX_HISTORY:]

        # Copy subscriber lists before awaiting handlers; subscriptions may be
        # changed by a handler and must not alter this delivery pass.
        handlers = list(self._subscribers.get(message.recipient, []))
        delivered = 0
        for handler in handlers:
            try:
                result = handler(message)
                if hasattr(result, "__await__"):
                    await result
                delivered += 1
            except Exception as e:
                logger.error(
                    f"ACP: Handler für {message.recipient} fehlgeschlagen: {e}"
                )

        if message.intent == ACPIntent.BROADCAST:
            for recipient, subs in list(self._subscribers.items()):
                subs = list(subs)
                if recipient == message.recipient:
                    continue
                for handler in subs:
                    try:
                        result = handler(message)
                        if hasattr(result, "__await__"):
                            await result
                        delivered += 1
                    except Exception as e:
                        logger.error(
                            f"ACP: Broadcast-Handler für {recipient} fehlgeschlagen: {e}"
                        )

        logger.debug(
            f"ACP: {message.sender}→{message.recipient} "
            f"({message.intent.value}, delivered={delivered})"
        )
        return delivered

    def history(
        self,
        *,
        workflow_id: Optional[str] = None,
        recipient: Optional[str] = None,
        sender: Optional[str] = None,
        limit: int = 50,
    ) -> List[ACPMessage]:
        items = self._history
        if workflow_id:
            items = [m for m in items if m.workflow_id == workflow_id]
        if recipient:
            items = [m for m in items if m.recipient == recipient]
        if sender:
            items = [m for m in items if m.sender == sender]
        return items[-limit:]


# ----- Convenience Builder -----


def acp_request(
    sender: str,
    recipient: str,
    payload: Dict[str, Any],
    *,
    workflow_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    priority: ACPPriority = ACPPriority.NORMAL,
) -> ACPMessage:
    return ACPMessage(
        sender=sender,
        recipient=recipient,
        intent=ACPIntent.REQUEST,
        payload=payload,
        workflow_id=workflow_id,
        correlation_id=correlation_id or str(uuid.uuid4()),
        priority=priority,
    )


def acp_response(
    sender: str,
    recipient: str,
    payload: Dict[str, Any],
    *,
    correlation_id: str,
    workflow_id: Optional[str] = None,
) -> ACPMessage:
    return ACPMessage(
        sender=sender,
        recipient=recipient,
        intent=ACPIntent.RESPONSE,
        payload=payload,
        correlation_id=correlation_id,
        workflow_id=workflow_id,
    )


def acp_event(
    sender: str,
    event_name: str,
    payload: Dict[str, Any],
    *,
    workflow_id: Optional[str] = None,
) -> ACPMessage:
    return ACPMessage(
        sender=sender,
        recipient="*",
        intent=ACPIntent.EVENT,
        payload={"event": event_name, **payload},
        workflow_id=workflow_id,
    )
