import asyncio

from backend.contracts.models import ErrorCode
from backend.contracts.runtime import Failed

from .conftest import GPT, MINI, GatedRuntime, fresh_runtime, running_app, wait_for


async def test_sse_frames_and_reconnect_without_duplicates_or_requests(env):
    ada = await env.agent("Ada")
    conv = await env.direct(ada["id"])
    run = await env.run_to_end(conv, "hello streaming world")
    requests_before = len(env.runtime.requests)

    frames = await env.events(run["id"])
    seqs = [int(f["id"]) for f in frames]
    assert seqs == list(range(1, len(frames) + 1))
    assert [f["event"] for f in frames] == [f["json"]["type"] for f in frames]
    assert frames[0]["event"] == "run.started"
    assert frames[1]["event"] == "message.started"
    assert frames[-1]["event"] == "run.completed"
    deltas = [f["json"]["delta"] for f in frames if f["event"] == "message.delta"]
    final = [f for f in frames if f["event"] == "message.completed"][0]["json"]["message"]
    assert "".join(deltas) == final["content"]

    # Reconnect via Last-Event-ID and via ?after=.
    cut = seqs[3]
    replay = await env.events(run["id"], headers={"Last-Event-ID": str(cut)})
    assert [int(f["id"]) for f in replay] == [s for s in seqs if s > cut]
    replay = await env.events(run["id"], params={"after": cut})
    assert [int(f["id"]) for f in replay] == [s for s in seqs if s > cut]
    assert await env.events(run["id"], headers={"Last-Event-ID": str(seqs[-1])}) == []

    assert len(env.runtime.requests) == requests_before  # reconnects never call the runtime
    assert len(await env.messages(conv)) == 2  # and never duplicate messages


async def test_live_subscriber_follows_a_running_run_and_ends_on_terminal(tmp_path):
    runtime = GatedRuntime()
    async with running_app(tmp_path / "d", runtime) as env:
        ada = await env.agent("Ada")
        conv = await env.direct(ada["id"])
        runtime.gate.clear()
        sent = await env.send(conv)
        await runtime.arrived.wait()
        seen: list[str] = []

        async def follow():
            async for frame in env.service.runs.stream_events(sent["run_id"], 0):
                seen.append(frame["event"])

        task = asyncio.create_task(follow())
        await wait_for(lambda: "message.started" in seen)
        assert "run.completed" not in seen
        runtime.gate.set()
        await asyncio.wait_for(task, 5)
        assert seen[0] == "run.started" and seen[-1] == "run.completed"
        assert seen.count("run.completed") == 1


async def test_unknown_run_events_and_run_404(env):
    assert (await env.client.get("/api/runs/nope/events")).status_code == 404
    assert (await env.client.get("/api/runs/nope")).status_code == 404
    assert (await env.client.post("/api/runs/nope/stop")).status_code == 404


async def _group_with_stalled_second_participant(env, runtime):
    coord = await env.agent("Coord", GPT)
    p0 = await env.agent("P0", GPT)
    p1 = await env.agent("P1", MINI)  # MINI is stalled in the test
    p2 = await env.agent("P2", GPT)
    p3 = await env.agent("P3", GPT)
    await env.set_coordinator(coord["id"])
    conv = await env.group([p["id"] for p in (p0, p1, p2, p3)])
    runtime.stall_models.add(MINI)
    return conv


async def test_stop_cancels_stalled_turn_and_prevents_later_participants(env):
    conv = await _group_with_stalled_second_participant(env, env.runtime)
    sent = await env.send(conv)

    async def stalled():
        msgs = await env.messages(conv)
        return any(m["content"] == "..." and m["status"] == "streaming" for m in msgs)

    await wait_for(stalled)
    assert len(env.runtime.requests) == 3  # agenda, P0, P1 (stalled)
    run = (await env.client.post(f"/api/runs/{sent['run_id']}/stop")).json()
    assert run["status"] == "cancelled" and run["finished_at"] is not None
    assert len(env.runtime.requests) == 3  # P2, P3 and round 2 never started
    stalled_session = env.runtime.requests[2].session_id
    assert stalled_session in env.runtime.aborted  # runtime abort was called

    msgs = [m for m in await env.messages(conv) if m["role"] == "agent"]
    assert [m["status"] for m in msgs] == ["complete", "complete", "cancelled"]
    assert msgs[-1]["content"] == "..."  # partial text kept
    frames = await env.events(sent["run_id"])
    assert frames[-1]["event"] == "run.cancelled"
    done = [f["json"]["message"] for f in frames if f["event"] == "message.completed"]
    assert done[-1]["status"] == "cancelled"

    # Idempotent, and a stopped run frees the slot.
    again = (await env.client.post(f"/api/runs/{sent['run_id']}/stop")).json()
    assert again["status"] == "cancelled"
    assert len(await env.events(sent["run_id"])) == len(frames)
    detail = (await env.client.get(f"/api/conversations/{conv}")).json()
    assert detail["active_run_id"] is None
    env.runtime.stall_models.clear()
    assert (await env.run_to_end(conv, "again"))["status"] == "completed"


async def test_stop_on_finished_run_returns_its_state(env):
    ada = await env.agent("Ada")
    run = await env.run_to_end(await env.direct(ada["id"]))
    r = await env.client.post(f"/api/runs/{run['id']}/stop")
    assert r.status_code == 200 and r.json()["status"] == "completed"


async def test_edit_and_archive_during_run_do_not_change_that_run(tmp_path):
    runtime = GatedRuntime()
    async with running_app(tmp_path / "d", runtime) as env:
        coord = await env.agent("Coord", GPT)
        p0, p1 = await env.agent("P0", GPT, persona="orig0"), await env.agent("P1", MINI)
        await env.set_coordinator(coord["id"])
        conv = await env.group([p0["id"], p1["id"]])
        runtime.gate.clear()
        sent = await env.send(conv)
        await runtime.arrived.wait()  # the agenda turn is in flight

        r = await env.client.patch(
            f"/api/agents/{p0['id']}",
            json={"name": "Renamed", "persona": "new0", "model_id": "gpt-6-mini"},
        )
        assert r.status_code == 200 and r.json()["revision"] == 2
        await env.client.patch(f"/api/agents/{p1['id']}", json={"archived": True})
        await env.client.patch(f"/api/agents/{coord['id']}", json={"name": "Coord2"})
        runtime.gate.set()
        run = await env.wait(sent["run_id"])

        assert run["status"] == "completed"
        assert run["participants"][0]["name"] == "P0" and run["participants"][0]["revision"] == 1
        assert run["coordinator"]["name"] == "Coord"
        by_stage = [m for m in await env.messages(conv) if m["role"] == "agent"]
        p0_msgs = [m for m in by_stage if m["agent"]["agent_id"] == p0["id"]]
        assert {m["speaker_name"] for m in p0_msgs} == {"P0"}
        assert {m["model_id"] for m in p0_msgs} == {"gpt-6-sol"}
        p0_reqs = [r for r in runtime.requests if "orig0" in r.system]
        assert len(p0_reqs) == 2 and all(r.model_id == "gpt-6-sol" for r in p0_reqs)
        assert any(r.model_id == "gpt-6-mini" for r in runtime.requests) is True  # P1 (archived)


async def test_history_labels_survive_rename_model_change_and_archive(env):
    ada = await env.agent("Ada", GPT, persona="old persona")
    conv = await env.direct(ada["id"])
    await env.run_to_end(conv, "hi")
    before = (await env.messages(conv))[1]
    await env.client.patch(
        f"/api/agents/{ada['id']}",
        json={"name": "Grace", "provider_id": "openrouter", "model_id": "deepseek/deepseek-chat"},
    )
    await env.client.patch(f"/api/agents/{ada['id']}", json={"archived": True})
    after = (await env.messages(conv))[1]
    assert after == before
    assert after["speaker_name"] == "Ada" and after["agent"]["name"] == "Ada"
    assert after["agent"]["persona"] == "old persona" and after["agent"]["revision"] == 1
    assert (after["provider_id"], after["model_id"]) == GPT
    export = (await env.client.get(f"/api/conversations/{conv}/export")).text
    assert "Ada (openai/gpt-6-sol)" in export and "Grace" not in export


async def test_runtime_failures_mark_message_and_run(tmp_path):
    runtime = fresh_runtime()
    async with running_app(tmp_path / "d", runtime) as env:
        ada = await env.agent("Ada", GPT)
        conv = await env.direct(ada["id"])

        # Rate limit.
        runtime.fail_models[GPT] = Failed(ErrorCode.RATE_LIMITED, "429 too many requests")
        run = await env.run_to_end(conv)
        assert run["status"] == "failed" and run["error_code"] == "rate_limited"
        failed = (await env.messages(conv))[-1]
        assert failed["status"] == "failed" and failed["error_code"] == "rate_limited"
        assert failed["error"] == "429 too many requests"
        assert failed["agent"]["agent_id"] == ada["id"]  # lets the UI offer "Edit agent"
        assert (await env.events(run["id"]))[-1]["event"] == "run.failed"

        # Model no longer offered.
        del runtime.fail_models[GPT]
        runtime.providers[0].models = [
            m for m in runtime.providers[0].models if m.id != "gpt-6-sol"
        ]
        run = await env.run_to_end(conv)
        assert run["status"] == "failed" and run["error_code"] == "model_unavailable"
        msg = (await env.messages(conv))[-1]
        assert msg["error_code"] == "model_unavailable" and msg["agent"]["agent_id"] == ada["id"]
        agents = (await env.client.get("/api/agents")).json()
        assert agents[0]["model_id"] == "gpt-6-sol"  # never silently changed

        # Provider disconnected.
        runtime.providers[0].connected = False
        run = await env.run_to_end(conv)
        assert run["status"] == "failed" and run["error_code"] == "provider_unavailable"
        assert (await env.messages(conv))[-1]["agent"]["agent_id"] == ada["id"]


async def test_runtime_unreachable_fails_the_run(env):
    from backend.contracts.runtime import RuntimeUnavailableError

    ada = await env.agent("Ada")
    conv = await env.direct(ada["id"])

    async def boom(title):
        raise RuntimeUnavailableError("connection refused")

    env.runtime.create_session = boom
    run = await env.run_to_end(conv)
    assert run["status"] == "failed" and run["error_code"] == "provider_unavailable"
    providers = env.runtime.list_providers

    async def down(refresh=False):
        raise RuntimeUnavailableError("down")

    env.runtime.list_providers = down
    r = await env.client.get("/api/providers")
    assert r.status_code == 503 and r.json()["code"] == "provider_unavailable"
    env.runtime.list_providers = providers
