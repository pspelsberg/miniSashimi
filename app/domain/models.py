"""Domain entities, SQLModel database tables, and OpenAI-compatible API schemas for MiniSashimi.

Includes:
- Task & WorkflowState: Core execution graph states and token accounting.
- SQLModel Tables: ApiKeysTable, SettingsTable, WorkflowsTable, SessionTable for SQLite persistence.
- OpenAI-compatible REST schemas: ChatMessage, ChatCompletionRequest, ChatCompletionResponse.
"""

from typing import List, Dict, Optional, Any, Union
from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlmodel import SQLModel, Field as SQLField
from datetime import datetime, timezone
import uuid
import json
import re


class TaskStatus(str, Enum):
    """Lifecycle states of sub-tasks and workflows."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class Task(BaseModel):
    """Represents a discrete step or node in a structured LLM workflow graph.
    
    Attributes:
        id: Unique UUID task identifier.
        title: Descriptive name of the execution step.
        assigned_model: Model/role configured for this task.
        payload: Input parameters and prompts passed to the model.
        status: Current execution status.
        output: Model response output upon completion.
        error: Error details if task execution failed.
        prompt_tokens: Number of prompt tokens consumed.
        completion_tokens: Number of completion tokens consumed.
        total_tokens: Total tokens consumed by this step.
        cache_read_tokens: Prompt cache tokens reused.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    assigned_model: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    output: Optional[str] = None
    error: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0


class WorkflowState(BaseModel):
    """Encapsulates the complete lifecycle and financial tracking of an orchestration workflow.
    
    Attributes:
        workflow_id: Unique UUID for the execution trace.
        client_prompt: Original incoming prompt instruction.
        tasks: Sequenced list of child tasks executed in the workflow.
        is_aborted: Flag indicating if workflow was aborted via Kill-Switch.
        accumulated_cost_usd: Estimated total financial cost in USD.
        status: Overall workflow status.
    """
    workflow_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_prompt: str
    tasks: List[Task] = Field(default_factory=list)
    is_aborted: bool = False
    accumulated_cost_usd: float = 0.0
    status: TaskStatus = TaskStatus.PENDING
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    error: Optional[str] = None


# SQLModel Database Tables for SQLite persistence of keys & runtime settings
class ApiKeysTable(SQLModel, table=True):
    """Database table storing AES-GCM encrypted provider API credentials."""
    __tablename__ = "api_keys"
    id: int = SQLField(default=None, primary_key=True)
    provider: str = SQLField(unique=True, index=True)  # openai, anthropic, mistral, openrouter, deepseek, etc.
    encrypted_key: str = SQLField(default="")
    updated_at: datetime = SQLField(default_factory=lambda: datetime.now(timezone.utc))


class SettingsTable(SQLModel, table=True):
    """Database table storing key-value pairs for routing and governance configuration."""
    __tablename__ = "settings"
    id: int = SQLField(default=None, primary_key=True)
    key: str = SQLField(unique=True, index=True)  # model_thinker, model_conductor, cost_limit, pii_masking_enabled, etc.
    value: str = SQLField(default="")
    updated_at: datetime = SQLField(default_factory=lambda: datetime.now(timezone.utc))


class WorkflowsTable(SQLModel, table=True):
    """Database table storing historical workflow runs and state snapshots as JSON."""
    __tablename__ = "workflows"
    workflow_id: str = SQLField(primary_key=True, index=True)
    state_json: str = SQLField(default="{}")
    updated_at: datetime = SQLField(default_factory=lambda: datetime.now(timezone.utc))


class SessionTable(SQLModel, table=True):
    """Server-side session metadata; only credential hashes are persisted."""
    __tablename__ = "sessions"
    session_id: str = SQLField(primary_key=True, index=True, max_length=128)
    token_hash: str = SQLField(max_length=64)
    csrf_hash: str = SQLField(max_length=64)
    expires_at: datetime = SQLField(index=True)


# Pydantic models for OpenAI-compatible API endpoints (/v1/chat/completions)
class ChatMessage(BaseModel):
    """Single turn message in an OpenAI-compatible chat conversation."""
    role: str = Field(min_length=1, max_length=32)
    content: Optional[Union[str, List[Any]]] = None
    name: Optional[str] = Field(default=None, max_length=128)
    tool_calls: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=32)

    @field_validator("content")
    @classmethod
    def validate_content_size(cls, value: Optional[Union[str, List[Any]]]):
        """Enforces a 100,000 character limit on message contents to prevent memory exhaustion."""
        if isinstance(value, str) and len(value) > 100_000:
            raise ValueError("message content exceeds the maximum length")
        if isinstance(value, list):
            if len(value) > 128:
                raise ValueError("message content parts exceed the maximum count")
            try:
                encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise ValueError("message content must be JSON serializable") from exc
            if len(encoded) > 100_000:
                raise ValueError("message content exceeds the maximum length")
        return value


class ChatCompletionRequest(BaseModel):
    """OpenAI standard request payload for chat completion endpoints."""
    model: str = Field(min_length=1, max_length=256)
    messages: List[ChatMessage] = Field(min_length=1, max_length=100)

    @field_validator("model")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        """Sanitizes model name to prevent log injection and delimiter bypasses."""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.:/-]+)?", value):
            raise ValueError("invalid model identifier")
        return value

    temperature: Optional[float] = Field(default=1.0, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(default=1.0, ge=0.0, le=1.0)
    n: Optional[int] = Field(default=1, ge=1, le=8)
    stream: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = None
    max_tokens: Optional[int] = Field(default=None, ge=1, le=8192)
    presence_penalty: Optional[float] = Field(default=0.0, ge=-2.0, le=2.0)
    frequency_penalty: Optional[float] = Field(default=0.0, ge=-2.0, le=2.0)
    user: Optional[str] = Field(default=None, max_length=256)
    tools: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=32)
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None

    @field_validator("tools")
    @classmethod
    def validate_tools_size(cls, value: Optional[List[Dict[str, Any]]]):
        """Guards against oversized tool payload submissions."""
        if value is not None:
            try:
                if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 256_000:
                    raise ValueError("tools payload exceeds the maximum length")
            except (TypeError, ValueError) as exc:
                if isinstance(exc, ValueError) and str(exc).startswith("tools payload"):
                    raise
                raise ValueError("tools must be JSON serializable") from exc
        return value

    @model_validator(mode="after")
    def validate_request_size(self):
        """Enforces a maximum payload byte size across all messages combined."""
        try:
            encoded = json.dumps(
                [message.model_dump(mode="json") for message in self.messages],
                ensure_ascii=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("messages must be JSON serializable") from exc
        if len(encoded) > 2_000_000:
            raise ValueError("messages payload exceeds the maximum length")
        return self


class ChatCompletionChoice(BaseModel):
    """Single completion choice in the OpenAI chat completion response."""
    index: int
    message: ChatMessage
    finish_reason: str = "stop"


class ChatCompletionUsage(BaseModel):
    """Token usage metrics reported in OpenAI response."""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    """Complete OpenAI-compatible response object for /v1/chat/completions."""
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(datetime.now(timezone.utc).timestamp()))
    model: str
    choices: List[ChatCompletionChoice]
    usage: ChatCompletionUsage
