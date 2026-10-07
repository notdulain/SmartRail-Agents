"""Runtime interface: the seam between the application layer and OpenCode.

``backend/application`` depends only on this Protocol. ``backend/runtime`` implements it
against a real OpenCode server. ``backend/contracts/fake_runtime.py`` implements it
deterministically for automated tests.

Owned by the coordinating development agent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from .models import ErrorCode, ImageAttachment, ProvidersResponse, ToolAccess, ToolCall, Usage


@dataclass(frozen=True)
class CompletionRequest:
    """One model turn. The runtime sends it to OpenCode as one prompt in ``session_id``.

    ``system`` is the agent persona plus discussion framing (per-request system text; the
    runtime uses a single neutral, tool-disabled OpenCode agent). ``user_text`` is the new
    text for this turn (for group turns, the transcript the participant has not yet seen).
    OpenCode session history supplies earlier context.

    ``directory`` is the agent's working directory (absolute) and must equal the directory the
    session was created with. ``tool_access`` decides which file tools the model may use in
    this turn; the runtime enforces it (``none`` means no tools at all) and never lets a tool
    reach outside ``directory``.
    """

    session_id: str
    provider_id: str
    model_id: str
    system: str
    user_text: str
    max_output_tokens: int
    directory: str | None = None
    tool_access: ToolAccess = ToolAccess.NONE
    images: tuple[ImageAttachment, ...] = ()


@dataclass(frozen=True)
class TextDelta:
    text: str
    type: Literal["delta"] = "delta"


@dataclass(frozen=True)
class ToolActivity:
    """A file-tool call started or changed state (same ``call.id`` = same call)."""

    call: ToolCall
    type: Literal["tool"] = "tool"


@dataclass(frozen=True)
class Completed:
    text: str  # full final text
    usage: Usage = field(default_factory=Usage)
    type: Literal["completed"] = "completed"


@dataclass(frozen=True)
class Failed:
    code: ErrorCode  # provider_unavailable / model_unavailable / rate_limited / runtime_error
    message: str
    type: Literal["failed"] = "failed"


RuntimeEvent = TextDelta | ToolActivity | Completed | Failed


class RuntimeUnavailableError(Exception):
    """OpenCode server unreachable or unhealthy."""


@runtime_checkable
class OpenCodeRuntime(Protocol):
    async def health(self) -> bool:
        """True when the OpenCode server answers its health check."""
        ...

    async def list_providers(self, refresh: bool = False) -> ProvidersResponse:
        """Provider/model catalog from OpenCode. ``refresh=True`` bypasses any cache."""
        ...

    async def create_session(self, title: str, directory: str | None = None) -> str:
        """Create an OpenCode session and return its ID.

        ``directory`` (absolute) scopes the session to an agent's working directory; ``None``
        uses the runtime's neutral directory. A session's directory never changes.
        """
        ...

    def stream(self, request: CompletionRequest) -> AsyncIterator[RuntimeEvent]:
        """Run one turn, yielding (TextDelta | ToolActivity)* then one Completed or Failed.

        Contract:
        - Never raises for provider/model/rate-limit problems; yields ``Failed`` instead.
        - If the consuming task is cancelled or the iterator is closed early, the runtime
          must abort the in-flight OpenCode generation for ``request.session_id``.
        - Only the file tools allowed by ``request.tool_access`` are available, confined to
          ``request.directory``; shell, web, MCP and delegation tools are always denied.
        - A turn that used tools must still end with reply text; an empty reply fails as before.
        """
        ...

    async def abort(self, session_id: str) -> None:
        """Abort any in-flight generation in the session. Idempotent."""
        ...

    async def aclose(self) -> None:
        """Release HTTP clients and background tasks."""
        ...
