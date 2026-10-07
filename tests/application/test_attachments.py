"""@-attachments: files from a participant's working directory sent with a user message."""

import os
import sys
from pathlib import Path

import pytest

from backend.application import prompts
from backend.contracts.models import Message, MessageRole, MessageStatus

from .conftest import GPT, MINI, running_app


def _write(root: Path, rel: str, data: str | bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8", newline="")
    return path


async def _agent(env, name, directory=None, access="read_only", model=GPT):
    body = {"name": name, "persona": "p", "provider_id": model[0], "model_id": model[1]}
    if directory is not None:
        body |= {"working_directory": str(directory), "tool_access": access}
    r = await env.client.post("/api/agents", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _send(env, conv, attachments, content="look at this"):
    return await env.client.post(
        f"/api/conversations/{conv}/messages",
        json={"content": content, "attachments": attachments},
    )


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    _write(root, "src/app.py", "print('hi')\n")
    _write(root, "notes.md", "no trailing newline")
    _write(tmp_path / "outside", "secret.txt", "top secret")
    return root


async def test_direct_attachment_is_sent_stored_and_kept_as_sent(env, project):
    ada = await _agent(env, "Ada", project)
    conv = await env.direct(ada["id"])
    refs = [
        {"agent_id": ada["id"], "path": "src/app.py"},
        {"agent_id": ada["id"], "path": "./notes.md"},
        {"agent_id": ada["id"], "path": "src//app.py"},  # same file again: attached once
    ]
    r = await _send(env, conv, refs)
    assert r.status_code == 202, r.text
    await env.wait(r.json()["run_id"])
    (request,) = env.runtime.requests
    assert request.user_text == (
        "look at this\n\n"
        "<attached_file path=\"src/app.py\">\nprint('hi')\n</attached_file>\n\n"
        '<attached_file path="notes.md">\nno trailing newline\n</attached_file>'
    )
    user = (await env.messages(conv))[0]
    assert user["content"] == "look at this"  # the visible message is just the text
    assert user["attachments"] == [
        {"agent_id": ada["id"], "path": "src/app.py"},
        {"agent_id": ada["id"], "path": "notes.md"},
    ]


async def test_group_attachment_reaches_every_speaker(env, project, tmp_path):
    other = tmp_path / "other"
    _write(other, "plan.txt", "the plan")
    ada = await _agent(env, "Ada", project)
    bob = await _agent(env, "Bob", other)
    cid = await _agent(env, "Coord", model=MINI)
    await env.set_coordinator(cid["id"])
    conv = await env.group([ada["id"], bob["id"]])
    refs = [
        {"agent_id": ada["id"], "path": "src/app.py"},
        {"agent_id": bob["id"], "path": "plan.txt"},
    ]
    r = await _send(env, conv, refs)
    assert r.status_code == 202, r.text
    run = await env.wait(r.json()["run_id"])
    assert run["status"] == "completed"
    block_a = '<attached_file path="src/app.py" agent="Ada">\nprint(\'hi\')\n</attached_file>'
    block_b = '<attached_file path="plan.txt" agent="Bob">\nthe plan\n</attached_file>'
    agenda, first_a, first_b = env.runtime.requests[:3]
    for request in (agenda, first_a, first_b):  # everyone's first turn of this run
        assert block_a in request.user_text and block_b in request.user_text
    # Later turns only see what is new; the files are not repeated.
    for request in env.runtime.requests[3:]:
        assert "<attached_file" not in request.user_text


async def test_attachment_survives_transcript_cap(env, project, monkeypatch):
    monkeypatch.setattr(prompts, "MAX_TRANSCRIPT_CHARS", 50)
    _write(project, "big.txt", "B" * 500)
    ada = await _agent(env, "Ada", project)
    bob = await _agent(env, "Bob")
    cid = await _agent(env, "Coord", model=MINI)
    await env.set_coordinator(cid["id"])
    conv = await env.group([ada["id"], bob["id"]])
    r = await _send(env, conv, [{"agent_id": ada["id"], "path": "big.txt"}], content="go")
    await env.wait(r.json()["run_id"])
    # Bob speaks after the agenda and Ada's contribution; the user message is the oldest of
    # what he has not seen, yet its file is still there in full.
    bob_first = env.runtime.requests[2]
    assert "B" * 500 in bob_first.user_text


def _msg(id_: str, role: MessageRole, text: str) -> Message:
    from datetime import UTC, datetime

    return Message(
        id=id_,
        conversation_id="c",
        role=role,
        speaker_name="You" if role is MessageRole.USER else "Ada",
        content=text,
        status=MessageStatus.COMPLETE,
        created_at=datetime.now(UTC),
    )


def test_format_transcript_keeps_pinned_messages_and_marks_gaps(monkeypatch):
    monkeypatch.setattr(prompts, "MAX_TRANSCRIPT_CHARS", 30)
    msgs = [
        _msg("old", MessageRole.AGENT, "x" * 40),
        _msg("user", MessageRole.USER, "question"),
        _msg("a", MessageRole.AGENT, "a" * 20),
        _msg("b", MessageRole.AGENT, "b" * 20),
    ]
    text = prompts.format_transcript(msgs, {"user": "<attached_file/>"}, keep={"user"})
    assert text.split("\n\n") == [
        prompts.OMITTED,
        "[User]: question",
        "<attached_file/>",
        prompts.OMITTED,
        "[Ada]: " + "b" * 20,
    ]
    # Without pinning, the old behaviour: newest that fit, then one marker.
    text = prompts.format_transcript(msgs)
    assert text.split("\n\n") == [prompts.OMITTED, "[Ada]: " + "b" * 20]


@pytest.mark.parametrize(
    ("path", "words"),
    [
        ("../outside/secret.txt", "inside the working directory"),
        ("src/../../outside/secret.txt", "inside the working directory"),
        ("src\\..\\..\\outside\\secret.txt", "inside the working directory"),
        ("/etc/passwd", "relative"),
        ("C:\\Windows\\win.ini", "relative"),
        ("C:secret.txt", "relative"),
        ("\\\\server\\share\\x.txt", "relative"),
        ("missing.txt", "does not exist"),
        ("src", "not a regular file"),
        (".", "does not name a file"),
    ],
)
async def test_bad_paths_are_rejected(env, project, path, words):
    ada = await _agent(env, "Ada", project)
    conv = await env.direct(ada["id"])
    r = await _send(env, conv, [{"agent_id": ada["id"], "path": path}])
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "validation_error" and words in body["message"]
    assert repr(path) in body["message"] and body["agent_id"] == ada["id"]
    assert await env.messages(conv) == [] and env.runtime.requests == []


async def test_absolute_path_inside_root_is_still_rejected(env, project):
    ada = await _agent(env, "Ada", project)
    conv = await env.direct(ada["id"])
    absolute = str(project.resolve() / "notes.md")
    r = await _send(env, conv, [{"agent_id": ada["id"], "path": absolute}])
    assert r.status_code == 422 and "relative" in r.json()["message"]


async def test_owner_must_be_a_participant_with_a_directory(env, project):
    ada = await _agent(env, "Ada", project)
    stranger = await _agent(env, "Stranger", project)
    plain = await _agent(env, "Plain")
    conv = await env.direct(ada["id"])
    r = await _send(env, conv, [{"agent_id": stranger["id"], "path": "notes.md"}])
    assert r.status_code == 422 and "not a participant" in r.json()["message"]
    r = await _send(env, conv, [{"agent_id": "agt_nope", "path": "notes.md"}])
    assert r.status_code == 422 and "not a participant" in r.json()["message"]

    conv2 = await env.direct(plain["id"])
    r = await _send(env, conv2, [{"agent_id": plain["id"], "path": "notes.md"}])
    assert r.status_code == 422 and "no working directory" in r.json()["message"]


async def test_coordinator_is_not_an_attachment_owner(env, project):
    ada = await _agent(env, "Ada", project)
    bob = await _agent(env, "Bob")
    coord = await _agent(env, "Coord", project, model=MINI)
    await env.set_coordinator(coord["id"])
    conv = await env.group([ada["id"], bob["id"]])
    r = await _send(env, conv, [{"agent_id": coord["id"], "path": "notes.md"}])
    assert r.status_code == 422 and "not a participant" in r.json()["message"]


async def test_binary_non_utf8_and_size_limits(env, project):
    _write(project, "image.bin", b"\x89PNG\x00\x00data")
    _write(project, "latin1.txt", "caf\xe9".encode("latin-1"))
    _write(project, "big.txt", "x" * (200 * 1024 + 1))
    _write(project, "limit.txt", "y" * (200 * 1024))
    _write(project, "bom.txt", "\ufeffwith bom".encode())
    ada = await _agent(env, "Ada", project)
    conv = await env.direct(ada["id"])

    async def attach(*paths):
        return await _send(env, conv, [{"agent_id": ada["id"], "path": p} for p in paths])

    r = await attach("image.bin")
    assert r.status_code == 422 and "binary" in r.json()["message"]
    r = await attach("latin1.txt")
    assert r.status_code == 422 and "UTF-8" in r.json()["message"]
    r = await attach("big.txt")
    assert r.status_code == 422 and "larger than 200 KB" in r.json()["message"]
    assert "'big.txt'" in r.json()["message"]

    for i in range(5):
        _write(project, f"part{i}.txt", "z" * (200 * 1024))
    r = await attach("limit.txt", *[f"part{i}.txt" for i in range(5)])
    assert r.status_code == 422 and "in total" in r.json()["message"]
    assert "'part4.txt'" in r.json()["message"]

    r = await _send(env, conv, [{"agent_id": ada["id"], "path": f"p{i}"} for i in range(21)])
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    assert await env.messages(conv) == []

    r = await attach("limit.txt", "bom.txt")  # exactly at the per-file limit is fine
    assert r.status_code == 202, r.text
    await env.wait(r.json()["run_id"])
    assert "\ufeff" not in env.runtime.requests[0].user_text
    assert '<attached_file path="bom.txt">\nwith bom\n' in env.runtime.requests[0].user_text


def _link_dir(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi

        _winapi.CreateJunction(str(target), str(link))


async def test_links_resolving_outside_are_rejected(env, project, tmp_path):
    try:
        _link_dir(project / "escape", tmp_path / "outside")
        _link_dir(project / "inside", project / "src")
    except OSError as exc:
        pytest.skip(f"cannot create directory links here: {exc}")
    ada = await _agent(env, "Ada", project)
    conv = await env.direct(ada["id"])
    r = await _send(env, conv, [{"agent_id": ada["id"], "path": "escape/secret.txt"}])
    assert r.status_code == 422 and "outside the working directory" in r.json()["message"]
    r = await _send(env, conv, [{"agent_id": ada["id"], "path": "inside/app.py"}])
    assert r.status_code == 202, r.text  # a link that stays inside is fine


async def test_contents_are_read_once_at_send_time(tmp_path, project):
    from .conftest import GatedRuntime

    runtime = GatedRuntime()
    async with running_app(tmp_path / "data", runtime) as env:
        ada = await _agent(env, "Ada", project)
        bob = await _agent(env, "Bob")
        coord = await _agent(env, "Coord", model=MINI)
        await env.set_coordinator(coord["id"])
        conv = await env.group([ada["id"], bob["id"]])
        runtime.gate.clear()
        r = await _send(env, conv, [{"agent_id": ada["id"], "path": "notes.md"}])
        assert r.status_code == 202
        _write(project, "notes.md", "CHANGED")  # edited (or deleted) mid-run
        runtime.gate.set()
        await env.wait(r.json()["run_id"])
        texts = [req.user_text for req in runtime.requests[:3]]
        assert all("no trailing newline" in t and "CHANGED" not in t for t in texts)


async def test_deleted_working_directory_blocks_send(env, tmp_path):
    gone = tmp_path / "gone"
    gone.mkdir()
    ada = await _agent(env, "Ada", gone)
    conv = await env.direct(ada["id"])
    gone.rmdir()
    r = await env.client.post(f"/api/conversations/{conv}/messages", json={"content": "hi"})
    assert r.status_code == 422 and "no longer exists" in r.json()["message"]
    assert r.json()["agent_id"] == ada["id"]


async def test_attachments_survive_restart_and_reach_a_fresh_session(tmp_path, project):
    data = tmp_path / "data"
    async with running_app(data) as env:
        ada = await _agent(env, "Ada", project)
        conv = await env.direct(ada["id"])
        r = await _send(env, conv, [{"agent_id": ada["id"], "path": "notes.md"}])
        await env.wait(r.json()["run_id"])
        other = tmp_path / "other"
        other.mkdir()
        await env.client.patch(f"/api/agents/{ada['id']}", json={"working_directory": str(other)})
    async with running_app(data) as env:
        user = (await env.messages(conv))[0]
        assert user["attachments"] == [{"agent_id": ada["id"], "path": "notes.md"}]
        await env.run_to_end(conv, "and now?")
        (request,) = env.runtime.requests
        # The new session (directory changed) is given the earlier transcript with the file.
        assert '<attached_file path="notes.md">\nno trailing newline\n' in request.user_text
