"""Application layer: registry, persistence, API routes, discussions, budget.

Owned by the application agent.

Public surface (do not rename; ``backend/main.py`` imports it):

    create_app(config: AppConfig, runtime: OpenCodeRuntime) -> FastAPI

The returned app serves every route declared in ``backend/contracts/api_spec.py`` under
``/api`` and owns the SQLite database lifecycle. The database opens in the lifespan startup
or, if the host never runs lifespan events (plain ASGI test transports), lazily on the first
request.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.config import AppConfig
from backend.contracts.runtime import OpenCodeRuntime

from .errors import install_error_handlers
from .routes import router
from .service import Service


class _AppState:
    """Owns the service and starts it exactly once."""

    def __init__(self, service: Service) -> None:
        self.service = service
        self._started = False
        self._lock = asyncio.Lock()

    async def ensure_started(self) -> None:
        if self._started:
            return
        async with self._lock:
            if not self._started:
                await self.service.start()
                self._started = True

    async def shutdown(self) -> None:
        if self._started:
            self._started = False
            await self.service.stop()


def create_app(config: AppConfig, runtime: OpenCodeRuntime) -> FastAPI:
    state = _AppState(Service(config, runtime))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await state.ensure_started()
        try:
            yield
        finally:
            await state.shutdown()

    app = FastAPI(title="SmartRail Agents", version="0.1.0", lifespan=lifespan)
    app.state.smartrail = state
    install_error_handlers(app)
    app.include_router(router)
    return app


__all__ = ["create_app"]
