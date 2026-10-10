"""Зависимости FastAPI для оператора: сессия по cookie, права, CSRF и фильтр по офису.

get_operator — нет валидной сессии → 401 (на ВСЕХ маршрутах /api/operator/*, зависимость стоит
на роутере). require(perm) — нет права → 403 и запись access.denied. scoped_where — единственное
место, где строится условие видимости: оператор видит только заявки своих офисов, supervisor — все;
чужой объект выглядит как несуществующий (404).
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Request

from app.audit.log import audit
from app.messages import AppError

COOKIE = "__Host-Http-hr_session"

PERMISSIONS = {
    "applications:read": {"doc-reader", "doc-editor", "supervisor"},
    "files:read": {"doc-reader", "doc-editor", "supervisor"},
    "queue:read": {"doc-reader", "doc-editor", "supervisor"},
    "history:read": {"doc-reader", "doc-editor", "supervisor"},
    "fields:edit": {"doc-editor", "supervisor"},
    "documents:reprocess": {"doc-editor", "supervisor"},
    "exports": {"doc-editor", "supervisor"},
    "requests:send": {"doc-editor", "supervisor"},
    # Редактор заводит сотрудников только своего офиса и только reader/editor (проверка — в staff_create).
    "staff:manage": {"doc-editor", "supervisor"},
}


@dataclass
class Operator:
    session_hash: str
    sub: str
    name: str
    roles: list[str]
    offices: list[str]

    @property
    def is_supervisor(self) -> bool:
        return "supervisor" in self.roles


async def get_operator(request: Request) -> Operator:
    sid = request.cookies.get(COOKIE)
    if not sid:
        raise AppError("UNAUTHORIZED", 401)
    s, pool = request.app.state.settings, request.app.state.pool
    h = hashlib.sha256(sid.encode()).hexdigest()
    row = await pool.fetchrow("SELECT * FROM sessions WHERE id_hash=$1", h)
    now = datetime.now(UTC)
    if (
        not row
        or now - row["last_seen_at"] > timedelta(minutes=s.SESSION_IDLE_MIN)
        or now - row["created_at"] > timedelta(hours=s.SESSION_ABS_HOURS)
    ):
        if row:
            await pool.execute("DELETE FROM sessions WHERE id_hash=$1", h)
        raise AppError("UNAUTHORIZED", 401)
    if now - row["last_seen_at"] > timedelta(minutes=1):
        await pool.execute("UPDATE sessions SET last_seen_at=now() WHERE id_hash=$1", h)
    return Operator(h, row["sub"], row["name"], list(row["roles"]), list(row["offices"]))


async def csrf_guard(request: Request) -> None:
    """Для изменяющих запросов: обязательный X-Requested-With и Origin только наш."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origin = request.headers.get("origin")
    if request.headers.get("x-requested-with") is None or (
        origin and origin != request.app.state.settings.PUBLIC_BASE_URL
    ):
        raise AppError("CSRF", 403)


def require(perm: str) -> Callable[..., Awaitable[Operator]]:
    async def dep(request: Request, op: Operator = Depends(get_operator)) -> Operator:
        if not set(op.roles) & PERMISSIONS[perm]:
            await audit(
                request.app.state.pool,
                op.name,
                "access.denied",
                f"Отказ в доступе: {op.name} пытался выполнить действие «{perm}» без права",
            )
            raise AppError("FORBIDDEN", 403)
        return op

    return dep


def scoped_where(op: Operator, alias: str = "a", param: int = 1) -> tuple[str, list[object]]:
    """Условие видимости заявок оператора и его параметры (для supervisor — без ограничений)."""
    if op.is_supervisor:
        return "TRUE", []
    return f"{alias}.office_slug = ANY(${param}::text[])", [op.offices]
