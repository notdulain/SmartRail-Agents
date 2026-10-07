"""Tool turns end to end against the fake OpenCode: ToolActivity events, text, limits."""

from __future__ import annotations

import asyncio
import os

from backend.contracts.models import ErrorCode, ToolAccess, ToolCallStatus
from backend.contracts.runtime import Completed, Failed, TextDelta, ToolActivity
from backend.runtime.opencode import HttpOpenCodeRuntime

from .conftest import make_config, request
from .fake_opencode import (
    FakeOpenCode,
    ToolStep,
    assistant_info,
    delta,
    message_updated,
    part_updated,
    status,
    tool_part,
    tool_state,
    tool_turn,
    tool_updated,
    user_message,
)

WORK = "/work/proj a"
DENIED = (
    "The user has specified a rule which prevents you from using this specific tool call. "
    'Here are some of the relevant rules [{"permission":"*","action":"allow","pattern":"*"}]'
)
SECRET = "SECRET FILE CONTENT"


async def collect(runtime, req):
    return [event async for event in runtime.stream(req)]


def tool_events(events):
    return [
        (e.call.tool, e.call.status, e.call.title, e.call.error)
        for e in events
        if isinstance(e, ToolActivity)
    ]


def text_of(events):
    return "".join(e.text for e in events if isinstance(e, TextDelta))


async def tool_session(runtime, access=ToolAccess.READ_ONLY):
    session = await runtime.create_session("chat", WORK)
    return session, request(session, directory=WORK, access=access)


async def test_tool_calls_are_reported_once_per_status_change(fake: FakeOpenCode, runtime):
    fake.default_behavior = tool_turn(
        [
            [ToolStep("read", {"filePath": "notes.txt"}, output=SECRET)],
            [ToolStep("grep", {"pattern": "TODO", "path": ".."}, "error", error=DENIED)],
        ],
        ["Found ", "it."],
    )
    session, req = await tool_session(runtime)
    events = await collect(runtime, req)

    running, completed, error = (
        ToolCallStatus.RUNNING,
        ToolCallStatus.COMPLETED,
        ToolCallStatus.ERROR,
    )
    assert tool_events(events) == [
        ("read", running, "notes.txt", None),
        ("read", completed, "notes.txt", None),
        ("grep", running, "TODO in ..", None),
        ("grep", error, "TODO in ..", "Not allowed: outside the working directory"),
    ]
    calls = [e.call for e in events if isinstance(e, ToolActivity)]
    assert calls[0].id == calls[1].id != calls[2].id == calls[3].id
    assert isinstance(events[-1], Completed)
    assert events[-1].text == "Found it." == text_of(events)


async def test_tool_parts_and_contents_never_reach_text_or_titles(fake: FakeOpenCode, runtime):
    fake.default_behavior = tool_turn(
        [
            [
                ToolStep("read", {"filePath": "a.txt"}, output=SECRET, title=SECRET),
                ToolStep("write", {"filePath": "b.txt", "content": SECRET}, output="Wrote"),
                ToolStep(
                    "edit",
                    {"filePath": "c.txt", "oldString": SECRET, "newString": SECRET},
                    "error",
                    error="oldString not found in content",
                ),
            ]
        ],
        ["done"],
    )
    session, req = await tool_session(runtime, ToolAccess.READ_WRITE)
    events = await collect(runtime, req)
    assert events[-1].text == "done"
    for event in events:
        if isinstance(event, TextDelta):
            assert SECRET not in event.text
        if isinstance(event, ToolActivity):
            assert SECRET not in event.call.title and SECRET not in (event.call.error or "")
    assert {e.call.title for e in events if isinstance(e, ToolActivity)} == {
        "a.txt",
        "b.txt",
        "c.txt",
    }


async def test_multi_step_turn_keeps_all_reply_text_and_sums_usage(fake: FakeOpenCode, runtime):
    fake.default_behavior = tool_turn(
        [[ToolStep("glob", {"pattern": "*.md"})], [ToolStep("read", {"filePath": "a.md"})]],
        ["All ", "good."],
        step_text=["Let me look.", ""],
        tokens=(10, 2, 1),
    )
    session, req = await tool_session(runtime)
    events = await collect(runtime, req)
    done = events[-1]
    assert isinstance(done, Completed)
    assert done.text == "Let me look.\n\nAll good." == text_of(events)
    # three assistant messages (two tool steps and the reply), all answering our prompt
    assert (done.usage.input_tokens, done.usage.output_tokens) == (30, 9)
    # text and tool activity arrive in the order they happened
    kinds = [type(e).__name__ for e in events]
    assert kinds.index("ToolActivity") > kinds.index("TextDelta")


async def test_a_turn_with_only_tool_calls_fails_as_empty(fake: FakeOpenCode, runtime):
    fake.default_behavior = tool_turn([[ToolStep("read", {"filePath": "a"})]], [""])
    session, req = await tool_session(runtime)
    events = await collect(runtime, req)
    assert isinstance(events[-1], Failed) and "no text" in events[-1].message
    assert len(tool_events(events)) == 2


async def test_tool_activity_counts_as_progress_for_the_stall_timeout(fake: FakeOpenCode):
    rt = HttpOpenCodeRuntime(make_config(fake.url), stall_timeout=0.3)
    try:
        # ~1.2 s of tool work without any text, but never 0.3 s without an event
        steps = [[ToolStep("read", {"filePath": f"f{i}.txt"})] for i in range(6)]
        fake.default_behavior = tool_turn(steps, ["finally"], delay=0.05)
        session = await rt.create_session("chat", WORK)
        events = await asyncio.wait_for(
            collect(rt, request(session, directory=WORK, access=ToolAccess.READ_ONLY)), 10
        )
        assert isinstance(events[-1], Completed) and events[-1].text == "finally"
        assert len(tool_events(events)) == 12
        assert not fake.aborts
    finally:
        await rt.aclose()


async def test_a_tool_that_never_finishes_is_reported_interrupted(fake: FakeOpenCode):
    rt = HttpOpenCodeRuntime(make_config(fake.url), stall_timeout=0.3)
    try:
        fake.default_behavior = tool_turn(
            [[ToolStep("grep", {"pattern": "x"}, hold=60)]], ["never"]
        )
        session = await rt.create_session("chat", WORK)

        async def heartbeat():
            while True:
                fake.publish({"id": "evt_hb", "type": "server.heartbeat", "properties": {}})
                await asyncio.sleep(0.1)

        beat = asyncio.create_task(heartbeat())
        try:
            events = await asyncio.wait_for(
                collect(rt, request(session, directory=WORK, access=ToolAccess.READ_ONLY)), 5
            )
        finally:
            beat.cancel()
        assert [(t, s) for t, s, _, _ in tool_events(events)] == [
            ("grep", ToolCallStatus.RUNNING),
            ("grep", ToolCallStatus.ERROR),
        ]
        assert tool_events(events)[-1][3] == "Interrupted"
        assert isinstance(events[-1], Failed) and events[-1].code is ErrorCode.RUNTIME_ERROR
        assert len(fake.aborts) == 1
    finally:
        await rt.aclose()


async def test_output_cap_waits_for_a_running_tool(fake: FakeOpenCode, runtime):
    aborts_before_the_write_finished: list[int] = []

    async def behavior(ctx):
        # A write starts; while it runs the model keeps streaming text past the cap.
        sid, mid = ctx.session_id, ctx.assistant_id
        write = {"filePath": "big.txt", "content": "..."}
        pid, tid = "prt_text", "prt_write"
        await ctx.publish(
            user_message(sid, ctx.user_id),
            status(sid, "busy"),
            message_updated(sid, assistant_info(sid, mid, ctx.user_id)),
            tool_updated(sid, tool_part(sid, mid, tid, "write", tool_state("pending"))),
            tool_updated(sid, tool_part(sid, mid, tid, "write", tool_state("running", write))),
            part_updated(sid, mid, pid, "text"),
            delta(sid, mid, pid, "x" * 200),
        )
        await asyncio.sleep(0.3)
        aborts_before_the_write_finished.append(len(fake.aborts))
        done = tool_state("completed", write, output="Wrote file successfully.")
        await ctx.publish(tool_updated(sid, tool_part(sid, mid, tid, "write", done)))
        await asyncio.sleep(0.05)
        await ctx.publish(delta(sid, mid, pid, "never seen"))  # aborted before this
        await asyncio.Event().wait()

    fake.default_behavior = behavior
    session, _ = await tool_session(runtime, ToolAccess.READ_WRITE)
    req = request(session, directory=WORK, access=ToolAccess.READ_WRITE, max_tokens=10)
    events = await asyncio.wait_for(collect(runtime, req), 5)
    assert aborts_before_the_write_finished == [0]
    statuses = [s for _, s, _, _ in tool_events(events)]
    assert statuses == [ToolCallStatus.RUNNING, ToolCallStatus.COMPLETED]
    assert isinstance(events[-1], Completed)
    assert events[-1].text == "x" * 200
    assert len(fake.aborts) == 1


async def test_reconnect_during_a_tool_turn(fake: FakeOpenCode, runtime):
    steps = [
        [ToolStep("read", {"filePath": f"f{i}.txt"}, duplicate_updates=False)] for i in range(3)
    ]
    fake.default_behavior = tool_turn(steps, ["one ", "two ", "three"], delay=0.02)
    fake.drop_event_streams_after = 7
    session, req = await tool_session(runtime)
    events = await asyncio.wait_for(collect(runtime, req), 10)
    assert len(fake.calls_to("GET", "/event")) >= 2
    assert len(fake.prompts) == 1
    done = events[-1]
    assert isinstance(done, Completed)
    assert done.text == "one two three" == text_of(events)
    # every call ends completed exactly once and never goes back to running
    by_id: dict[str, list[ToolCallStatus]] = {}
    for e in events:
        if isinstance(e, ToolActivity):
            by_id.setdefault(e.call.id, []).append(e.call.status)
    assert len(by_id) == 3
    for statuses in by_id.values():
        assert statuses[-1] is ToolCallStatus.COMPLETED
        assert statuses.count(ToolCallStatus.COMPLETED) == 1
        assert len(statuses) == len(set(statuses))


ABS_WORK = os.path.abspath("some-project")
ABS_OUTSIDE = os.path.join(os.path.dirname(ABS_WORK), "elsewhere.txt")
ABS_INSIDE = os.path.join(ABS_WORK, "docs", "a.md")
PATCH = "\n".join(
    [
        "*** Begin Patch",
        "*** Add File: src/new.py",
        "+x",
        f"*** Update File: {os.path.join(ABS_WORK, 'README.md')}",
        "@@",
        "-a",
        "+b",
        "*** End Patch",
    ]
)


async def test_apply_patch_and_absolute_paths_get_relative_titles(fake: FakeOpenCode, runtime):
    fake.default_behavior = tool_turn(
        [
            [
                ToolStep("apply_patch", {"patchText": PATCH}),
                ToolStep("read", {"filePath": ABS_INSIDE}),
                ToolStep("read", {"filePath": ABS_OUTSIDE}, "error", error=DENIED),
            ]
        ],
        ["ok"],
    )
    session = await runtime.create_session("chat", ABS_WORK)
    req = request(session, directory=ABS_WORK, access=ToolAccess.READ_WRITE)
    events = await collect(runtime, req)
    finished = [(title, error) for _, s, title, error in tool_events(events) if s != "running"]
    assert finished == [
        ("src/new.py, README.md", None),
        ("docs/a.md", None),
        (ABS_OUTSIDE, "Not allowed: outside the working directory"),
    ]
