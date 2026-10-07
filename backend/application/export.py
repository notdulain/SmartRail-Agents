"""Markdown transcript rendering (in memory only; generated on explicit request)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from backend.contracts.models import (
    Conversation,
    ConversationType,
    Message,
    MessageRole,
    MessageStatus,
    ToolCallStatus,
)

from .util import utcnow


def _stamp(moment) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC")


def _slug(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()
    return (slug or "transcript")[:60]


def _code(text: str) -> str:
    return "`" + text.replace("`", "'").replace("\n", " ") + "`"


def _attachments_line(msg: Message, conv: Conversation, names: Mapping[str, str]) -> str:
    items = []
    for ref in msg.attachments:
        item = _code(ref.path)
        if conv.type is ConversationType.GROUP:
            item += f" ({names.get(ref.agent_id, ref.agent_id)})"
        items.append(item)
    return "_Attached:_ " + ", ".join(items)


def _tool_calls_line(msg: Message) -> str:
    items = []
    for call in msg.tool_calls:
        item = f"{call.tool} {_code(call.title)}" if call.title else call.tool
        if call.status is ToolCallStatus.ERROR:
            item += f" (failed: {call.error})" if call.error else " (failed)"
        elif call.status is ToolCallStatus.RUNNING:
            item += " (unfinished)"
        items.append(item)
    return "_Tool calls:_ " + " · ".join(items)


def render_markdown(
    conv: Conversation,
    messages: Sequence[Message],
    participant_names: Sequence[str],
    agent_names: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """``agent_names`` labels attachment owners (agent id -> name) in group discussions."""
    agent_names = agent_names or {}
    kind = "Direct chat" if conv.type is ConversationType.DIRECT else "Group discussion"
    lines = [f"# {conv.title}", ""]
    lines.append(f"- **Type:** {kind}")
    if conv.topic:
        lines.append(f"- **Topic:** {conv.topic}")
    if participant_names:
        lines.append(f"- **Participants:** {', '.join(participant_names)}")
    lines.append(f"- **Started:** {_stamp(conv.created_at)}")
    lines.append(f"- **Exported:** {_stamp(utcnow())}")
    lines.append("")
    for msg in messages:
        if msg.role is MessageRole.USER:
            label = "You"
        else:
            label = f"{msg.speaker_name} ({msg.provider_id}/{msg.model_id})"
            if msg.stage in (0, 3):
                label += " [coordinator]"
        lines += ["---", "", f"### {label} · {_stamp(msg.created_at)}", ""]
        if msg.content:
            lines += [msg.content.rstrip(), ""]
        if msg.attachments:
            lines += [_attachments_line(msg, conv, agent_names), ""]
        if msg.images:
            lines += ["_Images:_ " + ", ".join(_code(image.filename) for image in msg.images), ""]
        if msg.tool_calls:
            lines += [_tool_calls_line(msg), ""]
        if msg.status is MessageStatus.CANCELLED:
            lines += ["_[stopped before completion]_", ""]
        elif msg.status is MessageStatus.FAILED:
            lines += [f"_[failed: {msg.error or 'unknown error'}]_", ""]
        elif msg.status is MessageStatus.STREAMING:
            lines += ["_[unfinished]_", ""]
    return f"{_slug(conv.title)}.md", "\n".join(lines).rstrip() + "\n"
