from .conftest import GPT, MINI, ROUTER, fresh_runtime, running_app


async def _setup_group(env, count=12):
    models = [GPT, MINI, ROUTER]
    agents = [await env.agent(f"A{i:02d}", models[i % 3]) for i in range(count)]
    coord = await env.agent("Coord", GPT)
    await env.set_coordinator(coord["id"])
    conv = await env.group([a["id"] for a in agents])
    return agents, coord, conv


async def test_group_of_twelve_runs_both_rounds_with_peer_replies(env):
    agents, coord, conv = await _setup_group(env, 12)
    run = await env.run_to_end(conv, "Discuss door safety")
    assert run["status"] == "completed", run
    assert [p["agent_id"] for p in run["participants"]] == [a["id"] for a in agents]
    assert run["coordinator"]["agent_id"] == coord["id"]

    msgs = await env.messages(conv)
    agent_msgs = [m for m in msgs if m["role"] == "agent"]
    assert len(msgs) == 1 + 1 + 12 + 12 + 1
    assert [m["stage"] for m in agent_msgs] == [0] + [1] * 12 + [2] * 12 + [3]
    assert agent_msgs[0]["agent"]["agent_id"] == coord["id"]
    assert agent_msgs[-1]["agent"]["agent_id"] == coord["id"]

    ids = [a["id"] for a in agents]
    stage1 = [m for m in agent_msgs if m["stage"] == 1]
    stage2 = [m for m in agent_msgs if m["stage"] == 2]
    assert [m["agent"]["agent_id"] for m in stage1] == ids  # deterministic order
    assert [m["agent"]["agent_id"] for m in stage2] == ids
    by_id = {m["id"]: m for m in stage1}
    for i, reply in enumerate(stage2):
        target = by_id[reply["reply_to_id"]]
        assert target["agent"]["agent_id"] == ids[(i + 1) % 12]  # next participant's round-1
        assert reply["agent"]["agent_id"] != target["agent"]["agent_id"]
    assert all(m["status"] == "complete" for m in agent_msgs)
    assert len(env.runtime.requests) == 26
    # Different models were really used per participant.
    used = {(m["provider_id"], m["model_id"]) for m in stage1}
    assert used == {GPT, MINI, ROUTER}


async def test_participants_see_the_shared_discussion_incrementally(env):
    agents, coord, conv = await _setup_group(env, 4)
    await env.run_to_end(conv, "Kickoff topic")
    reqs = env.runtime.requests
    # 0 agenda, 1..4 round 1, 5..8 round 2, 9 summary
    assert reqs[0].user_text.startswith("Open the discussion")
    assert "[User]: Kickoff topic" in reqs[0].user_text
    # Participant 1 (A00) sees the agenda; participant 2 (A01) also sees A00's contribution.
    assert "(coordinator)]" in reqs[1].user_text and "[A00]" not in reqs[1].user_text
    assert "[A00]:" in reqs[2].user_text
    assert "[A00]:" in reqs[3].user_text and "[A01]:" in reqs[3].user_text
    # Round 2: A00 replies to A01 and sees everything it has not seen yet, not its own message.
    assert reqs[5].user_text.startswith("Round 2: respond directly to A01")
    assert "[A01]:" in reqs[5].user_text and "[A03]:" in reqs[5].user_text
    assert "[A00]:" not in reqs[5].user_text
    assert "(coordinator)]" not in reqs[5].user_text  # agenda already seen
    # Last participant wraps around to reply to the first.
    assert reqs[8].user_text.startswith("Round 2: respond directly to A00")
    # Coordinator summary sees round-2 replies and reuses its agenda session.
    assert reqs[9].user_text.startswith("Summarize the discussion")
    assert "[A03]:" in reqs[9].user_text and reqs[9].session_id == reqs[0].session_id
    # Persona + discussion framing is in system text; participants reuse one session each.
    assert "group discussion" in reqs[1].system and "A00, A01, A02, A03" in reqs[1].system
    assert reqs[1].session_id == reqs[5].session_id
    assert len({r.session_id for r in reqs}) == 5
    assert [r.max_output_tokens for r in reqs] == [2048] + [1024] * 8 + [2048]


async def test_followup_starts_another_bounded_exchange(env):
    agents, coord, conv = await _setup_group(env, 3)
    r1 = await env.run_to_end(conv, "first")
    assert len(env.runtime.requests) == 8
    r2 = await env.run_to_end(conv, "follow-up question")
    assert r2["status"] == "completed" and r2["id"] != r1["id"]
    assert len(env.runtime.requests) == 16  # nothing ran in the background in between
    assert "[User]: follow-up question" in env.runtime.requests[8].user_text
    # The participant has already seen the first exchange, so it is not resent.
    assert "[User]: first" not in env.runtime.requests[8].user_text


async def test_coordinator_may_also_participate(env):
    agents = [await env.agent(f"A{i}") for i in range(2)]
    await env.set_coordinator(agents[0]["id"])
    conv = await env.group([a["id"] for a in agents])
    run = await env.run_to_end(conv)
    assert run["status"] == "completed"
    assert len(env.runtime.requests) == 2 + 2 + 2


async def test_group_needs_usable_coordinator(env):
    a, b = await env.agent("A"), await env.agent("B")
    conv = await env.group([a["id"], b["id"]])
    r = await env.client.post(f"/api/conversations/{conv}/messages", json={"content": "go"})
    assert r.status_code == 422 and "coordinator" in r.json()["message"].lower()
    assert len(await env.messages(conv)) == 0

    c = await env.agent("C")
    await env.set_coordinator(c["id"])
    await env.client.patch(f"/api/agents/{c['id']}", json={"archived": True})
    r = await env.client.post(f"/api/conversations/{conv}/messages", json={"content": "go"})
    assert r.status_code == 422 and r.json()["agent_id"] == c["id"]
    assert "archived" in r.json()["message"]
    assert env.runtime.requests == []

    r = await env.client.patch("/api/settings", json={"coordinator_agent_id": c["id"]})
    assert r.status_code == 422  # cannot pick an archived coordinator
    r = await env.client.patch("/api/settings", json={"coordinator_agent_id": "ghost"})
    assert r.status_code == 404


async def test_group_validation(env):
    a = await env.agent("A")
    r = await env.client.post(
        "/api/conversations", json={"type": "group", "participant_ids": [a["id"]], "topic": "t"}
    )
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    r = await env.client.post(
        "/api/conversations",
        json={"type": "group", "participant_ids": [a["id"], "ghost"], "topic": "t"},
    )
    assert r.status_code == 404


async def test_failed_participant_halts_the_exchange(tmp_path):
    runtime = fresh_runtime()
    async with running_app(tmp_path / "d", runtime) as env:
        agents = [
            await env.agent("P0", GPT),
            await env.agent("P1", MINI),
            await env.agent("P2", GPT),
        ]
        coord = await env.agent("Coord", GPT)
        await env.set_coordinator(coord["id"])
        conv = await env.group([a["id"] for a in agents])
        from backend.contracts.models import ErrorCode
        from backend.contracts.runtime import Failed

        runtime.fail_models[MINI] = Failed(ErrorCode.RATE_LIMITED, "slow down")
        run = await env.run_to_end(conv)
        assert run["status"] == "failed" and run["error_code"] == "rate_limited"
        assert len(runtime.requests) == 3  # agenda, P0, P1(failed); P2 and round 2 never ran
        msgs = [m for m in await env.messages(conv) if m["role"] == "agent"]
        assert [m["status"] for m in msgs] == ["complete", "complete", "failed"]
        assert (
            msgs[-1]["error_code"] == "rate_limited"
            and msgs[-1]["agent"]["agent_id"] == agents[1]["id"]
        )
        frames = await env.events(run["id"])
        assert frames[-1]["event"] == "run.failed"
