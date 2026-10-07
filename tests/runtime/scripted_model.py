"""A scripted OpenAI-compatible chat model for live tests (no real provider, no network).

A real OpenCode is configured with a custom provider pointing here, so it executes real tool
calls (and enforces its permissions) on calls this server scripts. The prompt text carries the
script: ``CALLS: <json>``, a list of steps, each a list of ``{"name", "arguments"}`` tool calls.
Step ``n`` is answered once ``n`` tool rounds happened since the last user message; afterwards
the reply is plain text. Each request's offered tool names are recorded in ``offered``.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Iterator
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

PROVIDER_ID = "smartrail-scripted"
MODEL_ID = "scripted"
REPLY = "Scripted reply."


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def _chunk(delta: dict[str, Any], finish: str | None = None) -> str:
    body = {
        "id": "chatcmpl-scripted",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(body)}\n\n"


class ScriptedModel:
    def __init__(self, port: int = 0) -> None:
        self.offered: list[list[str]] = []
        self._port = port
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def provider_config(self) -> dict[str, Any]:
        """The ``provider`` entry to add to OpenCode's config."""
        return {
            PROVIDER_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Scripted test model",
                "options": {"baseURL": f"{self.url}/v1", "apiKey": "unused"},
                "models": {
                    MODEL_ID: {
                        "name": "Scripted",
                        "tool_call": True,
                        "limit": {"context": 100000, "output": 4000},
                    }
                },
            }
        }

    async def _completions(self, request: Request) -> Response:
        body = await request.json()
        messages = body.get("messages", [])
        tools = sorted(t["function"]["name"] for t in body.get("tools") or [])
        self.offered.append(tools)
        last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=0)
        script: list[list[dict[str, Any]]] = []
        match = re.search(r"CALLS:\s*(\[.*\])", _text(messages[last_user].get("content")), re.S)
        if match:
            script = json.loads(match.group(1))
        rounds = sum(
            1 for m in messages[last_user:] if m.get("role") == "assistant" and m.get("tool_calls")
        )

        def stream() -> Iterator[str]:
            yield _chunk({"role": "assistant", "content": ""})
            if rounds < len(script) and tools:
                for i, call in enumerate(script[rounds]):
                    function = {"name": call["name"], "arguments": json.dumps(call["arguments"])}
                    tool_call = {"index": i, "id": f"call_{rounds}_{i}", "type": "function"}
                    yield _chunk({"tool_calls": [{**tool_call, "function": function}]})
                yield _chunk({}, "tool_calls")
            else:
                yield _chunk({"content": REPLY})
                yield _chunk({}, "stop")
            usage = {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
            yield f"data: {json.dumps({'id': 'x', 'choices': [], 'usage': usage})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    async def _models(self, request: Request) -> Response:
        return JSONResponse({"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]})

    def start(self) -> None:
        app = Starlette(
            routes=[
                Route("/v1/chat/completions", self._completions, methods=["POST"]),
                Route("/v1/models", self._models),
            ]
        )
        config = uvicorn.Config(app, host="127.0.0.1", port=self._port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("scripted model did not start")
            time.sleep(0.02)
        port = self._server.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
