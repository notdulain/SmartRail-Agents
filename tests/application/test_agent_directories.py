"""Agent working directories and tool access: validation, normalization, revisions."""

from pathlib import Path

from .conftest import GPT


def _body(**extra):
    return {"name": "Ada", "persona": "p", "provider_id": GPT[0], "model_id": GPT[1], **extra}


async def test_create_with_directory_normalizes_and_snapshots(env, tmp_path):
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    raw = str(project / "src" / "..")  # not normalized on purpose
    r = await env.client.post(
        "/api/agents", json=_body(working_directory=raw, tool_access="read_write")
    )
    assert r.status_code == 201, r.text
    agent = r.json()
    assert agent["working_directory"] == str(project.resolve())
    assert agent["tool_access"] == "read_write" and agent["revision"] == 1
    listed = (await env.client.get("/api/agents")).json()[0]
    assert listed["working_directory"] == agent["working_directory"]

    conv = await env.direct(agent["id"])
    run = await env.run_to_end(conv)
    (snapshot,) = run["participants"]
    assert snapshot["working_directory"] == agent["working_directory"]
    assert snapshot["tool_access"] == "read_write"


async def test_defaults_are_no_directory_and_no_tools(env):
    agent = await env.agent("Ada")
    assert agent["working_directory"] is None and agent["tool_access"] == "none"


async def test_directory_without_tools_is_allowed(env, tmp_path):
    r = await env.client.post("/api/agents", json=_body(working_directory=str(tmp_path)))
    assert r.status_code == 201, r.text
    assert r.json()["tool_access"] == "none"
    assert r.json()["working_directory"] == str(tmp_path.resolve())


async def test_create_rejects_missing_file_and_relative_directories(env, tmp_path):
    missing = tmp_path / "nope"
    r = await env.client.post("/api/agents", json=_body(working_directory=str(missing)))
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    assert "does not exist" in r.json()["message"] and str(missing) in r.json()["message"]

    a_file = tmp_path / "file.txt"
    a_file.write_text("x")
    r = await env.client.post("/api/agents", json=_body(working_directory=str(a_file)))
    assert r.status_code == 422 and "not a directory" in r.json()["message"]

    r = await env.client.post("/api/agents", json=_body(working_directory="relative/dir"))
    assert r.status_code == 422 and "absolute" in r.json()["message"]
    assert (await env.client.get("/api/agents")).json() == []


async def test_tilde_expands_to_home(env, monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "work").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    r = await env.client.post("/api/agents", json=_body(working_directory="~/work"))
    assert r.status_code == 201, r.text
    assert r.json()["working_directory"] == str((home / "work").resolve())


async def test_tool_access_requires_directory_on_create(env):
    r = await env.client.post("/api/agents", json=_body(tool_access="read_only"))
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    assert "working directory" in r.json()["message"]
    r = await env.client.post(
        "/api/agents", json=_body(tool_access="read_only", working_directory="   ")
    )
    assert r.status_code == 422 and "working directory" in r.json()["message"]


async def test_patch_directory_and_tool_access_rules(env, tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    agent = await env.agent("Ada")
    url = f"/api/agents/{agent['id']}"

    r = await env.client.patch(url, json={"tool_access": "read_only"})
    assert r.status_code == 422 and r.json()["agent_id"] == agent["id"]
    assert "working directory" in r.json()["message"]

    r = await env.client.patch(
        url, json={"working_directory": str(one), "tool_access": "read_only"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["working_directory"] == str(one.resolve()) and r.json()["revision"] == 2

    # The same directory written differently is not an edit.
    r = await env.client.patch(url, json={"working_directory": str(one / ".")})
    assert r.json()["revision"] == 2

    r = await env.client.patch(url, json={"working_directory": str(two)})
    assert r.json()["working_directory"] == str(two.resolve()) and r.json()["revision"] == 3

    r = await env.client.patch(url, json={"tool_access": "read_write"})
    assert r.json()["tool_access"] == "read_write" and r.json()["revision"] == 4

    # Clearing the directory while tools are enabled is refused...
    r = await env.client.patch(url, json={"working_directory": ""})
    assert r.status_code == 422 and "working directory" in r.json()["message"]
    # ...but allowed together with turning tools off.
    r = await env.client.patch(url, json={"working_directory": "", "tool_access": "none"})
    assert r.status_code == 200, r.text
    assert r.json()["working_directory"] is None and r.json()["tool_access"] == "none"
    assert r.json()["revision"] == 5

    r = await env.client.patch(url, json={"working_directory": str(tmp_path / "missing")})
    assert r.status_code == 422 and "does not exist" in r.json()["message"]
    assert r.json()["agent_id"] == agent["id"]
    final = (await env.client.get("/api/agents")).json()[0]
    assert final["working_directory"] is None and final["revision"] == 5


async def test_directory_survives_restart(tmp_path):
    from .conftest import running_app

    project = tmp_path / "proj"
    project.mkdir()
    async with running_app(tmp_path / "data") as first:
        r = await first.client.post(
            "/api/agents", json=_body(working_directory=str(project), tool_access="read_only")
        )
        agent_id = r.json()["id"]
    async with running_app(tmp_path / "data") as second:
        (agent,) = (await second.client.get("/api/agents")).json()
        assert agent["id"] == agent_id and agent["tool_access"] == "read_only"
        assert Path(agent["working_directory"]) == project.resolve()
