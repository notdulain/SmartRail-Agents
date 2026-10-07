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
from pathlib import Path

from backend.contracts.models import DirectoryEntry, DirectoryListing

MAX_DIRECTORY_ENTRIES = 1000


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
