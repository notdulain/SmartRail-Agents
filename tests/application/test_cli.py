"""The ``smartrail ask`` command line, driven against the real app on FakeRuntime."""

import asyncio
import json

import httpx
import pytest

from backend import cli

from .conftest import GatedRuntime, running_app


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(cli, "POLL_S", 0.01)
    monkeypatch.setattr(cli, "BUSY_RETRY_S", 0.02)


def agents(*names):
    return [{"id": f"agt_{n.lower()}", "name": n} for n in names]


def test_pick_agent_by_id_name_or_unique_fragment():
    found = agents("Station Manager", "UI Designer", "Signal Engineer")
    assert cli.pick_agent(found, "agt_ui designer")["name"] == "UI Designer"
    assert cli.pick_agent(found, "ui designer")["name"] == "UI Designer"
    assert cli.pick_agent(found, "signal")["name"] == "Signal Engineer"


def test_pick_agent_rejects_unknown_and_ambiguous():
    found = agents("Station Manager", "Station Designer")
    with pytest.raises(cli.CliError, match="several agents") as ambiguous:
        cli.pick_agent(found, "station")
    with pytest.raises(cli.CliError, match="No agent matches") as unknown:
        cli.pick_agent(found, "nobody")
    assert ambiguous.value.code == unknown.value.code == cli.EXIT_USAGE


async def test_ask_by_name_creates_a_direct_conversation_and_returns_the_reply(env):
    ada = await env.agent("Ada Designer")
    reply = await cli.ask(
        env.client, "Should the platform sign be blue?", agent_ref="ada", label="Claude Code"
    )
    assert reply.ok and reply.status == "completed"
    assert len(reply.messages) == 1
    assert reply.text.startswith("[gpt-6-sol]")

    request = env.runtime.requests[0]
    assert "Should the platform sign be blue?" in request.user_text
    assert "Claude Code" in request.user_text

    detail = (await env.client.get(f"/api/conversations/{reply.conversation_id}")).json()
    conversation = detail["conversation"]
    assert conversation["type"] == "direct"
    assert conversation["participant_ids"] == [ada["id"]]
    assert conversation["title"].startswith("Claude Code → Ada Designer: Should the platform")


async def test_follow_up_continues_the_same_conversation(env):
    await env.agent("Ada")
    first = await cli.ask(env.client, "first question", agent_ref="Ada")
    second = await cli.ask(env.client, "and a follow-up", conversation_id=first.conversation_id)
    assert second.conversation_id == first.conversation_id
    assert second.run_id != first.run_id
    assert len(await env.messages(first.conversation_id)) == 4
    assert len({r.session_id for r in env.runtime.requests}) == 1  # same agent session


async def test_ask_waits_for_the_busy_run_slot(tmp_path):
    runtime = GatedRuntime()
    runtime.gate.clear()
    async with running_app(tmp_path / "data", runtime) as env:
        ada = await env.agent("Ada")
        busy = await env.direct(ada["id"])
        sent = await env.send(busy)
        await runtime.arrived.wait()

        notes: list[str] = []
        waiting = asyncio.create_task(
            cli.ask(env.client, "me too", agent_ref="Ada", log=notes.append)
        )
        await asyncio.sleep(0.15)
        assert not waiting.done()
        runtime.gate.set()
        await env.wait(sent["run_id"])
        reply = await asyncio.wait_for(waiting, 10)
        assert reply.ok
        assert any("waiting" in n for n in notes)


async def test_timeout_leaves_the_run_going_and_reply_collects_it(tmp_path):
    runtime = GatedRuntime()
    runtime.gate.clear()
    async with running_app(tmp_path / "data", runtime) as env:
        await env.agent("Ada")
        with pytest.raises(cli.CliError, match="smartrail reply") as timed_out:
            await cli.ask(env.client, "slow one", agent_ref="Ada", timeout_s=0.1)
        assert timed_out.value.code == cli.EXIT_TIMEOUT
        conv = (await env.client.get("/api/conversations")).json()[0]["id"]

        runtime.gate.set()
        reply = await cli.latest_reply(env.client, conv, timeout_s=10)
        assert reply.ok and reply.text.startswith("[gpt-6-sol]")


async def test_failed_run_reports_the_error(env):
    from backend.contracts.models import ErrorCode
    from backend.contracts.runtime import Failed

    from .conftest import GPT

    await env.agent("Ada")
    env.runtime.fail_models[GPT] = Failed(ErrorCode.RUNTIME_ERROR, "model exploded")
    reply = await cli.ask(env.client, "hello", agent_ref="Ada")
    assert not reply.ok and reply.status == "failed"
    assert "model exploded" in json.dumps(reply.as_json())


async def test_message_validation(env):
    await env.agent("Ada")
    with pytest.raises(cli.CliError, match="empty"):
        await cli.ask(env.client, "   ", agent_ref="Ada")
    with pytest.raises(cli.CliError, match="too long"):
        await cli.ask(env.client, "x" * 20_000, agent_ref="Ada")
    with pytest.raises(cli.CliError, match="not found|Conversation"):
        await cli.ask(env.client, "hi", conversation_id="cnv_missing")


async def test_main_prints_reply_on_stdout_and_conversation_on_stderr(env, capsys):
    await env.agent("Ada")
    transport = httpx.ASGITransport(app=env.app)

    code = await cli.main_async(["ask", "Ada", "hello there"], transport=transport)
    out, err = capsys.readouterr()
    assert code == cli.EXIT_OK
    assert out.startswith("[gpt-6-sol]")
    assert "conversation cnv_" in err

    code = await cli.main_async(["agents", "--json"], transport=transport)
    listed = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK and [a["name"] for a in listed] == ["Ada"]

    code = await cli.main_async(["ask", "Nobody", "hi"], transport=transport)
    assert code == cli.EXIT_USAGE
    assert "No agent matches" in capsys.readouterr().err


async def test_main_json_output(env, capsys):
    await env.agent("Ada")
    transport = httpx.ASGITransport(app=env.app)
    code = await cli.main_async(["ask", "--json", "Ada", "hi"], transport=transport)
    data = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK
    assert data["status"] == "completed"
    assert data["replies"][0]["agent"] == "Ada"


async def test_unreachable_app_has_its_own_exit_code(capsys):
    transport = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused"))
    )
    code = await cli.main_async(["agents"], transport=transport)
    assert code == cli.EXIT_UNREACHABLE
    assert "uv run smartrail" in capsys.readouterr().err
