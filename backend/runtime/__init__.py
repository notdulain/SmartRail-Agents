"""OpenCode runtime adapter. Owned by the runtime agent.

Public surface (do not rename; the application layer and launcher import these):

    create_runtime(config: AppConfig) -> OpenCodeRuntime
    build_opencode_config() -> dict   # inline config for the neutral, tool-disabled agent
"""

from __future__ import annotations

from typing import Any

from backend.config import AppConfig
from backend.contracts.runtime import OpenCodeRuntime


def build_opencode_config() -> dict[str, Any]:
    """Config passed to the app-managed server via OPENCODE_CONFIG_CONTENT."""
    raise NotImplementedError("runtime agent: implement")


def create_runtime(config: AppConfig) -> OpenCodeRuntime:
    raise NotImplementedError("runtime agent: implement")
