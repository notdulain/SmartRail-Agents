async def test_direct_roundtrip(env):
    agent = await env.agent("Ada")
    conv = await env.direct(agent["id"])
    run = await env.run_to_end(conv, "hi there")
    assert run["status"] == "completed", run
    msgs = await env.messages(conv)
    assert [m["role"] for m in msgs] == ["user", "agent"]
    assert msgs[1]["content"].startswith("[gpt-6-sol] hi there")
    frames = await env.events(run["id"])
    assert frames[0]["event"] == "run.started" and frames[-1]["event"] == "run.completed"
