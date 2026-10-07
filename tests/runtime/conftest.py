from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from backend.config import AppConfig
from backend.contracts.models import ToolAccess
from backend.contracts.runtime import CompletionRequest
from backend.runtime.opencode import HttpOpenCodeRuntime

from .fake_opencode import PASSWORD, USERNAME, FakeOpenCode, text_turn

PROVIDERS_PAYLOAD = {
    "all": [
        {
            "id": "zeta",
            "name": "Zeta Cloud",
            "models": {
                "z1": {
                    "id": "z1",
                    "name": "Z One",
                    "status": "active",
                    "cost": {"input": 0, "output": 0},
                    "limit": {"context": 1000, "output": 100},
                    "capabilities": {"output": {"text": True}},
                }
            },
        },
        {
            "id": "openai",
            "name": "OpenAI",
            "models": {
                "gpt-6-sol": {
                    "id": "gpt-6-sol",
                    "name": "GPT-6 Sol",
                    "status": "active",
                    "cost": {"input": 2.0, "output": 10.0, "cache": {"read": 0.1, "write": 0}},
                    "limit": {"context": 1050000, "input": 922000, "output": 128000},
                    "capabilities": {"output": {"text": True}},
                },
                "chatgpt-image-latest": {
                    "id": "chatgpt-image-latest",
                    "name": "Image",
                    "status": "active",
                    "cost": {"input": 0, "output": 0},
                    "limit": {"context": 0, "output": 0},
                    "capabilities": {"output": {"text": False}},
                },
                "old-model": {"id": "old-model", "name": "Old", "status": "deprecated"},
            },
        },
        {
            "id": "openrouter",
            "name": "OpenRouter",
            "models": {
                "deepseek/deepseek-chat": {
                    "id": "deepseek/deepseek-chat",
                    "name": "DeepSeek Chat",
                    "cost": {"input": 0.3, "output": 1.0},
                    "limit": {"context": 128000, "output": 8000},
                },
                "mystery": {"id": "mystery", "name": "Mystery"},
            },
        },
        {"id": "anthropic", "name": "Anthropic", "models": {}},
    ],
    "default": {},
    "connected": ["openai", "openrouter"],
}


@pytest.fixture
async def fake() -> AsyncIterator[FakeOpenCode]:
    server = FakeOpenCode(providers_payload=PROVIDERS_PAYLOAD)
    server.default_behavior = text_turn(["Hello", " there", "!"])
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


def make_config(url: str, password: str = PASSWORD) -> AppConfig:
    return AppConfig(
        data_dir=Path("."),
        opencode_url=url,
        opencode_username=USERNAME,
        opencode_password=password,
    )


@pytest.fixture
async def runtime(fake: FakeOpenCode) -> AsyncIterator[HttpOpenCodeRuntime]:
    rt = HttpOpenCodeRuntime(make_config(fake.url), reconnect_backoff=0.01)
    try:
        yield rt
    finally:
        await rt.aclose()


def request(
    session_id: str,
    *,
    provider: str = "openai",
    model: str = "gpt-6-sol",
    system: str = "You are a test persona.",
    text: str = "hi",
    max_tokens: int = 1024,
    directory: str | None = None,
    access: ToolAccess = ToolAccess.NONE,
) -> CompletionRequest:
    return CompletionRequest(
        session_id, provider, model, system, text, max_tokens, directory, access
    )
