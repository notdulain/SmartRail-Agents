from __future__ import annotations

import pytest

from backend.contracts.runtime import OpenCodeRuntime, RuntimeUnavailableError
from backend.runtime import create_runtime
from backend.runtime.catalog import map_providers
from backend.runtime.opencode import HttpOpenCodeRuntime

from .conftest import PROVIDERS_PAYLOAD, make_config
from .fake_opencode import FakeOpenCode


def test_connected_first_then_alphabetical():
    result = map_providers(PROVIDERS_PAYLOAD)
    assert [(p.id, p.connected) for p in result.providers] == [
        ("openai", True),
        ("openrouter", True),
        ("anthropic", False),
        ("zeta", False),
    ]


def test_prices_context_and_filtering():
    providers = {p.id: p for p in map_providers(PROVIDERS_PAYLOAD).providers}
    openai = providers["openai"]
    # image-only (no text output) and deprecated models are dropped
    assert [m.id for m in openai.models] == ["gpt-6-sol"]
    sol = openai.models[0]
    assert sol.name == "GPT-6 Sol"
    assert sol.price is not None
    assert (sol.price.input_per_mtok, sol.price.output_per_mtok) == (2.0, 10.0)
    assert sol.context_limit == 1050000

    routed = {m.id: m for m in providers["openrouter"].models}
    assert routed["deepseek/deepseek-chat"].price.output_per_mtok == 1.0  # type: ignore[union-attr]
    assert routed["mystery"].price is None  # unknown cost stays unknown
    assert routed["mystery"].context_limit is None

    free = providers["zeta"].models[0]
    assert free.price is not None and free.price.input_per_mtok == 0.0  # free is not unknown


def test_tolerates_garbage_entries():
    result = map_providers(
        {"all": [None, {"id": 3}, {"id": "p", "models": "nope"}], "connected": 5}
    )
    assert [p.id for p in result.providers] == ["p"]
    assert result.providers[0].models == []


async def test_catalog_is_cached_and_refresh_bypasses_cache(fake: FakeOpenCode, runtime):
    first = await runtime.list_providers()
    second = await runtime.list_providers()
    assert second is first
    assert len(fake.calls_to("GET", "/provider")) == 1

    fake.providers_payload = {**PROVIDERS_PAYLOAD, "connected": ["openai", "openrouter", "zeta"]}
    assert (await runtime.list_providers()) is first  # still cached
    refreshed = await runtime.list_providers(refresh=True)
    assert len(fake.calls_to("GET", "/provider")) == 2
    assert refreshed is not first
    assert {p.id for p in refreshed.providers if p.connected} == {"openai", "openrouter", "zeta"}
    assert (await runtime.list_providers()) is refreshed  # refresh replaced the cache


async def test_catalog_cache_expires(fake: FakeOpenCode):
    rt = HttpOpenCodeRuntime(make_config(fake.url), catalog_ttl=0.0)
    try:
        await rt.list_providers()
        await rt.list_providers()
        assert len(fake.calls_to("GET", "/provider")) == 2
    finally:
        await rt.aclose()


async def test_unreachable_opencode_raises_runtime_unavailable():
    rt = HttpOpenCodeRuntime(make_config("http://127.0.0.1:9"))
    try:
        assert await rt.health() is False
        with pytest.raises(RuntimeUnavailableError):
            await rt.list_providers()
        with pytest.raises(RuntimeUnavailableError):
            await rt.create_session("x")
    finally:
        await rt.aclose()


async def test_create_runtime_satisfies_the_protocol(fake: FakeOpenCode):
    rt = create_runtime(make_config(fake.url))
    try:
        assert isinstance(rt, OpenCodeRuntime)
        assert await rt.health() is True
    finally:
        await rt.aclose()
