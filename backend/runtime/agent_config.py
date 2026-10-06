"""Inline OpenCode configuration for the single neutral, conversational-only runtime agent.

The app starts OpenCode with ``OPENCODE_CONFIG_CONTENT=<json of build_opencode_config()>``.
Every user-created SmartRail agent is only a (persona, provider, model) triple; per request
the runtime selects the model and sends the persona as ``system`` text to this one agent.

Safety posture: the agent can only produce text. Every tool (filesystem, shell, web, MCP,
task/subagent delegation, questions, todo, skills, ...) is disabled twice over: with a
``tools`` map and with a deny-everything permission ruleset. There are no MCP servers,
plugins, LSP servers, formatters, snapshots, shared sessions or auto-updates, and the
built-in coding agents are disabled so the neutral agent is the only selectable one.
"""

from __future__ import annotations

import copy
from typing import Any

AGENT_NAME = "smartrail"

NEUTRAL_PROMPT = (
    "You are a conversational assistant inside a text-only chat application. "
    "You cannot read or write files, run commands, browse the web, call tools or delegate "
    "work, and you must never pretend to. Reply with plain text (Markdown is fine). "
    "Each request carries additional system instructions that define who you are in this "
    "conversation (a persona and the discussion setting). Follow them faithfully and stay in "
    "that role."
)

# Built-in OpenCode agents that would otherwise be selectable (coding assistants, subagents).
_DISABLED_BUILTIN_AGENTS = ("build", "plan", "general", "explore")

_DENY_ALL: dict[str, str] = {"*": "deny"}

_CONFIG: dict[str, Any] = {
    "$schema": "https://opencode.ai/config.json",
    "autoupdate": False,
    "autoshare": False,
    "share": "disabled",
    "snapshot": False,
    "default_agent": AGENT_NAME,
    "subagent_depth": 0,
    # No project rules, extra instruction files, plugins, MCP servers, skills, LSP, formatters.
    "instructions": [],
    "plugin": [],
    "mcp": {},
    "skills": {"paths": [], "urls": []},
    "lsp": False,
    "formatter": False,
    "tools": {"*": False},
    "permission": dict(_DENY_ALL),
    "experimental": {"batch_tool": False},
    "agent": {
        **{name: {"disable": True} for name in _DISABLED_BUILTIN_AGENTS},
        AGENT_NAME: {
            "description": "Neutral conversational agent (no tools).",
            "mode": "primary",
            "prompt": NEUTRAL_PROMPT,
            "tools": {"*": False},
            "permission": dict(_DENY_ALL),
        },
    },
}


def build_opencode_config() -> dict[str, Any]:
    """A fresh copy of the inline OpenCode config (safe for callers to mutate)."""
    return copy.deepcopy(_CONFIG)
