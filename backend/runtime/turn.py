"""Pure (I/O-free) state machine that follows one prompt through OpenCode's event stream.

Events handled (all carry ``properties.sessionID``; shapes verified against 1.18.22/1.18.33)::

    message.updated        {info: {id, role, parentID?, summary?, error?, cost?, tokens?, ...}}
    message.part.updated   {part: {id, messageID, type: text|reasoning|tool|step-start|..., text?}}
                           tool parts: {tool, callID, state: {status: pending|running|completed
                           |error, input, title?, output?, error?}}
    message.part.delta     {messageID, partID, field: "text", delta}
    session.status         {status: {type: busy | idle | retry(attempt, message, action?)}}
    session.idle           {}
    session.error          {error: {name, data}}

Rules:
* Only the assistant message answering *our* user message is followed (``parentID`` equals
  the first user message seen after subscribing). Other sessions never reach this class
  (events are filtered by session id) and other turns in the same session are ignored.
* A turn that uses tools is several assistant messages (one per model step), all with our
  user message as ``parentID``; all of them are followed and their usage is summed.
* Only ``text`` parts become text (consecutive text parts are separated by a blank line).
  ``reasoning`` parts, tool parts, synthetic/ignored parts and compaction summaries never
  become text.
* ``tool`` parts become :class:`ToolActivity` when their status changes (``pending`` is not
  reported: its input is still streaming). The id is the part id (unique, unlike provider call
  ids), the title a short summary of the input (file path relative to the working directory,
  search pattern, ...; never file contents or tool output), errors are shortened. Completed
  or errored calls never go back to running.
* ``message.part.updated`` carries the cumulative part text, so any delta that was missed
  (late subscription, reconnect) is recovered as a suffix without duplicating text.
* Deltas carry no sequence number, so they are only trusted while they extend a part that was
  followed without interruption since its last cumulative text. After an SSE reconnect
  (:meth:`TurnTracker.mark_gap`) deltas of the parts in progress may have been lost, so those
  parts ignore further deltas and only grow from cumulative text (``message.part.updated`` at
  the end of the part, or a snapshot). Deltas for a part whose ``message.part.updated`` was
  never seen are ignored for the same reason. Emitted text is therefore always a prefix of
  OpenCode's real text: never a gap, never a duplicate.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from backend.contracts.models import ToolCall, ToolCallStatus, Usage
from backend.contracts.runtime import TextDelta, ToolActivity

TurnEvent = TextDelta | ToolActivity

_HELD_LIMIT = 200
TITLE_MAX = 200
_STATUSES = {
    "running": ToolCallStatus.RUNNING,
    "completed": ToolCallStatus.COMPLETED,
    "error": ToolCallStatus.ERROR,
}
_IN_FLIGHT = frozenset({"pending", "running"})
_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", re.MULTILINE)
_GAP = {"type": "smartrail.gap"}  # marker in the held-event queue: a reconnect happened here
# MessageOutputLengthError just means the model hit its own output limit: a normal completion.
_NON_FAILURES = frozenset({"MessageAbortedError", "MessageOutputLengthError"})


@dataclass
class _Part:
    message_id: str
    text: str = ""
    ignored: bool = False
    gapped: bool = False  # deltas may have been missed: grow only from cumulative text


@dataclass
class _Tool:
    raw: str  # OpenCode's latest status for the part
    call: ToolCall | None = None  # last reported state, if any


def _int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _one_line(value: str) -> str:
    return " ".join(value.split())


def _shorten(value: str, keep_end: bool = True) -> str:
    value = _one_line(value)
    if len(value) <= TITLE_MAX:
        return value
    keep = TITLE_MAX - 3
    return "..." + value[-keep:] if keep_end else value[:keep] + "..."


def _relative(path: str, directory: str | None) -> str | None:
    """``path`` relative to ``directory`` (``..`` when outside); None if not computable."""
    try:
        if os.path.isabs(path):
            if directory is None:
                return None
            return os.path.relpath(path, directory)
        return os.path.normpath(path)
    except ValueError:  # e.g. another drive on Windows
        return None


def _outside(path: Any, directory: str | None) -> bool:
    if not isinstance(path, str) or not path:
        return False
    relative = _relative(path, directory)
    return relative is None or relative == ".." or relative.startswith(".." + os.sep)


def display_path(path: Any, directory: str | None) -> str:
    """A path as the user knows it: relative to the working directory when inside it."""
    if not isinstance(path, str) or not path:
        return ""
    relative = _relative(path, directory)
    if relative is None or _outside(path, directory):
        return path
    return relative.replace(os.sep, "/")


def tool_title(tool: str, data: Any, directory: str | None, fallback: Any = None) -> str:
    """Short human summary of a tool call from its input (never file contents)."""
    data = data if isinstance(data, dict) else {}
    title = ""
    if tool in ("read", "edit", "write", "list"):
        title = display_path(data.get("filePath") or data.get("path"), directory)
    elif tool in ("glob", "grep"):
        pattern = data.get("pattern")
        title = pattern if isinstance(pattern, str) else ""
        include = data.get("include")
        if isinstance(include, str) and include:
            title += f" ({include})"
        where = display_path(data.get("path"), directory)
        if where:
            title += f" in {where}"
    elif tool == "apply_patch":
        patch = data.get("patchText")
        files = _PATCH_FILE.findall(patch) if isinstance(patch, str) else []
        title = ", ".join(display_path(f, directory) for f in files)
    elif isinstance(fallback, str):
        title = fallback
    return _shorten(title.strip()) or tool


def tool_error(raw: Any, tool: str, data: Any, directory: str | None) -> str:
    """OpenCode's tool error, shortened (permission errors list every rule; never shown)."""
    message = _one_line(raw) if isinstance(raw, str) else ""
    if "prevents you from using this specific tool call" in message:
        data = data if isinstance(data, dict) else {}
        path = data.get("filePath") or data.get("path")
        if tool == "apply_patch" and isinstance(data.get("patchText"), str):
            path = next(iter(_PATCH_FILE.findall(data["patchText"])), None)
        if _outside(path, directory):
            return "Not allowed: outside the working directory"
        return "Not allowed for this agent"
    if message.startswith("Model tried to call unavailable tool"):
        return "Tool not available to this agent"
    return _shorten(message, keep_end=False) or "Tool failed"


class TurnTracker:
    def __init__(self, session_id: str, directory: str | None = None) -> None:
        self.session_id = session_id
        self.directory = directory  # working directory, for tool titles
        self.user_message_id: str | None = None
        self.started = False  # saw our user message, a busy status, or an error
        self.idle = False  # the session finished processing (terminal)
        self.retry: dict[str, Any] | None = None  # latest OpenCode retry status, if any
        self.error: dict[str, Any] | None = None  # failure reported by OpenCode (not an abort)
        self.aborted = False  # OpenCode reported MessageAbortedError
        self.text = ""  # everything emitted as text so far
        self._assistants: dict[str, dict[str, Any]] = {}
        self._parts: dict[str, _Part] = {}
        self._tools: dict[str, _Tool] = {}
        self._held: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ accessors

    @property
    def assistant_ids(self) -> list[str]:
        return list(self._assistants)

    @property
    def has_assistant(self) -> bool:
        return bool(self._assistants)

    @property
    def tools_running(self) -> bool:
        """A tool call is being prepared or executed right now."""
        return any(tool.raw in _IN_FLIGHT for tool in self._tools.values())

    def interrupt_tools(self) -> list[ToolActivity]:
        """The turn ends early: report calls still shown as running as interrupted."""
        events: list[ToolActivity] = []
        for tool in self._tools.values():
            if tool.call is not None and tool.call.status is ToolCallStatus.RUNNING:
                tool.raw = "error"
                tool.call = tool.call.model_copy(
                    update={"status": ToolCallStatus.ERROR, "error": "Interrupted"}
                )
                events.append(ToolActivity(tool.call))
        return events

    def usage(self) -> Usage:
        """Billed tokens summed over our assistant messages; cost is exposed separately."""
        input_tokens = output_tokens = 0
        for info in self._assistants.values():
            tokens = info.get("tokens")
            if not isinstance(tokens, dict):
                continue
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            input_tokens += _int(tokens.get("input")) + _int(cache.get("read"))
            input_tokens += _int(cache.get("write"))
            output_tokens += _int(tokens.get("output")) + _int(tokens.get("reasoning"))
        return Usage(input_tokens=input_tokens, output_tokens=output_tokens)

    def reported_cost(self) -> float | None:
        """Sum of OpenCode's per-message ``cost`` (None if OpenCode reported none)."""
        costs = [
            float(info["cost"])
            for info in self._assistants.values()
            if isinstance(info.get("cost"), int | float) and not isinstance(info.get("cost"), bool)
        ]
        return sum(costs) if costs else None

    def failure(self) -> dict[str, Any] | None:
        """The first non-abort error: from ``session.error`` or an assistant message."""
        if self.error is not None:
            return self.error
        for info in self._assistants.values():
            error = info.get("error")
            if isinstance(error, dict) and error.get("name") not in _NON_FAILURES:
                return error
        return None

    # ------------------------------------------------------------------ feeding

    def mark_gap(self) -> None:
        """The event stream was interrupted (reconnect): events may have been lost here."""
        for state in self._parts.values():
            state.gapped = True
        if self._held and len(self._held) < _HELD_LIMIT:
            self._held.append(_GAP)

    def feed(self, event: dict[str, Any]) -> list[TurnEvent]:
        """Consume one event; return the new text deltas and tool activity (possibly none)."""
        props = event.get("properties")
        if not isinstance(props, dict) or props.get("sessionID") != self.session_id:
            return []
        kind = event.get("type")
        if kind == "message.updated":
            return self._on_message(props.get("info"))
        if kind == "message.part.updated":
            return self._on_part(props.get("part"))
        if kind == "message.part.delta":
            return self._on_delta(props)
        if kind == "session.status":
            self._on_status(props.get("status"))
        elif kind == "session.idle":
            self.idle = self.started or self.has_assistant
        elif kind == "session.error":
            self.started = True
            error = props.get("error")
            if isinstance(error, dict) and error.get("name") == "MessageAbortedError":
                self.aborted = True
            elif isinstance(error, dict):
                self.error = error
            else:
                self.error = {"name": "UnknownError", "data": {}}
        return []

    def apply_snapshot(self, messages: list[dict[str, Any]]) -> list[TurnEvent]:
        """Reconcile with ``GET /session/{id}/message`` output; returns anything missed."""
        deltas: list[TurnEvent] = []
        if self.user_message_id is None:
            for item in reversed(messages):
                info = item.get("info") if isinstance(item, dict) else None
                if isinstance(info, dict) and info.get("role") == "user":
                    self.user_message_id = info.get("id")
                    self.started = True
                    break
        for item in messages:
            if not isinstance(item, dict) or not isinstance(item.get("info"), dict):
                continue
            info = item["info"]
            if info.get("role") != "assistant" or not self._accepts(info):
                continue
            deltas += self._accept(info)
            for part in item.get("parts") or []:
                if isinstance(part, dict):
                    deltas += self._apply_part(part)
        return deltas

    # ------------------------------------------------------------------ internals

    def _accepts(self, info: dict[str, Any]) -> bool:
        if info.get("summary"):  # compaction summaries are never surfaced
            return False
        if self.user_message_id is None:
            return True
        return info.get("parentID") == self.user_message_id

    def _on_status(self, status: Any) -> None:
        if not isinstance(status, dict):
            return
        kind = status.get("type")
        if kind == "busy":
            self.started = True
        elif kind == "retry":
            self.started = True
            self.retry = status
        elif kind == "idle":
            self.idle = self.started or self.has_assistant

    def _on_message(self, info: Any) -> list[TurnEvent]:
        if not isinstance(info, dict):
            return []
        role = info.get("role")
        if role == "user":
            if self.user_message_id is None and isinstance(info.get("id"), str):
                self.user_message_id = info["id"]
                self.started = True
            return []
        if role != "assistant" or not self._accepts(info):
            return []
        return self._accept(info)

    def _accept(self, info: dict[str, Any]) -> list[TurnEvent]:
        message_id = info.get("id")
        if not isinstance(message_id, str):
            return []
        if self.user_message_id is None and isinstance(info.get("parentID"), str):
            self.user_message_id = info["parentID"]
        first_time = message_id not in self._assistants
        self._assistants[message_id] = info
        self.started = True
        error = info.get("error")
        if isinstance(error, dict) and error.get("name") == "MessageAbortedError":
            self.aborted = True
        deltas: list[TurnEvent] = []
        if first_time and self._held:
            held, self._held = self._held, []
            for event in held:
                if event is _GAP:
                    self.mark_gap()
                else:
                    deltas += self.feed(event)
        return deltas

    def _known_message(self, message_id: Any) -> bool:
        return isinstance(message_id, str) and message_id in self._assistants

    def _hold(self, event_type: str, props: dict[str, Any]) -> None:
        if len(self._held) < _HELD_LIMIT:
            self._held.append({"type": event_type, "properties": props})

    def _on_part(self, part: Any) -> list[TurnEvent]:
        if not isinstance(part, dict):
            return []
        message_id = part.get("messageID")
        if not self._known_message(message_id):
            if message_id != self.user_message_id:
                self._hold("message.part.updated", {"sessionID": self.session_id, "part": part})
            return []
        return self._apply_part(part, live=True)

    def _apply_part(self, part: dict[str, Any], *, live: bool = False) -> list[TurnEvent]:
        """Apply a part's cumulative state. ``live``: it came in order on the event stream
        (so later deltas continue it); otherwise it came from a snapshot (unordered)."""
        part_id = part.get("id")
        message_id = part.get("messageID")
        if not isinstance(part_id, str) or not isinstance(message_id, str):
            return []
        if part.get("type") == "tool":
            return self._on_tool(part_id, part)
        state = self._parts.get(part_id)
        if state is None:
            if part.get("type") != "text" or part.get("synthetic") or part.get("ignored"):
                self._parts[part_id] = _Part(message_id, ignored=True)
                return []
            state = _Part(message_id, gapped=not live)
            self._parts[part_id] = state
        elif state.ignored or part.get("type") != "text":
            return []
        full_text = part.get("text")
        deltas = self._grow(state, full_text)
        if live and state.text == full_text:
            state.gapped = False  # in sync again: following deltas extend this exact text
        return deltas

    def _on_tool(self, part_id: str, part: dict[str, Any]) -> list[TurnEvent]:
        state = part.get("state")
        tool_name = part.get("tool")
        if not isinstance(state, dict) or not isinstance(tool_name, str):
            return []
        raw = state.get("status")
        raw = raw if isinstance(raw, str) else ""
        tool = self._tools.setdefault(part_id, _Tool(raw))
        previous = tool.call.status if tool.call is not None else None
        if previous in (ToolCallStatus.COMPLETED, ToolCallStatus.ERROR):
            return []  # final; a stale running update (e.g. from a snapshot) cannot undo it
        tool.raw = raw
        status = _STATUSES.get(raw)
        if status is None or status is previous:
            return []
        data = state.get("input")
        error = None
        if status is ToolCallStatus.ERROR:
            error = tool_error(state.get("error"), tool_name, data, self.directory)
        tool.call = ToolCall(
            id=part_id,
            tool=tool_name,
            title=tool_title(tool_name, data, self.directory, state.get("title")),
            status=status,
            error=error,
        )
        return [ToolActivity(tool.call)]

    def _grow(self, state: _Part, full_text: Any) -> list[TurnEvent]:
        """The part now holds ``full_text`` (cumulative); emit only what is new."""
        if not isinstance(full_text, str) or len(full_text) <= len(state.text):
            return []
        if not full_text.startswith(state.text):
            return []
        return self._append(state, full_text[len(state.text) :])

    def _append(self, state: _Part, delta: str) -> list[TurnEvent]:
        if not delta:
            return []
        if not state.text and self.text:  # a new text part (e.g. after tool calls)
            separator = (
                "" if self.text.endswith("\n\n") else "\n" if self.text.endswith("\n") else "\n\n"
            )
            delta_out = separator + delta
        else:
            delta_out = delta
        state.text += delta
        self.text += delta_out
        return [TextDelta(delta_out)]

    def _on_delta(self, props: dict[str, Any]) -> list[TurnEvent]:
        delta = props.get("delta")
        part_id = props.get("partID")
        message_id = props.get("messageID")
        if (
            props.get("field") != "text"
            or not isinstance(delta, str)
            or not isinstance(part_id, str)
            or not isinstance(message_id, str)
        ):
            return []
        if not self._known_message(message_id):
            if message_id != self.user_message_id:
                self._hold("message.part.delta", props)
            return []
        state = self._parts.get(part_id)
        # Unknown part: its start (and maybe earlier deltas) was lost, so this delta has no
        # known offset. The cumulative text recovers it later.
        if state is None or state.ignored or state.gapped:
            return []
        return self._append(state, delta)
