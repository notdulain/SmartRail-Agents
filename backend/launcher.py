"""`uv run smartrail`: start OpenCode and the app, open the browser, clean up on exit.

Owned by the coordinating development agent. Works on macOS and native Windows:
executables are resolved with ``shutil.which`` (honours ``.exe``/``.cmd`` via PATHEXT),
child processes are started from argument arrays (never a shell), and the child's whole
process tree is stopped on exit.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path

import httpx

from backend.config import (
    LOOPBACK,
    OPENCODE_USERNAME,
    AppConfig,
    load_config,
)

IS_WINDOWS = sys.platform == "win32"
READY_TIMEOUT_S = 45.0


class LauncherError(Exception):
    """User-facing startup problem. ``main`` prints it without a traceback."""


# ----------------------------------------------------------------------------- helpers


def port_in_use(port: int, host: str = LOOPBACK) -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def find_free_port(host: str = LOOPBACK) -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def resolve_executable(name: str, override: str | None = None, hint: str = "") -> str:
    """Absolute path to ``name`` (or ``override``), or LauncherError with install guidance."""
    candidate = override or name
    found = shutil.which(candidate)
    if not found:
        what = f"'{candidate}'" + (f" (set via override {override!r})" if override else "")
        raise LauncherError(f"Cannot find {what} on PATH. {hint}".strip())
    return found


def _tree_kill_windows(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        check=False,
    )


class ManagedProcess:
    """A child process whose entire process tree is stopped by ``stop()``."""

    def __init__(
        self,
        args: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        log_path: Path | None = None,
    ) -> None:
        self.args = list(args)
        self._log_file = None
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = open(log_path, "ab")  # noqa: SIM115 - closed in stop()
        kwargs: dict[str, object] = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(
            self.args,
            env=dict(env) if env is not None else None,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=self._log_file or subprocess.DEVNULL,
            stderr=subprocess.STDOUT if self._log_file else subprocess.DEVNULL,
            **kwargs,  # type: ignore[arg-type]
        )

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    def stop(self, timeout: float = 8.0) -> None:
        try:
            if self.running:
                if IS_WINDOWS:
                    _tree_kill_windows(self.proc.pid)
                else:
                    try:
                        os.killpg(self.proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                try:
                    self.proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    if not IS_WINDOWS:
                        try:
                            os.killpg(self.proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        self.proc.kill()
                    self.proc.wait(timeout=timeout)
            elif not IS_WINDOWS:
                # Leader gone; sweep any stragglers left in its group.
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        finally:
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None


# ----------------------------------------------------------------------------- OpenCode


def opencode_env(config: AppConfig, base: Mapping[str, str] | None = None) -> dict[str, str]:
    from backend.runtime import build_opencode_config

    env = dict(os.environ if base is None else base)
    env.update(
        OPENCODE_SERVER_USERNAME=config.opencode_username,
        OPENCODE_SERVER_PASSWORD=config.opencode_password,
        OPENCODE_CONFIG_CONTENT=json.dumps(build_opencode_config()),
        OPENCODE_DISABLE_AUTOUPDATE="1",
        OPENCODE_DISABLE_PROJECT_CONFIG="1",
    )
    return env


def opencode_args(binary: str, port: int) -> list[str]:
    return [binary, "serve", "--hostname", LOOPBACK, "--port", str(port)]


def wait_for_opencode(config: AppConfig, child: ManagedProcess | None, timeout: float) -> None:
    """Block until OpenCode's health endpoint answers, or raise LauncherError."""
    deadline = time.monotonic() + timeout
    auth = httpx.BasicAuth(config.opencode_username, config.opencode_password)
    with httpx.Client(base_url=config.opencode_url, auth=auth, timeout=2.0) as client:
        while time.monotonic() < deadline:
            if child is not None and not child.running:
                raise LauncherError(
                    f"OpenCode exited during startup (code {child.proc.returncode}). "
                    f"See {config.logs_dir / 'opencode.log'}"
                )
            try:
                response = client.get("/global/health")
                if response.status_code == 200 and response.json().get("healthy"):
                    return
                if response.status_code == 401:
                    raise LauncherError("OpenCode rejected the generated password (HTTP 401).")
            except (httpx.TransportError, ValueError):
                pass
            time.sleep(0.25)
    raise LauncherError(f"OpenCode did not become ready within {timeout:.0f}s.")


# ----------------------------------------------------------------------------- main


def _open_browser_when_ready(host: str, port: int, server: object) -> None:
    url = f"http://{host}:{port}/"
    for _ in range(200):
        if getattr(server, "started", False):
            if os.environ.get("SMARTRAIL_NO_BROWSER") != "1":
                webbrowser.open(url)
            return
        time.sleep(0.1)


def _prepare_config(env: Mapping[str, str]) -> tuple[AppConfig, str | None]:
    """Resolve config. Returns (config, opencode_binary or None when attaching)."""
    config = load_config(env)
    if port_in_use(config.port):
        raise LauncherError(
            f"Port {config.port} is already in use. Stop the other process or set "
            f"SMARTRAIL_PORT to a free port."
        )
    if env.get("SMARTRAIL_OPENCODE_URL"):
        return config, None  # attach mode: user manages OpenCode and its password

    if env.get("SMARTRAIL_OPENCODE_PORT"):
        opencode_port = int(env["SMARTRAIL_OPENCODE_PORT"])
        if port_in_use(opencode_port):
            raise LauncherError(
                f"OpenCode port {opencode_port} is already in use. Change "
                f"SMARTRAIL_OPENCODE_PORT or unset it to pick a free port automatically."
            )
    else:
        opencode_port = find_free_port()
    binary = resolve_executable(
        "opencode",
        env.get("SMARTRAIL_OPENCODE_BIN"),
        hint="Install OpenCode (https://opencode.ai/docs/) and make sure it is on PATH.",
    )
    config = AppConfig(
        data_dir=config.data_dir,
        port=config.port,
        opencode_url=f"http://{LOOPBACK}:{opencode_port}",
        opencode_username=OPENCODE_USERNAME,
        opencode_password=secrets.token_urlsafe(24),
    )
    return config, binary


def run(env: Mapping[str, str] | None = None) -> None:
    import uvicorn

    env = os.environ if env is None else env
    config, binary = _prepare_config(env)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.opencode_dir.mkdir(parents=True, exist_ok=True)

    child: ManagedProcess | None = None
    try:
        if binary is not None:
            port = int(config.opencode_url.rsplit(":", 1)[1])
            child = ManagedProcess(
                opencode_args(binary, port),
                env=opencode_env(config),
                cwd=config.opencode_dir,
                log_path=config.logs_dir / "opencode.log",
            )
        wait_for_opencode(config, child, READY_TIMEOUT_S)

        from backend.main import build_app

        server = uvicorn.Server(
            uvicorn.Config(build_app(config), host=config.host, port=config.port, log_level="info")
        )
        threading.Thread(
            target=_open_browser_when_ready,
            args=(config.host, config.port, server),
            daemon=True,
        ).start()
        print(f"SmartRail running at http://{config.host}:{config.port}/  (Ctrl+C to stop)")
        server.run()
    finally:
        if child is not None:
            child.stop()


def main() -> None:
    try:
        run()
    except LauncherError as exc:
        print(f"smartrail: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
