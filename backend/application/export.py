"""Markdown transcript rendering (in memory only; generated on explicit request)."""

from __future__ import annotations

import re
from collections.abc import Sequence

from backend.contracts.models import (
    Conversation,
    ConversationType,
    Message,
    MessageRole,
    MessageStatus,
)

from .util import utcnow


def _stamp(moment) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC")


def _slug(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()
    return (slug or "transcript")[:60]


def render_markdown(
    conv: Conversation, messages: Sequence[Message], participant_names: Sequence[str]
) -> tuple[str, str]:
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
        if msg.status is MessageStatus.CANCELLED:
            lines += ["_[stopped before completion]_", ""]
        elif msg.status is MessageStatus.FAILED:
            lines += [f"_[failed: {msg.error or 'unknown error'}]_", ""]
        elif msg.status is MessageStatus.STREAMING:
            lines += ["_[unfinished]_", ""]
    return f"{_slug(conv.title)}.md", "\n".join(lines).rstrip() + "\n"
