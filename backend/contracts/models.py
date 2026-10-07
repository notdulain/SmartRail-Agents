"""Shared API data contracts.

These Pydantic models are the single source of truth for the HTTP/SSE interface.
The frontend TypeScript types are generated from the OpenAPI schema built from them
(see ``backend/contracts/api_spec.py`` and ``scripts/export_openapi.py``).

Owned by the coordinating development agent. Feature workers must not edit this file;
request changes from the coordinator.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


# --------------------------------------------------------------------------- errors


class ErrorCode(StrEnum):
    NOT_FOUND = "not_found"
    VALIDATION_ERROR = "validation_error"
    PROVIDER_UNAVAILABLE = "provider_unavailable"  # provider disconnected / OpenCode unreachable
    MODEL_UNAVAILABLE = "model_unavailable"  # model no longer offered by its provider
    RATE_LIMITED = "rate_limited"
    BUDGET_EXHAUSTED = "budget_exhausted"
    RUN_ACTIVE = "run_active"  # another run is already active
    RUNTIME_ERROR = "runtime_error"


class ErrorResponse(ApiModel):
    code: ErrorCode
    message: str
    # Set for model_unavailable / provider_unavailable so the UI can offer "Edit agent".
    agent_id: str | None = None


# --------------------------------------------------------------------------- agents

NAME_MAX = 80
PERSONA_MAX = 8000
PATH_MAX = 1024


class ToolAccess(StrEnum):
    """What an agent may do inside its working directory (never outside it).

    ``read_only``: read, list, glob and grep files. ``read_write``: also create and edit files.
    No level grants shell, web, MCP or delegation tools.
    """

    NONE = "none"
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class AgentCreate(ApiModel):
    name: str = Field(min_length=1, max_length=NAME_MAX)
    persona: str = Field(max_length=PERSONA_MAX)
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    # Absolute path of an existing directory on this machine. Required unless
    # ``tool_access`` is ``none``. The server normalizes it (resolved, absolute).
    working_directory: str | None = Field(default=None, min_length=1, max_length=PATH_MAX)
    tool_access: ToolAccess = ToolAccess.NONE

    @model_validator(mode="after")
    def _check_tools(self) -> AgentCreate:
        if self.tool_access is not ToolAccess.NONE and not self.working_directory:
            raise ValueError("file tools need a working directory")
        return self


class AgentUpdate(ApiModel):
    """PATCH body. Omitted fields are unchanged. ``archived`` archives/unarchives."""

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    persona: str | None = Field(default=None, max_length=PERSONA_MAX)
    provider_id: str | None = Field(default=None, min_length=1)
    model_id: str | None = Field(default=None, min_length=1)
    archived: bool | None = None
    # Send "" to clear the working directory (only valid together with tool_access "none",
    # or when the agent's tool access is already "none").
    working_directory: str | None = Field(default=None, max_length=PATH_MAX)
    tool_access: ToolAccess | None = None


class Agent(ApiModel):
    id: str
    name: str
    persona: str
    provider_id: str
    model_id: str
    working_directory: str | None = None
    tool_access: ToolAccess = ToolAccess.NONE
    # Incremented on every edit of name/persona/provider/model/working_directory/tool_access
    # (not on archive).
    revision: int = Field(ge=1)
    archived: bool = False
    created_at: datetime
    updated_at: datetime


class AgentSnapshot(ApiModel):
    """Immutable copy of an agent's configuration at the moment a run started."""

    agent_id: str
    name: str
    persona: str
    provider_id: str
    model_id: str
    revision: int = Field(ge=1)
    working_directory: str | None = None
    tool_access: ToolAccess = ToolAccess.NONE


# --------------------------------------------------------------------------- files


class FileRef(ApiModel):
    """A file inside an agent's working directory, attached to a message with ``@``.

    ``path`` is relative to that agent's working directory and uses ``/`` separators.
    """

    agent_id: str
    path: str = Field(min_length=1, max_length=PATH_MAX)


class FileEntry(ApiModel):
    path: str  # relative to the working directory, ``/`` separators
    is_dir: bool = False
    size: int | None = None  # bytes, files only


class FileSearchResponse(ApiModel):
    """``GET /api/agents/{id}/files?q=``: matches inside the agent's working directory."""

    root: str  # the agent's working directory (absolute)
    files: list[FileEntry]
    truncated: bool = False  # more matches exist than were returned


class DirectoryEntry(ApiModel):
    name: str
    path: str  # absolute


class DirectoryListing(ApiModel):
    """``GET /api/fs/directories?path=``: sub-directories, for the working-directory picker."""

    path: str  # absolute, normalized; the directory listed
    parent: str | None = None  # null at a filesystem root
    entries: list[DirectoryEntry]  # sub-directories only, sorted by name
    home: str  # the user's home directory
    roots: list[str]  # filesystem roots (drive letters on Windows, "/" elsewhere)


# --------------------------------------------------------------------------- providers


class ModelPrice(ApiModel):
    """USD per one million tokens, when OpenCode supplies it."""

    input_per_mtok: float | None = None
    output_per_mtok: float | None = None


class ModelInfo(ApiModel):
    id: str
    name: str
    price: ModelPrice | None = None
    context_limit: int | None = None


class ProviderInfo(ApiModel):
    id: str
    name: str
    connected: bool
    models: list[ModelInfo]


class ProvidersResponse(ApiModel):
    providers: list[ProviderInfo]
    refreshed_at: datetime


# --------------------------------------------------------------------------- conversations


class ConversationType(StrEnum):
    DIRECT = "direct"
    GROUP = "group"


class ConversationCreate(ApiModel):
    type: ConversationType
    participant_ids: list[str] = Field(min_length=1)
    title: str | None = Field(default=None, max_length=200)
    # Required for group discussions; ignored for direct chats.
    topic: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check_shape(self) -> ConversationCreate:
        if len(set(self.participant_ids)) != len(self.participant_ids):
            raise ValueError("participant_ids must be unique")
        if self.type is ConversationType.DIRECT and len(self.participant_ids) != 1:
            raise ValueError("a direct conversation has exactly one participant")
        if self.type is ConversationType.GROUP:
            if len(self.participant_ids) < 2:
                raise ValueError("a group discussion needs at least two participants")
            if not (self.topic and self.topic.strip()):
                raise ValueError("a group discussion needs a topic")
        return self


class Conversation(ApiModel):
    id: str
    type: ConversationType
    title: str
    topic: str | None = None
    participant_ids: list[str]
    created_at: datetime
    updated_at: datetime


class MessageRole(StrEnum):
    USER = "user"
    AGENT = "agent"


class ToolCallStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


class ToolCall(ApiModel):
    """One file-tool call an agent made while producing a message (shown, never re-run)."""

    id: str
    tool: str  # OpenCode tool name: read, list, glob, grep, edit, write, patch, ...
    title: str  # short human summary, e.g. the file path or search pattern
    status: ToolCallStatus
    error: str | None = None


class MessageStatus(StrEnum):
    STREAMING = "streaming"
    COMPLETE = "complete"
    CANCELLED = "cancelled"  # stopped by the user; content is whatever arrived
    FAILED = "failed"  # see Message.error


class Message(ApiModel):
    id: str
    conversation_id: str
    run_id: str | None = None
    role: MessageRole
    # Speaker label as it was when the message was written (never rewritten on rename).
    speaker_name: str
    # Agent configuration snapshot at write time; null for user messages.
    agent: AgentSnapshot | None = None
    # Provider/model that actually produced the text; null for user messages.
    provider_id: str | None = None
    model_id: str | None = None
    content: str
    status: MessageStatus
    error: str | None = None
    error_code: ErrorCode | None = None
    reply_to_id: str | None = None
    # User messages: files attached with ``@`` (their contents were sent with the message).
    attachments: list[FileRef] = Field(default_factory=list)
    # Agent messages: file-tool calls made while producing the message, in call order.
    tool_calls: list[ToolCall] = Field(default_factory=list)
    # Group discussions: 0 = coordinator agenda, 1 = first round, 2 = peer-response round,
    # 3 = coordinator summary. Null for direct chats and user messages.
    stage: int | None = Field(default=None, ge=0, le=3)
    created_at: datetime


class ConversationDetail(ApiModel):
    conversation: Conversation
    messages: list[Message]
    active_run_id: str | None = None


# --------------------------------------------------------------------------- runs


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    PAUSED = "paused"  # budget or coordinator allowance exhausted


class Usage(ApiModel):
    input_tokens: int = 0
    output_tokens: int = 0
    # Only set for OpenRouter-billed calls; subscription usage is not priced.
    cost_usd: float | None = None


class Run(ApiModel):
    id: str
    conversation_id: str
    status: RunStatus
    participants: list[AgentSnapshot]
    coordinator: AgentSnapshot | None = None
    usage: Usage = Field(default_factory=Usage)
    error: str | None = None
    error_code: ErrorCode | None = None
    created_at: datetime
    finished_at: datetime | None = None


MAX_ATTACHMENTS = 20


class SendMessageRequest(ApiModel):
    content: str = Field(min_length=1, max_length=20000)
    # Each ``agent_id`` must be a participant of the conversation with a working directory.
    attachments: list[FileRef] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)


class SendMessageResponse(ApiModel):
    run_id: str
    user_message_id: str


# --------------------------------------------------------------------------- SSE events
#
# ``GET /api/runs/{id}/events`` is a Server-Sent Events stream. Each SSE frame is:
#     id: <seq>
#     event: <type>
#     data: <JSON of the event model>
# ``seq`` starts at 1 and increases by one per event within a run. Events are persisted
# per run; a client reconnecting with ``Last-Event-ID`` (or ``?after=<seq>``) receives
# only events with a greater ``seq``. A reconnect never triggers new model requests.
# The stream ends after a terminal event (run.completed / run.failed / run.cancelled /
# run.paused). Connecting to a finished run replays its events then closes.


class _EventBase(ApiModel):
    seq: int = Field(ge=1)
    run_id: str


class RunStartedEvent(_EventBase):
    type: Literal["run.started"] = "run.started"
    run: Run


class MessageStartedEvent(_EventBase):
    type: Literal["message.started"] = "message.started"
    message: Message  # status=streaming, content=""


class MessageDeltaEvent(_EventBase):
    type: Literal["message.delta"] = "message.delta"
    message_id: str
    delta: str


class MessageToolEvent(_EventBase):
    """A tool call started or changed state. Upsert into ``Message.tool_calls`` by ``id``."""

    type: Literal["message.tool"] = "message.tool"
    message_id: str
    tool_call: ToolCall


class MessageCompletedEvent(_EventBase):
    type: Literal["message.completed"] = "message.completed"
    message: Message  # final content; status complete / cancelled / failed


class RunCompletedEvent(_EventBase):
    type: Literal["run.completed"] = "run.completed"
    run: Run


class RunFailedEvent(_EventBase):
    type: Literal["run.failed"] = "run.failed"
    run: Run


class RunCancelledEvent(_EventBase):
    type: Literal["run.cancelled"] = "run.cancelled"
    run: Run


class RunPausedEvent(_EventBase):
    type: Literal["run.paused"] = "run.paused"
    run: Run
    reason: str


RunEvent = Annotated[
    RunStartedEvent
    | MessageStartedEvent
    | MessageDeltaEvent
    | MessageToolEvent
    | MessageCompletedEvent
    | RunCompletedEvent
    | RunFailedEvent
    | RunCancelledEvent
    | RunPausedEvent,
    Field(discriminator="type"),
]

TERMINAL_EVENT_TYPES = frozenset({"run.completed", "run.failed", "run.cancelled", "run.paused"})


# --------------------------------------------------------------------------- settings

DEFAULT_OPENROUTER_BUDGET_USD = 5.0
DEFAULT_PARTICIPANT_MAX_TOKENS = 1024
DEFAULT_COORDINATOR_MAX_TOKENS = 2048


class Settings(ApiModel):
    project_brief: str = ""
    coordinator_agent_id: str | None = None
    openrouter_monthly_budget_usd: float = Field(default=DEFAULT_OPENROUTER_BUDGET_USD, ge=0)
    openrouter_spent_usd: float = Field(default=0.0, ge=0)  # current month; read-only via API
    participant_max_tokens: int = Field(default=DEFAULT_PARTICIPANT_MAX_TOKENS, ge=1)
    coordinator_max_tokens: int = Field(default=DEFAULT_COORDINATOR_MAX_TOKENS, ge=1)


class SettingsUpdate(ApiModel):
    project_brief: str | None = None
    coordinator_agent_id: str | None = None
    openrouter_monthly_budget_usd: float | None = Field(default=None, ge=0)
    participant_max_tokens: int | None = Field(default=None, ge=1)
    coordinator_max_tokens: int | None = Field(default=None, ge=1)


class HealthResponse(ApiModel):
    status: Literal["ok"] = "ok"
    opencode_healthy: bool
    version: str
