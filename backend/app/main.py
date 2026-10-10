"""Точка входа FastAPI (`uvicorn app.main:app`).

При старте проверяются настройки и реестр документов: ошибка в .env или в
registry/documents.yaml останавливает процесс с понятным текстом, а не всплывает
на первом запросе. Маршруты подключаются из app/api/*.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.config import get_settings
from app.logging import get_logger, setup_logging
from app.registry.loader import RegistryError, get_registry

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    try:
        registry = get_registry()
    except RegistryError as e:
        log.error("registry_invalid", detail=str(e))
        raise SystemExit(2) from None
    log.info("startup", env=settings.APP_ENV, documents=len(registry.documents))
    yield


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
    app.include_router(health.router)
    return app


app = create_app()
