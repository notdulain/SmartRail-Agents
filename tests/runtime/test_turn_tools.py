"""TurnTracker: tool parts become ToolActivity (pure, no I/O)."""

from __future__ import annotations

import pytest

from backend.contracts.models import ToolCallStatus
from backend.contracts.runtime import TextDelta, ToolActivity
from backend.runtime.turn import TITLE_MAX, TurnTracker, tool_error, tool_title

from .fake_opencode import (
    assistant_info,
    delta,
    message_updated,
    part_updated,
    tool_part,
    tool_state,
    tool_updated,
    user_message,
)

S = "ses_1"
RUNNING, COMPLETED, ERROR = ToolCallStatus.RUNNING, ToolCallStatus.COMPLETED, ToolCallStatus.ERROR


def started(directory: str | None = None) -> TurnTracker:
    t = TurnTracker(S, directory)
    t.feed(user_message(S, "msg_u"))
    t.feed(message_updated(S, assistant_info(S, "msg_a", "msg_u")))
    return t


def update(t: TurnTracker, status_: str, tool="read", input_=None, pid="prt_t", **kw):
    part = tool_part(S, "msg_a", pid, tool, tool_state(status_, input_, **kw))
    return t.feed(tool_updated(S, part))


def calls(events):
    assert all(isinstance(e, ToolActivity) for e in events)
    return [(e.call.status, e.call.title, e.call.error) for e in events]


def test_status_changes_are_reported_once_and_pending_is_not():
    t = started()
    data = {"filePath": "notes.txt"}
    out = update(t, "pending")
    assert out == [] and t.tools_running
    out += update(t, "running", input_=data)
    out += update(t, "running", input_=data)  # repeated update
    assert t.tools_running
    out += update(t, "completed", input_=data, title="Users/x/notes.txt", output="SECRET")
    out += update(t, "completed", input_=data, title="Users/x/notes.txt", output="SECRET")
    assert calls(out) == [(RUNNING, "notes.txt", None), (COMPLETED, "notes.txt", None)]
    assert {e.call.id for e in out} == {"prt_t"}
    assert out[0].call.tool == "read"
    assert not t.tools_running
    assert t.text == ""  # tool output never becomes text


def test_a_final_status_never_goes_back_to_running():
    t = started()
    update(t, "running", input_={"filePath": "a"})
    update(t, "error", input_={"filePath": "a"}, error="boom")
    assert update(t, "running", input_={"filePath": "a"}) == []
    assert update(t, "completed", input_={"filePath": "a"}) == []


def test_a_call_first_seen_completed_is_reported_completed():
    t = started()
    out = update(t, "completed", tool="glob", input_={"pattern": "*.py"})
    assert calls(out) == [(COMPLETED, "*.py", None)]


def test_tool_parts_before_their_message_are_held_then_replayed():
    t = TurnTracker(S)
    t.feed(user_message(S, "msg_u"))
    part = tool_part(S, "msg_a", "prt_t", "read", tool_state("running", {"filePath": "x"}))
    assert t.feed(tool_updated(S, part)) == []
    out = t.feed(message_updated(S, assistant_info(S, "msg_a", "msg_u")))
    assert calls(out) == [(RUNNING, "x", None)]


def test_tool_parts_of_other_turns_are_ignored():
    t = started()
    old = tool_part(S, "msg_old", "prt_o", "read", tool_state("running", {"filePath": "x"}))
    t.feed(message_updated(S, assistant_info(S, "msg_old", "msg_older")))
    assert t.feed(tool_updated(S, old)) == []


def test_snapshot_reports_missed_tool_states():
    t = started()
    update(t, "running", input_={"filePath": "a"})
    snapshot = [
        {"info": {"id": "msg_u", "role": "user"}, "parts": []},
        {
            "info": assistant_info(S, "msg_a", "msg_u"),
            "parts": [
                tool_part(S, "msg_a", "prt_t", "read", tool_state("completed", {"filePath": "a"}))
            ],
        },
    ]
    assert calls(t.apply_snapshot(snapshot)) == [(COMPLETED, "a", None)]
    assert t.apply_snapshot(snapshot) == []


def test_interrupt_reports_running_calls_once():
    t = started()
    update(t, "running", input_={"filePath": "a"}, pid="prt_1")
    update(t, "completed", input_={"filePath": "b"}, pid="prt_2")
    assert calls(t.interrupt_tools()) == [(ERROR, "a", "Interrupted")]
    assert t.interrupt_tools() == []
    assert update(t, "completed", input_={"filePath": "a"}, pid="prt_1") == []


def test_text_after_tools_is_a_new_paragraph():
    t = started()
    out = t.feed(part_updated(S, "msg_a", "prt_1", "text"))
    out += t.feed(delta(S, "msg_a", "prt_1", "Let me check."))
    out += update(t, "completed", input_={"filePath": "a"})
    out += t.feed(part_updated(S, "msg_a", "prt_2", "text"))
    out += t.feed(delta(S, "msg_a", "prt_2", "Done."))
    assert [e.text for e in out if isinstance(e, TextDelta)] == ["Let me check.", "\n\nDone."]
    assert t.text == "Let me check.\n\nDone."


# ----------------------------------------------------------------------------- titles / errors


@pytest.mark.parametrize(
    ("tool", "data", "title"),
    [
        ("read", {"filePath": "src/app.py", "offset": 10}, "src/app.py"),
        ("read", {"filePath": "./src/../src/app.py"}, "src/app.py"),
        ("read", {"filePath": "."}, "."),
        ("write", {"filePath": "out.md", "content": "SECRET"}, "out.md"),
        ("edit", {"filePath": "a.md", "oldString": "SECRET", "newString": "SECRET"}, "a.md"),
        ("glob", {"pattern": "**/*.ts"}, "**/*.ts"),
        ("glob", {"pattern": "*.ts", "path": "src"}, "*.ts in src"),
        ("grep", {"pattern": "TODO", "include": "*.py", "path": "lib"}, "TODO (*.py) in lib"),
        (
            "apply_patch",
            {"patchText": "*** Begin Patch\n*** Delete File: old.txt\n*** End Patch"},
            "old.txt",
        ),
        ("apply_patch", {"patchText": "garbage"}, "apply_patch"),
        ("read", {}, "read"),
        ("bash", {"command": "rm -rf /"}, "bash"),
    ],
)
def test_titles_summarise_the_input(tool, data, title):
    assert tool_title(tool, data, None) == title


def test_title_falls_back_to_opencode_title_for_other_tools_and_is_capped():
    assert (
        tool_title("webfetch", {"url": "x"}, None, "https://example.com") == "https://example.com"
    )
    long = tool_title("read", {"filePath": "d/" * 300 + "end.txt"}, None)
    assert len(long) == TITLE_MAX and long.startswith("...") and long.endswith("end.txt")
    assert "\n" not in tool_title("grep", {"pattern": "a\nb"}, None)


def test_errors_are_short_and_never_list_permission_rules():
    rules = (
        "The user has specified a rule which prevents you from using this specific tool call. "
        "Here are some of the relevant rules [" + "{}," * 500 + "]"
    )
    assert tool_error(rules, "read", {"filePath": "../x"}, None) == (
        "Not allowed: outside the working directory"
    )
    assert tool_error(rules, "edit", {"filePath": "a.txt"}, None) == "Not allowed for this agent"
    unavailable = (
        "Model tried to call unavailable tool 'invalid'. Available tools: glob, grep, read."
    )
    assert tool_error(unavailable, "bash", {}, None) == "Tool not available to this agent"
    long = tool_error("x" * 1000, "read", {}, None)
    assert len(long) == TITLE_MAX and long.endswith("...")
    assert tool_error(None, "read", {}, None) == "Tool failed"
    assert tool_error("line one\nline two", "read", {}, None) == "line one line two"
