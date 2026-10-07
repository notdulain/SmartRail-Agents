"""Tool access levels: agent config, per-session rulesets and how a turn applies them."""

from __future__ import annotations

import json

import pytest

from backend.contracts.models import ErrorCode, ToolAccess
from backend.contracts.runtime import Completed, Failed
from backend.runtime.agent_config import (
    AGENT_NAME,
    ALWAYS_DENIED,
    READ_AGENT_NAME,
    WRITE_AGENT_NAME,
    agent_for,
    build_opencode_config,
    session_permission,
)

from .conftest import request
from .fake_opencode import FakeOpenCode
from .opencode_rules import ALL_TOOLS, evaluate, from_config, offered

NONE, RO, RW = ToolAccess.NONE, ToolAccess.READ_ONLY, ToolAccess.READ_WRITE
WORK = "/work/proj a"
DENY_ALL = [{"permission": "*", "pattern": "*", "action": "deny"}]
NEVER = {"bash", "task", "webfetch", "websearch", "todowrite", "skill", "question"}


def all_actions(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from all_actions(item)
    elif isinstance(value, list):
        for item in value:
            yield from all_actions(item)
    elif isinstance(value, str):
        yield value


# ----------------------------------------------------------------------------- config


def agent_rules(name: str) -> list[dict[str, str]]:
    config = build_opencode_config()
    # global permission first, then the agent's own (OpenCode's merge order)
    return from_config(config["permission"]) + from_config(config["agent"][name]["permission"])


def test_one_agent_per_level():
    assert (agent_for(NONE), agent_for(RO), agent_for(RW)) == (
        AGENT_NAME,
        READ_AGENT_NAME,
        WRITE_AGENT_NAME,
    )
    agents = build_opencode_config()["agent"]
    for name in (READ_AGENT_NAME, WRITE_AGENT_NAME):
        assert agents[name]["mode"] == "primary"
        assert "tools" not in agents[name]  # governed by permissions only
        assert agents[name]["steps"] > 1


@pytest.mark.parametrize(
    ("name", "tools"),
    [
        (AGENT_NAME, set()),
        (READ_AGENT_NAME, {"read", "glob", "grep"}),
        (WRITE_AGENT_NAME, {"read", "glob", "grep", "edit", "write", "apply_patch"}),
    ],
)
def test_agent_tool_sets(name, tools):
    assert offered(agent_rules(name)) == tools


def test_tool_agents_deny_outside_paths_secrets_and_never_tools():
    for name in (READ_AGENT_NAME, WRITE_AGENT_NAME):
        rules = agent_rules(name)
        assert evaluate(rules, "external_directory", "/etc/*") == "deny"
        assert evaluate(rules, "read", "proj/.env") == "deny"
        assert evaluate(rules, "read", "proj/.env.local") == "deny"
        assert evaluate(rules, "read", "proj/.env.example") == "allow"
        for permission in ALWAYS_DENIED:
            assert evaluate(rules, permission, "anything") == "deny"
    assert evaluate(agent_rules(READ_AGENT_NAME), "edit", "a.txt") == "deny"


def test_no_rule_anywhere_is_ask():
    config = build_opencode_config()
    assert "ask" not in set(all_actions(config["permission"]))
    for agent in config["agent"].values():
        assert "ask" not in set(all_actions(agent.get("permission", {})))
    for access in ToolAccess:
        for confine in (None, "sub"):
            assert {r["action"] for r in session_permission(access, confine)} <= {"allow", "deny"}


def test_prompts_match_the_levels():
    agents = build_opencode_config()["agent"]
    assert "cannot read or write files" in agents[AGENT_NAME]["prompt"]
    for name in (READ_AGENT_NAME, WRITE_AGENT_NAME):
        prompt = agents[name]["prompt"]
        assert "only inside your working directory" in prompt
        assert "persona" in prompt
        assert "cannot run commands" in prompt
    assert "cannot create, edit or delete" in agents[READ_AGENT_NAME]["prompt"]
    assert "create and edit files" in agents[WRITE_AGENT_NAME]["prompt"]


# ----------------------------------------------------------------------------- session rules


def test_session_rules_start_with_deny_all_so_they_alone_decide():
    for access in ToolAccess:
        for confine in (None, "sub"):
            assert session_permission(access, confine)[0] == DENY_ALL[0]
    assert session_permission(NONE) == DENY_ALL
    assert session_permission(NONE, "sub") == DENY_ALL


@pytest.mark.parametrize(
    ("access", "tools"),
    [
        (NONE, set()),
        (RO, {"read", "glob", "grep"}),
        (RW, {"read", "glob", "grep", "edit", "write", "apply_patch"}),
    ],
)
def test_session_rules_override_any_agent(access, tools):
    # the most permissive agent followed by the session's rules: the session decides
    rules = agent_rules(WRITE_AGENT_NAME) + session_permission(access)
    assert offered(rules) == tools
    assert not offered(rules) & NEVER
    assert evaluate(rules, "external_directory", "/elsewhere/*") == "deny"


def test_confined_rules_scope_read_and_edit_and_drop_search_tools():
    rules = session_permission(RW, "docs/notes")
    assert offered(rules) == {"read", "edit", "write", "apply_patch"}
    assert evaluate(rules, "read", "docs/notes") == "allow"  # listing the directory itself
    assert evaluate(rules, "read", "docs\\notes\\a\\b.md") == "allow"
    assert evaluate(rules, "read", "docs/notes-old/a.md") == "deny"
    assert evaluate(rules, "read", "docs/other.md") == "deny"
    assert evaluate(rules, "read", "docs/notes/.env") == "deny"
    assert evaluate(rules, "read", "docs/notes/.env.example") == "allow"
    assert evaluate(rules, "read", "other/.env.example") == "deny"
    assert evaluate(rules, "edit", "docs/notes/new/file.md") == "allow"
    assert evaluate(rules, "edit", "README.md") == "deny"
    assert offered(session_permission(RO, "docs/notes")) == {"read"}


def test_all_tools_list_is_covered():
    # every OpenCode tool is either a file tool or always denied
    for tool in ALL_TOOLS:
        assert tool in {"read", "glob", "grep", "edit", "write", "apply_patch"} or (
            tool in ALWAYS_DENIED
        )


# ----------------------------------------------------------------------------- turns


async def collect(runtime, req):
    return [event async for event in runtime.stream(req)]


async def test_neutral_turn_is_unchanged(fake: FakeOpenCode, runtime):
    session = await runtime.create_session("chat")
    events = await collect(runtime, request(session))
    assert isinstance(events[-1], Completed)
    assert fake.prompts[0].body["agent"] == AGENT_NAME
    assert "tools" not in fake.prompts[0].body
    assert not fake.calls_to("PATCH", session) and not fake.calls_to("GET", "/path")
    assert fake.permissions[session] == DENY_ALL


async def test_tools_without_a_directory_fall_back_to_none(fake: FakeOpenCode, runtime):
    session = await runtime.create_session("chat")
    await collect(runtime, request(session, access=RW))
    assert fake.prompts[0].body["agent"] == AGENT_NAME
    assert not fake.calls_to("PATCH", session)
    assert fake.permissions[session] == DENY_ALL


async def test_none_in_a_directory_stays_locked_without_extra_calls(fake: FakeOpenCode, runtime):
    session = await runtime.create_session("chat", WORK)
    await collect(runtime, request(session, directory=WORK, access=NONE))
    assert fake.prompts[0].body["agent"] == AGENT_NAME
    assert not fake.calls_to("PATCH", session) and not fake.calls_to("GET", "/path")
    assert offered(fake.permissions[session]) == set()


@pytest.mark.parametrize(("access", "agent"), [(RO, READ_AGENT_NAME), (RW, WRITE_AGENT_NAME)])
async def test_tool_turn_applies_the_level_before_prompting(
    fake: FakeOpenCode, runtime, access, agent
):
    session = await runtime.create_session("chat", WORK)
    await collect(runtime, request(session, directory=WORK, access=access))
    (patch,) = fake.calls_to("PATCH", f"/session/{session}")
    assert patch.directory == WORK
    assert patch.body == {"permission": session_permission(access)}
    assert fake.calls.index(patch) < fake.calls.index(fake.prompts[0])
    assert fake.prompts[0].body["agent"] == agent
    assert fake.calls_to("GET", "/path")[0].directory == WORK


async def test_level_is_only_patched_when_it_changes(fake: FakeOpenCode, runtime):
    session = await runtime.create_session("chat", WORK)
    for access in (RO, RO, RW, RW, NONE, NONE, RO):
        await collect(runtime, request(session, directory=WORK, access=access))
    patches = [c.body["permission"] for c in fake.calls_to("PATCH", f"/session/{session}")]
    assert patches == [
        session_permission(RO),
        session_permission(RW),
        session_permission(NONE),
        session_permission(RO),
    ]
    agents = [c.body["agent"] for c in fake.prompts]
    assert agents == [READ_AGENT_NAME] * 2 + [WRITE_AGENT_NAME] * 2 + [AGENT_NAME] * 2 + [
        READ_AGENT_NAME
    ]


async def test_back_to_none_relocks_a_session_that_had_tools(fake: FakeOpenCode, runtime):
    session = await runtime.create_session("chat", WORK)
    await collect(runtime, request(session, directory=WORK, access=RW))
    await collect(runtime, request(session, directory=WORK, access=NONE))
    # OpenCode appends PATCHed rules; the accumulated ruleset must still deny everything
    assert offered(agent_rules(AGENT_NAME) + fake.permissions[session]) == set()


async def test_restarted_runtime_reapplies_the_level(fake: FakeOpenCode, runtime):
    fake.add_session("ses_old", WORK)
    await collect(runtime, request("ses_old", directory=WORK, access=NONE))
    # unknown previous rules (maybe read_write): relocked explicitly
    (patch,) = fake.calls_to("PATCH", "/session/ses_old")
    assert patch.body == {"permission": DENY_ALL}


async def test_git_subfolder_is_confined(fake: FakeOpenCode, runtime):
    fake.worktrees[WORK] = "/work"
    session = await runtime.create_session("chat", WORK)
    await collect(runtime, request(session, directory=WORK, access=RW))
    (patch,) = fake.calls_to("PATCH", f"/session/{session}")
    rules = patch.body["permission"]
    assert rules == session_permission(RW, "proj a")
    assert evaluate(rules, "read", "proj a/x.md") == "allow"
    assert evaluate(rules, "read", "other/x.md") == "deny"


async def test_git_root_is_not_confined_further(fake: FakeOpenCode, runtime):
    fake.worktrees[WORK] = WORK
    session = await runtime.create_session("chat", WORK)
    await collect(runtime, request(session, directory=WORK, access=RO))
    (patch,) = fake.calls_to("PATCH", f"/session/{session}")
    assert patch.body == {"permission": session_permission(RO)}


async def test_unconfinable_layout_fails_closed(fake: FakeOpenCode, runtime):
    fake.worktrees[WORK] = "/elsewhere"  # worktree not above the directory
    session = await runtime.create_session("chat", WORK)
    events = await collect(runtime, request(session, directory=WORK, access=RO))
    assert [type(e) for e in events] == [Failed]
    assert events[0].code is ErrorCode.RUNTIME_ERROR
    assert not fake.prompts and not fake.calls_to("PATCH", session)


async def test_failed_permission_update_fails_without_prompting(fake: FakeOpenCode, runtime):
    fake.patch_status = 500
    session = await runtime.create_session("chat", WORK)
    events = await collect(runtime, request(session, directory=WORK, access=RW))
    assert [type(e) for e in events] == [Failed]
    assert not fake.prompts
    # nothing was applied, so the next turn tries again
    fake.patch_status = 200
    await collect(runtime, request(session, directory=WORK, access=RW))
    assert len(fake.calls_to("PATCH", f"/session/{session}")) == 2


def test_config_round_trips_as_json():
    assert json.loads(json.dumps(build_opencode_config())) == build_opencode_config()
