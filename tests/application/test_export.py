from backend.application.export import render_markdown
from backend.contracts.models import Conversation, FileRef, Message, ToolCall

from .conftest import GPT, MINI, fresh_runtime, running_app


async def test_export_is_markdown_attachment_generated_only_on_request(env, tmp_path):
    ada = await env.agent("Ada", GPT)
    bob = await env.agent("Bob", GPT)
    coord = await env.agent("Coord", GPT)
    await env.set_coordinator(coord["id"])
    conv = await env.group([ada["id"], bob["id"]], topic="Door safety")
    await env.run_to_end(conv, "Start please")
    # Nothing is written to disk other than the database files.
    data_dir = env.service.config.data_dir
    assert not [p for p in data_dir.rglob("*") if p.suffix in {".md", ".txt"}]

    r = await env.client.get(f"/api/conversations/{conv}/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.headers["content-disposition"].startswith("attachment;")
    text = r.text
    assert text.startswith("# Door safety")
    assert "**Topic:** Door safety" in text
    assert "### You ·" in text and "Start please" in text
    assert "### Ada (openai/gpt-6-sol) ·" in text and "### Bob (openai/gpt-6-sol) ·" in text
    assert "Coord (openai/gpt-6-sol) [coordinator]" in text
    assert "UTC" in text
    assert not [p for p in data_dir.rglob("*") if p.suffix in {".md", ".txt"}]  # still no files


async def test_export_unknown_conversation(env):
    r = await env.client.get("/api/conversations/nope/export")
    assert r.status_code == 404 and r.json()["code"] == "not_found"


async def test_export_lists_attachments_and_tool_calls(tmp_path):
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("print(1)\n")
    (project / "plan.md").write_text("plan\n")
    runtime = fresh_runtime()
    runtime.tool_models[GPT] = [("read", "src/app.py"), ("grep", "TODO")]
    async with running_app(tmp_path / "data", runtime) as env:

        async def make(name, model, directory=None):
            body = {"name": name, "persona": "p", "provider_id": model[0], "model_id": model[1]}
            if directory:
                body |= {"working_directory": str(directory), "tool_access": "read_only"}
            r = await env.client.post("/api/agents", json=body)
            return r.json()

        ada = await make("Ada", GPT, project)
        bob = await make("Bob", MINI, project)
        coord = await make("Coord", MINI)
        await env.set_coordinator(coord["id"])
        conv = await env.group([ada["id"], bob["id"]], topic="Code review")
        r = await env.client.post(
            f"/api/conversations/{conv}/messages",
            json={
                "content": "Review please",
                "attachments": [
                    {"agent_id": ada["id"], "path": "src/app.py"},
                    {"agent_id": bob["id"], "path": "plan.md"},
                ],
            },
        )
        assert r.status_code == 202, r.text
        await env.wait(r.json()["run_id"])
        text = (await env.client.get(f"/api/conversations/{conv}/export")).text
        assert "Review please\n\n_Attached:_ `src/app.py` (Ada), `plan.md` (Bob)" in text
        assert "_Tool calls:_ read `src/app.py` · grep `TODO`" in text
        assert text.count("_Tool calls:_") == 2  # Ada's two turns; Bob and Coord used none
        assert "print(1)" not in text  # file contents are not exported


async def test_export_marks_failed_tool_calls_in_direct_chat(env, tmp_path):
    ada = await env.agent("Ada", GPT)
    conv_id = await env.direct(ada["id"])
    await env.run_to_end(conv_id)
    detail = (await env.client.get(f"/api/conversations/{conv_id}")).json()
    conv = Conversation.model_validate(detail["conversation"])
    user, reply = (Message.model_validate(m) for m in detail["messages"])
    user = user.model_copy(update={"attachments": [FileRef(agent_id=ada["id"], path="a.md")]})
    reply = reply.model_copy(
        update={
            "tool_calls": [
                ToolCall(id="1", tool="edit", title="a`b.md", status="error", error="cancelled"),
                ToolCall(id="2", tool="list", title="", status="completed"),
            ]
        }
    )
    _, text = render_markdown(conv, [user, reply], ["Ada"], {ada["id"]: "Ada"})
    assert "_Attached:_ `a.md`\n" in text  # no owner label in a direct chat
    assert "_Tool calls:_ edit `a'b.md` (failed: cancelled) · list" in text
