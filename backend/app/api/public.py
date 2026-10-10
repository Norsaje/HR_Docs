"""Публичное API соискателя: страны, набор документов, заявка, загрузка, отправка, статус.

Соискатель без регистрации: доступ к заявке — по токену (256 бит) в заголовке
X-Application-Token; в БД хранится только его SHA-256. Ответ о статусе НЕ содержит
значений извлечённых полей — утёкшая ссылка не раскрывает паспортные данные.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from pydantic import BaseModel

from app.audit.log import audit
from app.intake.service import accept_file
from app.messages import AppError
from app.notify.email import app_link
from app.notify.email import send as send_mail

router = APIRouter(prefix="/api/public")

STATUS_RU = {
    "missing": "Не загружен",
    "uploaded": "Принят, ждёт разбора",
    "processing": "Разбирается",
    "ready": "Готово",
    "needs_review": "Есть замечания",
    "unreadable": "Не прочитался",
    "failed": "Ошибка разбора",
}


STAGE_RU = {
    "queued": "В очереди, этап 1 из 4",
    "preprocess": "Подготовка изображения, этап 2 из 4",
    "extract": "Читаем документ, этап 3 из 4",
    "ocr": "Читаем документ, этап 3 из 4",
    "verify": "Проверяем данные, этап 4 из 4",
}


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def load_app(request: Request, app_id: str, token: str | None) -> asyncpg.Record:
    """Заявка по id и токену. Неверный, чужой или истёкший токен — 401, без подсказок."""
    if not token:
        raise AppError("TOKEN_INVALID", 401)
    try:
        uid = uuid.UUID(app_id)
    except ValueError:
        raise AppError("TOKEN_INVALID", 401) from None
    row = await request.app.state.pool.fetchrow("SELECT * FROM applications WHERE id = $1", uid)
    if (
        not row
        or not secrets.compare_digest(row["token_hash"], _sha(token))
        or row["token_expires"] < datetime.now(UTC)
    ):
        raise AppError("TOKEN_INVALID", 401)
    return row


@router.get("/countries")
async def countries(request: Request) -> list[dict[str, str]]:
    reg = request.app.state.registry
    out = []
    for gid, group in reg.country_groups.items():
        for code in group.countries:
            out.append({"code": code, "name_ru": reg.country_names[code], "group": gid})
    return sorted(out, key=lambda c: c["name_ru"])


@router.get("/requirements")
async def requirements(request: Request, country: str) -> dict[str, Any]:
    reg = request.app.state.registry
    group = reg.group_for_country(country.upper())
    if not group:
        raise AppError("COUNTRY_NOT_SUPPORTED", 422)
    return {
        "documents": [
            {
                "doc_type": d,
                "title": reg.documents[d].title,
                "hint": reg.documents[d].applicant_hint,
                "max_files": reg.documents[d].max_files,
                "required": reg.documents[d].required,
            }
            for d in group.documents
        ]
    }


class NewApplication(BaseModel):
    country_code: str
    full_name: str
    email: str
    consent_accepted: bool = False
    office: str | None = None


@router.post("/applications", status_code=201)
async def create_application(request: Request, body: NewApplication) -> dict[str, Any]:
    reg, settings, pool = request.app.state.registry, request.app.state.settings, request.app.state.pool
    if body.consent_accepted is not True:
        raise AppError("CONSENT_REQUIRED", 422)
    country = body.country_code.upper()
    if not reg.group_for_country(country):
        raise AppError("COUNTRY_NOT_SUPPORTED", 422)
    name = " ".join(body.full_name.split())
    if not (2 <= len(name) <= 200):
        raise AppError("VALIDATION", 422, detail="ФИО от 2 до 200 символов")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", body.email.strip()) or len(body.email) > 200:
        raise AppError("VALIDATION", 422, detail="неверный email")
    offices = {o.slug for o in reg.offices}
    office = body.office if body.office in offices else reg.default_office().slug
    token = secrets.token_urlsafe(32)
    app_id = uuid.uuid4()
    public_no = f"A-{datetime.now(UTC):%y%m%d}-{secrets.randbelow(10**4):04d}"
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO applications (id, public_no, office_slug, country_code, full_name, email, token_hash, "
            "token_expires, consent_version, consent_at, consent_ip) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,now(),$10)",
            app_id,
            public_no,
            office,
            country,
            name,
            body.email.strip(),
            _sha(token),
            datetime.now(UTC) + timedelta(days=settings.APPLICANT_TOKEN_TTL_DAYS),
            settings.CONSENT_VERSION,
            request.client.host if request.client else None,
        )
        await audit(
            conn,
            "соискатель",
            "application.created",
            f"Создана заявка {public_no} (страна {country}), согласие {settings.CONSENT_VERSION} получено",
            application_id=app_id,
        )
    code = await _new_email_code(pool, app_id)
    row = await pool.fetchrow("SELECT * FROM applications WHERE id=$1", app_id)
    await _send_code(pool, settings, row, code)
    return {"application_id": str(app_id), "public_no": public_no, "token": token}


def _code_hash(app_id: Any, code: str) -> str:
    # Код короткий (6 цифр), поэтому хешируем вместе с id заявки; перебор ограничен 5 попытками.
    return _sha(f"{app_id}:{code}")


async def _new_email_code(pool: asyncpg.Pool, app_id: Any) -> str:
    code = f"{secrets.randbelow(10**6):06d}"
    await pool.execute(
        "UPDATE applications SET email_confirm_hash=$2, email_confirm_attempts=0, email_code_sent_at=now() WHERE id=$1",
        app_id,
        _code_hash(app_id, code),
    )
    return code


async def _send_code(pool: asyncpg.Pool, settings: Any, row: asyncpg.Record, code: str) -> None:
    body_text = "\n".join(
        [
            f"Здравствуйте, {row['full_name']}!",
            "",
            f"Код подтверждения email для заявки {row['public_no']}: {code}",
            "",
            "Введите его на странице заявки. Если вы не подавали заявку, просто проигнорируйте это письмо.",
            "",
            "Кадровое агентство",
        ]
    )
    await send_mail(
        pool,
        settings,
        row,
        "confirm",
        f"Код подтверждения для заявки {row['public_no']}",
        body_text,
        actor="соискатель",
    )


class ConfirmBody(BaseModel):
    code: str


@router.post("/applications/{app_id}/confirm-email")
async def confirm_email(
    request: Request, app_id: str, body: ConfirmBody, x_application_token: str | None = Header(default=None)
) -> dict[str, Any]:
    """Соискатель вводит код из письма. Без подтверждения сервер не принимает файлы и отправку."""
    row = await load_app(request, app_id, x_application_token)
    pool = request.app.state.pool
    if row["email_confirmed_at"] is not None:
        return {"email_confirmed": True}
    if row["email_confirm_attempts"] >= 5:
        raise AppError("CODE_TOO_MANY", 429)
    code = "".join(ch for ch in body.code if ch.isdigit())
    if not row["email_confirm_hash"] or not secrets.compare_digest(
        row["email_confirm_hash"], _code_hash(row["id"], code)
    ):
        await pool.execute(
            "UPDATE applications SET email_confirm_attempts=email_confirm_attempts+1 WHERE id=$1", row["id"]
        )
        raise AppError("CODE_INVALID", 422, left=max(0, 4 - row["email_confirm_attempts"]))
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE applications SET email_confirmed_at=now(), email_confirm_hash=NULL WHERE id=$1", row["id"]
        )
        await audit(
            conn,
            "соискатель",
            "email.confirmed",
            f"Email для заявки {row['public_no']} подтверждён кодом",
            application_id=row["id"],
        )
    return {"email_confirmed": True}


@router.post("/applications/{app_id}/resend-code")
async def resend_code(
    request: Request, app_id: str, x_application_token: str | None = Header(default=None)
) -> dict[str, Any]:
    row = await load_app(request, app_id, x_application_token)
    st = request.app.state
    if row["email_confirmed_at"] is not None:
        return {"sent": False, "email_confirmed": True}
    sent = row["email_code_sent_at"]
    if sent and (datetime.now(UTC) - sent).total_seconds() < 60:
        raise AppError("CODE_TOO_SOON", 429)
    code = await _new_email_code(st.pool, row["id"])
    await _send_code(st.pool, st.settings, row, code)
    return {"sent": True}


def require_confirmed(row: asyncpg.Record) -> None:
    if row["email_confirmed_at"] is None:
        raise AppError("EMAIL_NOT_CONFIRMED", 403)


@router.post("/applications/{app_id}/files")
async def upload_files(
    request: Request,
    app_id: str,
    doc_type: str = Form(...),
    files: list[UploadFile] = File(...),
    x_application_token: str | None = Header(default=None),
) -> dict[str, Any]:
    st = request.app.state
    row = await load_app(request, app_id, x_application_token)
    require_confirmed(row)
    if len(files) > st.settings.MAX_FILES_PER_REQUEST:
        raise AppError("TOO_MANY_FILES_REQUEST", 422, n=st.settings.MAX_FILES_PER_REQUEST)
    results = []
    limit = st.settings.MAX_FILE_MB * 1024 * 1024
    for f in files:
        data = await f.read(limit + 1)  # читаем не больше лимита + 1 байт: этого хватает, чтобы отказать
        name = f.filename or "файл"
        try:
            acc = await accept_file(
                st.pool,
                st.storage,
                st.settings,
                st.registry,
                row,
                doc_type,
                name,
                data,
                request.state.request_id,
            )
            results.append({"name": name, "accepted": True, "file_id": str(acc.file_id)})
        except AppError as e:
            if len(files) == 1 and e.code == "DUPLICATE_FILE":
                raise
            results.append({"name": name, "accepted": False, "error": {"code": e.code, "message": e.message}})
    return {"results": results}


async def missing_docs(pool: asyncpg.Pool, reg: Any, row: asyncpg.Record) -> list[str]:
    group = reg.group_for_country(row["country_code"])
    have = {
        r["doc_type"]
        for r in await pool.fetch(
            "SELECT DISTINCT d.doc_type FROM documents d JOIN files f ON f.document_id = d.id WHERE d.application_id=$1",
            row["id"],
        )
    }
    return [d for d in group.documents if reg.documents[d].required and d not in have]


@router.post("/applications/{app_id}/submit")
async def submit(
    request: Request, app_id: str, x_application_token: str | None = Header(default=None)
) -> dict[str, Any]:
    st = request.app.state
    row = await load_app(request, app_id, x_application_token)
    require_confirmed(row)
    missing = await missing_docs(st.pool, st.registry, row)
    async with st.pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE applications SET status = CASE WHEN status='draft' THEN 'submitted' ELSE status END, "
            "submitted_at = coalesce(submitted_at, now()), updated_at = now() WHERE id = $1",
            row["id"],
        )
        await audit(
            conn,
            "соискатель",
            "application.submitted",
            f"Заявка {row['public_no']} отправлена"
            + (f"; не хватает: {', '.join(missing)}" if missing else ""),
            application_id=row["id"],
        )
    if row["status"] == "draft":  # первое «Отправить» — письмо со ссылкой на заявку (дозагрузка по ней)
        titles = [st.registry.documents[d].title for d in missing]
        link = app_link(st.settings, row["id"], x_application_token)
        lines = [
            f"Здравствуйте, {row['full_name']}!",
            "",
            f"Заявка {row['public_no']} принята. Статус документов и дозагрузка — по ссылке:",
            link,
            "",
            f"Не хватает: {', '.join(titles)}." if titles else "",
            "Ссылка личная, не пересылайте её.",
            "",
            "Кадровое агентство",
        ]
        body = "\n".join(lines)
        await send_mail(
            st.pool, st.settings, row, "link", f"Заявка {row['public_no']} принята", body, actor="соискатель"
        )
    return {"public_no": row["public_no"], "missing": missing}


@router.get("/applications/{app_id}")
async def get_application(
    request: Request, app_id: str, x_application_token: str | None = Header(default=None)
) -> dict[str, Any]:
    row = await load_app(request, app_id, x_application_token)  # сначала токен, потом всё остальное
    st = request.app.state
    reg = st.registry
    docs = {
        r["doc_type"]: r
        for r in await st.pool.fetch("SELECT * FROM documents WHERE application_id=$1", row["id"])
    }
    files = await st.pool.fetch(
        "SELECT f.id, f.document_id, f.original_name, f.status, f.error_message_ru FROM files f "
        "WHERE f.application_id=$1 ORDER BY f.created_at",
        row["id"],
    )
    issues = await st.pool.fetch(
        "SELECT i.document_id, i.applicant_message, i.severity FROM issues i JOIN documents d ON d.id=i.document_id "
        "WHERE d.application_id=$1 AND i.applicant_message IS NOT NULL",
        row["id"],
    )
    group = reg.group_for_country(row["country_code"])
    out = []
    for dt in group.documents:
        d = docs.get(dt)
        msgs = [i["applicant_message"] for i in issues if d and i["document_id"] == d["id"]]
        out.append(
            {
                "doc_type": dt,
                "title": reg.documents[dt].title,
                "required": reg.documents[dt].required,
                "status": d["status"] if d else "missing",
                "status_ru": STATUS_RU[d["status"] if d else "missing"],
                "stage": d["stage"] if d else None,
                "stage_ru": STAGE_RU.get(d["stage"] or "")
                if d and d["status"] in ("uploaded", "processing")
                else None,
                "message": "; ".join(filter(None, [d["message_ru"] if d else None, *msgs])) or None,
                "files": [
                    {
                        "file_id": str(f["id"]),
                        "name": f["original_name"],
                        "status": f["status"],
                        "error_message": f["error_message_ru"],
                    }
                    for f in files
                    if d and f["document_id"] == d["id"]
                ],
            }
        )
    return {
        "public_no": row["public_no"],
        "status": row["status"],
        "email_confirmed": row["email_confirmed_at"] is not None,
        "documents": out,
        "missing": await missing_docs(st.pool, reg, row),
    }
