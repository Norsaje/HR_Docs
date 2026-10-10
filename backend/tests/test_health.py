"""/api/healthz отвечает 200 без внешних зависимостей."""

from app.main import app
from fastapi.testclient import TestClient


def test_healthz() -> None:
    with TestClient(app) as client:
        r = client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
