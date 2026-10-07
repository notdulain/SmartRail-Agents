"""A small fake OpenCode HTTP server that replays recorded-shape events (verified vs 1.18.33).

Served by uvicorn on a random loopback port so SSE streams incrementally, like the real thing.
Tests script behaviour per ``(provider_id, model_id)`` through ``FakeOpenCode.behaviors`` and
inspect ``FakeOpenCode.calls`` afterwards.

Like real OpenCode, sessions created with ``?directory=D`` belong to the instance of ``D``: their
events are only delivered to ``GET /event?directory=D`` subscribers (sessions without a
directory belong to the server's own cwd, i.e. subscribers without the parameter).
``PATCH /session/{id}`` *appends* permission rules (as OpenCode 1.18.33 does); ``GET /path``
reports the worktree configured in ``FakeOpenCode.worktrees`` (default ``/``: not in git).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import itertools
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

USERNAME = "opencode"
PASSWORD = "s3cret-test-password"
NEUTRAL_DIR = "/srv/neutral"  # the fake server's own cwd

_ids = itertools.count(1)


def _id(prefix: str) -> str:
    return f"{prefix}_{next(_ids):08d}"


# ----------------------------------------------------------------------------- event shapes


def event(type_: str, **props: Any) -> dict[str, Any]:
    return {"id": _id("evt"), "type": type_, "properties": props}


def user_message(sid: str, mid: str) -> dict[str, Any]:
    info = {
        "id": mid,
        "role": "user",
        "sessionID": sid,
        "agent": "smartrail",
        "time": {"created": 1},
    }
    return event("message.updated", sessionID=sid, info=info)


def assistant_info(
    sid: str,
    mid: str,
    parent: str,
    *,
    tokens: tuple[int, int, int] | None = None,
    cost: float | None = None,
    error: dict[str, Any] | None = None,
    summary: bool = False,
    completed: bool = False,
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "id": mid,
        "parentID": parent,
        "role": "assistant",
        "mode": "smartrail",
        "agent": "smartrail",
        "sessionID": sid,
        "time": {"created": 2, **({"completed": 3} if completed else {})},
    }
    if tokens is not None:
        info["tokens"] = {
            "input": tokens[0],
            "output": tokens[1],
            "reasoning": tokens[2],
            "cache": {"read": 0, "write": 0},
        }
    if cost is not None:
        info["cost"] = cost
    if error is not None:
        info["error"] = error
    if summary:
        info["summary"] = True
    return info


def message_updated(sid: str, info: dict[str, Any]) -> dict[str, Any]:
    return event("message.updated", sessionID=sid, info=info)


def part_updated(sid: str, mid: str, pid: str, type_: str, text: str = "") -> dict[str, Any]:
    part = {"id": pid, "messageID": mid, "sessionID": sid, "type": type_}
    if type_ in ("text", "reasoning"):
        part["text"] = text
    return event("message.part.updated", sessionID=sid, part=part, time=1)


def delta(sid: str, mid: str, pid: str, text: str) -> dict[str, Any]:
    return event(
        "message.part.delta", sessionID=sid, messageID=mid, partID=pid, field="text", delta=text
    )


def status(sid: str, type_: str, **extra: Any) -> dict[str, Any]:
    return event("session.status", sessionID=sid, status={"type": type_, **extra})


def idle(sid: str) -> dict[str, Any]:
    return event("session.idle", sessionID=sid)


def session_error(sid: str, error: dict[str, Any]) -> dict[str, Any]:
    return event("session.error", sessionID=sid, error=error)


def api_error(status_code: int, message: str, retryable: bool = False) -> dict[str, Any]:
    return {
        "name": "APIError",
        "data": {
            "message": message,
            "statusCode": status_code,
            "isRetryable": retryable,
            "responseBody": json.dumps({"error": {"message": message}}),
        },
    }


# ----------------------------------------------------------------------------- the server


@dataclass
class Call:
    method: str
    path: str
    body: Any = None
    authorization: str | None = None
    params: dict[str, str] = field(default_factory=dict)

    @property
    def directory(self) -> str | None:
        return self.params.get("directory")


@dataclass
class TurnContext:
    """Handed to a behavior: lets it publish events for one prompt."""

    fake: FakeOpenCode
    session_id: str
    user_id: str
    assistant_id: str
    provider_id: str
    model_id: str
    system: str
    text: str

    async def publish(self, *events: dict[str, Any], delay: float = 0.0) -> None:
        for item in events:
            if delay:
                await asyncio.sleep(delay)
            self.fake.publish(item)

    def remember(self, info: dict[str, Any], parts: list[dict[str, Any]]) -> None:
        stored = self.fake.messages.setdefault(self.session_id, [])
        user_info = {"id": self.user_id, "role": "user", "sessionID": self.session_id}
        stored.append({"info": user_info, "parts": []})
        stored.append({"info": info, "parts": parts})


Behavior = Callable[[TurnContext], Awaitable[None]]


def text_turn(
    chunks: list[str],
    *,
    tokens: tuple[int, int, int] = (21, 6, 0),
    cost: float | None = 0.000033,
    delay: float = 0.0,
    reasoning: list[str] | None = None,
    skip_deltas: bool = False,
) -> Behavior:
    """A normal successful turn: reasoning (optional), text deltas, step-finish, idle."""

    async def run(ctx: TurnContext) -> None:
        sid, mid = ctx.session_id, ctx.assistant_id
        info = assistant_info(sid, mid, ctx.user_id)
        await ctx.publish(
            user_message(sid, ctx.user_id),
            status(sid, "busy"),
            message_updated(sid, info),
            part_updated(sid, mid, _id("prt"), "step-start"),
        )
        if reasoning:
            rid = _id("prt")
            await ctx.publish(part_updated(sid, mid, rid, "reasoning"))
            for piece in reasoning:
                await ctx.publish(delta(sid, mid, rid, piece), delay=delay)
        pid = _id("prt")
        await ctx.publish(part_updated(sid, mid, pid, "text"))
        for piece in chunks:
            if not skip_deltas:
                await ctx.publish(delta(sid, mid, pid, piece), delay=delay)
            elif delay:
                await asyncio.sleep(delay)
        full = "".join(chunks)
        done = assistant_info(sid, mid, ctx.user_id, tokens=tokens, cost=cost, completed=True)
        text_part = {"id": pid, "messageID": mid, "sessionID": sid, "type": "text", "text": full}
        ctx.remember(done, [text_part])
        await ctx.publish(
            part_updated(sid, mid, pid, "text", full),
            message_updated(sid, done),
            status(sid, "idle"),
            idle(sid),
        )

    return run


def error_turn(error: dict[str, Any], *, before_assistant: bool = False) -> Behavior:
    async def run(ctx: TurnContext) -> None:
        sid, mid = ctx.session_id, ctx.assistant_id
        events = [user_message(sid, ctx.user_id), status(sid, "busy")]
        if not before_assistant:
            events.append(message_updated(sid, assistant_info(sid, mid, ctx.user_id)))
        events += [session_error(sid, error), status(sid, "idle"), idle(sid)]
        await ctx.publish(*events)

    return run


def retry_turn(message: str, attempts: int = 1, **extra: Any) -> Behavior:
    """OpenCode retries 429/5xx itself, reporting ``retry`` statuses and never going idle."""

    async def run(ctx: TurnContext) -> None:
        sid = ctx.session_id
        await ctx.publish(user_message(sid, ctx.user_id), status(sid, "busy"))
        for attempt in range(1, attempts + 1):
            await ctx.publish(
                status(sid, "retry", attempt=attempt, message=message, next=0, **extra)
            )
            await asyncio.sleep(0.01)
        await asyncio.Event().wait()

    return run


def stall_turn(chunks: list[str], delay: float = 0.01) -> Behavior:
    """Streams ``chunks`` and then never finishes (until aborted)."""

    async def run(ctx: TurnContext) -> None:
        sid, mid = ctx.session_id, ctx.assistant_id
        pid = _id("prt")
        await ctx.publish(
            user_message(sid, ctx.user_id),
            status(sid, "busy"),
            message_updated(sid, assistant_info(sid, mid, ctx.user_id)),
            part_updated(sid, mid, pid, "text"),
        )
        for piece in chunks:
            await ctx.publish(delta(sid, mid, pid, piece), delay=delay)
        await asyncio.Event().wait()

    return run


@dataclass
class FakeOpenCode:
    providers_payload: dict[str, Any] = field(default_factory=dict)
    behaviors: dict[tuple[str, str], Behavior] = field(default_factory=dict)
    default_behavior: Behavior | None = None
    require_auth: bool = True
    prompt_status: int = 204
    drop_event_streams_after: int | None = None  # close each /event stream after N events
    calls: list[Call] = field(default_factory=list)
    messages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    sessions: list[str] = field(default_factory=list)
    session_dirs: dict[str, str | None] = field(default_factory=dict)  # None = neutral
    permissions: dict[str, list[dict[str, str]]] = field(default_factory=dict)  # per session
    worktrees: dict[str, str] = field(default_factory=dict)  # directory -> git worktree
    patch_status: int = 200
    busy: set[str] = field(default_factory=set)
    url: str = ""
    # (directory, queue) per /event subscriber; directory None = no parameter (neutral)
    _subscribers: list[tuple[str | None, asyncio.Queue[dict[str, Any] | None]]] = field(
        default_factory=list
    )
    _turns: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    _server: uvicorn.Server | None = None
    _task: asyncio.Task[None] | None = None
    connect_order: list[str] = field(default_factory=list)

    # ---- helpers for tests
    def calls_to(self, method: str, suffix: str) -> list[Call]:
        return [c for c in self.calls if c.method == method and c.path.endswith(suffix)]

    @property
    def prompts(self) -> list[Call]:
        return self.calls_to("POST", "/prompt_async")

    @property
    def aborts(self) -> list[Call]:
        return self.calls_to("POST", "/abort")

    def add_session(self, sid: str, directory: str | None = None) -> None:
        """Pretend a session already exists (e.g. created before a runtime restart)."""
        self.sessions.append(sid)
        self.session_dirs[sid] = directory

    def publish(self, item: dict[str, Any]) -> None:
        """Deliver to the subscribers of the session's instance (unknown sessions: everyone)."""
        props = item.get("properties")
        sid = props.get("sessionID") if isinstance(props, dict) else None
        scoped = sid in self.session_dirs
        for directory, queue in list(self._subscribers):
            if not scoped or directory == self.session_dirs[sid]:
                queue.put_nowait(item)

    # ---- request handlers
    def _record(self, request: Request, body: Any = None) -> Call:
        call = Call(
            request.method,
            request.url.path,
            body,
            request.headers.get("authorization"),
            dict(request.query_params),
        )
        self.calls.append(call)
        return call

    def _unauthorized(self, call: Call) -> Response | None:
        if not self.require_auth:
            return None
        expected = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
        if call.authorization != expected:
            return Response(status_code=401)
        return None

    async def _health(self, request: Request) -> Response:
        if denied := self._unauthorized(self._record(request)):
            return denied
        return JSONResponse({"healthy": True, "version": "1.18.22"})

    async def _providers(self, request: Request) -> Response:
        if denied := self._unauthorized(self._record(request)):
            return denied
        return JSONResponse(self.providers_payload)

    def _session_info(self, sid: str) -> dict[str, Any]:
        return {
            "id": sid,
            "directory": self.session_dirs.get(sid) or NEUTRAL_DIR,
            "projectID": "global",
            "title": "chat",
        }

    async def _create_session(self, request: Request) -> Response:
        body = await request.json()
        call = self._record(request, body)
        if denied := self._unauthorized(call):
            return denied
        sid = _id("ses")
        self.add_session(sid, call.directory)
        self.permissions[sid] = list(body.get("permission") or [])
        return JSONResponse({**self._session_info(sid), "title": body.get("title")})

    async def _patch_session(self, request: Request) -> Response:
        body = await request.json()
        if denied := self._unauthorized(self._record(request, body)):
            return denied
        sid = request.path_params["sid"]
        if self.patch_status != 200 or sid not in self.session_dirs:
            status_code = self.patch_status if self.patch_status != 200 else 404
            return JSONResponse({"name": "Error", "data": {"message": "nope"}}, status_code)
        self.permissions.setdefault(sid, []).extend(body.get("permission") or [])
        return JSONResponse({**self._session_info(sid), "permission": self.permissions[sid]})

    async def _path(self, request: Request) -> Response:
        call = self._record(request)
        if denied := self._unauthorized(call):
            return denied
        directory = call.directory or NEUTRAL_DIR
        return JSONResponse(
            {"directory": directory, "worktree": self.worktrees.get(directory, "/")}
        )

    async def _get_session(self, request: Request) -> Response:
        if denied := self._unauthorized(self._record(request)):
            return denied
        sid = request.path_params["sid"]
        if sid not in self.session_dirs:
            return JSONResponse({"name": "NotFoundError", "data": {"message": "nope"}}, 404)
        return JSONResponse(self._session_info(sid))

    async def _status(self, request: Request) -> Response:
        if denied := self._unauthorized(self._record(request)):
            return denied
        return JSONResponse({sid: {"type": "busy"} for sid in self.busy})

    async def _messages(self, request: Request) -> Response:
        if denied := self._unauthorized(self._record(request)):
            return denied
        return JSONResponse(self.messages.get(request.path_params["sid"], []))

    async def _message(self, request: Request) -> Response:
        if denied := self._unauthorized(self._record(request)):
            return denied
        for item in self.messages.get(request.path_params["sid"], []):
            if item["info"]["id"] == request.path_params["mid"]:
                return JSONResponse(item)
        return JSONResponse({"name": "NotFoundError", "data": {"message": "nope"}}, 404)

    async def _events(self, request: Request) -> Response:
        call = self._record(request)
        if denied := self._unauthorized(call):
            return denied
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        subscriber = (call.directory, queue)
        self._subscribers.append(subscriber)
        self.connect_order.append("event")

        async def stream() -> AsyncIterator[bytes]:
            sent = 0
            try:
                yield b'data: {"id":"evt_0","type":"server.connected","properties":{}}\n\n'
                while True:
                    item = await queue.get()
                    if item is None:
                        return
                    yield f"data: {json.dumps(item)}\n\n".encode()
                    sent += 1
                    if self.drop_event_streams_after and sent >= self.drop_event_streams_after:
                        return
            finally:
                with contextlib.suppress(ValueError):
                    self._subscribers.remove(subscriber)

        return StreamingResponse(stream(), media_type="text/event-stream")

    async def _prompt(self, request: Request) -> Response:
        body = await request.json()
        call = self._record(request, body)
        if denied := self._unauthorized(call):
            return denied
        self.connect_order.append("prompt")
        if self.prompt_status != 204:
            return JSONResponse(
                {"name": "NotFoundError", "data": {"message": "Session not found"}},
                self.prompt_status,
            )
        sid = request.path_params["sid"]
        model = body.get("model") or {}
        key = (model.get("providerID", ""), model.get("modelID", ""))
        behavior = self.behaviors.get(key) or self.default_behavior
        assert behavior is not None, f"no behavior scripted for {key}"
        text = "".join(p.get("text", "") for p in body.get("parts", []))
        ctx = TurnContext(
            self, sid, _id("msg"), _id("msg"), key[0], key[1], body.get("system", ""), text
        )
        self.busy.add(sid)

        async def run() -> None:
            try:
                await behavior(ctx)
            finally:
                self.busy.discard(sid)

        self._turns[sid] = asyncio.create_task(run())
        return Response(status_code=204)

    async def _abort(self, request: Request) -> Response:
        if denied := self._unauthorized(self._record(request)):
            return denied
        sid = request.path_params["sid"]
        turn = self._turns.pop(sid, None)
        if turn is not None and not turn.done():
            turn.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await turn
            self.publish(
                session_error(sid, {"name": "MessageAbortedError", "data": {"message": "Aborted"}})
            )
            self.publish(status(sid, "idle"))
            self.publish(idle(sid))
        return JSONResponse(True)

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/global/health", self._health),
                Route("/provider", self._providers),
                Route("/session", self._create_session, methods=["POST"]),
                Route("/session/status", self._status),
                Route("/session/{sid}", self._get_session, methods=["GET"]),
                Route("/session/{sid}", self._patch_session, methods=["PATCH"]),
                Route("/path", self._path),
                Route("/session/{sid}/message", self._messages),
                Route("/session/{sid}/message/{mid}", self._message),
                Route("/session/{sid}/prompt_async", self._prompt, methods=["POST"]),
                Route("/session/{sid}/abort", self._abort, methods=["POST"]),
                Route("/event", self._events),
            ]
        )

    # ---- lifecycle
    async def start(self) -> None:
        config = uvicorn.Config(
            self.app(),
            host="127.0.0.1",
            port=0,
            log_level="warning",
            lifespan="off",
            timeout_graceful_shutdown=1,
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        while not self._server.started:
            if self._task.done():
                raise RuntimeError("fake OpenCode failed to start")
            await asyncio.sleep(0.01)
        port = self._server.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        for turn in self._turns.values():
            turn.cancel()
        for _, queue in list(self._subscribers):
            queue.put_nowait(None)
        if self._server is not None and self._task is not None:
            self._server.should_exit = True
            await self._task
