"""Filesystem access for agent working directories: validation, listing, search, attachments.

Everything here is blocking; callers run it in a worker thread (``asyncio.to_thread``).
Functions raise :class:`FileProblem` with a user-facing message; the service turns it into a
422 ``validation_error``.
"""

from __future__ import annotations

import os
import stat
import string
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from backend.contracts.models import DirectoryEntry, DirectoryListing, FileEntry

MAX_DIRECTORY_ENTRIES = 1000

# File search walks at most this many entries / seconds per request, breadth first.
MAX_WALK_ENTRIES = 20_000
WALK_SECONDS = 2.0

# Version control, dependency, virtualenv, cache and build output folders.
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".bzr",
        ".worktrees",
        "node_modules",
        "bower_components",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".eggs",
        "dist",
        "build",
        "target",
        ".next",
        ".nuxt",
        ".svelte-kit",
        ".turbo",
        ".parcel-cache",
        ".cache",
        ".gradle",
        ".terraform",
        ".idea",
        "coverage",
        "htmlcov",
        "Pods",
        "DerivedData",
    }
)
IGNORED_FILES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})


class FileProblem(ValueError):
    """A user-facing problem with a path (missing, not a directory, outside the root, ...)."""


def normalize_directory(raw: str, what: str = "Working directory") -> str:
    """Expand ``~``, require an absolute path to an existing directory; return it resolved."""
    text = raw.strip()
    if not text:
        raise FileProblem(f"{what} must not be empty.")
    if "\x00" in text:
        raise FileProblem(f"{what} contains an invalid character.")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise FileProblem(f"{what} must be an absolute path: {text}")
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        raise FileProblem(f"{what} does not exist: {text}") from None
    except OSError as exc:
        raise FileProblem(f"{what} cannot be opened: {text} ({_reason(exc)})") from None
    if not resolved.is_dir():
        raise FileProblem(f"{what} is not a directory: {text}")
    return str(resolved)


def directory_exists(path: str) -> bool:
    try:
        return Path(path).is_dir()
    except OSError:
        return False


# ----------------------------------------------------------------------- directory picker


def home_directory() -> str:
    return str(Path.home())


def filesystem_roots() -> list[str]:
    """Existing drive roots on Windows (such as ``C:`` with a trailing separator), else ``/``."""
    if sys.platform != "win32":
        return ["/"]
    listdrives = getattr(os, "listdrives", None)
    drives = listdrives() if listdrives else [f"{c}:\\" for c in string.ascii_uppercase]
    return [d for d in drives if os.path.isdir(d)]


def _hidden(entry: os.DirEntry[str]) -> bool:
    if entry.name.startswith("."):
        return True
    if sys.platform == "win32":
        try:
            attrs = entry.stat(follow_symlinks=False).st_file_attributes
        except OSError:
            return False
        return bool(attrs & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))
    return False


def list_directories(raw: str | None) -> DirectoryListing:
    """Sub-directories of ``raw`` (default: home), visible ones first, each group by name.

    Children that cannot be inspected are skipped; only an unreadable listed directory itself
    is an error. At most :data:`MAX_DIRECTORY_ENTRIES` entries are returned.
    """
    target = normalize_directory(raw if raw and raw.strip() else home_directory(), "Directory")
    path = Path(target)
    found: list[tuple[bool, str, str]] = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if not entry.is_dir():  # follows symlinks: a linked folder is a folder
                        continue
                    hidden = _hidden(entry)
                except OSError:
                    continue
                found.append((hidden, entry.name, str(path / entry.name)))
    except PermissionError:
        raise FileProblem(f"Cannot read directory {target}: permission denied") from None
    except OSError as exc:
        raise FileProblem(f"Cannot read directory {target}: {_reason(exc)}") from None
    found.sort(key=lambda item: (item[0], item[1].casefold(), item[1]))
    parent = path.parent
    return DirectoryListing(
        path=target,
        parent=str(parent) if parent != path else None,
        entries=[DirectoryEntry(name=n, path=p) for _, n, p in found[:MAX_DIRECTORY_ENTRIES]],
        home=home_directory(),
        roots=filesystem_roots(),
    )


# ----------------------------------------------------------------------- file search


@dataclass(frozen=True)
class _Found:
    path: str  # relative, "/" separators
    is_dir: bool
    size: int | None
    mtime: float


def is_inside(path: Path, root: Path) -> bool:
    """Containment for resolved paths (case-insensitive on Windows, like the filesystem)."""
    return path == root or root in path.parents


def _walk(root: Path) -> tuple[list[_Found], bool]:
    """Breadth-first listing of ``root`` without following links out of it (or into loops).

    Links (symlinks, junctions) that resolve inside the root are listed but never descended
    into; links that resolve outside it are skipped. Returns (entries, truncated).
    """
    deadline = time.monotonic() + WALK_SECONDS
    found: list[_Found] = []
    queue: deque[tuple[str, str]] = deque([(str(root), "")])
    visited = 0
    while queue:
        directory, prefix = queue.popleft()
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            if visited >= MAX_WALK_ENTRIES or time.monotonic() > deadline:
                return found, True
            visited += 1
            name = entry.name
            try:
                if _is_link(entry):
                    target = Path(entry.path).resolve(strict=True)
                    if not is_inside(target, root):
                        continue
                    info = target.stat()
                    is_dir, descend = stat.S_ISDIR(info.st_mode), False
                else:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    info = entry.stat(follow_symlinks=False)
                    descend = is_dir
            except (OSError, RuntimeError):  # RuntimeError: symlink loop on older Pythons
                continue
            rel = prefix + name
            if is_dir:
                if name in IGNORED_DIRS:
                    continue
                found.append(_Found(rel, True, None, info.st_mtime))
                if descend:
                    queue.append((entry.path, rel + "/"))
            elif name not in IGNORED_FILES:
                found.append(_Found(rel, False, info.st_size, info.st_mtime))
    return found, False


def _span(query: str, text: str) -> int | None:
    """Length of the tightest window of ``text`` containing ``query`` as a subsequence."""
    best: int | None = None
    start = text.find(query[0])
    while start != -1:
        pos = start
        for ch in query[1:]:
            pos = text.find(ch, pos + 1)
            if pos == -1:
                return best
        width = pos - start + 1
        if best is None or width < best:
            best = width
        start = text.find(query[0], start + 1)
    return best


def match_rank(query: str, path: str) -> tuple[int, int, int, str] | None:
    """Sort key for ``path`` against a lower-case ``query``; ``None`` if it does not match.

    Tiers: exact file name, name prefix, name substring, path substring, name subsequence,
    path subsequence. Within a tier, tighter subsequence matches and shorter paths first.
    """
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    if name == query:
        tier, spread = 0, 0
    elif name.startswith(query):
        tier, spread = 1, 0
    elif query in name:
        tier, spread = 2, 0
    elif query in lowered:
        tier, spread = 3, 0
    elif (width := _span(query, name)) is not None:
        tier, spread = 4, width - len(query)
    elif (width := _span(query, lowered)) is not None:
        tier, spread = 5, width - len(query)
    else:
        return None
    return tier, spread, len(path), lowered


def search_files(root: str, query: str, limit: int) -> tuple[list[FileEntry], bool]:
    """Matches for ``query`` under ``root`` (see :func:`match_rank`); empty query: files by
    most recent modification. Returns (entries, truncated)."""
    root_path = Path(root).resolve()
    found, truncated = _walk(root_path)
    needle = "".join(query.split()).lower().replace("\\", "/")
    if needle:
        ranked = [(key, f) for f in found if (key := match_rank(needle, f.path)) is not None]
        ranked.sort(key=lambda item: item[0])
        matches = [f for _, f in ranked]
    else:
        matches = sorted((f for f in found if not f.is_dir), key=lambda f: (-f.mtime, f.path))
    if len(matches) > limit:
        truncated = True
    return [
        FileEntry(path=f.path, is_dir=f.is_dir, size=f.size) for f in matches[:limit]
    ], truncated


def _reason(exc: OSError) -> str:
    if isinstance(exc, PermissionError):
        return "permission denied"
    return exc.strerror or exc.__class__.__name__


def _is_link(entry: os.DirEntry[str]) -> bool:
    """Symlinks and (on Windows) directory junctions."""
    try:
        return entry.is_symlink() or entry.is_junction()
    except OSError:
        return True
