"""Deterministic in-memory OpenCodeRuntime for tests and UI development.

Behaviour is scriptable per (provider_id, model_id) so tests can exercise failures,
slow generation (for Stop), and disconnected providers without a real OpenCode server.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from .models import (
    ErrorCode,
    ModelInfo,
    ModelPrice,
    ProviderInfo,
    ProvidersResponse,
    ToolCall,
    ToolCallStatus,
    Usage,
)
from .runtime import Completed, CompletionRequest, Failed, RuntimeEvent, TextDelta, ToolActivity

DEFAULT_PROVIDERS = (
    ProviderInfo(
        id="openai",
        name="OpenAI",
        connected=True,
        models=[
            ModelInfo(id="gpt-6-sol", name="GPT-6 Sol", context_limit=400_000),
            ModelInfo(id="gpt-6-mini", name="GPT-6 Mini", context_limit=200_000),
        ],
    ),
    ProviderInfo(
        id="openrouter",
        name="OpenRouter",
        connected=True,
        models=[
            ModelInfo(
                id="deepseek/deepseek-chat",
                name="DeepSeek Chat",
                price=ModelPrice(input_per_mtok=0.3, output_per_mtok=1.0),
                context_limit=128_000,
            ),
        ],
    ),
    ProviderInfo(
        id="anthropic",
        name="Anthropic",
        connected=False,
        models=[ModelInfo(id="claude-sonnet-5-5", name="Claude Sonnet 5.5")],
    ),
)


class FakeRuntime:
    """Echoing runtime. Reply text is ``[<model_id>] <first 80 chars of the user text>``."""

    def __init__(
        self,
        providers: tuple[ProviderInfo, ...] = DEFAULT_PROVIDERS,
        chunk_delay: float = 0.0,
    ) -> None:
        self.providers = list(providers)
        self.chunk_delay = chunk_delay
        self.requests: list[CompletionRequest] = []
        self.aborted: list[str] = []
        self.fail_models: dict[tuple[str, str], Failed] = {}
        self.stall_models: set[tuple[str, str]] = set()  # never finish; for Stop tests
        # Tool calls (tool, title) to report before replying, per model.
        self.tool_models: dict[tuple[str, str], list[tuple[str, str]]] = {}
        self.sessions: dict[str, str | None] = {}  # session id -> directory
        self.healthy = True
        self._ids = itertools.count(1)

    async def health(self) -> bool:
        return self.healthy

    async def list_providers(self, refresh: bool = False) -> ProvidersResponse:
        return ProvidersResponse(providers=self.providers, refreshed_at=datetime.now(UTC))

    async def create_session(self, title: str, directory: str | None = None) -> str:
        session_id = f"ses_fake_{next(self._ids)}"
        self.sessions[session_id] = directory
        return session_id

    def _model_available(self, provider_id: str, model_id: str) -> Failed | None:
        for provider in self.providers:
            if provider.id == provider_id:
                if not provider.connected:
                    return Failed(ErrorCode.PROVIDER_UNAVAILABLE, f"{provider_id} not connected")
                if any(m.id == model_id for m in provider.models):
                    return None
                return Failed(ErrorCode.MODEL_UNAVAILABLE, f"{model_id} not offered")
        return Failed(ErrorCode.PROVIDER_UNAVAILABLE, f"unknown provider {provider_id}")

    async def stream(self, request: CompletionRequest) -> AsyncIterator[RuntimeEvent]:
        self.requests.append(request)
        key = (request.provider_id, request.model_id)
        if (failure := self.fail_models.get(key)) or (failure := self._model_available(*key)):
            yield failure
            return
        text = f"[{request.model_id}] {request.user_text[:80]}"
        try:
            if key in self.stall_models:
                yield TextDelta("...")
                await asyncio.Event().wait()
            for n, (tool, title) in enumerate(self.tool_models.get(key, ()), start=1):
                call_id = f"{request.session_id}_call_{n}"
                yield ToolActivity(ToolCall(id=call_id, tool=tool, title=title,
                                            status=ToolCallStatus.RUNNING))
                yield ToolActivity(ToolCall(id=call_id, tool=tool, title=title,
                                            status=ToolCallStatus.COMPLETED))
            words = text.split(" ")
            for i, word in enumerate(words):
                if self.chunk_delay:
                    await asyncio.sleep(self.chunk_delay)
                yield TextDelta(word if i == 0 else f" {word}")
            yield Completed(
                text, Usage(input_tokens=len(request.user_text) // 4, output_tokens=len(words))
            )
        except asyncio.CancelledError:
            await self.abort(request.session_id)
            raise
        except GeneratorExit:
            self.aborted.append(request.session_id)
            raise

    async def abort(self, session_id: str) -> None:
        self.aborted.append(session_id)

    async def aclose(self) -> None:
        return None
