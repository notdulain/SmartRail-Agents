"""Shared helpers: a running app on FakeRuntime and small API/SSE utilities."""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from backend.application import create_app
from backend.config import AppConfig
from backend.contracts.fake_runtime import DEFAULT_PROVIDERS, FakeRuntime
from backend.contracts.runtime import Completed

GPT = ("openai", "gpt-6-sol")
MINI = ("openai", "gpt-6-mini")
ROUTER = ("openrouter", "deepseek/deepseek-chat")


def fresh_runtime(**kwargs) -> FakeRuntime:
    """FakeRuntime with its own copy of the provider catalog (tests mutate it)."""
    return FakeRuntime(providers=copy.deepcopy(DEFAULT_PROVIDERS), **kwargs)


class GatedRuntime(FakeRuntime):
    """FakeRuntime whose turns wait for ``gate`` (set = open) so tests can act mid-run."""

    def __init__(self) -> None:
        super().__init__(providers=copy.deepcopy(DEFAULT_PROVIDERS))
        self.gate = asyncio.Event()
        self.gate.set()
        self.arrived = asyncio.Event()

    async def stream(self, request):
        self.arrived.set()
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            await self.abort(request.session_id)
            raise
        async for event in super().stream(request):
            yield event


class CostRuntime(FakeRuntime):
    """FakeRuntime that reports ``cost_usd`` on every completed turn."""

    def __init__(self, cost: float) -> None:
        super().__init__(providers=copy.deepcopy(DEFAULT_PROVIDERS))
        self.cost = cost

    async def stream(self, request):
        async for event in super().stream(request):
            if isinstance(event, Completed):
                usage = event.usage.model_copy(update={"cost_usd": self.cost})
                event = Completed(event.text, usage)
            yield event


async def wait_for(predicate, limit: float = 10.0):
    """Await a sync predicate or coroutine function until truthy."""
    async with asyncio.timeout(limit):
        while True:
            value = predicate()
            if asyncio.iscoroutine(value):
                value = await value
            if value:
                return value
            await asyncio.sleep(0.005)


@dataclass
class Env:
    client: httpx.AsyncClient
    runtime: FakeRuntime
    app: object

    async def agent(self, name: str = "Agent", model: tuple[str, str] = GPT, persona: str = "p"):
        r = await self.client.post(
            "/api/agents",
            json={
                "name": name,
                "persona": persona,
                "provider_id": model[0],
                "model_id": model[1],
            },
        )
        assert r.status_code == 201, r.text
        return r.json()

    async def agents(self, count: int, model: tuple[str, str] = GPT, prefix: str = "A"):
        return [await self.agent(f"{prefix}{i:02d}", model) for i in range(count)]

    async def direct(self, agent_id: str) -> str:
        r = await self.client.post(
            "/api/conversations", json={"type": "direct", "participant_ids": [agent_id]}
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    async def group(self, agent_ids: list[str], topic: str = "Platform doors") -> str:
        r = await self.client.post(
            "/api/conversations",
            json={"type": "group", "participant_ids": agent_ids, "topic": topic},
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    async def set_coordinator(self, agent_id: str | None) -> None:
        r = await self.client.patch("/api/settings", json={"coordinator_agent_id": agent_id})
        assert r.status_code == 200, r.text

    async def send(self, conv_id: str, content: str = "hello") -> dict:
        r = await self.client.post(
            f"/api/conversations/{conv_id}/messages", json={"content": content}
        )
        assert r.status_code == 202, r.text
        return r.json()

    async def wait(self, run_id: str, limit: float = 10.0) -> dict:
        """Poll until the run leaves ``running``."""
        async with asyncio.timeout(limit):
            while True:
                run = (await self.client.get(f"/api/runs/{run_id}")).json()
                if run["status"] != "running":
                    return run
                await asyncio.sleep(0.005)

    @property
    def service(self):
        return self.app.state.smartrail.service

    async def run_to_end(self, conv_id: str, content: str = "hello") -> dict:
        sent = await self.send(conv_id, content)
        return await self.wait(sent["run_id"])

    async def messages(self, conv_id: str) -> list[dict]:
        r = await self.client.get(f"/api/conversations/{conv_id}")
        assert r.status_code == 200, r.text
        return r.json()["messages"]

    async def events(self, run_id: str, **kwargs) -> list[dict]:
        r = await self.client.get(f"/api/runs/{run_id}/events", **kwargs)
        assert r.status_code == 200, r.text
        return parse_sse(r.text)


def parse_sse(text: str) -> list[dict]:
    frames = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        frame: dict = {}
        for line in block.split("\n"):
            if line.startswith(":") or not line:
                continue
            key, _, value = line.partition(":")
            frame[key] = value.lstrip(" ")
        if "data" in frame:
            frame["json"] = json.loads(frame["data"])
            frames.append(frame)
    return frames


@asynccontextmanager
async def running_app(data_dir: Path, runtime: FakeRuntime | None = None) -> AsyncIterator[Env]:
    runtime = runtime or fresh_runtime()
    app = create_app(AppConfig(data_dir=data_dir), runtime)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Env(client=client, runtime=runtime, app=app)


@pytest.fixture
async def env(tmp_path) -> AsyncIterator[Env]:
    async with running_app(tmp_path / "data") as e:
        yield e
