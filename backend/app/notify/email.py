"""Письма соискателю через SMTP (на стенде — Mailpit, в проде — SMTP заказчика).

Три письма: ссылка на заявку после отправки; «чего не хватает / что не прочиталось» после разбора
(не чаще раза в NOTIFY_MIN_INTERVAL_MIN); запрос документов от оператора с комментарием.
Отправка — smtplib в отдельном потоке; каждое письмо пишется в notifications и в журнал.
В письме нет значений полей документов — только статусы и ссылка.
"""

from __future__ import annotations

import asyncio
import os
import smtplib
from email.message import EmailMessage
from typing import Any

import asyncpg

from app.audit.log import audit
from app.config import Settings
from app.logging import get_logger
from app.registry.loader import Registry

log = get_logger(__name__)


def _send(settings: Settings, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.MAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
        smtp.send_message(msg)


def app_link(settings: Settings, app_id: Any, token: str | None) -> str:
    # Токен есть только в момент создания заявки; позже ссылка ведёт на страницу заявки без токена.
    return f"{settings.PUBLIC_BASE_URL}/#/a/{app_id}/{token}" if token else f"{settings.PUBLIC_BASE_URL}/"


async def send(
    pool: asyncpg.Pool,
    settings: Settings,
    app_row: Any,
    kind: str,
    subject: str,
    body: str,
    actor: str = "система",
) -> bool:
    try:
        await asyncio.to_thread(_send, settings, app_row["email"], subject, body)
        status = "sent"
    except (OSError, smtplib.SMTPException) as e:
        log.warning("mail_failed", kind=kind, error=type(e).__name__)
        status = "failed"
    await pool.execute(
        "INSERT INTO notifications (application_id, kind, subject, status) VALUES ($1,$2,$3,$4)",
        app_row["id"],
        kind,
        subject,
        status,
    )
    await audit(
        pool,
        actor,
        f"notify.{kind}",
        f"Письмо соискателю «{subject}» — {'отправлено' if status == 'sent' else 'не отправлено (почта недоступна)'}",
        application_id=app_row["id"],
    )
    return status == "sent"


async def notify_problems(pool: asyncpg.Pool, settings: Settings, registry: Registry, app_id: Any) -> None:
    """После разбора: если по обязательным документам чего-то не хватает или не прочиталось — письмо (с паузой)."""
    a = await pool.fetchrow("SELECT * FROM applications WHERE id=$1", app_id)
    if not a or a["status"] == "draft":
        return
    interval = int(os.environ.get("NOTIFY_MIN_INTERVAL_MIN", "10"))
    recent = await pool.fetchval(
        "SELECT 1 FROM notifications WHERE application_id=$1 AND kind='missing' "
        "AND created_at > now() - make_interval(mins => $2)",
        app_id,
        interval,
    )
    if recent:
        return
    if await pool.fetchval(
        "SELECT 1 FROM documents WHERE application_id=$1 AND status IN ('uploaded','processing')", app_id
    ):
        return  # ещё разбирается — письмо после окончания
    group = registry.group_for_country(a["country_code"])
    docs = {
        r["doc_type"]: r for r in await pool.fetch("SELECT * FROM documents WHERE application_id=$1", app_id)
    }
    lines = []
    for dt in group.documents if group else []:
        ddef = registry.documents[dt]
        d = docs.get(dt)
        if d is None and ddef.required:
            lines.append(f"• {ddef.title} — не загружен")
        elif d is not None and d["status"] in ("unreadable", "failed"):
            lines.append(
                f"• {ddef.title} — не прочитался: {d['message_ru'] or 'загрузите более чёткое фото'}"
            )
    if not lines:
        return
    body = (
        f"Здравствуйте, {a['full_name']}!\n\nПо заявке {a['public_no']} нужно дослать документы:\n"
        + "\n".join(lines)
        + f"\n\nОткройте ссылку на заявку из первого письма и загрузите файлы в ту же заявку.\n"
        f"Если ссылки нет — {settings.PUBLIC_BASE_URL}/\n\nКадровое агентство"
    )
    await send(pool, settings, a, "missing", f"Заявка {a['public_no']}: не хватает документов", body)
