from .conftest import GPT, MINI, GatedRuntime, running_app, wait_for


async def test_direct_chat_streams_one_agent_with_persona_and_brief(env):
    ada = await env.agent("Ada", GPT, persona="You are Ada, a station manager.")
    bob = await env.agent("Bob", MINI)
    await env.client.patch("/api/settings", json={"project_brief": "Build a ticket kiosk."})
    conv = await env.direct(ada["id"])
    sent = await env.send(conv, "What is our plan?")
    run = await env.wait(sent["run_id"])
    assert run["status"] == "completed"
    assert len(env.runtime.requests) == 1  # only the selected agent was invoked
    req = env.runtime.requests[0]
    assert (req.provider_id, req.model_id) == GPT
    assert "You are Ada, a station manager." in req.system
    assert "Build a ticket kiosk." in req.system
    assert req.user_text == "What is our plan?"
    assert req.max_output_tokens == 1024

    msgs = await env.messages(conv)
    assert [m["role"] for m in msgs] == ["user", "agent"]
    assert msgs[0]["id"] == sent["user_message_id"] and msgs[0]["run_id"] == run["id"]
    agent_msg = msgs[1]
    assert agent_msg["speaker_name"] == "Ada" and agent_msg["status"] == "complete"
    assert agent_msg["agent"]["agent_id"] == ada["id"] and agent_msg["agent"]["revision"] == 1
    assert (agent_msg["provider_id"], agent_msg["model_id"]) == GPT
    assert agent_msg["stage"] is None and bob  # bob untouched
    assert run["usage"]["output_tokens"] > 0


async def test_participant_max_tokens_comes_from_settings(env):
    ada = await env.agent("Ada")
    await env.client.patch("/api/settings", json={"participant_max_tokens": 77})
    await env.run_to_end(await env.direct(ada["id"]))
    assert env.runtime.requests[-1].max_output_tokens == 77


async def test_one_opencode_session_per_direct_conversation(env):
    ada = await env.agent("Ada")
    c1, c2 = await env.direct(ada["id"]), await env.direct(ada["id"])
    await env.run_to_end(c1, "one")
    await env.run_to_end(c1, "two")
    await env.run_to_end(c2, "three")
    s = [r.session_id for r in env.runtime.requests]
    assert s[0] == s[1] != s[2]
    assert env.runtime.requests[1].user_text == "two"  # session history carries the context


async def test_latest_brief_is_used_on_each_run(env):
    ada = await env.agent("Ada")
    conv = await env.direct(ada["id"])
    await env.client.patch("/api/settings", json={"project_brief": "brief v1"})
    await env.run_to_end(conv)
    await env.client.patch("/api/settings", json={"project_brief": "brief v2"})
    await env.run_to_end(conv)
    assert "brief v1" in env.runtime.requests[0].system
    assert "brief v2" in env.runtime.requests[1].system
    assert "brief v1" not in env.runtime.requests[1].system


async def test_second_concurrent_run_is_409_and_first_is_unaffected(tmp_path):
    runtime = GatedRuntime()
    async with running_app(tmp_path / "d", runtime) as env:
        ada, bob = await env.agent("Ada"), await env.agent("Bob")
        c1, c2 = await env.direct(ada["id"]), await env.direct(bob["id"])
        runtime.gate.clear()
        first = await env.send(c1)
        await runtime.arrived.wait()
        r = await env.client.post(f"/api/conversations/{c2}/messages", json={"content": "x"})
        assert r.status_code == 409 and r.json()["code"] == "run_active"
        r = await env.client.post(f"/api/conversations/{c1}/messages", json={"content": "x"})
        assert r.status_code == 409
        detail = (await env.client.get(f"/api/conversations/{c1}")).json()
        assert detail["active_run_id"] == first["run_id"]
        assert (await env.client.get(f"/api/conversations/{c2}")).json()["active_run_id"] is None
        runtime.gate.set()
        assert (await env.wait(first["run_id"]))["status"] == "completed"
        assert len(await env.messages(c2)) == 0  # rejected send stored nothing
        await env.run_to_end(c2)  # slot is free again


async def test_unknown_conversation_and_blank_content(env):
    r = await env.client.post("/api/conversations/nope/messages", json={"content": "x"})
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    ada = await env.agent("Ada")
    conv = await env.direct(ada["id"])
    for bad in ("", "   "):
        r = await env.client.post(f"/api/conversations/{conv}/messages", json={"content": bad})
        assert r.status_code == 422 and r.json()["code"] == "validation_error"
    assert (await env.client.get("/api/conversations/nope")).status_code == 404


async def test_sending_to_archived_agent_conversation_is_refused(env):
    ada = await env.agent("Ada")
    conv = await env.direct(ada["id"])
    await env.client.patch(f"/api/agents/{ada['id']}", json={"archived": True})
    r = await env.client.post(f"/api/conversations/{conv}/messages", json={"content": "x"})
    assert r.status_code == 422 and r.json()["agent_id"] == ada["id"]
    assert len(await env.messages(conv)) == 0  # transcript still readable
    await wait_for(lambda: True)


async def test_conversation_list_title_defaults(env):
    ada = await env.agent("Ada")
    await env.direct(ada["id"])
    listed = (await env.client.get("/api/conversations")).json()
    assert listed[0]["title"] == "Ada" and listed[0]["type"] == "direct"
