"""Журнал аудита: запись события в ТОЙ ЖЕ транзакции, что и действие.

Если действие откатилось, откатывается и запись журнала, и наоборот — запись не может
«потеряться» отдельно от действия. Текст — по-русски, для человека. У роли hr_app на
audit_events только SELECT и INSERT: изменить или удалить запись приложение не может.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg


async def audit(
    conn: asyncpg.Connection | asyncpg.Pool,
    actor: str,
    action: str,
    message_ru: str,
    application_id: Any = None,
    object_id: Any = None,
    details: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO audit_events (actor, action, application_id, object_id, message_ru, details) "
        "VALUES ($1, $2, $3, $4, $5, $6::jsonb)",
        actor,
        action,
        application_id,
        str(object_id) if object_id else None,
        message_ru,
        json.dumps(details or {}, ensure_ascii=False),
    )
