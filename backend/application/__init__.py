"""Application layer: registry, persistence, API routes, discussions, budget.

Owned by the application agent.

Public surface (do not rename; ``backend/main.py`` imports it):

    create_app(config: AppConfig, runtime: OpenCodeRuntime) -> FastAPI

The returned app serves every route declared in ``backend/contracts/api_spec.py`` under
``/api`` and owns the SQLite database lifecycle (startup/shutdown via lifespan).
"""

from __future__ import annotations

from fastapi import FastAPI

from backend.config import AppConfig
from backend.contracts.runtime import OpenCodeRuntime


def create_app(config: AppConfig, runtime: OpenCodeRuntime) -> FastAPI:
    raise NotImplementedError("application agent: implement")
