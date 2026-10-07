"""``GET /api/agents/{id}/files``: @-mention search inside an agent's working directory."""

import os
import sys
import time
from pathlib import Path

import pytest

from backend.application import files

from .conftest import GPT


def _write(root: Path, rel: str, text: str = "x") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


async def _agent_with_dir(env, directory: Path, access: str = "read_only") -> dict:
    r = await env.client.post(
        "/api/agents",
        json={
            "name": "Ada",
            "persona": "p",
            "provider_id": GPT[0],
            "model_id": GPT[1],
            "working_directory": str(directory),
            "tool_access": access,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _search(env, agent_id: str, q: str = "", **params) -> dict:
    r = await env.client.get(f"/api/agents/{agent_id}/files", params={"q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    _write(root, "README.md")
    _write(root, "src/app.py")
    _write(root, "src/application/service.py")
    _write(root, "src/components/AppHeader.tsx")
    _write(root, "docs/api/overview.md")
    for ignored in (".git/config", "node_modules/pkg/app.js", ".venv/lib/app.py", "dist/app.js"):
        _write(root, ignored)
    _write(root, "src/__pycache__/app.cpython-312.pyc")
    _write(root, ".github/workflows/ci.yml")
    return root


async def test_paths_are_relative_with_forward_slashes_and_skip_ignored(env, project):
    agent = await _agent_with_dir(env, project)
    body = await _search(env, agent["id"], "", limit=200)
    assert body["root"] == str(project.resolve()) and body["truncated"] is False
    paths = {f["path"] for f in body["files"]}
    assert "src/application/service.py" in paths and "README.md" in paths
    assert ".github/workflows/ci.yml" in paths  # dot folders are fine unless ignored
    assert not any("\\" in p for p in paths)
    for ignored in (".git", "node_modules", ".venv", "dist", "__pycache__"):
        assert not any(ignored in p.split("/") for p in paths), ignored
    readme = next(f for f in body["files"] if f["path"] == "README.md")
    assert readme["is_dir"] is False and readme["size"] == 1


async def test_query_matches_directories_too(env, project):
    agent = await _agent_with_dir(env, project)
    body = await _search(env, agent["id"], "components")
    assert body["files"][0] == {"path": "src/components", "is_dir": True, "size": None}


async def test_ranking_prefers_names_then_contiguous_then_short(env, project):
    agent = await _agent_with_dir(env, project)
    paths = [f["path"] for f in (await _search(env, agent["id"], "app"))["files"]]
    # exact/prefix name matches first, shorter first; then name substrings; then path-only.
    assert paths[:3] == ["src/app.py", "src/application", "src/components/AppHeader.tsx"]
    assert paths.index("src/application") < paths.index("src/application/service.py")

    paths = [f["path"] for f in (await _search(env, agent["id"], "SERVICE"))["files"]]
    assert paths[0] == "src/application/service.py"  # case-insensitive

    paths = [f["path"] for f in (await _search(env, agent["id"], "aphd"))["files"]]
    assert paths == ["src/components/AppHeader.tsx"]  # subsequence

    paths = [f["path"] for f in (await _search(env, agent["id"], "src/app"))["files"]]
    assert paths[0] == "src/app.py"

    paths = [f["path"] for f in (await _search(env, agent["id"], "docs\\api"))["files"]]
    assert paths[0] == "docs/api"  # backslashes in the query mean "/"

    assert (await _search(env, agent["id"], "zzzz"))["files"] == []


def test_match_rank_tiers():
    rank = files.match_rank
    assert rank("app.py", "src/app.py")[0] == 0
    assert rank("app", "src/app.py")[0] == 1
    assert rank("pp", "src/app.py")[0] == 2
    assert rank("src/a", "src/app.py")[0] == 3
    assert rank("apy", "src/app.py")[0] == 4
    assert rank("sapy", "src/app.py")[0] == 5
    assert rank("q", "src/app.py") is None
    # Tighter subsequence wins within a tier.
    assert rank("ab", "a_b.txt") < rank("ab", "a___b.txt")


async def test_empty_query_lists_recent_files_first(env, tmp_path):
    root = tmp_path / "recent"
    now = time.time()
    for i, name in enumerate(["old.txt", "middle.txt", "new.txt"]):
        path = _write(root, f"d/{name}")
        os.utime(path, (now - 100 + i * 10, now - 100 + i * 10))
    agent = await _agent_with_dir(env, root)
    body = await _search(env, agent["id"], "")
    assert [f["path"] for f in body["files"]] == ["d/new.txt", "d/middle.txt", "d/old.txt"]


async def test_limit_and_truncation(env, tmp_path, monkeypatch):
    root = tmp_path / "many"
    for i in range(30):
        _write(root, f"f{i:02d}.txt")
    agent = await _agent_with_dir(env, root)
    body = await _search(env, agent["id"], "f", limit=10)
    assert len(body["files"]) == 10 and body["truncated"] is True
    body = await _search(env, agent["id"], "f", limit=200)
    assert len(body["files"]) == 30 and body["truncated"] is False

    monkeypatch.setattr(files, "MAX_WALK_ENTRIES", 5)
    body = await _search(env, agent["id"], "", limit=200)
    assert len(body["files"]) == 5 and body["truncated"] is True


async def test_errors(env, tmp_path):
    r = await env.client.get("/api/agents/nope/files")
    assert r.status_code == 404
    plain = await env.agent("Plain")
    r = await env.client.get(f"/api/agents/{plain['id']}/files")
    assert r.status_code == 422 and r.json()["agent_id"] == plain["id"]
    assert "no working directory" in r.json()["message"]

    gone = tmp_path / "gone"
    gone.mkdir()
    agent = await _agent_with_dir(env, gone)
    gone.rmdir()
    r = await env.client.get(f"/api/agents/{agent['id']}/files")
    assert r.status_code == 422 and "no longer exists" in r.json()["message"]
    r = await env.client.get(f"/api/agents/{agent['id']}/files", params={"limit": 0})
    assert r.status_code == 422


def _link_dir(link: Path, target: Path) -> None:
    """Directory symlink, or a junction on Windows without symlink privilege."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi

        _winapi.CreateJunction(str(target), str(link))


async def test_links_never_escape_the_root(env, tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    _write(outside, "secret.txt")
    _write(root, "inner/real.txt")
    try:
        _link_dir(root / "escape", outside)
        _link_dir(root / "alias", root / "inner")
    except OSError as exc:
        pytest.skip(f"cannot create directory links here: {exc}")
    file_link = True
    try:
        os.symlink(outside / "secret.txt", root / "secret-link.txt")
    except OSError:
        file_link = False

    agent = await _agent_with_dir(env, root)
    paths = {f["path"] for f in (await _search(env, agent["id"], "", limit=200))["files"]}
    assert paths == {"inner/real.txt"}  # links are never descended; outside files never seen
    found = {f["path"] for f in (await _search(env, agent["id"], "a", limit=200))["files"]}
    assert "alias" in found  # an inside link is listed (not descended)
    assert "escape" not in found and not any("secret" in p for p in found)
    if file_link:
        assert "secret-link.txt" not in found
