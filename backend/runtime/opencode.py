"""``OpenCodeRuntime`` implemented against a real OpenCode server (HTTP + SSE).

Shapes verified against OpenCode 1.18.22 and 1.18.33.

OpenCode runs one *instance* per directory and selects it per request from the ``directory``
query parameter (then the ``x-opencode-directory`` header, then its own cwd). A session created
with ``?directory=D`` lives in instance ``D``, and its events are published only on that
instance's ``GET /event?directory=D`` stream. So every call for a session with a working
directory (prompt, abort, message reads, ``/session/status``, the event subscription) carries
that directory; the runtime remembers session -> directory. Sessions without a directory use
OpenCode's own (neutral) cwd and never send the parameter.

Tool access (see ``agent_config``): every session is created deny-all. A turn picks the OpenCode
agent for its tool access, and for a session with a directory the runtime first makes sure the
session's permission ruleset matches that access (``PATCH /session/{id}``, only when it
changed): OpenCode enforces these rules server-side, both when choosing which tools the model
is offered and when a tool runs. Sessions without a directory never get tools.

How one turn works (see ``stream``):

1. Open ``GET /event`` and wait for its ``server.connected`` frame, so the subscription is
   live *before* the prompt exists and no early delta can be lost.
2. ``POST /session/{id}/prompt_async`` once (never retried, so a prompt is never duplicated)
   with the per-request ``model`` and ``system`` text for the single neutral agent.
3. Follow the events of that session / our user message with :class:`TurnTracker`, yielding
   ``TextDelta`` as text arrives. The terminal signal is the session going idle.
4. Reconcile with ``GET /session/{id}/message/{id}`` (recovers any missed suffix, gives final
   tokens/cost), then yield exactly one ``Completed`` or ``Failed``.

If the consumer is cancelled or closes the iterator early, the in-flight generation is aborted
(``POST /session/{id}/abort``, shielded so it completes) and the SSE connection is closed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import re
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import httpx

from backend.config import AppConfig
from backend.contracts.models import ErrorCode, ProvidersResponse, ToolAccess, Usage
from backend.contracts.runtime import (
    Completed,
    CompletionRequest,
    Failed,
    RuntimeEvent,
    RuntimeUnavailableError,
    TextDelta,
)

from .agent_config import AGENT_NAME, agent_for, session_permission
from .catalog import map_providers
from .errors import MAX_MESSAGE_CHARS, classify_error, classify_text, is_missing_model
from .sse import iter_sse_json
from .turn import TurnTracker

log = logging.getLogger("backend.runtime")

# OpenCode's prompt API has no per-request output limit (the model's own limit applies), so the
# requested ``max_output_tokens`` is enforced here on visible text with a ~4 chars/token estimate.
CHARS_PER_TOKEN = 4

CATALOG_TTL_S = 600.0
REQUEST_TIMEOUT_S = 15.0
CATALOG_TIMEOUT_S = 30.0
HEALTH_TIMEOUT_S = 3.0
ABORT_TIMEOUT_S = 10.0
EVENT_CONNECT_WAIT_S = 5.0
EVENT_READ_TIMEOUT_S = 35.0  # OpenCode sends a heartbeat roughly every 10 s
MAX_RECONNECTS = 3
RECONNECT_BACKOFF_S = 0.25
STALL_TIMEOUT_S = 180.0  # no event for this session (heartbeats do not count) -> give up
MAX_TRANSIENT_RETRIES = 2  # OpenCode's own backoff retries tolerated for non-rate-limit errors
SNAPSHOT_LIMIT = 8

_RATE_LIMIT_REASONS = {"free_tier_limit", "account_rate_limit"}
# Messages worth waiting out while OpenCode backs off. Anything else (e.g. an account gate such
# as "requires 18+ age confirmation") will not fix itself, so it fails the turn immediately.
_TRANSIENT = re.compile(
    r"overload|timeout|timed out|temporar|try again|service unavailable|bad gateway"
    r"|gateway|\b5\d\d\b|connection|econn|network|socket|reset by peer|capacity|busy",
    re.IGNORECASE,
)


class _StreamEnded(Exception):
    """The ``/event`` connection ended (EOF) before the turn finished."""


_STREAM_ERRORS = (httpx.TransportError, httpx.DecodingError, _StreamEnded, TimeoutError)


def _where(directory: str | None) -> dict[str, str]:
    """Query parameters selecting the OpenCode instance for ``directory`` (none = neutral)."""
    return {"directory": directory} if directory else {}


def _same_directory(a: str, b: str) -> bool:
    if os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b)):
        return True
    try:  # e.g. 8.3 short names or symlinks on one side
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, ValueError):
        return False


def _confinement(worktree: Any, directory: Any) -> str | None:
    """Where OpenCode's boundary is wider than ``directory``: its path relative to the worktree.

    OpenCode lets tools touch anything inside the session directory *or* its worktree (the git
    repository root; ``/`` outside git). ``None`` means the boundary already is ``directory``.
    Raises ``ValueError`` when the layout cannot be confined safely.
    """
    if not isinstance(worktree, str) or not isinstance(directory, str) or not directory:
        raise ValueError("OpenCode reported no working directory")
    if worktree in ("", "/") or _same_directory(worktree, directory):
        return None
    relative = os.path.relpath(directory, worktree)  # ValueError across Windows drives
    if relative.startswith("..") or os.path.isabs(relative) or any(c in relative for c in "*?"):
        raise ValueError(f"unexpected OpenCode worktree for {directory!r}")
    return relative.replace(os.sep, "/")


class _EventConnection:
    """One SSE subscription to ``GET /event`` of one OpenCode instance."""

    def __init__(self, client: httpx.AsyncClient, directory: str | None = None) -> None:
        self._client = client
        self._directory = directory
        self._response: httpx.Response | None = None
        self._events: AsyncIterator[dict[str, Any]] | None = None

    async def open(self) -> None:
        await self.aclose()
        request = self._client.build_request(
            "GET",
            "/event",
            params=_where(self._directory),
            headers={"Accept": "text/event-stream"},
            timeout=httpx.Timeout(REQUEST_TIMEOUT_S, read=EVENT_READ_TIMEOUT_S),
        )
        response = await self._client.send(request, stream=True)
        self._response = response
        if response.status_code != 200:
            status = response.status_code
            await self.aclose()
            raise _EventStatusError(status)
        events = iter_sse_json(response.aiter_lines())
        self._events = events
        async with asyncio.timeout(EVENT_CONNECT_WAIT_S):
            async for event in events:
                if event.get("type") == "server.connected":
                    return
        raise _StreamEnded()

    async def next(self) -> dict[str, Any]:
        if self._events is None:
            raise _StreamEnded()
        try:
            return await self._events.__anext__()
        except StopAsyncIteration:
            raise _StreamEnded() from None

    async def aclose(self) -> None:
        response, self._response, self._events = self._response, None, None
        if response is not None:
            with contextlib.suppress(Exception):
                await response.aclose()


class _EventStatusError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"OpenCode event stream returned HTTP {status}")
        self.status = status


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    data = body.get("data") if isinstance(body, dict) else None
    message = data.get("message") if isinstance(data, dict) else None
    return " ".join(message.split())[:MAX_MESSAGE_CHARS] if isinstance(message, str) else ""


def _http_failure(response: httpx.Response, what: str) -> Failed:
    status = response.status_code
    detail = _error_message(response)
    suffix = f": {detail}" if detail else ""
    if status in (401, 403):
        return Failed(ErrorCode.PROVIDER_UNAVAILABLE, f"OpenCode rejected the request ({status})")
    return Failed(ErrorCode.RUNTIME_ERROR, f"OpenCode {what} failed (HTTP {status}){suffix}")


class HttpOpenCodeRuntime:
    """Implements ``backend.contracts.runtime.OpenCodeRuntime`` over httpx (async)."""

    def __init__(
        self,
        config: AppConfig,
        *,
        client: httpx.AsyncClient | None = None,
        catalog_ttl: float = CATALOG_TTL_S,
        reconnect_backoff: float = RECONNECT_BACKOFF_S,
        stall_timeout: float = STALL_TIMEOUT_S,
    ) -> None:
        auth = (
            httpx.BasicAuth(config.opencode_username, config.opencode_password)
            if config.opencode_password
            else None
        )
        self._client = client or httpx.AsyncClient(
            base_url=config.opencode_url,
            auth=auth,
            timeout=httpx.Timeout(REQUEST_TIMEOUT_S),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=20),
        )
        self._catalog_ttl = catalog_ttl
        self._reconnect_backoff = reconnect_backoff
        self._stall_timeout = stall_timeout
        self._catalog: ProvidersResponse | None = None
        self._catalog_at = 0.0
        self._catalog_lock = asyncio.Lock()
        self._active: set[str] = set()
        self._directories: dict[str, str | None] = {}  # session id -> working directory
        self._policies: dict[str, list[dict[str, str]]] = {}  # session id -> applied ruleset
        self._tasks: set[asyncio.Future[Any]] = set()
        self._closed = False

    # ------------------------------------------------------------------ simple calls

    async def health(self) -> bool:
        try:
            response = await self._client.get("/global/health", timeout=HEALTH_TIMEOUT_S)
            return response.status_code == 200 and bool(response.json().get("healthy"))
        except (httpx.HTTPError, ValueError, AttributeError, RuntimeError):
            return False

    async def list_providers(self, refresh: bool = False) -> ProvidersResponse:
        async with self._catalog_lock:
            fresh = (
                self._catalog is not None
                and time.monotonic() - self._catalog_at < self._catalog_ttl
            )
            if self._catalog is not None and fresh and not refresh:
                return self._catalog
            try:
                response = await self._client.get("/provider", timeout=CATALOG_TIMEOUT_S)
            except (httpx.HTTPError, RuntimeError) as exc:
                name = type(exc).__name__
                raise RuntimeUnavailableError(f"OpenCode unreachable: {name}") from exc
            if response.status_code != 200:
                raise RuntimeUnavailableError(
                    f"OpenCode /provider returned HTTP {response.status_code}"
                )
            try:
                catalog = map_providers(response.json())
            except ValueError as exc:
                raise RuntimeUnavailableError("OpenCode /provider returned invalid JSON") from exc
            self._catalog, self._catalog_at = catalog, time.monotonic()
            return catalog

    async def create_session(self, title: str, directory: str | None = None) -> str:
        body = {
            "title": title.strip() or "SmartRail chat",  # a non-default title skips title-gen
            "agent": AGENT_NAME,
            "permission": session_permission(ToolAccess.NONE),  # opened per turn, if allowed
        }
        try:
            response = await self._client.post("/session", params=_where(directory), json=body)
        except (httpx.HTTPError, RuntimeError) as exc:
            raise RuntimeUnavailableError(f"OpenCode unreachable: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise RuntimeUnavailableError(
                f"OpenCode could not create a session (HTTP {response.status_code})"
            )
        try:
            session_id = response.json()["id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeUnavailableError("OpenCode returned an invalid session") from exc
        if not isinstance(session_id, str):
            raise RuntimeUnavailableError("OpenCode returned an invalid session id")
        self._directories[session_id] = directory
        if directory is not None:
            self._policies[session_id] = body["permission"]
        return session_id

    async def abort(self, session_id: str) -> None:
        """Abort any in-flight generation. Idempotent; never raises."""
        try:
            await self._client.post(
                f"/session/{quote(session_id, safe='')}/abort",
                params=_where(self._directories.get(session_id)),
                timeout=ABORT_TIMEOUT_S,
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            log.warning("abort failed for %s: %s", session_id, type(exc).__name__)

    async def aclose(self) -> None:
        self._closed = True
        for session_id in list(self._active):
            await self.abort(session_id)
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._client.aclose()

    # ------------------------------------------------------------------ streaming

    async def stream(self, request: CompletionRequest) -> AsyncIterator[RuntimeEvent]:
        session_id = request.session_id
        if self._closed:
            yield Failed(ErrorCode.PROVIDER_UNAVAILABLE, "The OpenCode runtime is shut down")
            return
        if session_id in self._active:
            yield Failed(
                ErrorCode.RUNTIME_ERROR, "A response is already being generated in this session"
            )
            return
        self._active.add(session_id)
        tracker = TurnTracker(session_id)
        events: _EventConnection | None = None
        settled = False  # True once nothing of ours can still be running in OpenCode
        try:
            directory, failure = await self._session_directory(request)
            if failure is not None:
                settled = True
                yield failure
                return
            access = request.tool_access if directory is not None else ToolAccess.NONE
            if directory is not None:
                failure = await self._apply_tool_access(session_id, directory, access)
                if failure is not None:
                    settled = True
                    yield failure
                    return
            events = _EventConnection(self._client, directory)
            try:
                await events.open()
            except (*_STREAM_ERRORS, _EventStatusError) as exc:
                settled = True  # no prompt was sent
                yield Failed(ErrorCode.PROVIDER_UNAVAILABLE, _unreachable_message(exc))
                return

            failure, delivered = await self._send_prompt(request, directory, access)
            if failure is not None:
                settled = not delivered
                yield failure
                return

            cap_chars = request.max_output_tokens * CHARS_PER_TOKEN
            reconnects = 0
            last_progress = time.monotonic()
            while not tracker.idle:
                if time.monotonic() - last_progress > self._stall_timeout:
                    await self._abort_shielded(session_id)
                    settled = True
                    yield Failed(
                        ErrorCode.RUNTIME_ERROR, "The model stopped responding (timed out)"
                    )
                    return
                try:
                    event = await events.next()
                except _STREAM_ERRORS as exc:
                    if self._closed or reconnects >= MAX_RECONNECTS:
                        yield Failed(ErrorCode.PROVIDER_UNAVAILABLE, _unreachable_message(exc))
                        return
                    reconnects += 1
                    tracker.mark_gap()  # deltas sent while disconnected are lost for good
                    await asyncio.sleep(self._reconnect_backoff * reconnects)
                    try:
                        await events.open()
                        missed = await self._resync(tracker, directory)
                    except (*_STREAM_ERRORS, _EventStatusError, ValueError):
                        continue
                    for text in missed:
                        yield TextDelta(text)
                    continue

                props = event.get("properties")
                if isinstance(props, dict) and props.get("sessionID") == session_id:
                    last_progress = time.monotonic()
                for text in tracker.feed(event):
                    yield TextDelta(text)

                if tracker.retry is not None:
                    status, tracker.retry = tracker.retry, None
                    failed = _retry_failure(status, tracker.text)
                    if failed is not None:
                        await self._abort_shielded(session_id)
                        settled = True
                        yield failed
                        return
                if cap_chars > 0 and len(tracker.text) >= cap_chars:
                    await self._abort_shielded(session_id)
                    settled = True
                    yield _truncated_completion(tracker)
                    return

            settled = True  # the session went idle on its own
            for text in await self._finalize(tracker, directory):
                yield TextDelta(text)
            outcome = await self._outcome(tracker, request)
            yield outcome
        finally:
            self._active.discard(session_id)
            try:
                if not settled:
                    await self._abort_shielded(session_id)
            finally:
                if events is not None:
                    await events.aclose()

    async def _abort_shielded(self, session_id: str) -> None:
        """Run ``abort`` to completion even if the awaiting task is cancelled meanwhile."""
        task = asyncio.ensure_future(self.abort(session_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        await asyncio.shield(task)

    async def _session_directory(
        self, request: CompletionRequest
    ) -> tuple[str | None, Failed | None]:
        """The working directory of ``request.session_id`` (``None`` = neutral).

        Known from ``create_session``; otherwise (e.g. after a restart) ``request.directory``,
        confirmed once against OpenCode's record of the session. A session's directory never
        changes, so a request naming a different one fails instead of reaching the wrong files.
        """
        session_id = request.session_id
        if session_id in self._directories:
            known = self._directories[session_id]
        elif request.directory is None:
            return None, None  # a neutral session (or an unknown one): no directory parameter
        else:
            known = await self._lookup_directory(session_id, request.directory)
            self._directories[session_id] = known
        if request.directory is not None and (
            known is None or not _same_directory(known, request.directory)
        ):
            return known, Failed(
                ErrorCode.RUNTIME_ERROR,
                "This conversation's session belongs to a different working directory",
            )
        return known, None

    async def _lookup_directory(self, session_id: str, requested: str) -> str:
        try:
            response = await self._client.get(
                f"/session/{quote(session_id, safe='')}", params=_where(requested)
            )
            actual = response.json().get("directory") if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError, AttributeError, RuntimeError):
            actual = None
        # Unknown to OpenCode (or unreachable): route as requested and let the prompt report it.
        return actual if isinstance(actual, str) and actual else requested

    async def _apply_tool_access(
        self, session_id: str, directory: str, access: ToolAccess
    ) -> Failed | None:
        """Make the session's permission ruleset match ``access`` before the prompt runs."""
        where = _where(directory)
        try:
            confine_to = None
            if access is not ToolAccess.NONE:
                response = await self._client.get("/path", params=where)
                info = response.json() if response.status_code == 200 else {}
                if not isinstance(info, dict):
                    raise ValueError("invalid /path response")
                confine_to = _confinement(info.get("worktree"), info.get("directory"))
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            log.warning("cannot confine tools for %s: %s", session_id, exc)
            return Failed(
                ErrorCode.RUNTIME_ERROR,
                "Could not confine file tools to the agent's working directory",
            )
        rules = session_permission(access, confine_to)
        if self._policies.get(session_id) == rules:
            return None
        try:
            response = await self._client.patch(
                f"/session/{quote(session_id, safe='')}", params=where, json={"permission": rules}
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            return Failed(ErrorCode.PROVIDER_UNAVAILABLE, _unreachable_message(exc))
        if response.status_code != 200:
            return _http_failure(response, "permission update")
        self._policies[session_id] = rules
        return None

    async def _send_prompt(
        self, request: CompletionRequest, directory: str | None, access: ToolAccess
    ) -> tuple[Failed | None, bool]:
        """POST the prompt once. Returns ``(failure, maybe_delivered)``; never retried."""
        body: dict[str, Any] = {
            "agent": agent_for(access),
            "model": {"providerID": request.provider_id, "modelID": request.model_id},
            "parts": [{"type": "text", "text": request.user_text}],
        }
        if request.system:
            body["system"] = request.system
        path = f"/session/{quote(request.session_id, safe='')}/prompt_async"
        try:
            response = await self._client.post(path, params=_where(directory), json=body)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            return Failed(ErrorCode.PROVIDER_UNAVAILABLE, _unreachable_message(exc)), False
        except (httpx.HTTPError, RuntimeError) as exc:
            # Sent but no answer: the prompt may be running. Never resend; the caller aborts.
            return (
                Failed(
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    f"Lost connection to OpenCode while sending ({type(exc).__name__})",
                ),
                True,
            )
        if response.status_code in (200, 204):
            return None, True
        return _http_failure(response, "prompt"), False

    async def _resync(self, tracker: TurnTracker, directory: str | None) -> list[str]:
        """After an SSE reconnect: recover missed text and detect a turn that already finished."""
        session_path = f"/session/{quote(tracker.session_id, safe='')}"
        where = _where(directory)
        status = await self._client.get("/session/status", params=where)
        busy = False
        if status.status_code == 200:
            statuses = status.json()
            current = statuses.get(tracker.session_id) if isinstance(statuses, dict) else None
            busy = isinstance(current, dict) and current.get("type") in ("busy", "retry")
        messages = await self._client.get(
            f"{session_path}/message", params={**where, "limit": SNAPSHOT_LIMIT}
        )
        missed: list[str] = []
        if messages.status_code == 200 and isinstance(messages.json(), list):
            missed = tracker.apply_snapshot(messages.json())
        if not busy and tracker.started:
            tracker.idle = True
        return missed

    async def _finalize(self, tracker: TurnTracker, directory: str | None) -> list[str]:
        """Authoritative read of our assistant message(s): missed suffix, tokens, cost."""
        session_path = f"/session/{quote(tracker.session_id, safe='')}"
        snapshot: list[dict[str, Any]] = []
        for message_id in tracker.assistant_ids:
            try:
                response = await self._client.get(
                    f"{session_path}/message/{quote(message_id)}", params=_where(directory)
                )
                payload = response.json() if response.status_code == 200 else None
            except (httpx.HTTPError, ValueError, RuntimeError):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("info"), dict):
                snapshot.append(payload)
        return tracker.apply_snapshot(snapshot) if snapshot else []

    async def _outcome(self, tracker: TurnTracker, request: CompletionRequest) -> RuntimeEvent:
        error = tracker.failure()
        if error is not None:
            failed = classify_error(error)
            if is_missing_model(failed):
                failed = await self._explain_missing_model(request, failed)
            return failed
        if tracker.aborted:
            return Failed(ErrorCode.RUNTIME_ERROR, "Generation was aborted")
        if not tracker.text.strip():
            return Failed(ErrorCode.RUNTIME_ERROR, "The model returned no text")
        usage = tracker.usage()
        cost = tracker.reported_cost() if request.provider_id == "openrouter" else None
        return Completed(
            tracker.text,
            Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=cost,
            ),
        )

    async def _explain_missing_model(self, request: CompletionRequest, failed: Failed) -> Failed:
        """OpenCode says 'Model not found' for both a disconnected provider and a bad model id."""
        try:
            catalog = await self.list_providers(refresh=True)
        except RuntimeUnavailableError:
            return failed
        provider = next((p for p in catalog.providers if p.id == request.provider_id), None)
        if provider is None or not provider.connected:
            return Failed(
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"Provider '{request.provider_id}' is not connected in OpenCode",
            )
        if not any(m.id == request.model_id for m in provider.models):
            return Failed(
                ErrorCode.MODEL_UNAVAILABLE,
                f"Model '{request.model_id}' is not offered by provider '{request.provider_id}'",
            )
        return failed


def _unreachable_message(exc: BaseException) -> str:
    if isinstance(exc, _EventStatusError) and exc.status in (401, 403):
        return "OpenCode rejected the app's credentials"
    return f"OpenCode is unreachable ({type(exc).__name__})"


def _retry_failure(status: dict[str, Any], text_so_far: str) -> Failed | None:
    """Decide whether an OpenCode ``retry`` status should fail the turn now.

    OpenCode retries rate limits and transient errors with long backoff (seconds to a minute).
    Rate limits, auth and model problems fail immediately; other transient errors are
    tolerated for a couple of attempts unless text has already streamed (a retry would
    restart the answer).
    """
    raw = status.get("message")
    message = " ".join(raw.split())[:MAX_MESSAGE_CHARS] if isinstance(raw, str) else ""
    action = status.get("action")
    reason = action.get("reason") if isinstance(action, dict) else None
    code = ErrorCode.RATE_LIMITED if reason in _RATE_LIMIT_REASONS else classify_text(message or "")
    attempt = status.get("attempt")
    attempts = attempt if isinstance(attempt, int) else 1
    transient = not message or _TRANSIENT.search(message) is not None
    if (
        code is ErrorCode.RUNTIME_ERROR
        and transient
        and not text_so_far
        and attempts <= MAX_TRANSIENT_RETRIES
    ):
        return None
    return Failed(code, message or "The provider kept failing and OpenCode is retrying")


def _truncated_completion(tracker: TurnTracker) -> Completed:
    """Output reached ``max_output_tokens``: finish with what streamed (token count estimated)."""
    estimated = math.ceil(len(tracker.text) / CHARS_PER_TOKEN)
    return Completed(tracker.text, Usage(output_tokens=estimated))
