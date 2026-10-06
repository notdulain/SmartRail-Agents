from backend.application.util import month_key

from .conftest import GPT, MINI, ROUTER, CostRuntime, fresh_runtime, running_app


async def test_exhausted_budget_pauses_before_calling_runtime(env):
    router = await env.agent("Router", ROUTER)
    conv = await env.direct(router["id"])
    await env.client.patch("/api/settings", json={"openrouter_monthly_budget_usd": 0.0000001})
    sent = await env.send(conv, "hello")
    run = await env.wait(sent["run_id"])
    assert run["status"] == "paused" and run["error_code"] == "budget_exhausted"
    assert env.runtime.requests == []  # the runtime was never called
    frames = await env.events(run["id"])
    assert frames[-1]["event"] == "run.paused" and "budget" in frames[-1]["json"]["reason"].lower()
    assert (await env.client.get(f"/api/runs/{run['id']}")).json()["status"] == "paused"
    # Pausing frees the slot; raising the budget lets the user continue.
    await env.client.patch("/api/settings", json={"openrouter_monthly_budget_usd": 5})
    assert (await env.run_to_end(conv, "again"))["status"] == "completed"


async def test_subscription_providers_are_not_gated_or_priced(env):
    ada = await env.agent("Ada", GPT)
    await env.client.patch("/api/settings", json={"openrouter_monthly_budget_usd": 0})
    run = await env.run_to_end(await env.direct(ada["id"]))
    assert run["status"] == "completed" and run["usage"]["cost_usd"] is None
    assert (await env.client.get("/api/settings")).json()["openrouter_spent_usd"] == 0


async def test_actual_cost_is_recorded_and_later_requests_pause(tmp_path):
    runtime = CostRuntime(cost=0.004)
    async with running_app(tmp_path / "d", runtime) as env:
        router = await env.agent("Router", ROUTER)
        conv = await env.direct(router["id"])
        # Estimated worst case per request is about $0.001 (1024 output tokens at $1/Mtok).
        await env.client.patch("/api/settings", json={"openrouter_monthly_budget_usd": 0.0075})
        run = await env.run_to_end(conv)
        assert run["status"] == "completed" and run["usage"]["cost_usd"] == 0.004
        assert (await env.client.get("/api/settings")).json()["openrouter_spent_usd"] == 0.004
        run = await env.run_to_end(conv)  # 0.004 + ~0.001 <= 0.0075: allowed
        assert run["status"] == "completed"
        settings = (await env.client.get("/api/settings")).json()
        assert abs(settings["openrouter_spent_usd"] - 0.008) < 1e-9
        run = await env.run_to_end(conv)  # 0.008 + estimate > 0.0075: paused
        assert run["status"] == "paused" and len(runtime.requests) == 2


async def test_cost_falls_back_to_catalog_price_when_runtime_reports_none(env):
    router = await env.agent("Router", ROUTER)
    run = await env.run_to_end(await env.direct(router["id"]))
    spent = (await env.client.get("/api/settings")).json()["openrouter_spent_usd"]
    assert run["status"] == "completed" and 0 < spent < 0.001
    assert run["usage"]["cost_usd"] == spent


async def test_monthly_rollover_resets_spent(env):
    await env.service.store.db.execute(
        "INSERT INTO openrouter_spend (month, usd) VALUES (?, ?)", ("1999-01", 4.99)
    )
    assert (await env.client.get("/api/settings")).json()["openrouter_spent_usd"] == 0
    await env.service.store.db.execute(
        "INSERT INTO openrouter_spend (month, usd) VALUES (?, ?)", (month_key(), 1.25)
    )
    assert (await env.client.get("/api/settings")).json()["openrouter_spent_usd"] == 1.25


async def test_group_pauses_midway_when_budget_runs_out(tmp_path):
    runtime = CostRuntime(cost=0.002)
    async with running_app(tmp_path / "d", runtime) as env:
        coord = await env.agent("Coord", GPT)
        a, b = await env.agent("A", ROUTER), await env.agent("B", ROUTER)
        await env.set_coordinator(coord["id"])
        conv = await env.group([a["id"], b["id"]])
        await env.client.patch("/api/settings", json={"openrouter_monthly_budget_usd": 0.0025})
        run = await env.run_to_end(conv)
        assert run["status"] == "paused" and run["error_code"] == "budget_exhausted"
        assert len(runtime.requests) == 2  # agenda + A; B's turn was gated
        msgs = [m for m in await env.messages(conv) if m["role"] == "agent"]
        assert [m["status"] for m in msgs] == ["complete", "complete"]


async def test_coordinator_allowance_exhausted_pauses(env):
    coord = await env.agent("Coord", GPT)
    a, b = await env.agent("A"), await env.agent("B")
    await env.set_coordinator(coord["id"])
    conv = await env.group([a["id"], b["id"]])
    await env.client.patch("/api/settings", json={"coordinator_max_tokens": 1})
    run = await env.run_to_end(conv)
    assert run["status"] == "paused" and run["error_code"] == "budget_exhausted"
    assert len(env.runtime.requests) == 1 and env.runtime.requests[0].max_output_tokens == 1
    frames = await env.events(run["id"])
    assert frames[-1]["event"] == "run.paused" and "Coordinator" in frames[-1]["json"]["reason"]


async def test_settings_roundtrip_and_validation(env):
    s = (await env.client.get("/api/settings")).json()
    assert s == {
        "project_brief": "",
        "coordinator_agent_id": None,
        "openrouter_monthly_budget_usd": 5.0,
        "openrouter_spent_usd": 0.0,
        "participant_max_tokens": 1024,
        "coordinator_max_tokens": 2048,
    }
    ada = await env.agent("Ada", MINI)
    r = await env.client.patch(
        "/api/settings",
        json={"project_brief": "B", "coordinator_agent_id": ada["id"], "participant_max_tokens": 5},
    )
    assert r.json()["project_brief"] == "B" and r.json()["coordinator_agent_id"] == ada["id"]
    assert (await env.client.patch("/api/settings", json={"coordinator_agent_id": None})).json()[
        "coordinator_agent_id"
    ] is None
    assert (
        await env.client.patch("/api/settings", json={"openrouter_spent_usd": 0})
    ).status_code == 422
    assert (
        await env.client.patch("/api/settings", json={"participant_max_tokens": 0})
    ).status_code == 422
    assert (await env.client.patch("/api/settings", json={})).status_code == 200
    assert fresh_runtime  # imported helper kept for symmetry
