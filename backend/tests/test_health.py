"""/api/healthz отвечает 200 без внешних зависимостей."""

from app.main import app
from fastapi.testclient import TestClient


def test_healthz() -> None:
    client = TestClient(app)  # без with: lifespan (БД, S3) для healthz не нужен
    r = client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
