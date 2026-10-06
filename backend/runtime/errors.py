"""Translate OpenCode error objects into the contract's ``Failed`` events.

OpenCode reports a failed turn through ``session.error`` events (and the assistant message's
``error`` field) shaped ``{"name": "<ErrorName>", "data": {...}}``. Verified names:
``ProviderAuthError``, ``APIError`` (``statusCode``, ``isRetryable``, ``message``,
``responseBody``), ``UnknownError`` (e.g. ``Model not found: openai/gpt-6-sol``),
``MessageAbortedError``, ``MessageOutputLengthError``, ``ContextOverflowError``,
``ContentFilterError`` and ``StructuredOutputError``.

Messages are kept short and never include response headers or bodies, which may echo request
details.
"""

from __future__ import annotations

import re
from typing import Any

from backend.contracts.models import ErrorCode
from backend.contracts.runtime import Failed

MAX_MESSAGE_CHARS = 400

_RATE_LIMIT = re.compile(
    r"\b429\b|rate[\s_-]?limit|too many requests|quota|resource[\s_-]?exhausted"
    r"|usage limit|limit reached|free usage exceeded|slow down|throttl"
    r"|(?:requests|tokens) per (?:min|minute|day|hour)",
    re.IGNORECASE,
)
_AUTH = re.compile(
    r"unauthori[sz]ed|\bforbidden\b|invalid[\s_-]?api[\s_-]?key|incorrect api key"
    r"|authenticat|not connected|no credentials|credentials? (?:missing|expired|invalid)"
    r"|token (?:expired|invalid|revoked)|sign[\s-]?in again|log[\s-]?in again",
    re.IGNORECASE,
)
_MODEL_MISSING = re.compile(
    r"model not found|model_not_found|(?:unknown|invalid|unsupported|unavailable) model"
    r"|model[^.\n]{0,80}(?:not found|not supported|does not exist|unavailable|not available"
    r"|not exist|is not a valid)",
    re.IGNORECASE,
)

# OpenCode's wording when the provider is not loaded or the model id is not in its catalog.
MODEL_NOT_FOUND_PREFIX = "Model not found"


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_MESSAGE_CHARS else text[: MAX_MESSAGE_CHARS - 1] + "…"


def _message(data: Any, default: str) -> str:
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, str) and message.strip():
            return _short(message)
    return default


def classify_text(text: str) -> ErrorCode:
    """Best-effort classification of free text (retry statuses, unknown errors)."""
    if _RATE_LIMIT.search(text):
        return ErrorCode.RATE_LIMITED
    if _MODEL_MISSING.search(text):
        return ErrorCode.MODEL_UNAVAILABLE
    if _AUTH.search(text):
        return ErrorCode.PROVIDER_UNAVAILABLE
    return ErrorCode.RUNTIME_ERROR


def is_missing_model(failed: Failed) -> bool:
    """True for OpenCode's 'Model not found: provider/model' (disconnected provider or bad id)."""
    return failed.code is ErrorCode.MODEL_UNAVAILABLE and failed.message.startswith(
        MODEL_NOT_FOUND_PREFIX
    )


def classify_error(error: Any) -> Failed:
    """Map an OpenCode error object (``{"name", "data"}``) to a ``Failed`` event."""
    if not isinstance(error, dict):
        return Failed(ErrorCode.RUNTIME_ERROR, "OpenCode reported an unknown error")
    name = error.get("name")
    data = error.get("data")

    if name == "ProviderAuthError":
        provider = data.get("providerID") if isinstance(data, dict) else None
        default = f"Provider {provider!r} is not authenticated" if provider else "Not authenticated"
        return Failed(ErrorCode.PROVIDER_UNAVAILABLE, _message(data, default))

    if name == "APIError" and isinstance(data, dict):
        message = _message(data, "The provider returned an error")
        status = data.get("statusCode")
        body = data.get("responseBody") if isinstance(data.get("responseBody"), str) else ""
        haystack = f"{message} {body}"
        if status == 429:
            return Failed(ErrorCode.RATE_LIMITED, message)
        if status in (401, 403):
            return Failed(ErrorCode.PROVIDER_UNAVAILABLE, message)
        if status == 404 or (status in (400, 422) and _MODEL_MISSING.search(haystack)):
            return Failed(ErrorCode.MODEL_UNAVAILABLE, message)
        if status == 402:
            return Failed(ErrorCode.RUNTIME_ERROR, message)
        code = classify_text(haystack)
        # Free text can say "unauthorized" for many reasons; only trust it for auth-ish bodies.
        return Failed(code, message)

    if name == "ContextOverflowError":
        return Failed(
            ErrorCode.RUNTIME_ERROR,
            _message(data, "The conversation no longer fits the model's context window"),
        )
    if name == "ContentFilterError":
        return Failed(ErrorCode.RUNTIME_ERROR, _message(data, "Blocked by the provider's filter"))
    if name == "MessageAbortedError":
        return Failed(ErrorCode.RUNTIME_ERROR, "Generation was aborted")

    message = _message(data, f"OpenCode error: {name}" if name else "OpenCode error")
    return Failed(classify_text(message), message)
