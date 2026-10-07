"""Prompt text: persona + framing as system text, unseen transcript as user text."""

from __future__ import annotations

from collections.abc import Sequence

from backend.contracts.models import Message, MessageRole, MessageStatus

# Hard cap on transcript text sent in one turn; the OpenCode session keeps older context.
MAX_TRANSCRIPT_CHARS = 24_000


def system_text(persona: str, brief: str, framing: str = "") -> str:
    parts = [persona.strip()]
    if framing:
        parts.append(framing)
    if brief.strip():
        parts.append(
            "Shared project brief (the latest version, shared by every agent; "
            "private conversations stay separate):\n" + brief.strip()
        )
    return "\n\n".join(p for p in parts if p)


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


def format_transcript(messages: Sequence[Message]) -> str:
    """Render messages oldest-first, keeping the newest ones that fit the cap."""
    blocks: list[str] = []
    for msg in messages:
        if msg.status in (MessageStatus.STREAMING, MessageStatus.FAILED) and not msg.content:
            continue
        if not msg.content.strip():
            continue
        blocks.append(f"[{speaker_label(msg)}]: {msg.content.strip()}")
    kept: list[str] = []
    total = 0
    for block in reversed(blocks):
        if total + len(block) > MAX_TRANSCRIPT_CHARS and kept:
            kept.append("[earlier messages omitted]")
            break
        kept.append(block)
        total += len(block)
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
