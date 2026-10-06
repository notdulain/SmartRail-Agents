from __future__ import annotations

import asyncio
import base64

import pytest

from backend.contracts.models import ErrorCode
from backend.contracts.runtime import Completed, Failed, TextDelta
from backend.runtime.opencode import HttpOpenCodeRuntime

from .conftest import make_config, request
from .fake_opencode import (
    PASSWORD,
    USERNAME,
    FakeOpenCode,
    api_error,
    delta,
    error_turn,
    idle,
    retry_turn,
    session_error,
    stall_turn,
    text_turn,
)


async def collect(runtime, req):
    return [event async for event in runtime.stream(req)]


def deltas(events):
    return [e.text for e in events if isinstance(e, TextDelta)]


# ----------------------------------------------------------------------------- happy path


async def test_streams_deltas_then_one_completed_with_usage(fake: FakeOpenCode, runtime):
    fake.default_behavior = text_turn(["Hel", "lo ", "world"], tokens=(21, 5, 2), cost=0.5)
    session = await runtime.create_session("chat")
    events = await collect(runtime, request(session))

    assert deltas(events) == ["Hel", "lo ", "world"]
    assert [type(e) for e in events] == [TextDelta, TextDelta, TextDelta, Completed]
    done = events[-1]
    assert done.text == "Hello world"
    # output tokens include reasoning tokens (they are billed as output)
    assert (done.usage.input_tokens, done.usage.output_tokens) == (21, 7)
    assert done.usage.cost_usd is None  # only OpenRouter calls carry a cost


async def test_prompt_body_model_system_text_agent_and_basic_auth(fake: FakeOpenCode, runtime):
    session = await runtime.create_session("chat")
    await collect(
        runtime,
        request(session, system="Be Pete.", text="ahoy", provider="openai", model="gpt-6-sol"),
    )
    (prompt,) = fake.prompts
    assert prompt.path == f"/session/{session}/prompt_async"
    assert prompt.body == {
        "agent": "smartrail",
        "model": {"providerID": "openai", "modelID": "gpt-6-sol"},
        "system": "Be Pete.",
        "parts": [{"type": "text", "text": "ahoy"}],
    }
    expected = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
    assert fake.calls and all(c.authorization == expected for c in fake.calls)


async def test_empty_system_is_omitted(fake: FakeOpenCode, runtime):
    session = await runtime.create_session("chat")
    await collect(runtime, request(session, system=""))
    assert "system" not in fake.prompts[0].body


async def test_subscription_is_live_before_the_prompt_is_sent(fake: FakeOpenCode, runtime):
    # The fake publishes its first event inside the prompt request itself; if the SSE
    # subscription were not established first, that delta would be lost.
    fake.default_behavior = text_turn(["first", " second"])
    session = await runtime.create_session("chat")
    events = await collect(runtime, request(session))
    assert fake.connect_order[:2] == ["event", "prompt"]
    assert deltas(events) == ["first", " second"]


async def test_wrong_password_is_provider_unavailable(fake: FakeOpenCode):
    rt = HttpOpenCodeRuntime(make_config(fake.url, password="wrong"))
    try:
        assert await rt.health() is False
        events = await collect(rt, request("ses_x"))
        assert [type(e) for e in events] == [Failed]
        assert events[0].code is ErrorCode.PROVIDER_UNAVAILABLE
        assert not fake.prompts
    finally:
        await rt.aclose()


async def test_reasoning_and_compaction_summaries_are_never_surfaced(fake: FakeOpenCode, runtime):
    fake.default_behavior = text_turn(["answer"], reasoning=["secret ", "thoughts"])
    session = await runtime.create_session("chat")
    events = await collect(runtime, request(session))
    assert deltas(events) == ["answer"]
    assert events[-1].text == "answer"


async def test_missed_deltas_are_recovered_from_the_final_message(fake: FakeOpenCode, runtime):
    # Deltas never arrive (as with a late subscription); part.updated/final fetch carry the text.
    fake.default_behavior = text_turn(["Hel", "lo"], skip_deltas=True)
    session = await runtime.create_session("chat")
    events = await collect(runtime, request(session))
    assert "".join(deltas(events)) == "Hello"
    assert events[-1].text == "Hello"
    assert sum(isinstance(e, Completed) for e in events) == 1


async def test_openrouter_reports_cost_other_providers_do_not(fake: FakeOpenCode, runtime):
    fake.behaviors[("openrouter", "deepseek/deepseek-chat")] = text_turn(["ok"], cost=0.0123)
    fake.behaviors[("openai", "gpt-6-sol")] = text_turn(["ok"], cost=0.5)
    s1 = await runtime.create_session("a")
    s2 = await runtime.create_session("b")
    routed = (
        await collect(runtime, request(s1, provider="openrouter", model="deepseek/deepseek-chat"))
    )[-1]
    plain = (await collect(runtime, request(s2)))[-1]
    assert routed.usage.cost_usd == pytest.approx(0.0123)
    assert plain.usage.cost_usd is None


async def test_openrouter_without_reported_cost_has_none(fake: FakeOpenCode, runtime):
    fake.behaviors[("openrouter", "m")] = text_turn(["ok"], cost=None)
    session = await runtime.create_session("a")
    done = (await collect(runtime, request(session, provider="openrouter", model="m")))[-1]
    assert done.usage.cost_usd is None


async def test_max_output_tokens_is_enforced_by_aborting(fake: FakeOpenCode, runtime):
    fake.default_behavior = stall_turn(["x" * 8] * 50)  # never finishes by itself
    session = await runtime.create_session("chat")
    events = await collect(runtime, request(session, max_tokens=10))  # ~40 chars
    assert isinstance(events[-1], Completed)
    assert 40 <= len(events[-1].text) < 400
    assert events[-1].text == "".join(deltas(events))
    assert events[-1].usage.output_tokens >= 10
    assert len(fake.aborts) == 1


async def test_second_stream_in_the_same_session_is_refused(fake: FakeOpenCode, runtime):
    fake.default_behavior = stall_turn(["a"])
    session = await runtime.create_session("chat")
    first = runtime.stream(request(session))
    assert isinstance(await anext(first), TextDelta)
    second = await collect(runtime, request(session))
    assert [type(e) for e in second] == [Failed]
    assert second[0].code is ErrorCode.RUNTIME_ERROR
    await first.aclose()
    assert len(fake.prompts) == 1


async def test_create_session_sends_non_default_title_and_deny_all(fake: FakeOpenCode, runtime):
    await runtime.create_session("My chat")
    await runtime.create_session("   ")
    first, second = (c.body for c in fake.calls_to("POST", "/session"))
    assert first["title"] == "My chat" and second["title"] == "SmartRail chat"
    assert first["agent"] == "smartrail"
    assert first["permission"] == [{"permission": "*", "pattern": "*", "action": "deny"}]


# ----------------------------------------------------------------------------- error mapping


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (api_error(429, "Rate limit reached for gpt"), ErrorCode.RATE_LIMITED),
        (api_error(401, "Incorrect API key provided"), ErrorCode.PROVIDER_UNAVAILABLE),
        (api_error(403, "forbidden"), ErrorCode.PROVIDER_UNAVAILABLE),
        (api_error(404, "The model `x` does not exist"), ErrorCode.MODEL_UNAVAILABLE),
        (
            api_error(
                400, "The 'gpt-x' model is not supported when using Codex with a ChatGPT account"
            ),
            ErrorCode.MODEL_UNAVAILABLE,
        ),
        (api_error(500, "boom"), ErrorCode.RUNTIME_ERROR),
        (api_error(400, "bad request"), ErrorCode.RUNTIME_ERROR),
        (api_error(402, "insufficient credits"), ErrorCode.RUNTIME_ERROR),
        (
            {"name": "ProviderAuthError", "data": {"providerID": "openai", "message": "expired"}},
            ErrorCode.PROVIDER_UNAVAILABLE,
        ),
        (
            {"name": "ContextOverflowError", "data": {"message": "too long"}},
            ErrorCode.RUNTIME_ERROR,
        ),
        ({"name": "ContentFilterError", "data": {"message": "blocked"}}, ErrorCode.RUNTIME_ERROR),
        ({"name": "UnknownError", "data": {"message": "kaput"}}, ErrorCode.RUNTIME_ERROR),
    ],
)
async def test_session_errors_map_to_error_codes(fake: FakeOpenCode, runtime, error, code):
    fake.default_behavior = error_turn(error)
    session = await runtime.create_session("chat")
    events = await collect(runtime, request(session))
    assert [type(e) for e in events] == [Failed]
    assert events[0].code is code
    assert events[0].message
    assert "responseBody" not in events[0].message


async def test_error_yields_exactly_one_terminal_event(fake: FakeOpenCode, runtime):
    fake.default_behavior = error_turn(api_error(500, "mid-stream failure"))
    session = await runtime.create_session("chat")
    events = await collect(runtime, request(session))
    assert isinstance(events[-1], Failed)
    assert sum(isinstance(e, Failed | Completed) for e in events) == 1


async def test_model_not_found_for_disconnected_provider(fake: FakeOpenCode, runtime):
    message = "Model not found: anthropic/claude-x. Did you mean: claude-y?"
    fake.default_behavior = error_turn({"name": "UnknownError", "data": {"message": message}})
    session = await runtime.create_session("chat")
    events = await collect(runtime, request(session, provider="anthropic", model="claude-x"))
    assert events[-1].code is ErrorCode.PROVIDER_UNAVAILABLE
    assert "anthropic" in events[-1].message
    assert len(fake.calls_to("GET", "/provider")) == 1  # refreshed to find out why


async def test_model_not_found_for_connected_provider(fake: FakeOpenCode, runtime):
    fake.default_behavior = error_turn(
        {"name": "UnknownError", "data": {"message": "Model not found: openai/gpt-0."}}
    )
    session = await runtime.create_session("chat")
    events = await collect(runtime, request(session, provider="openai", model="gpt-0"))
    assert events[-1].code is ErrorCode.MODEL_UNAVAILABLE
    assert "gpt-0" in events[-1].message


async def test_model_not_found_for_unknown_provider(fake: FakeOpenCode, runtime):
    fake.default_behavior = error_turn(
        {"name": "UnknownError", "data": {"message": "Model not found: nosuch/x."}}
    )
    session = await runtime.create_session("chat")
    events = await collect(runtime, request(session, provider="nosuch", model="x"))
    assert events[-1].code is ErrorCode.PROVIDER_UNAVAILABLE


async def test_rate_limit_retry_status_fails_fast_and_aborts(fake: FakeOpenCode, runtime):
    fake.default_behavior = retry_turn("Rate limit exceeded, please retry")
    session = await runtime.create_session("chat")
    events = await asyncio.wait_for(collect(runtime, request(session)), 5)
    assert [type(e) for e in events] == [Failed]
    assert events[0].code is ErrorCode.RATE_LIMITED
    assert len(fake.aborts) == 1


async def test_free_tier_limit_retry_action_is_rate_limited(fake: FakeOpenCode, runtime):
    action = {
        "reason": "free_tier_limit",
        "provider": "opencode",
        "title": "t",
        "message": "m",
        "label": "l",
    }
    fake.default_behavior = retry_turn("Free usage exceeded", action=action)
    session = await runtime.create_session("chat")
    events = await asyncio.wait_for(collect(runtime, request(session)), 5)
    assert events[0].code is ErrorCode.RATE_LIMITED


async def test_transient_retries_are_tolerated_briefly_then_fail(fake: FakeOpenCode, runtime):
    fake.default_behavior = retry_turn("Provider is overloaded", attempts=3)
    session = await runtime.create_session("chat")
    events = await asyncio.wait_for(collect(runtime, request(session)), 5)
    assert [type(e) for e in events] == [Failed]
    assert events[0].code is ErrorCode.RUNTIME_ERROR
    assert len(fake.aborts) == 1


async def test_prompt_rejected_with_404(fake: FakeOpenCode, runtime):
    fake.prompt_status = 404
    events = await collect(runtime, request("ses_missing"))
    assert [type(e) for e in events] == [Failed]
    assert events[0].code is ErrorCode.RUNTIME_ERROR
    assert "Session not found" in events[0].message
    assert not fake.aborts  # nothing was started


async def test_unreachable_opencode_yields_failed_provider_unavailable():
    rt = HttpOpenCodeRuntime(make_config("http://127.0.0.1:9"))
    try:
        events = await collect(rt, request("ses_x"))
        assert [type(e) for e in events] == [Failed]
        assert events[0].code is ErrorCode.PROVIDER_UNAVAILABLE
    finally:
        await rt.aclose()


async def test_stream_after_aclose_fails_without_raising(fake: FakeOpenCode):
    rt = HttpOpenCodeRuntime(make_config(fake.url))
    await rt.aclose()
    events = await collect(rt, request("ses_x"))
    assert events[0].code is ErrorCode.PROVIDER_UNAVAILABLE


# ----------------------------------------------------------------------------- cancellation


async def wait_until(predicate):
    async with asyncio.timeout(3.0):
        while not predicate():  # noqa: ASYNC110 - polling a plain predicate
            await asyncio.sleep(0.01)


async def test_cancelling_the_consumer_aborts_the_session(fake: FakeOpenCode, runtime):
    fake.default_behavior = stall_turn(["a", "b"])
    session = await runtime.create_session("chat")
    seen: list = []

    async def consume():
        async for event in runtime.stream(request(session)):
            seen.append(event)

    task = asyncio.create_task(consume())
    await wait_until(lambda: len(seen) >= 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert [c.path for c in fake.aborts] == [f"/session/{session}/abort"]
    assert session not in fake.busy
    assert not fake._subscribers  # SSE connection was closed
    assert len(fake.prompts) == 1


async def test_closing_the_iterator_early_aborts_the_session(fake: FakeOpenCode, runtime):
    fake.default_behavior = stall_turn(["a", "b", "c"])
    session = await runtime.create_session("chat")
    stream = runtime.stream(request(session))
    assert isinstance(await anext(stream), TextDelta)
    await stream.aclose()
    assert len(fake.aborts) == 1
    assert session not in fake.busy
    await wait_until(lambda: not fake._subscribers)


async def test_abort_still_completes_when_cancelled_again_during_cleanup(
    fake: FakeOpenCode, runtime
):
    fake.default_behavior = stall_turn(["a"])
    session = await runtime.create_session("chat")
    got = asyncio.Event()

    async def consume():
        async for _ in runtime.stream(request(session)):
            got.set()

    task = asyncio.create_task(consume())
    await got.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()  # second cancellation while cleanup is running
    with pytest.raises(asyncio.CancelledError):
        await task
    await wait_until(lambda: len(fake.aborts) >= 1)
    await runtime.aclose()  # waits for any shielded abort still in flight
    assert fake.aborts


async def test_abort_is_idempotent_and_never_raises(fake: FakeOpenCode, runtime):
    session = await runtime.create_session("chat")
    await runtime.abort(session)
    await runtime.abort(session)
    assert len(fake.aborts) == 2


async def test_abort_never_raises_when_unreachable():
    rt = HttpOpenCodeRuntime(make_config("http://127.0.0.1:9"))
    try:
        await rt.abort("ses_x")
    finally:
        await rt.aclose()


async def test_no_leaked_tasks_after_a_cancelled_stream(fake: FakeOpenCode, runtime):
    before = {t for t in asyncio.all_tasks()}
    fake.default_behavior = stall_turn(["a"])
    session = await runtime.create_session("chat")
    got = asyncio.Event()

    async def consume():
        async for _ in runtime.stream(request(session)):
            got.set()

    task = asyncio.create_task(consume())
    await got.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.1)
    leaked = [t for t in asyncio.all_tasks() - before - {asyncio.current_task()} if not t.done()]
    # only the fake server's own tasks may remain; none belong to the runtime
    assert not [t for t in leaked if "backend/runtime" in repr(t.get_coro())]
    assert not runtime._tasks


async def test_aclose_aborts_in_flight_sessions(fake: FakeOpenCode):
    rt = HttpOpenCodeRuntime(make_config(fake.url))
    fake.default_behavior = stall_turn(["a"])
    session = await rt.create_session("chat")
    stream = rt.stream(request(session))
    await anext(stream)
    await rt.aclose()
    assert any(c.path.endswith(f"{session}/abort") for c in fake.aborts)
    events = []
    async for event in stream:
        events.append(event)
    assert isinstance(events[-1], Failed)


# ----------------------------------------------------------------------------- concurrency


async def test_concurrent_sessions_do_not_cross_streams(fake: FakeOpenCode, runtime):
    fake.behaviors[("openai", "gpt-6-sol")] = text_turn(
        ["alpha-1 ", "alpha-2 ", "alpha-3"], delay=0.02, tokens=(10, 3, 0)
    )
    fake.behaviors[("openrouter", "deepseek/deepseek-chat")] = text_turn(
        ["beta-1 ", "beta-2 ", "beta-3"], delay=0.015, tokens=(30, 3, 0), cost=0.25
    )
    s1 = await runtime.create_session("a")
    s2 = await runtime.create_session("b")
    first, second = await asyncio.gather(
        collect(runtime, request(s1)),
        collect(runtime, request(s2, provider="openrouter", model="deepseek/deepseek-chat")),
    )
    assert "".join(deltas(first)) == "alpha-1 alpha-2 alpha-3" == first[-1].text
    assert "".join(deltas(second)) == "beta-1 beta-2 beta-3" == second[-1].text
    assert first[-1].usage.input_tokens == 10 and second[-1].usage.input_tokens == 30
    assert first[-1].usage.cost_usd is None and second[-1].usage.cost_usd == 0.25


async def test_events_of_other_sessions_are_ignored(fake: FakeOpenCode, runtime):
    async def noisy(ctx):
        # unrelated session chatter interleaved with ours
        other = "ses_other"
        await ctx.publish(
            delta(other, "msg_x", "prt_x", "LEAK"),
            session_error(other, api_error(500, "other failure")),
            idle(other),
        )
        await text_turn(["mine"])(ctx)

    fake.default_behavior = noisy
    session = await runtime.create_session("chat")
    events = await collect(runtime, request(session))
    assert deltas(events) == ["mine"]
    assert events[-1].text == "mine"


async def test_cancelling_one_session_leaves_the_other_running(fake: FakeOpenCode, runtime):
    fake.behaviors[("openai", "gpt-6-sol")] = stall_turn(["x"] * 3)
    fake.behaviors[("openrouter", "deepseek/deepseek-chat")] = text_turn(
        ["ok-1 ", "ok-2"], delay=0.05
    )
    s1 = await runtime.create_session("a")
    s2 = await runtime.create_session("b")
    stalled = asyncio.create_task(collect(runtime, request(s1)))
    healthy = asyncio.create_task(
        collect(runtime, request(s2, provider="openrouter", model="deepseek/deepseek-chat"))
    )
    await wait_until(lambda: s1 in fake.busy and s2 in fake.busy)
    stalled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stalled
    events = await healthy
    assert isinstance(events[-1], Completed) and events[-1].text == "ok-1 ok-2"
    assert [c.path for c in fake.aborts] == [f"/session/{s1}/abort"]


# ----------------------------------------------------------------------------- reconnecting


async def test_sse_reconnect_recovers_text_without_duplicating_the_prompt(
    fake: FakeOpenCode, runtime
):
    fake.default_behavior = text_turn(["one ", "two ", "three ", "four"], delay=0.02)
    fake.drop_event_streams_after = 5  # each /event connection dies after 5 events
    session = await runtime.create_session("chat")
    events = await asyncio.wait_for(collect(runtime, request(session)), 10)
    assert events[-1].text == "one two three four"
    assert "".join(deltas(events)) == "one two three four"  # no duplicated text
    assert sum(isinstance(e, Completed) for e in events) == 1
    assert len(fake.prompts) == 1  # reconnecting never re-sends the prompt
    assert len(fake.calls_to("GET", "/event")) >= 2


async def test_a_silent_model_times_out_and_is_aborted(fake: FakeOpenCode):
    rt = HttpOpenCodeRuntime(make_config(fake.url), stall_timeout=0.3)
    try:
        fake.default_behavior = stall_turn([], delay=0)  # busy, then nothing at all
        session = await rt.create_session("chat")

        # heartbeats are not session events; the fake sends none, so poke the stream
        # periodically with another session's event to wake the loop like OpenCode's heartbeat
        async def heartbeat():
            while True:
                fake.publish({"id": "evt_hb", "type": "server.heartbeat", "properties": {}})
                await asyncio.sleep(0.1)

        beat = asyncio.create_task(heartbeat())
        try:
            events = await asyncio.wait_for(collect(rt, request(session)), 5)
        finally:
            beat.cancel()
        assert [type(e) for e in events] == [Failed]
        assert events[0].code is ErrorCode.RUNTIME_ERROR
        assert "timed out" in events[0].message
        assert len(fake.aborts) == 1
    finally:
        await rt.aclose()
