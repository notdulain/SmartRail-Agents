"""Optional live checks against a real, isolated ``opencode serve`` (no real model calls).

Skipped unless ``SMARTRAIL_LIVE_OPENCODE_TESTS=1`` and ``opencode`` is on PATH. It uses temp
XDG directories (never the user's real OpenCode data) and the port in
``SMARTRAIL_LIVE_OPENCODE_PORT`` (default 4171). OpenCode is given one extra provider: a local
scripted OpenAI-compatible model (``scripted_model``, port ``SMARTRAIL_LIVE_MODEL_PORT``,
default random), so real OpenCode executes real tool calls and enforces its permissions
without any real provider. It proves: the neutral agent has no tools; each tool level gets
exactly its tools; nothing reaches outside the working directory (also from a subfolder of a
git repository); shell and other tools are never available; sessions live in their directory.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from backend.config import AppConfig
from backend.contracts.models import ToolAccess, ToolCallStatus
from backend.contracts.runtime import Completed, CompletionRequest, ToolActivity
from backend.launcher import ManagedProcess, opencode_args, opencode_env
from backend.runtime import AGENT_NAME
from backend.runtime.agent_config import (
    ALWAYS_DENIED,
    READ_AGENT_NAME,
    WRITE_AGENT_NAME,
    session_permission,
)
from backend.runtime.opencode import HttpOpenCodeRuntime

from .conftest import make_config
from .opencode_rules import FILE_TOOLS, evaluate, offered
from .scripted_model import MODEL_ID, PROVIDER_ID, REPLY, ScriptedModel

pytestmark = pytest.mark.skipif(
    os.environ.get("SMARTRAIL_LIVE_OPENCODE_TESTS") != "1" or not shutil.which("opencode"),
    reason="set SMARTRAIL_LIVE_OPENCODE_TESTS=1 (and install opencode) to run",
)

OUTSIDE = "Not allowed: outside the working directory"
UNAVAILABLE = "Tool not available to this agent"


@dataclass
class Live:
    config: AppConfig
    model: ScriptedModel
    root: Path  # scratch: root/secret.txt, root/plain/, root/repo/sub/ (a git repo, if git)
    env: dict[str, str]

    def client(self) -> httpx.Client:
        auth = httpx.BasicAuth(self.config.opencode_username, self.config.opencode_password)
        return httpx.Client(base_url=self.config.opencode_url, auth=auth, timeout=10)


@pytest.fixture(scope="module")
def live(tmp_path_factory) -> Iterator[Live]:
    port = int(os.environ.get("SMARTRAIL_LIVE_OPENCODE_PORT", "4171"))
    config = make_config(f"http://127.0.0.1:{port}", password="live-test-pw")
    model = ScriptedModel(int(os.environ.get("SMARTRAIL_LIVE_MODEL_PORT", "0")))
    model.start()
    base = tmp_path_factory.mktemp("opencode-live").resolve()
    env = opencode_env(config)
    for name in ("DATA", "CONFIG", "STATE", "CACHE"):
        directory = base / name.lower()
        directory.mkdir()
        env[f"XDG_{name}_HOME"] = str(directory)
    settings = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    settings["provider"] = model.provider_config()
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(settings)
    root = base / "files"
    (root / "plain").mkdir(parents=True)
    (root / "plain" / "notes.txt").write_text("hello\n")
    (root / "secret.txt").write_text("outside\n")
    if shutil.which("git"):
        repo = root / "repo"
        (repo / "sub").mkdir(parents=True)
        (repo / "root.txt").write_text("repository root\n")
        (repo / "sub" / "inner.txt").write_text("inner\n")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    cwd = base / "neutral"
    cwd.mkdir()
    child = ManagedProcess(opencode_args(shutil.which("opencode"), port), env=env, cwd=cwd)
    try:
        state = Live(config, model, root, env)
        deadline = time.monotonic() + 40
        with state.client() as client:
            while True:
                try:
                    if client.get("/global/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, "opencode did not start"
                time.sleep(0.25)
        yield state
    finally:
        child.stop()
        model.stop()


# ----------------------------------------------------------------------------- configuration


def same_directory(a: str, b: Path) -> bool:
    return Path(a).resolve() == b.resolve()


def agent_rules(agents: list[dict], name: str) -> list[dict[str, str]]:
    return next(a for a in agents if a["name"] == name)["permission"]


def test_live_agents_have_exactly_their_tools(live: Live):
    with live.client() as client:
        agents = client.get("/agent").json()
        settings = client.get("/config").json()
        tool_ids = client.get("/experimental/tool/ids").json()
    visible = {a["name"] for a in agents if not a.get("hidden")}
    assert visible == {AGENT_NAME, READ_AGENT_NAME, WRITE_AGENT_NAME}
    wildcard = [r for r in agent_rules(agents, AGENT_NAME) if r["permission"] == "*"]
    assert wildcard[-1] == {"permission": "*", "pattern": "*", "action": "deny"}
    assert settings["tools"] == {"*": False}
    assert settings["mcp"] == {} and settings["plugin"] == []

    # Every tool OpenCode registers is a file tool or always denied: a new tool in a future
    # OpenCode release must be reviewed before it can slip through.
    assert set(tool_ids) - {"invalid"} <= set(FILE_TOOLS) | set(ALWAYS_DENIED)
    expected = {
        AGENT_NAME: set(),
        READ_AGENT_NAME: {"read", "glob", "grep"},
        WRITE_AGENT_NAME: {"read", "glob", "grep", "edit", "write", "apply_patch"},
    }
    for name, tools in expected.items():
        rules = agent_rules(agents, name)
        assert offered(rules, tool_ids) == tools & set(tool_ids), name
        for access in ToolAccess:  # the session ruleset decides, whatever the agent allows
            session_tools = offered(rules + session_permission(access), tool_ids)
            assert not session_tools & set(ALWAYS_DENIED)
        if name != AGENT_NAME:
            assert evaluate(rules, "external_directory", "/elsewhere/*") == "deny"
            assert evaluate(rules, "bash", "ls") == "deny"
            assert evaluate(rules, "read", "x/.env") == "deny"
        # no "ask" may remain effective for anything the agent could be asked about
        for permission in ("read", "edit", "glob", "grep", "external_directory", "doom_loop"):
            assert evaluate(rules, permission, "x/y.txt") in ("allow", "deny")


# ----------------------------------------------------------------------------- real tool turns


def calls(*steps: list[dict]) -> str:
    return "CALLS: " + json.dumps(list(steps))


def call(name: str, **arguments) -> dict:
    return {"name": name, "arguments": arguments}


async def turn(rt, model, session, directory, access, script):
    before = len(model.offered)
    request = CompletionRequest(
        session, PROVIDER_ID, MODEL_ID, "You are a test persona.", script, 4000, directory, access
    )
    events = [e async for e in rt.stream(request)]
    offered_tools = model.offered[before:]
    assert offered_tools and all(t == offered_tools[0] for t in offered_tools)
    final = {
        e.call.title: (e.call.tool, e.call.status, e.call.error)
        for e in events
        if isinstance(e, ToolActivity) and e.call.status is not ToolCallStatus.RUNNING
    }
    return events, set(offered_tools[0]), final


async def test_live_directory_session_and_tool_levels(live: Live):
    work = live.root / "plain"
    rt = HttpOpenCodeRuntime(live.config)
    try:
        session = await rt.create_session("live", str(work))
        with live.client() as client:
            info = client.get(f"/session/{session}").json()
            listed = client.get("/session", params={"directory": str(work)}).json()
        assert same_directory(info["directory"], work)
        assert info["permission"] == session_permission(ToolAccess.NONE)
        assert session in {s["id"] for s in listed}  # it lives in the directory's instance

        events, tools, final = await turn(
            rt,
            live.model,
            session,
            str(work),
            ToolAccess.READ_ONLY,
            calls(
                [
                    call("read", filePath="notes.txt"),
                    call("read", filePath="../secret.txt"),
                    call("read", filePath=str(live.root / "secret.txt")),
                    call("write", filePath="x.txt", content="no"),
                    call("bash", command="echo hi"),
                ]
            ),
        )
        assert tools == {"read", "glob", "grep"}
        assert final["notes.txt"] == ("read", ToolCallStatus.COMPLETED, None)
        assert final["../secret.txt"] == ("read", ToolCallStatus.ERROR, OUTSIDE)
        assert final[str(live.root / "secret.txt")][2] == OUTSIDE
        assert final["x.txt"] == ("write", ToolCallStatus.ERROR, UNAVAILABLE)
        assert final["bash"] == ("bash", ToolCallStatus.ERROR, UNAVAILABLE)
        assert events[-1] == Completed(REPLY, events[-1].usage)
        assert not (work / "x.txt").exists()

        events, tools, final = await turn(
            rt,
            live.model,
            session,
            str(work),
            ToolAccess.READ_WRITE,
            calls(
                [
                    call("write", filePath="new/deep.txt", content="made"),
                    call("write", filePath="../escape.txt", content="no"),
                ],
                [call("edit", filePath="notes.txt", oldString="hello", newString="hello again")],
            ),
        )
        assert {"read", "glob", "grep", "edit", "write"} <= tools <= set(FILE_TOOLS)
        assert final["new/deep.txt"][1] is ToolCallStatus.COMPLETED
        assert final["../escape.txt"][2] == OUTSIDE
        assert final["notes.txt"][1] is ToolCallStatus.COMPLETED
        assert (work / "new" / "deep.txt").read_text() == "made"
        assert (work / "notes.txt").read_text().startswith("hello again")
        assert not (live.root / "escape.txt").exists()

        events, tools, final = await turn(
            rt,
            live.model,
            session,
            str(work),
            ToolAccess.NONE,
            calls([call("read", filePath="notes.txt")]),
        )
        assert tools == set() and final == {}
        assert isinstance(events[-1], Completed)

        neutral = await rt.create_session("neutral")
        events, tools, final = await turn(
            rt,
            live.model,
            neutral,
            None,
            ToolAccess.READ_WRITE,
            calls([call("read", filePath="notes.txt")]),
        )
        assert tools == set() and final == {}
    finally:
        await rt.aclose()


@pytest.mark.skipif(not shutil.which("git"), reason="git is not installed")
async def test_live_git_subfolder_is_confined(live: Live):
    repo = live.root / "repo"
    sub = repo / "sub"
    rt = HttpOpenCodeRuntime(live.config)
    try:
        session = await rt.create_session("git", str(sub))
        events, tools, final = await turn(
            rt,
            live.model,
            session,
            str(sub),
            ToolAccess.READ_WRITE,
            calls(
                [
                    call("read", filePath="inner.txt"),
                    call("read", filePath="../root.txt"),
                    call("write", filePath="made/here.txt", content="ok"),
                    call("write", filePath="../made-outside.txt", content="no"),
                    call("glob", pattern="*", path=".."),
                ]
            ),
        )
        # OpenCode itself would allow the whole repository; the session rules confine it
        assert "read" in tools and not tools & {"glob", "grep"}
        assert final["inner.txt"][1] is ToolCallStatus.COMPLETED
        assert final["../root.txt"][2] == OUTSIDE
        assert final["made/here.txt"][1] is ToolCallStatus.COMPLETED
        assert final["../made-outside.txt"][2] == OUTSIDE
        assert final["* in .."][2] == UNAVAILABLE
        assert not (repo / "made-outside.txt").exists()
    finally:
        await rt.aclose()
