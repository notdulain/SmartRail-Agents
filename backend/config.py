"""Process-wide configuration, resolved from environment variables.

Environment overrides (for isolated tests and worktree development):
    SMARTRAIL_DATA_DIR      databases, runtime config, logs (default: platformdirs user data dir)
    SMARTRAIL_PORT          app port (default 8765)
    SMARTRAIL_OPENCODE_PORT app-managed OpenCode port (default 4096 + offset search by launcher)
    SMARTRAIL_OPENCODE_URL  attach to an already-running OpenCode instead of spawning one
    SMARTRAIL_OPENCODE_PASSWORD  password for that instance
    SMARTRAIL_OPENCODE_BIN  path to the opencode executable
    SMARTRAIL_NO_BROWSER    set to 1 to skip opening the browser
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

APP_NAME = "SmartRail"
DEFAULT_PORT = 8765
DEFAULT_OPENCODE_PORT = 4096
LOOPBACK = "127.0.0.1"
OPENCODE_USERNAME = "opencode"


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    port: int = DEFAULT_PORT
    host: str = LOOPBACK
    opencode_url: str = f"http://{LOOPBACK}:{DEFAULT_OPENCODE_PORT}"
    opencode_username: str = OPENCODE_USERNAME
    opencode_password: str = ""
    frontend_dist: Path = Path(__file__).resolve().parent.parent / "frontend" / "dist"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "smartrail.db"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def opencode_dir(self) -> Path:
        """Isolated runtime files for the app-managed OpenCode (never user credentials)."""
        return self.data_dir / "opencode"


def _int_env(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{key} must be an integer, got {raw!r}") from exc
    if not 1 <= value <= 65535:
        raise SystemExit(f"{key} must be between 1 and 65535, got {value}")
    return value


def resolve_data_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    override = env.get("SMARTRAIL_DATA_DIR")
    path = Path(override).expanduser() if override else user_data_path(APP_NAME, appauthor=False)
    return path.resolve()


def load_config(env: Mapping[str, str] | None = None) -> AppConfig:
    """Config for an app that attaches to ``SMARTRAIL_OPENCODE_URL`` (or the default URL).

    The launcher builds its own config with the spawned server's port and password.
    """
    env = os.environ if env is None else env
    opencode_port = _int_env(env, "SMARTRAIL_OPENCODE_PORT", DEFAULT_OPENCODE_PORT)
    return AppConfig(
        data_dir=resolve_data_dir(env),
        port=_int_env(env, "SMARTRAIL_PORT", DEFAULT_PORT),
        opencode_url=env.get("SMARTRAIL_OPENCODE_URL") or f"http://{LOOPBACK}:{opencode_port}",
        opencode_password=env.get("SMARTRAIL_OPENCODE_PASSWORD", ""),
    )
