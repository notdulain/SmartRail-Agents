"""Application assembly: application routes + runtime + built frontend on one local URL."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.application import create_app
from backend.config import AppConfig
from backend.contracts.runtime import OpenCodeRuntime
from backend.runtime import create_runtime


def build_app(config: AppConfig, runtime: OpenCodeRuntime | None = None) -> FastAPI:
    runtime = runtime or create_runtime(config)
    app = create_app(config, runtime)
    # Mounted last so /api routes win. html=True serves index.html for "/".
    if config.frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=config.frontend_dist, html=True), name="frontend")
    return app
