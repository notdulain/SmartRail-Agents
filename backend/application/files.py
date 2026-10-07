"""Filesystem access for agent working directories: validation, listing, search, attachments.

Everything here is blocking; callers run it in a worker thread (``asyncio.to_thread``).
Functions raise :class:`FileProblem` with a user-facing message; the service turns it into a
422 ``validation_error``.
"""

from __future__ import annotations

import os
from pathlib import Path


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
