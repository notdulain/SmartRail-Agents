"""Minimal Server-Sent Events decoding for OpenCode's ``GET /event`` stream.

OpenCode sends only ``data:`` lines (one JSON object per event, blank-line separated),
e.g. ``data: {"id":"evt_..","type":"message.part.delta","properties":{...}}``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any


async def iter_sse_json(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """Yield decoded JSON objects from SSE text lines. Malformed frames are skipped."""
    data: list[str] = []
    async for line in lines:
        if line == "":
            if data:
                frame = "\n".join(data)
                data = []
                try:
                    decoded = json.loads(frame)
                except ValueError:
                    continue
                if isinstance(decoded, dict):
                    yield decoded
            continue
        if line.startswith(":"):  # comment / keep-alive
            continue
        field, _, value = line.partition(":")
        if field == "data":
            data.append(value[1:] if value.startswith(" ") else value)
    if data:  # stream ended without the trailing blank line
        try:
            decoded = json.loads("\n".join(data))
        except ValueError:
            return
        if isinstance(decoded, dict):
            yield decoded
