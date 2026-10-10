"""Фильтр логов вырезает секреты, но не трогает метрики."""

from app.logging import _redact


def test_redact_secrets_keep_metrics() -> None:
    event = {
        "refresh_token": "x",
        "password": "y",
        "cookie": "z",
        "api_key": "k",
        "tokens_in": 10,
        "event": "e",
    }
    out = _redact(None, "info", dict(event))
    assert out["refresh_token"] == out["password"] == out["cookie"] == out["api_key"] == "***"
    assert out["tokens_in"] == 10 and out["event"] == "e"
