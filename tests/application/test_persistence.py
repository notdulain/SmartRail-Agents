import json
import sqlite3

from backend.application.db import SCHEMA_VERSION
from backend.config import AppConfig

from .conftest import GPT, fresh_runtime, running_app


async def test_chats_and_sessions_survive_restart(tmp_path):
    data = tmp_path / "data"
    async with running_app(data) as first:
        ada = await first.agent("Ada", GPT)
        await first.set_coordinator(ada["id"])
        await first.client.patch("/api/settings", json={"project_brief": "brief"})
        conv = await first.direct(ada["id"])
        run = await first.run_to_end(conv, "remember me")
        msgs_before = await first.messages(conv)
        events_before = await first.events(run["id"])
        session_before = first.runtime.requests[0].session_id

    runtime = fresh_runtime()
    async with running_app(data, runtime) as second:
        assert [a["id"] for a in (await second.client.get("/api/agents")).json()] == [ada["id"]]
        assert (await second.client.get("/api/settings")).json()["project_brief"] == "brief"
        assert (await second.messages(conv)) == msgs_before
        assert (await second.client.get(f"/api/runs/{run['id']}")).json()["status"] == "completed"
        assert await second.events(run["id"]) == events_before  # replay from the persisted log
        assert runtime.requests == []
        await second.run_to_end(conv, "and again")
        assert runtime.requests[0].session_id == session_before  # session mapping persisted
        assert len(await second.messages(conv)) == 4


async def test_database_setup(tmp_path):
    data = tmp_path / "nested" / "data"
    async with running_app(data) as env:
        await env.agent("Ada")
    db_path = AppConfig(data_dir=data).db_path
    assert db_path.exists()
    con = sqlite3.connect(db_path)
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    assert {
        "agents",
        "conversations",
        "participants",
        "messages",
        "runs",
        "run_events",
        "session_map",
        "settings",
        "openrouter_spend",
    } <= tables


async def test_interrupted_runs_are_failed_on_startup_without_new_requests(tmp_path):
    data = tmp_path / "data"
    async with running_app(data) as first:
        ada = await first.agent("Ada")
        conv = await first.direct(ada["id"])
        await first.run_to_end(conv)
    # Simulate a crash mid-run: a running run with a half-written streaming message.
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
        con.execute(
            "INSERT INTO runs (id, conversation_id, status, participants_json, created_at)"
            " VALUES ('run_dead', ?, 'running', '[]', '2026-01-01T00:00:00+00:00')",
            (conv,),
        )
        con.execute(
            "INSERT INTO messages (id, conversation_id, run_id, role, speaker_name, agent_json,"
            " provider_id, model_id, content, status, created_at)"
            " VALUES ('msg_dead', ?, 'run_dead', 'agent', 'Ada', ?, 'openai', 'gpt-6-sol',"
            " 'partial te', 'streaming', '2026-01-01T00:00:01+00:00')",
            (conv, agent_json),
        )
        con.execute(
            "INSERT INTO run_events (run_id, seq, type, data) VALUES ('run_dead', 1, 'run.started',"
            " '{}')"
        )
        con.commit()
    finally:
        con.close()

    runtime = fresh_runtime()
    async with running_app(data, runtime) as second:
        run = (await second.client.get("/api/runs/run_dead")).json()
        assert run["status"] == "failed" and run["error"] == "interrupted by restart"
        msg = next(m for m in await second.messages(conv) if m["id"] == "msg_dead")
        assert msg["status"] == "failed" and msg["content"] == "partial te"
        assert msg["error"] == "interrupted by restart"
        frames = await second.events("run_dead")
        assert [f["event"] for f in frames] == ["run.started", "message.completed", "run.failed"]
        assert [int(f["id"]) for f in frames] == [1, 2, 3]
        assert runtime.requests == []
        # The slot is free: new runs work.
        assert (await second.run_to_end(conv, "go on"))["status"] == "completed"
