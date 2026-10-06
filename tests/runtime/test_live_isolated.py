"""Optional live check against a real, isolated ``opencode serve`` (no model calls).

Skipped unless ``SMARTRAIL_LIVE_OPENCODE_TESTS=1`` and ``opencode`` is on PATH. It uses temp
XDG directories (never the user's real OpenCode data) and the port in
``SMARTRAIL_LIVE_OPENCODE_PORT`` (default 4171). It proves the neutral agent has no tools.
"""

from __future__ import annotations

import json
import os
import shutil
import time

import httpx
import pytest

from backend.launcher import ManagedProcess, opencode_args, opencode_env
from backend.runtime import AGENT_NAME

from .conftest import make_config

pytestmark = pytest.mark.skipif(
    os.environ.get("SMARTRAIL_LIVE_OPENCODE_TESTS") != "1" or not shutil.which("opencode"),
    reason="set SMARTRAIL_LIVE_OPENCODE_TESTS=1 (and install opencode) to run",
)


def test_live_agent_has_no_tools(tmp_path):
    port = int(os.environ.get("SMARTRAIL_LIVE_OPENCODE_PORT", "4171"))
    config = make_config(f"http://127.0.0.1:{port}", password="live-test-pw")
    env = opencode_env(config)
    for name in ("DATA", "CONFIG", "STATE", "CACHE"):
        directory = tmp_path / name.lower()
        directory.mkdir()
        env[f"XDG_{name}_HOME"] = str(directory)
    child = ManagedProcess(opencode_args(shutil.which("opencode"), port), env=env, cwd=tmp_path)
    try:
        auth = httpx.BasicAuth(config.opencode_username, config.opencode_password)
        deadline = time.monotonic() + 40
        with httpx.Client(base_url=config.opencode_url, auth=auth, timeout=5) as client:
            while True:
                try:
                    if client.get("/global/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, "opencode did not start"
                time.sleep(0.25)
            agents = client.get("/agent").json()
            settings = client.get("/config").json()
            tools = client.get("/experimental/tool/ids").json()
        visible = [a["name"] for a in agents if not a.get("hidden")]
        assert visible == [AGENT_NAME]
        agent = next(a for a in agents if a["name"] == AGENT_NAME)
        wildcard = [r for r in agent["permission"] if r["permission"] == "*"]
        assert wildcard[-1] == {"permission": "*", "pattern": "*", "action": "deny"}
        assert settings["tools"] == {"*": False}
        assert settings["mcp"] == {} and settings["plugin"] == []
        assert json.loads(env["OPENCODE_CONFIG_CONTENT"])["tools"] == {"*": False}
        assert isinstance(tools, list)  # registry still lists tools; the agent may use none
    finally:
        child.stop()
