"""OpenCode 1.18 permission semantics, mirrored for tests.

``evaluate``: the *last* rule whose permission and pattern both match wins; no match is "ask".
``disabled``: a tool is not offered to the model when the last rule matching its permission
(pattern ignored) is ``* deny``. ``edit``, ``write`` and ``apply_patch`` share the ``edit``
permission. Wildcards: ``*`` is any run of characters (path separators included), ``?`` one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

EDIT_TOOLS = ("edit", "write", "apply_patch")
FILE_TOOLS = ("read", "glob", "grep", *EDIT_TOOLS)
# Every tool id OpenCode 1.18.33 registers (GET /experimental/tool/ids), minus the fallback.
ALL_TOOLS = (
    "question",
    "bash",
    "read",
    "glob",
    "grep",
    "edit",
    "write",
    "task",
    "webfetch",
    "todowrite",
    "websearch",
    "skill",
    "apply_patch",
)

Rules = Iterable[dict[str, str]]


def _match(value: str, pattern: str) -> bool:
    value, pattern = value.replace("\\", "/"), pattern.replace("\\", "/")
    regex = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)
    return re.fullmatch(regex, value, re.S) is not None


def permission_of(tool: str) -> str:
    return "edit" if tool in EDIT_TOOLS else tool


def evaluate(rules: Rules, permission: str, pattern: str = "*") -> str:
    found = [
        r for r in rules if _match(permission, r["permission"]) and _match(pattern, r["pattern"])
    ]
    return found[-1]["action"] if found else "ask"


def disabled(rules: Rules, tool: str) -> bool:
    permission = permission_of(tool)
    found = [r for r in rules if _match(permission, r["permission"])]
    return bool(found) and found[-1]["pattern"] == "*" and found[-1]["action"] == "deny"


def from_config(permission: dict[str, str | dict[str, str]]) -> list[dict[str, str]]:
    """Config-style permission (``{"read": {"*": "allow"}, "bash": "deny"}``) as rules."""
    rules: list[dict[str, str]] = []
    for name, value in permission.items():
        patterns = value if isinstance(value, dict) else {"*": value}
        rules += [{"permission": name, "pattern": p, "action": a} for p, a in patterns.items()]
    return rules


def offered(rules: Rules, tools: Iterable[str] = ALL_TOOLS) -> set[str]:
    rules = list(rules)
    return {tool for tool in tools if not disabled(rules, tool)}
