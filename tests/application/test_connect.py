"""``smartrail connect`` writes user-level guides without disturbing existing content."""

import pytest

from backend import cli, connect


@pytest.fixture
def homes(tmp_path, monkeypatch):
    claude, codex = tmp_path / "claude", tmp_path / "codex"
    claude.mkdir()
    codex.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    return claude, codex


def test_install_writes_skill_and_codex_block_next_to_existing_content(homes):
    claude, codex = homes
    (codex / "AGENTS.md").write_text("# mine\n\nKeep tests fast.\n", encoding="utf-8")

    connect.install("C:/tools/smartrail.exe")

    skill = (claude / "skills" / "smartrail-agents" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: smartrail-agents\ndescription: ")
    assert 'C:/tools/smartrail.exe ask "<agent name or id>"' in skill
    agents = (codex / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.startswith("# mine\n\nKeep tests fast.\n")
    assert agents.count(connect.BLOCK_START) == 1
    assert "C:/tools/smartrail.exe agents" in agents


def test_install_twice_replaces_instead_of_duplicating(homes):
    _, codex = homes
    connect.install("first.exe")
    connect.install("second.exe")
    agents = (codex / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.count(connect.BLOCK_START) == 1
    assert "second.exe agents" in agents and "first.exe" not in agents


def test_remove_restores_codex_file_and_deletes_skill(homes):
    claude, codex = homes
    (codex / "AGENTS.md").write_text("# mine\n", encoding="utf-8")
    connect.install("x.exe")
    connect.remove()
    assert (codex / "AGENTS.md").read_text(encoding="utf-8") == "# mine\n"
    assert not (claude / "skills" / "smartrail-agents").exists()
    assert connect.remove() == ["Nothing to remove."]


def test_tools_that_are_not_installed_are_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    lines = connect.install("x.exe")
    assert all("skipped" in line for line in lines)
    assert not (tmp_path / "no-claude").exists() and not (tmp_path / "no-codex").exists()


def test_non_default_url_is_called_out():
    assert "--url http://127.0.0.1:9000" in connect.render_guide("x", "http://127.0.0.1:9000")
    assert "--url" not in connect.render_guide("x", "http://127.0.0.1:8765")


async def test_connect_command_uses_this_installations_executable(homes, capsys):
    code = await cli.main_async(["connect"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK and "Claude Code: wrote skill" in out
    skill = (homes[0] / "skills" / "smartrail-agents" / "SKILL.md").read_text(encoding="utf-8")
    assert "smartrail" in skill and "\\" not in skill  # forward slashes only
