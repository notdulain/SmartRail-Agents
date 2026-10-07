"""HTTP routes. Declarations mirror ``backend/contracts/api_spec.py`` exactly."""

from __future__ import annotations

from importlib import metadata
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import PlainTextResponse
from sse_starlette.sse import EventSourceResponse

from backend.contracts.api_spec import ERRORS
from backend.contracts.models import (
    Agent,
    AgentCreate,
    AgentUpdate,
    Conversation,
    ConversationCreate,
    ConversationDetail,
    DirectoryListing,
    FileSearchResponse,
    HealthResponse,
    ProvidersResponse,
    Run,
    SendMessageRequest,
    SendMessageResponse,
    Settings,
    SettingsUpdate,
)

from .service import Service


async def get_service(request: Request) -> Service:
    runtime_state = request.app.state.smartrail
    await runtime_state.ensure_started()
    return runtime_state.service


def _version() -> str:
    try:
        return metadata.version("smartrail")
    except metadata.PackageNotFoundError:
        return "0.1.0"


router = APIRouter(prefix="/api")
Svc = Depends(get_service)


@router.get("/health", response_model=HealthResponse)
async def get_health(svc: Service = Svc):
    try:
        healthy = bool(await svc.runtime.health())
    except Exception:
        healthy = False
    return HealthResponse(opencode_healthy=healthy, version=_version())


@router.get("/agents", response_model=list[Agent])
async def list_agents(include_archived: bool = False, svc: Service = Svc):
    return await svc.list_agents(include_archived)


@router.post("/agents", response_model=Agent, status_code=201, responses=ERRORS)
async def create_agent(body: AgentCreate, svc: Service = Svc):
    return await svc.create_agent(body)


@router.patch("/agents/{agent_id}", response_model=Agent, responses=ERRORS)
async def update_agent(agent_id: str, body: AgentUpdate, svc: Service = Svc):
    return await svc.update_agent(agent_id, body)


@router.get("/agents/{agent_id}/files", response_model=FileSearchResponse, responses=ERRORS)
async def search_agent_files(
    agent_id: str,
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    svc: Service = Svc,
):
    """Files (and directories) in the agent's working directory whose relative path matches
    ``q`` (case-insensitive subsequence; empty ``q`` lists recently modified files first).
    Skips VCS/dependency/build folders. 422 ``validation_error`` if the agent has no working
    directory or it no longer exists."""
    return await svc.search_agent_files(agent_id, q, limit)


@router.get("/fs/directories", response_model=DirectoryListing, responses=ERRORS)
async def list_directories(
    path: str | None = Query(default=None, max_length=1024), svc: Service = Svc
):
    """Sub-directories of ``path`` (default: the user's home) for the working-directory
    picker. 422 ``validation_error`` if ``path`` is not an existing directory."""
    return await svc.list_directories(path)


@router.get("/providers", response_model=ProvidersResponse)
async def get_providers(refresh: bool = False, svc: Service = Svc):
    """Provider/model catalog from OpenCode. ``refresh=true`` re-queries OpenCode."""
    return await svc.providers(refresh)


@router.get("/conversations", response_model=list[Conversation])
async def list_conversations(svc: Service = Svc):
    return await svc.list_conversations()


@router.post("/conversations", response_model=Conversation, status_code=201, responses=ERRORS)
async def create_conversation(body: ConversationCreate, svc: Service = Svc):
    return await svc.create_conversation(body)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail, responses=ERRORS)
async def get_conversation(conversation_id: str, svc: Service = Svc):
    return await svc.get_conversation(conversation_id)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=SendMessageResponse,
    status_code=202,
    responses=ERRORS,
)
async def send_message(conversation_id: str, body: SendMessageRequest, svc: Service = Svc):
    """Store the user message and start a run (one agent turn for direct chats; one bounded
    two-round exchange for group discussions). 409 ``run_active`` if a run is in progress.
    Attached files are read now and their text is sent with the message; 422
    ``validation_error`` if one is missing, outside the working directory, binary or too
    large."""
    return await svc.send_message(conversation_id, body)


@router.get("/runs/{run_id}", response_model=Run, responses=ERRORS)
async def get_run(run_id: str, svc: Service = Svc):
    return await svc.get_run(run_id)


@router.get(
    "/runs/{run_id}/events",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "SSE stream; each `data:` is a RunEvent JSON object (see components).",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        **ERRORS,
    },
)
async def run_events(
    run_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    after: int | None = Query(default=None, ge=0),
    svc: Service = Svc,
):
    await svc.get_run(run_id)  # 404 before the stream opens
    cursor = after or 0
    if last_event_id is not None and last_event_id.strip().isdigit():
        cursor = max(cursor, int(last_event_id.strip()))
    return EventSourceResponse(svc.runs.stream_events(run_id, cursor))


@router.post("/runs/{run_id}/stop", response_model=Run, responses=ERRORS)
async def stop_run(run_id: str, svc: Service = Svc):
    """Cancel a running run. Idempotent: returns the run's current state."""
    return await svc.stop_run(run_id)


@router.get("/settings", response_model=Settings)
async def get_settings(svc: Service = Svc):
    return await svc.get_settings()


@router.patch("/settings", response_model=Settings, responses=ERRORS)
async def update_settings(body: SettingsUpdate, svc: Service = Svc):
    return await svc.update_settings(body)


@router.get(
    "/conversations/{conversation_id}/export",
    response_class=PlainTextResponse,
    responses={200: {"content": {"text/markdown": {"schema": {"type": "string"}}}}, **ERRORS},
)
async def export_conversation(conversation_id: str, svc: Service = Svc):
    """Markdown transcript with ``Content-Disposition: attachment``. Generated only here."""
    filename, text = await svc.export_markdown(conversation_id)
    return PlainTextResponse(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"; '
            f"filename*=UTF-8''{quote(filename)}"
        },
    )
