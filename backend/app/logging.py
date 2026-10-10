"""Технические логи: JSON через structlog, со сквозным request_id.

В логи не попадают ПДн, изображения и ответы модели. Дополнительно фильтр вырезает
значения по «опасным» ключам (token, password, secret, authorization, cookie) —
на случай, если кто-то по ошибке передаст их в событие.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_SENSITIVE = ("token", "password", "secret", "authorization", "cookie", "api_key")


def _redact(_: Any, __: str, event: dict[str, Any]) -> dict[str, Any]:
    for key in list(event):
        if any(s in key.lower() for s in _SENSITIVE):
            event[key] = "***"
    return event


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,  # request_id, job_id из контекста
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
