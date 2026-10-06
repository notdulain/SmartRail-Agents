from .conftest import GPT, MINI, ROUTER, fresh_runtime, running_app


async def test_create_list_and_defaults(env):
    a = await env.agent("Ada", GPT, persona="You are Ada.")
    assert a["revision"] == 1 and a["archived"] is False
    assert a["provider_id"] == "openai" and a["model_id"] == "gpt-6-sol"
    r = await env.client.get("/api/agents")
    assert [x["id"] for x in r.json()] == [a["id"]]


async def test_more_than_ten_agents_shared_and_different_models(env):
    shared = await env.agents(8, GPT, prefix="S")
    other = await env.agents(4, MINI, prefix="M")
    routed = await env.agents(2, ROUTER, prefix="R")
    listed = (await env.client.get("/api/agents")).json()
    assert len(listed) == 14 == len(shared) + len(other) + len(routed)
    assert {a["model_id"] for a in listed} == {"gpt-6-sol", "gpt-6-mini", "deepseek/deepseek-chat"}
    assert [a["name"] for a in listed][:3] == ["S00", "S01", "S02"]  # stable creation order


async def test_models_from_multiple_connected_providers(env):
    a = await env.agent("A", GPT)
    b = await env.agent("B", ROUTER)
    assert {a["provider_id"], b["provider_id"]} == {"openai", "openrouter"}


async def test_revision_increments_only_on_config_edits(env):
    a = await env.agent("Ada")
    base = f"/api/agents/{a['id']}"
    r = await env.client.patch(base, json={"name": "Ada 2"})
    assert r.json()["revision"] == 2 and r.json()["name"] == "Ada 2"
    r = await env.client.patch(base, json={"persona": "new persona"})
    assert r.json()["revision"] == 3
    r = await env.client.patch(base, json={"model_id": "gpt-6-mini"})
    assert r.json()["revision"] == 4 and r.json()["model_id"] == "gpt-6-mini"
    r = await env.client.patch(base, json={"provider_id": "openrouter", "model_id": ROUTER[1]})
    assert r.json()["revision"] == 5 and r.json()["provider_id"] == "openrouter"
    r = await env.client.patch(base, json={"archived": True})
    assert r.json()["revision"] == 5 and r.json()["archived"] is True
    r = await env.client.patch(base, json={"archived": False})
    assert r.json()["revision"] == 5 and r.json()["archived"] is False
    r = await env.client.patch(base, json={"name": "Ada 2"})  # no actual change
    assert r.json()["revision"] == 5


async def test_archived_agents_hidden_by_default(env):
    a = await env.agent("Ada")
    b = await env.agent("Bob")
    await env.client.patch(f"/api/agents/{a['id']}", json={"archived": True})
    assert [x["id"] for x in (await env.client.get("/api/agents")).json()] == [b["id"]]
    r = await env.client.get("/api/agents", params={"include_archived": True})
    assert {x["id"] for x in r.json()} == {a["id"], b["id"]}


async def test_archived_agent_cannot_join_new_conversations(env):
    a = await env.agent("Ada")
    b = await env.agent("Bob")
    await env.client.patch(f"/api/agents/{a['id']}", json={"archived": True})
    r = await env.client.post(
        "/api/conversations", json={"type": "direct", "participant_ids": [a["id"]]}
    )
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    r = await env.client.post(
        "/api/conversations",
        json={"type": "group", "participant_ids": [a["id"], b["id"]], "topic": "t"},
    )
    assert r.status_code == 422


async def test_create_rejects_unknown_and_disconnected_provider_and_model(env):
    def body(provider, model):
        return {"name": "X", "persona": "", "provider_id": provider, "model_id": model}

    r = await env.client.post("/api/agents", json=body("nope", "m"))
    assert r.status_code == 422 and r.json()["code"] == "provider_unavailable"
    r = await env.client.post("/api/agents", json=body("anthropic", "claude-sonnet-5-5"))
    assert r.status_code == 422 and r.json()["code"] == "provider_unavailable"
    assert "not connected" in r.json()["message"]
    r = await env.client.post("/api/agents", json=body("openai", "gpt-0"))
    assert r.status_code == 422 and r.json()["code"] == "model_unavailable"
    assert (await env.client.get("/api/agents")).json() == []


async def test_edit_validates_model_and_never_changes_it_silently(env):
    a = await env.agent("Ada", GPT)
    r = await env.client.patch(f"/api/agents/{a['id']}", json={"model_id": "gpt-0"})
    assert r.status_code == 422
    assert r.json()["code"] == "model_unavailable" and r.json()["agent_id"] == a["id"]
    r = await env.client.patch(
        f"/api/agents/{a['id']}", json={"provider_id": "anthropic", "model_id": "claude-sonnet-5-5"}
    )
    assert r.status_code == 422 and r.json()["code"] == "provider_unavailable"
    now = (await env.client.get("/api/agents")).json()[0]
    assert (now["provider_id"], now["model_id"], now["revision"]) == ("openai", "gpt-6-sol", 1)


async def test_stale_catalog_gets_one_refresh_before_rejecting(tmp_path):
    runtime = fresh_runtime()
    async with running_app(tmp_path / "d", runtime) as env:
        calls: list[bool] = []
        original = runtime.list_providers

        async def spy(refresh: bool = False):
            calls.append(refresh)
            if refresh:  # the user connected the provider in OpenCode meanwhile
                runtime.providers[2].connected = True
            return await original(refresh)

        runtime.list_providers = spy
        a = await env.agent("C", ("anthropic", "claude-sonnet-5-5"))
        assert a["provider_id"] == "anthropic" and calls == [False, True]


async def test_unknown_agent_is_404_and_bad_body_is_validation_error(env):
    r = await env.client.patch("/api/agents/missing", json={"name": "x"})
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    r = await env.client.post("/api/agents", json={"name": ""})
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    assert set(r.json()) == {"code", "message", "agent_id"}
    r = await env.client.patch("/api/agents/missing", json={"unknown_field": 1})
    assert r.status_code == 422 and r.json()["code"] == "validation_error"


async def test_providers_and_health(env):
    r = await env.client.get("/api/providers")
    assert r.status_code == 200
    assert {p["id"] for p in r.json()["providers"]} == {"openai", "openrouter", "anthropic"}
    assert (await env.client.get("/api/providers", params={"refresh": True})).status_code == 200
    health = (await env.client.get("/api/health")).json()
    assert health["status"] == "ok" and health["opencode_healthy"] is True
    env.runtime.healthy = False
    assert (await env.client.get("/api/health")).json()["opencode_healthy"] is False
