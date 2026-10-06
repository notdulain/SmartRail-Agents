import socket
import sys
import time

import pytest

from backend.launcher import (
    LauncherError,
    ManagedProcess,
    find_free_port,
    port_in_use,
    resolve_executable,
)


def test_port_detection():
    port = find_free_port()
    assert not port_in_use(port)
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))
        s.listen()
        assert port_in_use(port)


def test_resolve_executable_finds_python():
    assert resolve_executable("python3" if sys.platform != "win32" else "python")


def test_missing_executable_message():
    with pytest.raises(LauncherError, match="Cannot find 'definitely-not-installed'"):
        resolve_executable("definitely-not-installed", hint="Install it.")


def test_managed_process_stops_whole_tree(tmp_path):
    marker = tmp_path / "grandchild.pid"
    code = (
        "import subprocess,sys,time;"
        f"p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"open(r'{marker}','w').write(str(p.pid));time.sleep(60)"
    )
    child = ManagedProcess([sys.executable, "-c", code], log_path=tmp_path / "child.log")
    deadline = time.time() + 10
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.05)
    grandchild_pid = int(marker.read_text())
    child.stop()
    assert not child.running
    time.sleep(0.3)
    if sys.platform != "win32":
        import os

        with pytest.raises(ProcessLookupError):
            os.kill(grandchild_pid, 0)
