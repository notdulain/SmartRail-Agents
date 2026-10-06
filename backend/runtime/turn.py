"""Pure (I/O-free) state machine that follows one prompt through OpenCode's event stream.

Events handled (all carry ``properties.sessionID``; shapes verified against 1.18.22)::

    message.updated        {info: {id, role, parentID?, summary?, error?, cost?, tokens?, ...}}
    message.part.updated   {part: {id, messageID, type: text|reasoning|step-start|..., text?}}
    message.part.delta     {messageID, partID, field: "text", delta}
    session.status         {status: {type: busy | idle | retry(attempt, message, action?)}}
    session.idle           {}
    session.error          {error: {name, data}}

Rules:
* Only the assistant message answering *our* user message is followed (``parentID`` equals
  the first user message seen after subscribing). Other sessions never reach this class
  (events are filtered by session id) and other turns in the same session are ignored.
* Only ``text`` parts become text. ``reasoning`` parts, synthetic/ignored parts and
  compaction summaries are never surfaced.
* ``message.part.updated`` carries the cumulative part text, so any delta that was missed
  (late subscription, reconnect) is recovered as a suffix without duplicating text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.contracts.models import Usage

_HELD_LIMIT = 200
# MessageOutputLengthError just means the model hit its own output limit: a normal completion.
_NON_FAILURES = frozenset({"MessageAbortedError", "MessageOutputLengthError"})


@dataclass
class _Part:
    message_id: str
    text: str = ""
    ignored: bool = False


def _int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


class TurnTracker:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.user_message_id: str | None = None
        self.started = False  # saw our user message, a busy status, or an error
        self.idle = False  # the session finished processing (terminal)
        self.retry: dict[str, Any] | None = None  # latest OpenCode retry status, if any
        self.error: dict[str, Any] | None = None  # failure reported by OpenCode (not an abort)
        self.aborted = False  # OpenCode reported MessageAbortedError
        self.text = ""  # everything emitted as text so far
        self._assistants: dict[str, dict[str, Any]] = {}
        self._parts: dict[str, _Part] = {}
        self._pending: dict[str, tuple[str, list[str]]] = {}
        self._held: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ accessors

    @property
    def assistant_ids(self) -> list[str]:
        return list(self._assistants)

    @property
    def has_assistant(self) -> bool:
        return bool(self._assistants)

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

    def feed(self, event: dict[str, Any]) -> list[str]:
        """Consume one event; return the new text deltas (possibly empty)."""
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

    def apply_snapshot(self, messages: list[dict[str, Any]]) -> list[str]:
        """Reconcile with ``GET /session/{id}/message`` output; returns any missed text."""
        deltas: list[str] = []
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

    def _on_message(self, info: Any) -> list[str]:
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

    def _accept(self, info: dict[str, Any]) -> list[str]:
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
        deltas: list[str] = []
        if first_time and self._held:
            held, self._held = self._held, []
            for event in held:
                deltas += self.feed(event)
        return deltas

    def _known_message(self, message_id: Any) -> bool:
        return isinstance(message_id, str) and message_id in self._assistants

    def _hold(self, event_type: str, props: dict[str, Any]) -> None:
        if len(self._held) < _HELD_LIMIT:
            self._held.append({"type": event_type, "properties": props})

    def _on_part(self, part: Any) -> list[str]:
        if not isinstance(part, dict):
            return []
        message_id = part.get("messageID")
        if not self._known_message(message_id):
            if message_id != self.user_message_id:
                self._hold("message.part.updated", {"sessionID": self.session_id, "part": part})
            return []
        return self._apply_part(part)

    def _apply_part(self, part: dict[str, Any]) -> list[str]:
        part_id = part.get("id")
        message_id = part.get("messageID")
        if not isinstance(part_id, str) or not isinstance(message_id, str):
            return []
        state = self._parts.get(part_id)
        if state is None:
            if part.get("type") != "text" or part.get("synthetic") or part.get("ignored"):
                self._parts[part_id] = _Part(message_id, ignored=True)
                self._pending.pop(part_id, None)
                return []
            state = _Part(message_id)
            self._parts[part_id] = state
            deltas = self._grow(state, part.get("text"))
            pending = self._pending.pop(part_id, None)
            if pending is not None and pending[0] == message_id:
                for delta in pending[1]:
                    deltas += self._append(state, delta)
            return deltas
        if state.ignored or part.get("type") != "text":
            return []
        return self._grow(state, part.get("text"))

    def _grow(self, state: _Part, full_text: Any) -> list[str]:
        """The part now holds ``full_text`` (cumulative); emit only what is new."""
        if not isinstance(full_text, str) or len(full_text) <= len(state.text):
            return []
        if not full_text.startswith(state.text):
            return []
        return self._append(state, full_text[len(state.text) :])

    def _append(self, state: _Part, delta: str) -> list[str]:
        if not delta:
            return []
        state.text += delta
        self.text += delta
        return [delta]

    def _on_delta(self, props: dict[str, Any]) -> list[str]:
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
        if state is None:
            held_message, deltas = self._pending.setdefault(part_id, (message_id, []))
            if held_message == message_id:
                deltas.append(delta)
            return []
        if state.ignored:
            return []
        return self._append(state, delta)
