from .conftest import GPT


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
