"""TurnTracker: pure event-following logic."""

from __future__ import annotations

from backend.runtime.turn import TurnTracker

from .fake_opencode import (
    assistant_info,
    delta,
    idle,
    message_updated,
    part_updated,
    session_error,
    status,
    user_message,
)

S = "ses_1"


def feed_all(tracker, events):
    out: list[str] = []
    for item in events:
        out += tracker.feed(item)
    return out


def test_follows_only_the_assistant_answering_our_user_message():
    t = TurnTracker(S)
    out = feed_all(
        t,
        [
            user_message(S, "msg_u"),
            message_updated(S, assistant_info(S, "msg_old", "msg_older")),  # earlier turn
            part_updated(S, "msg_old", "prt_old", "text", "stale"),
            delta(S, "msg_old", "prt_old", "STALE"),
            message_updated(S, assistant_info(S, "msg_a", "msg_u")),
            part_updated(S, "msg_a", "prt_a", "text"),
            delta(S, "msg_a", "prt_a", "fresh"),
        ],
    )
    assert out == ["fresh"]
    assert t.assistant_ids == ["msg_a"]


def test_part_update_after_deltas_does_not_duplicate():
    t = TurnTracker(S)
    out = feed_all(
        t,
        [
            user_message(S, "msg_u"),
            message_updated(S, assistant_info(S, "msg_a", "msg_u")),
            part_updated(S, "msg_a", "prt_a", "text"),
            delta(S, "msg_a", "prt_a", "ab"),
            delta(S, "msg_a", "prt_a", "cd"),
            part_updated(S, "msg_a", "prt_a", "text", "abcd"),
            part_updated(S, "msg_a", "prt_a", "text", "abcdef"),  # two chars never streamed
        ],
    )
    assert out == ["ab", "cd", "ef"]
    assert t.text == "abcdef"


def test_events_arriving_before_their_message_are_held_then_replayed():
    t = TurnTracker(S)
    out = feed_all(
        t,
        [
            user_message(S, "msg_u"),
            part_updated(S, "msg_a", "prt_a", "text"),
            delta(S, "msg_a", "prt_a", "early"),
            message_updated(S, assistant_info(S, "msg_a", "msg_u")),
        ],
    )
    assert out == ["early"]


def test_reasoning_and_summary_messages_are_ignored():
    t = TurnTracker(S)
    out = feed_all(
        t,
        [
            user_message(S, "msg_u"),
            message_updated(S, assistant_info(S, "msg_c", "msg_u", summary=True)),
            part_updated(S, "msg_c", "prt_c", "text", "summary text"),
            message_updated(S, assistant_info(S, "msg_a", "msg_u")),
            part_updated(S, "msg_a", "prt_r", "reasoning"),
            delta(S, "msg_a", "prt_r", "thinking"),
            part_updated(S, "msg_a", "prt_t", "text"),
            delta(S, "msg_a", "prt_t", "visible"),
        ],
    )
    assert out == ["visible"]


def test_idle_before_anything_started_is_not_terminal():
    t = TurnTracker(S)
    t.feed(status(S, "idle"))
    t.feed(idle(S))
    assert not t.idle
    t.feed(user_message(S, "msg_u"))
    t.feed(idle(S))
    assert t.idle


def test_abort_is_not_a_failure_but_other_errors_are():
    t = TurnTracker(S)
    t.feed(user_message(S, "msg_u"))
    t.feed(session_error(S, {"name": "MessageAbortedError", "data": {"message": "Aborted"}}))
    assert t.aborted and t.failure() is None
    t.feed(session_error(S, {"name": "UnknownError", "data": {"message": "x"}}))
    assert t.failure() is not None


def test_snapshot_recovers_text_and_usage():
    t = TurnTracker(S)
    messages = [
        {"info": {"id": "msg_u", "role": "user"}, "parts": []},
        {
            "info": assistant_info(S, "msg_a", "msg_u", tokens=(5, 7, 3), cost=0.5, completed=True),
            "parts": [{"id": "prt", "messageID": "msg_a", "type": "text", "text": "whole"}],
        },
    ]
    assert t.apply_snapshot(messages) == ["whole"]
    assert t.apply_snapshot(messages) == []  # idempotent
    usage = t.usage()
    assert (usage.input_tokens, usage.output_tokens) == (5, 10)
    assert t.reported_cost() == 0.5
