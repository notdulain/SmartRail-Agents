"""Runs with working directories and file tools: requests, sessions, tool-call events."""

import asyncio
import copy
import json
import sqlite3

from backend.application.runs import TOOL_CANCELLED, TOOL_INTERRUPTED
from backend.config import AppConfig
from backend.contracts.fake_runtime import DEFAULT_PROVIDERS, FakeRuntime
from backend.contracts.models import ErrorCode, ToolCall, ToolCallStatus
from backend.contracts.runtime import Failed, TextDelta, ToolActivity

from .conftest import GPT, MINI, fresh_runtime, running_app, wait_for


async def _agent(env, name, directory=None, access="none", model=GPT):
    body = {"name": name, "persona": f"I am {name}.", "provider_id": model[0], "model_id": model[1]}
    if directory is not None:
        body["working_directory"] = str(directory)
    body["tool_access"] = access
    r = await env.client.post("/api/agents", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def test_direct_request_carries_directory_and_tool_access(env, tmp_path):
    ada = await _agent(env, "Ada", tmp_path, "read_write")
    conv = await env.direct(ada["id"])
    await env.run_to_end(conv)
    (request,) = env.runtime.requests
    assert request.directory == ada["working_directory"]
    assert request.tool_access == "read_write"
    assert env.runtime.sessions[request.session_id] == ada["working_directory"]


async def test_agent_without_directory_gets_neutral_session(env):
    ada = await env.agent("Ada")
    conv = await env.direct(ada["id"])
    await env.run_to_end(conv)
    (request,) = env.runtime.requests
    assert request.directory is None and request.tool_access == "none"
    assert env.runtime.sessions[request.session_id] is None


async def test_group_participants_and_coordinator_use_their_own_directories(env, tmp_path):
    dirs = {n: tmp_path / n for n in ("ada", "bob", "coord")}
    for d in dirs.values():
        d.mkdir()
    ada = await _agent(env, "Ada", dirs["ada"], "read_only")
    bob = await _agent(env, "Bob")
    coord = await _agent(env, "Coord", dirs["coord"], "read_write", MINI)
    await env.set_coordinator(coord["id"])
    conv = await env.group([ada["id"], bob["id"]])
    await env.run_to_end(conv)
    by_model = {}
    for req in env.runtime.requests:
        by_model.setdefault((req.model_id, req.directory, req.tool_access), 0)
        by_model[(req.model_id, req.directory, req.tool_access)] += 1
    assert by_model == {
        ("gpt-6-mini", coord["working_directory"], "read_write"): 2,
        ("gpt-6-sol", ada["working_directory"], "read_only"): 2,
        ("gpt-6-sol", None, "none"): 2,
    }
    for req in env.runtime.requests:
        assert env.runtime.sessions[req.session_id] == req.directory


async def test_new_session_when_directory_changes(env, tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    ada = await _agent(env, "Ada", one, "read_only")
    conv = await env.direct(ada["id"])
    await env.run_to_end(conv, "first question")
    await env.run_to_end(conv, "second question")
    first, second = env.runtime.requests
    assert first.session_id == second.session_id
    assert second.user_text == "second question"  # the session already has the history

    r = await env.client.patch(f"/api/agents/{ada['id']}", json={"working_directory": str(two)})
    assert r.status_code == 200
    await env.run_to_end(conv, "third question")
    third = env.runtime.requests[2]
    assert third.session_id != first.session_id
    assert env.runtime.sessions[third.session_id] == str(two.resolve())
    assert third.directory == str(two.resolve())
    # The new session is told what was said before.
    assert third.user_text.startswith("Earlier messages in this conversation:")
    assert "first question" in third.user_text and "second question" in third.user_text
    assert third.user_text.endswith("New message:\nthird question")

    # Changing only tool access keeps the session.
    await env.client.patch(f"/api/agents/{ada['id']}", json={"tool_access": "read_write"})
    await env.run_to_end(conv, "fourth")
    fourth = env.runtime.requests[3]
    assert fourth.session_id == third.session_id and fourth.tool_access == "read_write"
    assert fourth.user_text == "fourth"


async def test_group_session_replaced_on_directory_change_sees_whole_transcript(env, tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    ada = await _agent(env, "Ada", one, "read_only")
    bob = await _agent(env, "Bob")
    coord = await _agent(env, "Coord", model=MINI)
    await env.set_coordinator(coord["id"])
    conv = await env.group([ada["id"], bob["id"]])
    await env.run_to_end(conv, "round one")
    ada_first = [r for r in env.runtime.requests if r.directory == str(one.resolve())]
    await env.client.patch(f"/api/agents/{ada['id']}", json={"working_directory": str(two)})
    env.runtime.requests.clear()
    await env.run_to_end(conv, "round two")
    ada_turns = [r for r in env.runtime.requests if r.directory == str(two.resolve())]
    assert len(ada_turns) == 2
    assert ada_turns[0].session_id != ada_first[0].session_id
    # The fresh session gets the earlier exchange, including the first user message.
    assert "round one" in ada_turns[0].user_text and "round two" in ada_turns[0].user_text
    # Bob's session was reused and only sees what is new.
    bob_turn = next(
        r for r in env.runtime.requests if r.directory is None and "Round 1" in r.user_text
    )
    assert "round one" not in bob_turn.user_text


async def test_tool_calls_are_streamed_persisted_and_replayed(tmp_path):
    runtime = fresh_runtime()
    runtime.tool_models[GPT] = [("read", "src/app.py"), ("grep", "TODO")]
    async with running_app(tmp_path / "data", runtime) as env:
        ada = await _agent(env, "Ada", tmp_path, "read_only")
        conv = await env.direct(ada["id"])
        run = await env.run_to_end(conv)
        frames = await env.events(run["id"])
        types = [f["event"] for f in frames]
        assert types[:2] == ["run.started", "message.started"]
        tool_frames = [f["json"] for f in frames if f["event"] == "message.tool"]
        assert [(t["tool_call"]["tool"], t["tool_call"]["status"]) for t in tool_frames] == [
            ("read", "running"),
            ("read", "completed"),
            ("grep", "running"),
            ("grep", "completed"),
        ]
        assert types.index("message.tool") < types.index("message.delta")
        message_id = frames[1]["json"]["message"]["id"]
        assert all(t["message_id"] == message_id for t in tool_frames)
        completed = next(f["json"] for f in frames if f["event"] == "message.completed")
        expected = [
            {"tool": "read", "title": "src/app.py", "status": "completed", "error": None},
            {"tool": "grep", "title": "TODO", "status": "completed", "error": None},
        ]
        strip = [
            {k: c[k] for k in ("tool", "title", "status", "error")}
            for c in completed["message"]["tool_calls"]
        ]
        assert strip == expected
        msgs = await env.messages(conv)
        assert msgs[1]["tool_calls"] == completed["message"]["tool_calls"]
        # Reconnecting mid-way replays the remaining events, tool events included.
        later = await env.events(run["id"], headers={"Last-Event-ID": "3"})
        assert [f["json"]["seq"] for f in later] == list(range(4, len(frames) + 1))

    async with running_app(tmp_path / "data", fresh_runtime()) as again:
        msgs = await again.messages(conv)
        assert [c["tool"] for c in msgs[1]["tool_calls"]] == ["read", "grep"]
        assert await again.events(run["id"]) == frames


class ToolStallRuntime(FakeRuntime):
    """Starts a tool call, writes a little text, then never finishes (for Stop/restart tests)."""

    def __init__(self) -> None:
        super().__init__(providers=copy.deepcopy(DEFAULT_PROVIDERS))
        self.stalled = asyncio.Event()

    async def stream(self, request):
        self.requests.append(request)
        done = ToolCall(id="c1", tool="read", title="a.txt", status=ToolCallStatus.COMPLETED)
        yield ToolActivity(done)
        yield ToolActivity(
            ToolCall(id="c2", tool="edit", title="b.txt", status=ToolCallStatus.RUNNING)
        )
        yield TextDelta("working")
        self.stalled.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await self.abort(request.session_id)
            raise


async def test_stop_marks_running_tool_calls_cancelled(tmp_path):
    runtime = ToolStallRuntime()
    async with running_app(tmp_path / "data", runtime) as env:
        ada = await _agent(env, "Ada", tmp_path, "read_write")
        conv = await env.direct(ada["id"])
        sent = await env.send(conv)
        await wait_for(runtime.stalled.is_set)
        mid = (await env.messages(conv))[1]
        assert mid["status"] == "streaming"
        assert [(c["id"], c["status"]) for c in mid["tool_calls"]] == [
            ("c1", "completed"),
            ("c2", "running"),
        ]
        r = await env.client.post(f"/api/runs/{sent['run_id']}/stop")
        assert r.json()["status"] == "cancelled"
        msg = (await env.messages(conv))[1]
        assert msg["status"] == "cancelled" and msg["content"] == "working"
        assert [(c["status"], c["error"]) for c in msg["tool_calls"]] == [
            ("completed", None),
            ("error", TOOL_CANCELLED),
        ]
        frames = await env.events(sent["run_id"])
        completed = next(f["json"] for f in frames if f["event"] == "message.completed")
        assert completed["message"]["tool_calls"] == msg["tool_calls"]
        assert [f["event"] for f in frames].count("message.tool") == 2


async def test_restart_marks_running_tool_calls_interrupted(tmp_path):
    data = tmp_path / "data"
    async with running_app(data) as env:
        ada = await env.agent("Ada")
        conv = await env.direct(ada["id"])
        await env.run_to_end(conv)
    con = sqlite3.connect(AppConfig(data_dir=data).db_path)
    try:
        agent_json = json.dumps(
            {
                "agent_id": ada["id"],
                "name": "Ada",
                "persona": "p",
                "provider_id": "openai",
                "model_id": "gpt-6-sol",
                "revision": 1,
            }
        )
        calls = json.dumps(
            [{"id": "c9", "tool": "write", "title": "x.md", "status": "running", "error": None}]
        )
        con.execute(
            "INSERT INTO runs (id, conversation_id, status, participants_json, created_at)"
            " VALUES ('run_dead', ?, 'running', '[]', '2026-01-01T00:00:00+00:00')",
            (conv,),
        )
        con.execute(
            "INSERT INTO messages (id, conversation_id, run_id, role, speaker_name, agent_json,"
            " provider_id, model_id, content, status, tool_calls_json, created_at)"
            " VALUES ('msg_dead', ?, 'run_dead', 'agent', 'Ada', ?, 'openai', 'gpt-6-sol',"
            " '', 'streaming', ?, '2026-01-01T00:00:01+00:00')",
            (conv, agent_json, calls),
        )
        con.commit()
    finally:
        con.close()
    async with running_app(data) as env:
        msg = next(m for m in await env.messages(conv) if m["id"] == "msg_dead")
        assert msg["status"] == "failed"
        assert msg["tool_calls"][0]["status"] == "error"
        assert msg["tool_calls"][0]["error"] == TOOL_INTERRUPTED


class ToolThenFailRuntime(FakeRuntime):
    async def stream(self, request):
        self.requests.append(request)
        yield ToolActivity(
            ToolCall(id="c1", tool="read", title="a.txt", status=ToolCallStatus.RUNNING)
        )
        yield Failed(ErrorCode.RATE_LIMITED, "slow down")


async def test_failure_settles_running_tool_calls(tmp_path):
    runtime = ToolThenFailRuntime(providers=copy.deepcopy(DEFAULT_PROVIDERS))
    async with running_app(tmp_path / "data", runtime) as env:
        ada = await _agent(env, "Ada", tmp_path, "read_only")
        conv = await env.direct(ada["id"])
        run = await env.run_to_end(conv)
        assert run["status"] == "failed" and run["error_code"] == "rate_limited"
        msg = (await env.messages(conv))[1]
        assert msg["status"] == "failed"
        assert msg["tool_calls"] == [
            {
                "id": "c1",
                "tool": "read",
                "title": "a.txt",
                "status": "error",
                "error": "interrupted",
            }
        ]
