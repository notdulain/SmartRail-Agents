"""The neutral runtime agent must be conversational only: no tool of any kind."""

from __future__ import annotations

import json

from backend.runtime import AGENT_NAME, build_opencode_config


def test_config_is_json_serialisable_and_fresh_each_call():
    first = build_opencode_config()
    json.dumps(first)
    first["agent"][AGENT_NAME]["prompt"] = "mutated"
    assert build_opencode_config()["agent"][AGENT_NAME]["prompt"] != "mutated"


def test_global_and_agent_tools_are_all_disabled():
    config = build_opencode_config()
    agent = config["agent"][AGENT_NAME]
    assert config["tools"] == {"*": False}
    assert agent["tools"] == {"*": False}
    assert config["permission"] == {"*": "deny"}
    assert agent["permission"] == {"*": "deny"}
    assert "steps" not in agent and "maxSteps" not in agent


def test_no_mcp_plugins_skills_lsp_formatters_or_project_rules():
    config = build_opencode_config()
    assert config["mcp"] == {}
    assert config["plugin"] == []
    assert config["instructions"] == []
    assert config["skills"] == {"paths": [], "urls": []}
    assert config["lsp"] is False
    assert config["formatter"] is False
    assert config["subagent_depth"] == 0
    assert config["experimental"]["batch_tool"] is False
    assert "command" not in config


def test_autoupdate_share_snapshot_off():
    config = build_opencode_config()
    assert config["autoupdate"] is False
    assert config["autoshare"] is False
    assert config["share"] == "disabled"
    assert config["snapshot"] is False


def test_neutral_agent_is_default_and_builtin_coding_agents_are_disabled():
    config = build_opencode_config()
    assert config["default_agent"] == AGENT_NAME
    assert config["agent"][AGENT_NAME]["mode"] == "primary"
    for builtin in ("build", "plan", "general", "explore"):
        assert config["agent"][builtin] == {"disable": True}


def test_neutral_prompt_replaces_the_coding_prompt_and_forbids_tools():
    prompt = build_opencode_config()["agent"][AGENT_NAME]["prompt"]
    assert "cannot read or write files" in prompt
    assert "persona" in prompt
