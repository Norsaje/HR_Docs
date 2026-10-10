"""Закрытость операторского API: тест сам перечисляет ВСЕ маршруты /api/operator/* приложения.

Без cookie сессии каждый маршрут обязан отдать 401 — до любой бизнес-логики (зависимость стоит
на роутере, поэтому БД не нужна). Новый маршрут попадает в проверку автоматически.
Плюс: изменяющий запрос без X-Requested-With отклоняется, публичный статус без токена — 401.
"""

from __future__ import annotations

import re

import pytest
from app.api import health, operator, public
from app.auth import oidc
from app.main import app
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

OPERATOR_ROUTES = [
    (method, route.path)
    # Все роутеры приложения (новые версии FastAPI прячут их в app.routes за обёрткой).
    for router in (health.router, public.router, oidc.router, operator.router)
    for route in router.routes
    if isinstance(route, APIRoute) and route.path.startswith("/api/operator")
    for method in sorted(route.methods)
]


def test_operator_routes_found() -> None:
    assert len(OPERATOR_ROUTES) >= 8


@pytest.mark.parametrize(("method", "path"), OPERATOR_ROUTES)
def test_operator_route_requires_session(method: str, path: str) -> None:
    client = TestClient(app)  # без lifespan: до БД запрос дойти не должен
    url = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", path)
    r = client.request(method, url, headers={"X-Requested-With": "fetch"}, json={})
    assert r.status_code == 401, f"{method} {path} без сессии вернул {r.status_code}"
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_forged_cookie_is_not_trusted_without_db() -> None:
    # Подделанная cookie требует проверки по БД; без неё доступа нет (тест на стенде — e2e-smoke).
    client = TestClient(app)
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_public_status_requires_token() -> None:
    client = TestClient(app)
    r = client.get("/api/public/applications/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 401
