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

from .models import ErrorCode, ProvidersResponse, Usage


@dataclass(frozen=True)
class CompletionRequest:
    """One model turn. The runtime sends it to OpenCode as one prompt in ``session_id``.

    ``system`` is the agent persona plus discussion framing (per-request system text; the
    runtime uses a single neutral, tool-disabled OpenCode agent). ``user_text`` is the new
    text for this turn (for group turns, the transcript the participant has not yet seen).
    OpenCode session history supplies earlier context.
    """

    session_id: str
    provider_id: str
    model_id: str
    system: str
    user_text: str
    max_output_tokens: int


@dataclass(frozen=True)
class TextDelta:
    text: str
    type: Literal["delta"] = "delta"


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


RuntimeEvent = TextDelta | Completed | Failed


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

    async def create_session(self, title: str) -> str:
        """Create an OpenCode session and return its ID."""
        ...

    def stream(self, request: CompletionRequest) -> AsyncIterator[RuntimeEvent]:
        """Run one turn, yielding TextDelta* then exactly one Completed or Failed.

        Contract:
        - Never raises for provider/model/rate-limit problems; yields ``Failed`` instead.
        - If the consuming task is cancelled or the iterator is closed early, the runtime
          must abort the in-flight OpenCode generation for ``request.session_id``.
        - Tools are disabled; the model only produces conversational text.
        """
        ...

    async def abort(self, session_id: str) -> None:
        """Abort any in-flight generation in the session. Idempotent."""
        ...

    async def aclose(self) -> None:
        """Release HTTP clients and background tasks."""
        ...
