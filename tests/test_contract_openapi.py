"""The committed openapi.json must match the Python contracts (regenerate on change)."""

from backend.contracts.openapi import OPENAPI_PATH, build_openapi, dump_openapi


def test_committed_openapi_is_current():
    assert OPENAPI_PATH.read_text(encoding="utf-8") == dump_openapi(build_openapi()), (
        "backend/contracts/openapi.json is stale: run "
        "`uv run python scripts/export_openapi.py` and commit it"
    )


def test_run_event_schema_exported():
    schemas = build_openapi()["components"]["schemas"]
    assert "RunEvent" in schemas
    assert {"MessageDeltaEvent", "RunPausedEvent", "Message", "Agent"} <= set(schemas)


def test_every_planned_interface_present():
    paths = build_openapi()["paths"]
    expected = {
        ("get", "/api/agents"),
        ("post", "/api/agents"),
        ("patch", "/api/agents/{agent_id}"),
        ("get", "/api/providers"),
        ("get", "/api/conversations"),
        ("post", "/api/conversations"),
        ("get", "/api/conversations/{conversation_id}"),
        ("post", "/api/conversations/{conversation_id}/messages"),
        ("get", "/api/runs/{run_id}/events"),
        ("post", "/api/runs/{run_id}/stop"),
        ("get", "/api/settings"),
        ("patch", "/api/settings"),
        ("get", "/api/conversations/{conversation_id}/export"),
    }
    actual = {(m, p) for p, ops in paths.items() for m in ops}
    assert expected <= actual
