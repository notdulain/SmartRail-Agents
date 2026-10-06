"""Map OpenCode's ``GET /provider`` payload to the contract's ``ProvidersResponse``.

OpenCode shape (verified against 1.18.22)::

    {"all": [{"id", "name", "models": {"<model id>": {
                "id", "name", "status", "cost": {"input", "output", "cache": {...}},
                "limit": {"context", "output"}, "capabilities": {"output": {"text": bool}}}}}],
     "connected": ["<provider id>", ...], "default": {...}}

Costs are USD per one million tokens already (``cost.input`` / ``cost.output``), a missing
or non-numeric ``cost`` means "unknown", and ``0`` means free.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.contracts.models import ModelInfo, ModelPrice, ProviderInfo, ProvidersResponse


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _price(cost: Any) -> ModelPrice | None:
    if not isinstance(cost, dict):
        return None
    input_per_mtok = _number(cost.get("input"))
    output_per_mtok = _number(cost.get("output"))
    if input_per_mtok is None and output_per_mtok is None:
        return None
    return ModelPrice(input_per_mtok=input_per_mtok, output_per_mtok=output_per_mtok)


def _context_limit(limit: Any) -> int | None:
    if not isinstance(limit, dict):
        return None
    value = _number(limit.get("context"))
    return int(value) if value and value > 0 else None


def _is_chat_model(model: dict[str, Any]) -> bool:
    """Skip deprecated models and models that cannot produce text (image, embedding)."""
    if model.get("status") == "deprecated":
        return False
    capabilities = model.get("capabilities")
    if isinstance(capabilities, dict):
        output = capabilities.get("output")
        if isinstance(output, dict) and output.get("text") is False:
            return False
    return True


def _map_model(key: str, model: dict[str, Any]) -> ModelInfo:
    model_id = model.get("id") if isinstance(model.get("id"), str) else key
    name = model.get("name") if isinstance(model.get("name"), str) and model["name"] else model_id
    return ModelInfo(
        id=model_id,
        name=name,
        price=_price(model.get("cost")),
        context_limit=_context_limit(model.get("limit")),
    )


def map_providers(payload: dict[str, Any], now: datetime | None = None) -> ProvidersResponse:
    """Connected providers first; each group sorted by name; models sorted by name."""
    connected = payload.get("connected")
    connected_ids = (
        {p for p in connected if isinstance(p, str)} if isinstance(connected, list) else set()
    )
    all_providers = payload.get("all")
    providers: list[ProviderInfo] = []
    for raw in all_providers if isinstance(all_providers, list) else []:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        models_raw = raw.get("models")
        models = [
            _map_model(key, model)
            for key, model in (models_raw.items() if isinstance(models_raw, dict) else [])
            if isinstance(model, dict) and _is_chat_model(model)
        ]
        models.sort(key=lambda m: (m.name.casefold(), m.id))
        name = raw.get("name") if isinstance(raw.get("name"), str) and raw["name"] else raw["id"]
        providers.append(
            ProviderInfo(
                id=raw["id"],
                name=name,
                connected=raw["id"] in connected_ids,
                models=models,
            )
        )
    providers.sort(key=lambda p: (not p.connected, p.name.casefold(), p.id))
    return ProvidersResponse(providers=providers, refreshed_at=now or datetime.now(UTC))
