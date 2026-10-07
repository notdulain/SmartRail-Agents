"""Prompt text: persona + framing as system text, unseen transcript as user text."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

from backend.contracts.models import (
    AgentSnapshot,
    Message,
    MessageRole,
    MessageStatus,
    ToolAccess,
)

# Hard cap on transcript text sent in one turn; the OpenCode session keeps older context.
MAX_TRANSCRIPT_CHARS = 24_000


def system_text(persona: str, brief: str, framing: str = "", tools: str = "") -> str:
    parts = [persona.strip()]
    if framing:
        parts.append(framing)
    if tools:
        parts.append(tools)
    if brief.strip():
        parts.append(
            "Shared project brief (the latest version, shared by every agent; "
            "private conversations stay separate):\n" + brief.strip()
        )
    return "\n\n".join(p for p in parts if p)


def tools_text(agent: AgentSnapshot) -> str:
    """What the agent may do with files, or "" when it has no file tools."""
    directory = agent.working_directory
    if agent.tool_access is ToolAccess.NONE or not directory:
        return ""
    if agent.tool_access is ToolAccess.READ_ONLY:
        allowed = (
            "You can read, list and search files there. Your access is read-only: do not try "
            "to create, edit or delete files."
        )
    else:
        allowed = "You can read, list, search, create and edit files there."
    return (
        f"File tools: your working directory is {directory}. {allowed} Stay inside this "
        "directory and never access paths outside it. Shell commands, web access and other "
        "tools are not available. Use files only when they help, and say which files you "
        "read or changed."
    )


def group_framing(
    speaker: str, participant_names: Sequence[str], topic: str, coordinator: bool
) -> str:
    roster = ", ".join(participant_names)
    lines = [
        f"You are {speaker} in a moderated group discussion. Topic: {topic}",
        f"Participants: {roster}.",
        "Reply only as yourself, never write other participants' turns, address others by "
        "name, and keep replies concise and conversational.",
    ]
    if coordinator:
        lines.append(
            "You are also the discussion coordinator: stay neutral, structure the discussion "
            "and do not answer the questions on the participants' behalf."
        )
    return "\n".join(lines)


def speaker_label(msg: Message) -> str:
    if msg.role is MessageRole.USER:
        return "User"
    if msg.stage in (0, 3):
        return f"{msg.speaker_name} (coordinator)"
    return msg.speaker_name


OMITTED = "[earlier messages omitted]"


def _attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;")


def render_attachments(files: Sequence[tuple[str, str | None, str]]) -> str:
    """Attached files as sent to the model: one ``<attached_file>`` block per
    (relative path, owning agent name or None, text)."""
    blocks = []
    for path, agent, text in files:
        attrs = f'path="{_attr(path)}"'
        if agent:
            attrs += f' agent="{_attr(agent)}"'
        body = text if text.endswith("\n") or not text else text + "\n"
        blocks.append(f"<attached_file {attrs}>\n{body}</attached_file>")
    return "\n\n".join(blocks)


def with_attachments(content: str, attachments_text: str | None) -> str:
    return f"{content}\n\n{attachments_text}" if attachments_text else content


def format_transcript(
    messages: Sequence[Message],
    attachments: Mapping[str, str] | None = None,
    keep: Collection[str] = (),
) -> str:
    """Render messages oldest-first, keeping the newest ones that fit the cap.

    ``attachments`` maps message IDs to their rendered attached files, shown with the
    message. Messages in ``keep`` (the current run's user message) are always included in
    full and do not count against the cap, so every participant sees the attached files.
    """
    attachments = attachments or {}
    blocks: list[tuple[str, str]] = []
    for msg in messages:
        if msg.status in (MessageStatus.STREAMING, MessageStatus.FAILED) and not msg.content:
            continue
        if not msg.content.strip() and not msg.images:
            continue
        block = f"[{speaker_label(msg)}]: {msg.content.strip() or '[Image attached]'}"
        blocks.append((msg.id, with_attachments(block, attachments.get(msg.id))))
    kept: list[str] = []
    total = 0
    capped = False
    for msg_id, block in reversed(blocks):
        if msg_id in keep:
            kept.append(block)
        elif not capped and (total == 0 or total + len(block) <= MAX_TRANSCRIPT_CHARS):
            kept.append(block)  # the newest message always fits
            total += len(block)
        else:
            capped = True
            if not kept or kept[-1] != OMITTED:
                kept.append(OMITTED)
    return "\n\n".join(reversed(kept))


def with_history(transcript: str, text: str) -> str:
    """Prefix a direct-chat turn with earlier messages for a session that has not seen them."""
    if not transcript:
        return text
    return f"Earlier messages in this conversation:\n{transcript}\n\nNew message:\n{text}"


def turn_text(task: str, topic: str, transcript: str) -> str:
    text = f"{task}\n\nTopic: {topic}"
    if transcript:
        text += f"\n\nDiscussion since your last turn:\n{transcript}"
    return text


def agenda_task(participant_names: Sequence[str]) -> str:
    return (
        "Open the discussion with a short agenda (three to five bullet points). "
        f"Contributors will speak in this order: {', '.join(participant_names)}."
    )


def contribution_task() -> str:
    return (
        "Round 1: give your own contribution to the topic and agenda from your role's perspective."
    )


def peer_reply_task(peer: str) -> str:
    return (
        f"Round 2: respond directly to {peer}'s round-1 contribution. Say what you agree with, "
        f"what you challenge and any question you have, addressing {peer} by name."
    )


def summary_task() -> str:
    return (
        "Summarize the discussion in three short sections: agreements, disagreements and "
        "unanswered questions."
    )
