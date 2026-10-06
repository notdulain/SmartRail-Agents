"""OpenCode runtime adapter. Owned by the runtime agent.

Public surface (do not rename; the application layer and launcher import these):

    create_runtime(config: AppConfig) -> OpenCodeRuntime
    build_opencode_config() -> dict   # inline config for the neutral, tool-disabled agent
"""

from __future__ import annotations

from backend.config import AppConfig
from backend.contracts.runtime import OpenCodeRuntime

from .agent_config import AGENT_NAME, build_opencode_config
from .opencode import HttpOpenCodeRuntime

__all__ = ["AGENT_NAME", "HttpOpenCodeRuntime", "build_opencode_config", "create_runtime"]


def create_runtime(config: AppConfig) -> OpenCodeRuntime:
    """An ``OpenCodeRuntime`` bound to ``config.opencode_url`` (HTTP Basic auth)."""
    return HttpOpenCodeRuntime(config)
