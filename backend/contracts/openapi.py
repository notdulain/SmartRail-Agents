"""Build the committed OpenAPI document (route spec + SSE event schemas)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from .api_spec import build_spec_app
from .models import RunEvent

OPENAPI_PATH = Path(__file__).with_name("openapi.json")


def build_openapi() -> dict[str, Any]:
    doc = build_spec_app().openapi()
    schema = TypeAdapter(RunEvent).json_schema(ref_template="#/components/schemas/{model}")
    defs = schema.pop("$defs", {})
    components = doc.setdefault("components", {}).setdefault("schemas", {})
    components.update(defs)
    components["RunEvent"] = schema
    return doc


def dump_openapi(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"
