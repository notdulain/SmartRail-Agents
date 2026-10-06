"""OpenRouter spend estimation. Subscription providers are not priced."""

from __future__ import annotations

from backend.contracts.models import ModelPrice, ProvidersResponse, Usage

OPENROUTER = "openrouter"


def estimate_tokens(text: str) -> int:
    """Cheap, deliberately generous token estimate (about four characters per token)."""
    return len(text) // 4 + 1


def find_price(
    catalog: ProvidersResponse | None, provider_id: str, model_id: str
) -> ModelPrice | None:
    if catalog is None:
        return None
    for provider in catalog.providers:
        if provider.id == provider_id:
            for model in provider.models:
                if model.id == model_id:
                    return model.price
    return None


def price_cost(price: ModelPrice | None, input_tokens: int, output_tokens: int) -> float:
    if price is None:
        return 0.0
    cost = (price.input_per_mtok or 0.0) * input_tokens / 1_000_000
    cost += (price.output_per_mtok or 0.0) * output_tokens / 1_000_000
    return cost


def estimate_request_cost(
    price: ModelPrice | None, input_text: str, max_output_tokens: int
) -> float:
    """Worst case: the whole prompt plus every allowed output token."""
    return price_cost(price, estimate_tokens(input_text), max_output_tokens)


def would_exceed(spent: float, estimate: float, budget: float) -> bool:
    """True when this request must not run. Unpriced models are blocked once the budget is gone."""
    if budget <= 0:
        return True
    if estimate <= 0:
        return spent >= budget
    return spent + estimate > budget


def actual_cost(
    usage: Usage, price: ModelPrice | None, fallback_input_tokens: int | None = None
) -> float:
    """Reported cost, or a price-based figure when the runtime reported none."""
    if usage.cost_usd is not None:
        return usage.cost_usd
    input_tokens = usage.input_tokens or fallback_input_tokens or 0
    return price_cost(price, input_tokens, usage.output_tokens)
