from typing import List, Dict, Optional, Any, Union
from enum import Enum
from pydantic import BaseModel, Field, field_validator
from sqlmodel import SQLModel, Field as SQLField
from datetime import datetime, timezone
import uuid

class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"

class Task(BaseModel):
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

# SQLModel-Datenbank-Modelle für Persistenz von Keys & Settings
class ApiKeysTable(SQLModel, table=True):
    __tablename__ = "api_keys"
    id: int = SQLField(default=None, primary_key=True)
    provider: str = SQLField(unique=True, index=True)  # openai, anthropic, mistral, openrouter, deepseek, etc.
    encrypted_key: str = SQLField(default="")
    updated_at: datetime = SQLField(default_factory=lambda: datetime.now(timezone.utc))

class SettingsTable(SQLModel, table=True):
    __tablename__ = "settings"
    id: int = SQLField(default=None, primary_key=True)
    key: str = SQLField(unique=True, index=True) # model_thinker, model_conductor, cost_limit, pii_masking_enabled, etc.
    value: str = SQLField(default="")
    updated_at: datetime = SQLField(default_factory=lambda: datetime.now(timezone.utc))

class WorkflowsTable(SQLModel, table=True):
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

# Pydantic-Modelle für API-Requests/Responses (OpenAI-Kompatibilität)
class ChatMessage(BaseModel):
    role: str = Field(min_length=1, max_length=32)
    content: Optional[Union[str, List[Any]]] = None
    name: Optional[str] = Field(default=None, max_length=128)
    tool_calls: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=32)

    @field_validator("content")
    @classmethod
    def validate_content_size(cls, value: Optional[Union[str, List[Any]]]):
        if isinstance(value, str) and len(value) > 100_000:
            raise ValueError("message content exceeds the maximum length")
        if isinstance(value, list) and len(value) > 128:
            raise ValueError("message content parts exceed the maximum count")
        return value


class ChatCompletionRequest(BaseModel):
    model: str = Field(min_length=1, max_length=256)
    messages: List[ChatMessage] = Field(min_length=1, max_length=100)
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

class ChatCompletionChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str = "stop"

class ChatCompletionUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class ChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(datetime.now(timezone.utc).timestamp()))
    model: str
    choices: List[ChatCompletionChoice]
    usage: ChatCompletionUsage
