"""Inline OpenCode configuration and per-session tool policy for SmartRail's runtime agents.

The app starts OpenCode with ``OPENCODE_CONFIG_CONTENT=<json of build_opencode_config()>``.
Every user-created SmartRail agent is a (persona, provider, model) triple plus an optional
working directory and a tool access level; per request the runtime selects the model, sends
the persona as ``system`` text and picks one of three OpenCode agents by tool access:

* ``smartrail`` (``none``): text only. Every tool (filesystem, shell, web, MCP, task/subagent
  delegation, questions, todo, skills, ...) is disabled twice over: with a ``tools`` map and
  with a deny-everything permission ruleset.
* ``smartrail-read`` (``read_only``): ``read`` (files and directory listings), ``glob``,
  ``grep``.
* ``smartrail-write`` (``read_write``): the above plus ``edit`` / ``write`` / ``apply_patch``
  (OpenCode governs all three with the ``edit`` permission).

Shell, web, MCP, delegation, todo, skill, question and LSP tools are denied at every level, as
is ``external_directory`` (any path outside the session's directory). No rule is ever "ask":
there is no approval UI, so an "ask" would hang the turn.

OpenCode evaluates ``agent rules + session rules`` and the *last* matching rule wins (a
permission with no matching rule at all means "ask", which is why every ruleset here starts with
``* deny``). The agents' own rules describe each level; :func:`session_permission` builds the
authoritative per-session ruleset the runtime applies before every tool turn, which also
confines file access when OpenCode's own boundary is wider than the working directory (a
directory inside a git repository: OpenCode treats the whole repository as "inside").

Global settings: no MCP servers, plugins, LSP servers, formatters, snapshots, shared sessions or
auto-updates, and the built-in coding agents are disabled so only SmartRail agents are
selectable. (Project config, AGENTS.md and CLAUDE.md in a working directory are ignored because
the launcher sets ``OPENCODE_DISABLE_PROJECT_CONFIG``.)

Tool ids verified against OpenCode 1.18.33 (``GET /experimental/tool/ids``): invalid, question,
bash, read, glob, grep, edit, write, task, webfetch, todowrite, websearch, skill, apply_patch.
"""

from __future__ import annotations

import copy
from typing import Any

from backend.contracts.models import ToolAccess

AGENT_NAME = "smartrail"
READ_AGENT_NAME = "smartrail-read"
WRITE_AGENT_NAME = "smartrail-write"

# OpenCode's step limit for tool agents: after this many model steps it asks for a final text
# answer with tools disabled, so a tool loop always ends in a reply.
MAX_TOOL_STEPS = 40

_ROLE = (
    "Each request carries additional system instructions that define who you are in this "
    "conversation (a persona and the discussion setting). Follow them faithfully and stay in "
    "that role."
)

NEUTRAL_PROMPT = (
    "You are a conversational assistant inside a text-only chat application. "
    "You cannot read or write files, run commands, browse the web, call tools or delegate "
    "work, and you must never pretend to. Reply with plain text (Markdown is fine). " + _ROLE
)

_TOOLS_COMMON = (
    "You can use file tools only inside your working directory (shown in the environment "
    "information below); never try to access anything outside it. You cannot run commands, "
    "browse the web or delegate work, and you must never pretend to. Use tools only when they "
    "help answer the request, then always finish with a plain-text reply to the user (Markdown "
    "is fine); never end your turn with only tool calls. "
)

READ_ONLY_PROMPT = (
    "You are an assistant inside a chat application with read-only access to the files in "
    "your working directory: you can list, find, search and read files there, but you cannot "
    "create, edit or delete anything. " + _TOOLS_COMMON + _ROLE
)

READ_WRITE_PROMPT = (
    "You are an assistant inside a chat application with access to the files in your working "
    "directory: you can list, find, search and read files there, and create and edit files "
    "there when the user asks you to. " + _TOOLS_COMMON + _ROLE
)

# Permission keys of the file tools. ``read`` also lists directories; ``edit`` governs the edit,
# write and apply_patch tools.
READ_PERMISSIONS = ("read", "glob", "grep")
EDIT_PERMISSION = "edit"

# Denied at every level, whatever else is allowed (explicit on top of the leading ``* deny``).
ALWAYS_DENIED = (
    "bash",
    "task",
    "webfetch",
    "websearch",
    "codesearch",
    "todowrite",
    "todoread",
    "question",
    "skill",
    "lsp",
    "doom_loop",
    "plan_enter",
    "plan_exit",
    "external_directory",
)

# Secrets: OpenCode's own default asks before reading .env files; without an approval UI the
# equivalent is to deny them (examples stay readable).
_ENV_FILES = ("*.env", "*.env.*")
_ENV_EXAMPLE = "*.env.example"

_DISABLED_BUILTIN_AGENTS = ("build", "plan", "general", "explore")

_DENY_ALL: dict[str, str] = {"*": "deny"}


def _rule(permission: str, pattern: str, action: str) -> dict[str, str]:
    return {"permission": permission, "pattern": pattern, "action": action}


def _agent_permission(access: ToolAccess) -> dict[str, Any]:
    """The agent-level (config) permission for a tool level; ``dict`` order is rule order."""
    if access is ToolAccess.NONE:
        return dict(_DENY_ALL)
    permission: dict[str, Any] = {
        "*": "deny",
        "read": {"*": "allow", **{p: "deny" for p in _ENV_FILES}, _ENV_EXAMPLE: "allow"},
        "glob": "allow",
        "grep": "allow",
    }
    if access is ToolAccess.READ_WRITE:
        permission[EDIT_PERMISSION] = "allow"
    permission.update({name: "deny" for name in ALWAYS_DENIED})
    return permission


def agent_for(access: ToolAccess) -> str:
    """The OpenCode agent that runs a turn with this tool access."""
    if access is ToolAccess.READ_WRITE:
        return WRITE_AGENT_NAME
    if access is ToolAccess.READ_ONLY:
        return READ_AGENT_NAME
    return AGENT_NAME


def session_permission(access: ToolAccess, confine_to: str | None = None) -> list[dict[str, str]]:
    """The complete per-session ruleset for one tool level (OpenCode ``PermissionRuleset``).

    It starts with ``* deny`` so it alone decides every permission, whatever rules the session
    or agent had before (OpenCode appends PATCHed rules and the last match wins).

    ``confine_to``: the working directory relative to OpenCode's worktree (``/`` separators)
    when the worktree is wider than the directory (a subfolder of a git repository); ``None``
    when OpenCode's own boundary already is the directory. Confined, ``read``/``edit`` only
    match paths under it, and ``glob``/``grep`` are denied because their search ``path`` is not
    covered by permission patterns.
    """
    rules = [_rule("*", "*", "deny")]
    if access is ToolAccess.NONE:
        return rules
    scopes = ["*"] if confine_to is None else [confine_to, f"{confine_to}/*"]
    rules += [_rule("read", scope, "allow") for scope in scopes]
    rules += [_rule("read", pattern, "deny") for pattern in _ENV_FILES]
    example = _ENV_EXAMPLE if confine_to is None else f"{confine_to}/{_ENV_EXAMPLE}"
    rules.append(_rule("read", example, "allow"))
    if confine_to is None:
        rules += [_rule(name, "*", "allow") for name in READ_PERMISSIONS if name != "read"]
    if access is ToolAccess.READ_WRITE:
        rules.append(_rule(EDIT_PERMISSION, "*" if confine_to is None else scopes[1], "allow"))
    rules += [_rule(name, "*", "deny") for name in ALWAYS_DENIED]
    return rules


def _tool_agent(description: str, prompt: str, access: ToolAccess) -> dict[str, Any]:
    return {
        "description": description,
        "mode": "primary",
        "prompt": prompt,
        "steps": MAX_TOOL_STEPS,
        "permission": _agent_permission(access),
    }


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
    # Deny-all default for every agent; only the tool agents re-allow their file tools.
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
        READ_AGENT_NAME: _tool_agent(
            "Conversational agent with read-only file tools in its working directory.",
            READ_ONLY_PROMPT,
            ToolAccess.READ_ONLY,
        ),
        WRITE_AGENT_NAME: _tool_agent(
            "Conversational agent with read/write file tools in its working directory.",
            READ_WRITE_PROMPT,
            ToolAccess.READ_WRITE,
        ),
    },
}


def build_opencode_config() -> dict[str, Any]:
    """A fresh copy of the inline OpenCode config (safe for callers to mutate)."""
    return copy.deepcopy(_CONFIG)
