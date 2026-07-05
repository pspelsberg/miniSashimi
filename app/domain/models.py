from typing import List, Dict, Optional, Any, Union
from enum import Enum
from pydantic import BaseModel, Field
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

# Pydantic-Modelle für API-Requests/Responses (OpenAI-Kompatibilität)
class ChatMessage(BaseModel):
    role: str
    content: Optional[Union[str, List[Any]]] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    stream: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    user: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None
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
