"""``GET /api/fs/directories``: the working-directory picker."""

import os
import sys
from pathlib import Path

import pytest

from backend.application import files


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "root"
    for name in ("beta", "Alpha", ".hidden", "gamma"):
        (root / name).mkdir(parents=True)
    (root / "file.txt").write_text("not a directory")
    return root


async def test_lists_sub_directories_sorted_with_hidden_last(env, tree):
    r = await env.client.get("/api/fs/directories", params={"path": str(tree)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"] == str(tree.resolve())
    assert [e["name"] for e in body["entries"]] == ["Alpha", "beta", "gamma", ".hidden"]
    assert all(Path(e["path"]).is_absolute() for e in body["entries"])
    assert Path(body["entries"][0]["path"]) == tree.resolve() / "Alpha"
    assert body["parent"] == str(tree.resolve().parent)
    assert body["home"] == str(Path.home())
    assert body["roots"]
    if sys.platform == "win32":
        assert all(len(r) == 3 and r.endswith(":\\") for r in body["roots"])
        assert any(str(tree.resolve()).upper().startswith(r.upper()) for r in body["roots"])
    else:
        assert body["roots"] == ["/"]


async def test_defaults_to_home(env, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    body = (await env.client.get("/api/fs/directories")).json()
    assert body["path"] == str(home.resolve()) and body["home"] == str(home)
    assert [e["name"] for e in body["entries"]] == ["Documents"]


async def test_parent_is_null_at_a_root(env):
    root = files.filesystem_roots()[0]
    body = (await env.client.get("/api/fs/directories", params={"path": root})).json()
    assert body["parent"] is None
    assert Path(body["path"]) == Path(root)


async def test_rejects_missing_file_and_relative_paths(env, tree):
    for path, words in (
        (str(tree / "missing"), "does not exist"),
        (str(tree / "file.txt"), "not a directory"),
        ("relative", "absolute"),
    ):
        r = await env.client.get("/api/fs/directories", params={"path": path})
        assert r.status_code == 422, path
        assert r.json()["code"] == "validation_error" and words in r.json()["message"]


class _Entry:
    def __init__(self, name: str, fail: bool) -> None:
        self.name = name
        self.fail = fail

    def is_dir(self, follow_symlinks: bool = True) -> bool:
        if self.fail:
            raise PermissionError("denied")
        return True

    def stat(self, follow_symlinks: bool = True):
        return os.stat(".")


class _Scan:
    def __init__(self, entries):
        self.entries = entries

    def __enter__(self):
        return iter(self.entries)

    def __exit__(self, *exc):
        return False


async def test_unreadable_child_is_skipped_not_fatal(env, tree, monkeypatch):
    entries = [_Entry("ok", False), _Entry("locked", True)]
    monkeypatch.setattr(files.os, "scandir", lambda path: _Scan(entries))
    r = await env.client.get("/api/fs/directories", params={"path": str(tree)})
    assert r.status_code == 200, r.text
    assert [e["name"] for e in r.json()["entries"]] == ["ok"]


async def test_unreadable_listed_directory_is_validation_error(env, tree, monkeypatch):
    def deny(path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(files.os, "scandir", deny)
    r = await env.client.get("/api/fs/directories", params={"path": str(tree)})
    assert r.status_code == 422 and "permission denied" in r.json()["message"]


async def test_entries_are_capped(env, tmp_path, monkeypatch):
    monkeypatch.setattr(files, "MAX_DIRECTORY_ENTRIES", 3)
    for i in range(5):
        (tmp_path / f"d{i}").mkdir()
    body = (await env.client.get("/api/fs/directories", params={"path": str(tmp_path)})).json()
    assert [e["name"] for e in body["entries"]] == ["d0", "d1", "d2"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
async def test_real_permission_error_child(env, tree):
    if os.geteuid() == 0:
        pytest.skip("root ignores permissions")
    locked = tree / "beta"
    locked.chmod(0)
    try:
        body = (await env.client.get("/api/fs/directories", params={"path": str(tree)})).json()
        assert "beta" in [e["name"] for e in body["entries"]]  # listed; just not enterable
        r = await env.client.get("/api/fs/directories", params={"path": str(locked)})
        assert r.status_code == 422 and "permission denied" in r.json()["message"]
    finally:
        locked.chmod(0o755)
