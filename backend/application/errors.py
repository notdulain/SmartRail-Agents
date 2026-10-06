"""Application errors and their mapping to ``ErrorResponse`` bodies."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.contracts.models import ErrorCode, ErrorResponse


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: ErrorCode,
        message: str,
        agent_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.agent_id = agent_id

    def response(self) -> JSONResponse:
        body = ErrorResponse(code=self.code, message=self.message, agent_id=self.agent_id)
        return JSONResponse(body.model_dump(mode="json"), status_code=self.status_code)


def not_found(what: str, ident: str) -> AppError:
    return AppError(404, ErrorCode.NOT_FOUND, f"{what} {ident!r} not found")


def invalid(message: str, agent_id: str | None = None) -> AppError:
    return AppError(422, ErrorCode.VALIDATION_ERROR, message, agent_id)


def _validation_message(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body",))
        msg = str(err.get("msg", "invalid value"))
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts) or "invalid request"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return exc.response()

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return invalid(_validation_message(exc)).response()

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if not request.url.path.startswith("/api"):
            # Non-API paths (static frontend) keep Starlette's plain behaviour.
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.VALIDATION_ERROR
        if exc.status_code >= 500:
            code = ErrorCode.RUNTIME_ERROR
        message = str(exc.detail) if exc.detail else "request failed"
        return AppError(exc.status_code, code, message).response()
