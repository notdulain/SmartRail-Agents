import asyncio

from backend.contracts.fake_runtime import FakeRuntime
from backend.contracts.models import ErrorCode
from backend.contracts.runtime import (
    Completed,
    CompletionRequest,
    Failed,
    OpenCodeRuntime,
    TextDelta,
)


def _req(model="gpt-6-sol", provider="openai", text="hello world"):
    return CompletionRequest("s1", provider, model, "persona", text, 100)


def test_fake_satisfies_protocol():
    assert isinstance(FakeRuntime(), OpenCodeRuntime)


async def _collect(rt, req):
    return [e async for e in rt.stream(req)]


async def test_streams_deltas_then_completed():
    events = await _collect(FakeRuntime(), _req())
    assert all(isinstance(e, TextDelta) for e in events[:-1])
    assert isinstance(events[-1], Completed)
    assert "".join(e.text for e in events[:-1]) == events[-1].text


async def test_disconnected_provider_yields_failed():
    (event,) = await _collect(FakeRuntime(), _req(provider="anthropic", model="claude-sonnet-5-5"))
    assert isinstance(event, Failed) and event.code is ErrorCode.PROVIDER_UNAVAILABLE


async def test_unknown_model_yields_failed():
    (event,) = await _collect(FakeRuntime(), _req(model="nope"))
    assert isinstance(event, Failed) and event.code is ErrorCode.MODEL_UNAVAILABLE


async def test_cancel_aborts_session():
    rt = FakeRuntime()
    rt.stall_models.add(("openai", "gpt-6-sol"))
    task = asyncio.create_task(_collect(rt, _req()))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert "s1" in rt.aborted
