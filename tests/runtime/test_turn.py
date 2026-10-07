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


def test_deltas_after_a_reconnect_gap_are_not_trusted_until_cumulative_text():
    # Root cause of the old reconnect bug: "one " was lost while disconnected, so appending
    # the later deltas produced "two three four" and the final cumulative text was rejected.
    t = TurnTracker(S)
    out = feed_all(
        t,
        [
            user_message(S, "msg_u"),
            message_updated(S, assistant_info(S, "msg_a", "msg_u")),
            part_updated(S, "msg_a", "prt_a", "text"),
        ],
    )
    t.mark_gap()  # "one " is published while we reconnect
    out += feed_all(
        t,
        [
            delta(S, "msg_a", "prt_a", "two "),
            delta(S, "msg_a", "prt_a", "three"),
            part_updated(S, "msg_a", "prt_a", "text", "one two three"),
            # a later part streams normally again
            part_updated(S, "msg_a", "prt_b", "text"),
            delta(S, "msg_a", "prt_b", "!"),
        ],
    )
    assert out == ["one two three", "!"]
    assert t.text == "one two three!"


def test_live_cumulative_text_resynchronises_a_gapped_part():
    t = TurnTracker(S)
    feed_all(
        t,
        [
            user_message(S, "msg_u"),
            message_updated(S, assistant_info(S, "msg_a", "msg_u")),
            part_updated(S, "msg_a", "prt_a", "text"),
            delta(S, "msg_a", "prt_a", "ab"),
        ],
    )
    t.mark_gap()
    out = feed_all(
        t,
        [
            part_updated(S, "msg_a", "prt_a", "text", "abcd"),
            delta(S, "msg_a", "prt_a", "ef"),  # follows "abcd" on the same connection
        ],
    )
    assert out == ["cd", "ef"]
    assert t.text == "abcdef"


def test_deltas_of_a_part_whose_start_was_missed_never_duplicate_text():
    # The part's start event was lost; its deltas arrive first, then the cumulative text.
    t = TurnTracker(S)
    out = feed_all(
        t,
        [
            user_message(S, "msg_u"),
            message_updated(S, assistant_info(S, "msg_a", "msg_u")),
            delta(S, "msg_a", "prt_a", "three "),
            delta(S, "msg_a", "prt_a", "four"),
            part_updated(S, "msg_a", "prt_a", "text", "one two three four"),
        ],
    )
    assert out == ["one two three four"]
    assert t.text == "one two three four"


def test_snapshot_parts_are_not_extended_by_unordered_deltas():
    t = TurnTracker(S)
    feed_all(t, [user_message(S, "msg_u"), message_updated(S, assistant_info(S, "msg_a", "msg_u"))])
    t.mark_gap()
    snapshot = [
        {
            "info": assistant_info(S, "msg_a", "msg_u"),
            "parts": [{"id": "prt_a", "messageID": "msg_a", "type": "text", "text": "abc"}],
        }
    ]
    out = t.apply_snapshot(snapshot)
    # this delta may already be inside the snapshot text ("c") or not: it cannot be placed
    out += feed_all(t, [delta(S, "msg_a", "prt_a", "c"), part_updated(S, "msg_a", "prt_a", "text")])
    out += feed_all(t, [part_updated(S, "msg_a", "prt_a", "text", "abcd")])
    assert out == ["abc", "d"]
    assert t.text == "abcd"


def test_held_events_replayed_across_a_gap_stay_gap_aware():
    t = TurnTracker(S)
    feed_all(
        t,
        [
            user_message(S, "msg_u"),
            part_updated(S, "msg_a", "prt_a", "text"),
            delta(S, "msg_a", "prt_a", "one "),
        ],
    )
    t.mark_gap()  # "two " lost
    out = feed_all(
        t,
        [
            delta(S, "msg_a", "prt_a", "three"),
            message_updated(S, assistant_info(S, "msg_a", "msg_u")),
            part_updated(S, "msg_a", "prt_a", "text", "one two three"),
        ],
    )
    assert out == ["one ", "two three"]
