"""Ошибки шлюза модели: один тип исключения с кодом причины и признаком «можно повторить».

Очередь смотрит только на `retryable`: повторяемые ошибки уходят в backoff задачи,
неповторяемые сразу завершают попытку. Код (`TIMEOUT`, `SCHEMA_INVALID`, ...) пишется
в журнал и в «В работе», человеку показывается русский текст из app/messages.py.
"""

from __future__ import annotations

from typing import Literal

ErrorCode = Literal["TIMEOUT", "UNAVAILABLE", "RATE_LIMITED", "BAD_RESPONSE", "SCHEMA_INVALID", "TRUNCATED"]


class ModelError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        detail: str,
        *,
        retryable: bool,
        retry_after_s: float | None = None,
        provider: str = "",
    ) -> None:
        super().__init__(f"{code}: {detail}")
        self.code: ErrorCode = code
        self.detail = detail  # технический текст для логов; без ПДн и без текста ответа модели
        self.retryable = retryable
        self.retry_after_s = retry_after_s
        self.provider = provider
