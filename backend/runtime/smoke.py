"""Manual smoke test against a running OpenCode: ``uv run python -m backend.runtime.smoke``.

Starts nothing. Attach with::

    SMARTRAIL_OPENCODE_URL=http://127.0.0.1:4171 SMARTRAIL_OPENCODE_PASSWORD=<pw> \\
        uv run python -m backend.runtime.smoke

(start that OpenCode with ``OPENCODE_CONFIG_CONTENT`` set to the JSON of
``build_opencode_config()``, as the launcher does, so the neutral ``smartrail`` agent exists.)

It prints a catalog summary and checks the agent is tool-free, then makes at most TWO tiny
streamed model calls (about 30 output tokens each):

* one to ``openai/gpt-6-sol`` only when the ``openai`` provider is connected and offers it;
* one to the cheapest ``openrouter`` model (or ``SMARTRAIL_SMOKE_OPENROUTER_MODEL``) only when
  ``openrouter`` is connected.

Nothing secret is printed: no passwords, tokens or OpenCode credential files are read.
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx

from backend.config import load_config
from backend.contracts.models import ProviderInfo
from backend.contracts.runtime import Completed, CompletionRequest, Failed, TextDelta
from backend.runtime import AGENT_NAME, create_runtime

MAX_TOKENS = 30
PROMPT = "Reply with exactly three words."
SYSTEM = "You are a terse test assistant."


async def check_agent(config) -> bool:
    """The neutral agent must exist, be the only visible agent, and deny every tool."""
    auth = httpx.BasicAuth(config.opencode_username, config.opencode_password)
    async with httpx.AsyncClient(base_url=config.opencode_url, auth=auth, timeout=10) as client:
        agents = (await client.get("/agent")).json()
        settings = (await client.get("/config")).json()
    visible = [a["name"] for a in agents if not a.get("hidden")]
    agent = next((a for a in agents if a["name"] == AGENT_NAME), None)
    deny_all = (
        bool(agent)
        and [r for r in agent["permission"] if r["permission"] == "*"][-1]["action"] == "deny"
    )
    tools_off = settings.get("tools") == {"*": False}
    ok = visible == [AGENT_NAME] and deny_all and tools_off and settings.get("mcp") == {}
    print(
        f"agent check: visible={visible} deny_all={deny_all} tools_off={tools_off} -> "
        f"{'OK' if ok else 'FAILED (start OpenCode with OPENCODE_CONFIG_CONTENT)'}"
    )
    return ok


def cheapest_model(provider: ProviderInfo) -> str | None:
    # Free ":free" routes are rate-limited and can stall, so a smoke test avoids them.
    priced = [
        m
        for m in provider.models
        if m.price
        and m.price.input_per_mtok is not None
        and m.price.output_per_mtok is not None
        and m.price.input_per_mtok + m.price.output_per_mtok > 0
        and not m.id.endswith(":free")
    ]
    if not priced:
        return None
    return min(priced, key=lambda m: (m.price.input_per_mtok + m.price.output_per_mtok, m.id)).id  # type: ignore[union-attr]


async def one_call(runtime, label: str, provider_id: str, model_id: str) -> bool:
    session = await runtime.create_session(f"smoke {label}")
    print(f"\n[{label}] {provider_id}/{model_id}  (max {MAX_TOKENS} tokens)")
    request = CompletionRequest(session, provider_id, model_id, SYSTEM, PROMPT, MAX_TOKENS)
    deltas = 0
    async for event in runtime.stream(request):
        if isinstance(event, TextDelta):
            deltas += 1
            print(event.text, end="", flush=True)
        elif isinstance(event, Completed):
            u = event.usage
            print(
                f"\n  completed: {deltas} deltas, tokens in={u.input_tokens} "
                f"out={u.output_tokens} cost_usd={u.cost_usd}"
            )
            return True
        elif isinstance(event, Failed):
            print(f"\n  FAILED [{event.code.value}]: {event.message}")
            return False
    return False


async def main() -> int:
    config = load_config()
    runtime = create_runtime(config)
    try:
        print(f"OpenCode: {config.opencode_url}")
        if not await runtime.health():
            print("OpenCode is not reachable (check SMARTRAIL_OPENCODE_URL / _PASSWORD)")
            return 2
        catalog = await runtime.list_providers(refresh=True)
        connected = [p for p in catalog.providers if p.connected]
        print(
            f"catalog: {len(catalog.providers)} providers, {len(connected)} connected, "
            f"{sum(len(p.models) for p in catalog.providers)} models"
        )
        for provider in connected:
            print(f"  connected: {provider.id} ({provider.name}) - {len(provider.models)} models")

        ok = await check_agent(config)
        by_id = {p.id: p for p in connected}

        openai = by_id.get("openai")
        sol = openai and next((m for m in openai.models if m.id == "gpt-6-sol"), None)
        print(f"\nopenai connected: {bool(openai)}; gpt-6-sol available: {bool(sol)}")
        if sol:
            ok = await one_call(runtime, "openai", "openai", "gpt-6-sol") and ok

        router = by_id.get("openrouter")
        if router:
            model = os.environ.get("SMARTRAIL_SMOKE_OPENROUTER_MODEL") or cheapest_model(router)
            if model:
                ok = await one_call(runtime, "openrouter", "openrouter", model) and ok
            else:
                print("\nopenrouter connected but no priced model found; skipping call")
        else:
            print("openrouter not connected; skipping")
        return 0 if ok else 1
    finally:
        await runtime.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
