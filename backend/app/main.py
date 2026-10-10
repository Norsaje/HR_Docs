"""Точка входа FastAPI (`uvicorn app.main:app`).

При старте: проверка настроек и реестра (ошибка останавливает запуск с понятным текстом),
миграции БД под hr_owner, пул соединений под hr_app, бакет S3. Любая ошибка наружу —
только {"error": {code, message по-русски, support_id}}, без трассировок.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import health, operator, operator_extra, public
from app.auth import oidc
from app.config import get_settings
from app.db.core import create_pool, migrate
from app.logging import get_logger, setup_logging
from app.messages import MESSAGES, AppError
from app.registry.loader import RegistryError, get_registry
from app.storage.s3 import Storage

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    try:
        registry = get_registry()
    except RegistryError as e:
        log.error("registry_invalid", detail=str(e))
        raise SystemExit(2) from None
    applied = await migrate(settings)
    app.state.settings, app.state.registry = settings, registry
    app.state.pool = await create_pool(settings)
    app.state.storage = Storage(settings)
    await app.state.storage.ensure_bucket()
    log.info("startup", env=settings.APP_ENV, documents=len(registry.documents), migrations=applied)
    yield
    await app.state.pool.close()


def _error(code: str, message: str, status: int, request: Request) -> JSONResponse:
    rid = getattr(request.state, "request_id", "")
    return JSONResponse({"error": {"code": code, "message": message, "support_id": rid}}, status_code=status)


def create_app() -> FastAPI:
    setup_logging()
    settings = get_settings()
    docs = settings.DOCS_ENABLED and settings.APP_ENV == "dev"
    app = FastAPI(
        title="Приём документов соискателей",
        lifespan=lifespan,
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        structlog.contextvars.bind_contextvars(request_id=rid)
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        if request.url.path.startswith(("/api/operator", "/api/public/applications", "/api/auth")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        return _error(exc.code, exc.message, exc.status, request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = ", ".join(str(e["loc"][-1]) for e in exc.errors())
        return _error(
            "VALIDATION",
            MESSAGES["VALIDATION"].format(detail=f"не заполнено или неверно: {fields}"),
            422,
            request,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND"}.get(exc.status_code, "INTERNAL")
        return _error(code, MESSAGES[code], exc.status_code, request)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled", path=request.url.path)
        return _error("INTERNAL", MESSAGES["INTERNAL"], 500, request)

    app.include_router(health.router)
    app.include_router(public.router)
    app.include_router(oidc.router)
    app.include_router(operator.router)
    app.include_router(operator_extra.router)
    return app


app = create_app()
