"""Typed route declarations that define the HTTP interface.

This module builds a FastAPI app whose handlers all return 501. It exists only to emit
the OpenAPI document (``backend/contracts/openapi.json``) from which the frontend types
are generated, and to give ``tests/test_contract_conformance.py`` something to compare the
real application against. The real handlers live in ``backend/application``.

Owned by the coordinating development agent.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sse_starlette.sse import EventSourceResponse

from .models import (
    Agent,
    AgentCreate,
    AgentUpdate,
    Conversation,
    ConversationCreate,
    ConversationDetail,
    ErrorResponse,
    HealthResponse,
    ProvidersResponse,
    Run,
    SendMessageRequest,
    SendMessageResponse,
    Settings,
    SettingsUpdate,
)

ERRORS = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


def _stub() -> NoReturn:
    raise HTTPException(status_code=501, detail="contract stub")


router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthResponse)
async def get_health():
    _stub()


@router.get("/agents", response_model=list[Agent])
async def list_agents(include_archived: bool = False):
    _stub()


@router.post("/agents", response_model=Agent, status_code=201, responses=ERRORS)
async def create_agent(body: AgentCreate):
    _stub()


@router.patch("/agents/{agent_id}", response_model=Agent, responses=ERRORS)
async def update_agent(agent_id: str, body: AgentUpdate):
    _stub()


@router.get("/providers", response_model=ProvidersResponse)
async def get_providers(refresh: bool = False):
    """Provider/model catalog from OpenCode. ``refresh=true`` re-queries OpenCode."""
    _stub()


@router.get("/conversations", response_model=list[Conversation])
async def list_conversations():
    _stub()


@router.post("/conversations", response_model=Conversation, status_code=201, responses=ERRORS)
async def create_conversation(body: ConversationCreate):
    _stub()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail, responses=ERRORS)
async def get_conversation(conversation_id: str):
    _stub()


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=SendMessageResponse,
    status_code=202,
    responses=ERRORS,
)
async def send_message(conversation_id: str, body: SendMessageRequest):
    """Store the user message and start a run (one agent turn for direct chats; one bounded
    two-round exchange for group discussions). 409 ``run_active`` if a run is in progress."""
    _stub()


@router.get("/runs/{run_id}", response_model=Run, responses=ERRORS)
async def get_run(run_id: str):
    _stub()


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
):
    _stub()


@router.post("/runs/{run_id}/stop", response_model=Run, responses=ERRORS)
async def stop_run(run_id: str):
    """Cancel a running run. Idempotent: returns the run's current state."""
    _stub()


@router.get("/settings", response_model=Settings)
async def get_settings():
    _stub()


@router.patch("/settings", response_model=Settings, responses=ERRORS)
async def update_settings(body: SettingsUpdate):
    _stub()


@router.get(
    "/conversations/{conversation_id}/export",
    response_class=PlainTextResponse,
    responses={200: {"content": {"text/markdown": {"schema": {"type": "string"}}}}, **ERRORS},
)
async def export_conversation(conversation_id: str):
    """Markdown transcript with ``Content-Disposition: attachment``. Generated only here."""
    _stub()


def build_spec_app() -> FastAPI:
    app = FastAPI(title="SmartRail Agents", version="0.1.0")
    app.include_router(router)
    return app
