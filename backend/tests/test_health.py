"""/api/healthz отвечает 200 без внешних зависимостей."""

from fastapi.testclient import TestClient

from app.main import app


def test_healthz() -> None:
    with TestClient(app) as client:
        r = client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
