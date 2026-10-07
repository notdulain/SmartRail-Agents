"""Application assembly: application routes + runtime + built frontend on one local URL."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from backend.application import create_app
from backend.config import AppConfig
from backend.contracts.runtime import OpenCodeRuntime
from backend.runtime import create_runtime


def build_app(config: AppConfig, runtime: OpenCodeRuntime | None = None) -> FastAPI:
    runtime = runtime or create_runtime(config)
    app = create_app(config, runtime)
    # Agents can read and write local files, so refuse requests addressed to any other host
    # name (DNS rebinding: a web page resolving its own domain to 127.0.0.1).
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=[config.host, "localhost", "127.0.0.1", "testserver"]
    )
    # Mounted last so /api routes win. html=True serves index.html for "/".
    if config.frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=config.frontend_dist, html=True), name="frontend")
    return app
