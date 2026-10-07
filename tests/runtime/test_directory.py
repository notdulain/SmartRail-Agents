"""Sessions scoped to a working directory: every OpenCode call targets that directory's instance."""

from __future__ import annotations

import asyncio

import pytest

from backend.contracts.models import ErrorCode
from backend.contracts.runtime import Completed, Failed, TextDelta

from .conftest import request
from .fake_opencode import FakeOpenCode, stall_turn, text_turn

WORK = "/work/proj a"


async def collect(runtime, req):
    return [event async for event in runtime.stream(req)]


def session_calls(fake: FakeOpenCode):
    """Every call made for a turn (everything except catalog/health/session creation)."""
    return [
        c
        for c in fake.calls
        if c.path not in ("/provider", "/global/health")
        and not (c.method == "POST" and c.path == "/session")
    ]


async def test_create_session_with_directory_targets_that_instance(fake: FakeOpenCode, runtime):
    session = await runtime.create_session("chat", WORK)
    (create,) = fake.calls_to("POST", "/session")
    assert create.directory == WORK
    # created locked down; tool access is applied per turn
    assert create.body["permission"] == [{"permission": "*", "pattern": "*", "action": "deny"}]
    assert fake.session_dirs[session] == WORK


async def test_create_session_without_directory_sends_no_parameter(fake: FakeOpenCode, runtime):
    await runtime.create_session("chat")
    (create,) = fake.calls_to("POST", "/session")
    assert create.params == {}


async def test_every_call_of_a_turn_targets_the_session_directory(fake: FakeOpenCode, runtime):
    fake.default_behavior = text_turn(["in ", "dir"])
    session = await runtime.create_session("chat", WORK)
    events = await collect(runtime, request(session, directory=WORK))
    assert events[-1] == Completed("in dir", events[-1].usage)
    calls = session_calls(fake)
    assert {c.path.rsplit("/", 1)[-1] for c in calls} >= {"event", "prompt_async"}
    assert calls and all(c.directory == WORK for c in calls)


async def test_events_of_a_directory_session_only_reach_that_instance(fake: FakeOpenCode, runtime):
    # The fake (like OpenCode) delivers a directory session's events only to /event?directory=;
    # a subscription without it would never see the reply and the turn could not complete.
    fake.default_behavior = text_turn(["scoped"])
    session = await runtime.create_session("chat", WORK)
    events = await asyncio.wait_for(collect(runtime, request(session, directory=WORK)), 5)
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["scoped"]


async def test_reconnect_and_final_reads_target_the_session_directory(fake: FakeOpenCode, runtime):
    fake.default_behavior = text_turn(["one ", "two ", "three ", "four"], delay=0.02)
    fake.drop_event_streams_after = 5
    session = await runtime.create_session("chat", WORK)
    events = await asyncio.wait_for(collect(runtime, request(session, directory=WORK)), 10)
    assert events[-1].text == "one two three four"
    assert len(fake.calls_to("GET", "/event")) >= 2
    assert fake.calls_to("GET", "/session/status")
    calls = session_calls(fake)
    assert all(c.directory == WORK for c in calls)


async def test_abort_targets_the_session_directory(fake: FakeOpenCode, runtime):
    fake.default_behavior = stall_turn(["a"])
    session = await runtime.create_session("chat", WORK)
    stream = runtime.stream(request(session, directory=WORK))
    assert isinstance(await anext(stream), TextDelta)
    await stream.aclose()
    (abort,) = fake.aborts
    assert abort.directory == WORK


async def test_neutral_sessions_never_send_a_directory(fake: FakeOpenCode, runtime):
    fake.default_behavior = text_turn(["hi"])
    fake.drop_event_streams_after = 3  # exercise the reconnect reads too
    session = await runtime.create_session("chat")
    events = await asyncio.wait_for(collect(runtime, request(session)), 10)
    assert isinstance(events[-1], Completed)
    assert all(c.params.get("directory") is None for c in fake.calls)
    assert not fake.calls_to("GET", f"/session/{session}")  # no lookup either


async def test_directory_is_remembered_when_the_request_omits_it(fake: FakeOpenCode, runtime):
    session = await runtime.create_session("chat", WORK)
    await collect(runtime, request(session))  # directory=None
    assert fake.prompts[0].directory == WORK


async def test_unknown_session_directory_is_confirmed_once(fake: FakeOpenCode, runtime):
    # e.g. the app restarted: the runtime has never seen this session
    fake.add_session("ses_restarted", WORK)
    fake.default_behavior = text_turn(["back"])
    for _ in range(2):
        events = await collect(runtime, request("ses_restarted", directory=WORK))
        assert isinstance(events[-1], Completed)
    (lookup,) = fake.calls_to("GET", "/session/ses_restarted")
    assert lookup.directory == WORK
    assert all(c.directory == WORK for c in session_calls(fake))


@pytest.mark.parametrize("actual", ["/work/other", None])
async def test_a_different_directory_fails_without_prompting(fake: FakeOpenCode, runtime, actual):
    session = await runtime.create_session("chat", actual)
    events = await collect(runtime, request(session, directory=WORK))
    assert [type(e) for e in events] == [Failed]
    assert events[0].code is ErrorCode.RUNTIME_ERROR
    assert "working directory" in events[0].message
    assert not fake.prompts and not fake.aborts


async def test_a_restarted_session_in_another_directory_fails(fake: FakeOpenCode, runtime):
    fake.add_session("ses_moved", "/work/elsewhere")
    events = await collect(runtime, request("ses_moved", directory=WORK))
    assert [type(e) for e in events] == [Failed]
    assert not fake.prompts


async def test_concurrent_sessions_in_different_directories(fake: FakeOpenCode, runtime):
    fake.behaviors[("openai", "gpt-6-sol")] = text_turn(["alpha ", "one"], delay=0.02)
    fake.behaviors[("openrouter", "m")] = text_turn(["beta ", "two"], delay=0.015)
    s1 = await runtime.create_session("a", "/work/a")
    s2 = await runtime.create_session("b", "/work/b")
    first, second = await asyncio.gather(
        collect(runtime, request(s1, directory="/work/a")),
        collect(runtime, request(s2, provider="openrouter", model="m", directory="/work/b")),
    )
    assert first[-1].text == "alpha one" and second[-1].text == "beta two"
    prompts = {c.path: c.directory for c in fake.prompts}
    assert prompts == {
        f"/session/{s1}/prompt_async": "/work/a",
        f"/session/{s2}/prompt_async": "/work/b",
    }
