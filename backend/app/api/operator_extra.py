"""Дополнительные действия оператора: «проверено», перенос файла в другой слот, запрос документов
у соискателя, превью без ПДн, состояние модели и раздел «Сотрудники» (Keycloak Admin API).

Тот же принцип, что в operator.py: роутер закрыт сессией (401), действия — правами роли (403),
объекты чужого офиса — 404, каждое действие пишется в журнал аудита.
Превью по умолчанию размыто целиком: оригинал открывается только по клику и с записью в журнал.
"""

from __future__ import annotations

import io
import json
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from PIL import Image, ImageFilter
from pydantic import BaseModel

from app.audit.log import audit
from app.auth.deps import Operator, csrf_guard, get_operator, require, scoped_where
from app.messages import AppError
from app.notify.email import send as send_mail
from app.pipeline.run import recompute_application

router = APIRouter(prefix="/api/operator", dependencies=[Depends(get_operator), Depends(csrf_guard)])


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise AppError("NOT_FOUND", 404) from None


@router.post("/documents/{doc_id}/verify")
async def verify(
    request: Request, doc_id: str, op: Operator = Depends(require("documents:reprocess"))
) -> dict[str, Any]:
    pool, reg = request.app.state.pool, request.app.state.registry
    where, params = scoped_where(op, "a", 2)
    d = await pool.fetchrow(
        f"SELECT d.* FROM documents d JOIN applications a ON a.id=d.application_id WHERE d.id=$1 AND {where}",
        _uuid(doc_id),
        *params,
    )
    if not d:
        raise AppError("NOT_FOUND", 404)
    title = reg.documents[d["doc_type"]].title if d["doc_type"] in reg.documents else d["doc_type"]
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE documents SET status='ready', verified_by=$2, verified_at=now(), updated_at=now() "
            "WHERE id=$1",
            d["id"],
            op.name,
        )
        await recompute_application(conn, d["application_id"])
        await audit(
            conn,
            op.name,
            "document.verified",
            f"{op.name} отметил документ «{title}» проверенным",
            application_id=d["application_id"],
            object_id=d["id"],
        )
    return {"ok": True}


class MoveBody(BaseModel):
    doc_type: str


@router.post("/files/{file_id}/move")
async def move_file(
    request: Request, file_id: str, body: MoveBody, op: Operator = Depends(require("documents:reprocess"))
) -> dict[str, Any]:
    """Соискатель ошибся слотом: файл переносится в другой документ и разбирается заново по его схеме."""
    pool, reg, settings = request.app.state.pool, request.app.state.registry, request.app.state.settings
    where, params = scoped_where(op, "a", 2)
    f = await pool.fetchrow(
        f"SELECT f.*, a.country_code, d.doc_type AS old_type FROM files f JOIN applications a ON a.id=f.application_id "
        f"JOIN documents d ON d.id=f.document_id WHERE f.id=$1 AND {where}",
        _uuid(file_id),
        *params,
    )
    if not f:
        raise AppError("NOT_FOUND", 404)
    group = reg.group_for_country(f["country_code"])
    if not group or body.doc_type not in group.documents:
        raise AppError("DOC_TYPE_NOT_ALLOWED", 422)
    async with pool.acquire() as conn, conn.transaction():
        new_doc = await conn.fetchval(
            "INSERT INTO documents (id, application_id, doc_type, status, stage) VALUES ($1,$2,$3,'uploaded','queued') "
            "ON CONFLICT (application_id, doc_type) DO UPDATE SET status='uploaded', stage='queued', updated_at=now() "
            "RETURNING id",
            uuid.uuid4(),
            f["application_id"],
            body.doc_type,
        )
        await conn.execute("DELETE FROM fields WHERE file_id=$1", f["id"])
        await conn.execute(
            "UPDATE files SET document_id=$2, status='queued', stage='queued', error_message_ru=NULL "
            "WHERE id=$1",
            f["id"],
            new_doc,
        )
        # Старый документ без файлов удаляем, иначе он висел бы пустым в карточке.
        await conn.execute(
            "DELETE FROM documents WHERE id=$1 AND NOT EXISTS (SELECT 1 FROM files WHERE document_id=$1)",
            f["document_id"],
        )
        await conn.execute(
            "INSERT INTO jobs (kind, payload, max_attempts) VALUES ('process_file', $1::jsonb, $2)",
            json.dumps({"file_id": str(f["id"]), "request_id": request.state.request_id}),
            settings.JOB_MAX_ATTEMPTS,
        )
        await audit(
            conn,
            op.name,
            "file.moved",
            f"{op.name} перенёс файл из «{reg.documents[f['old_type']].title}» в «{reg.documents[body.doc_type].title}»",
            application_id=f["application_id"],
            object_id=f["id"],
        )
    return {"ok": True}


class RequestDocs(BaseModel):
    doc_types: list[str] = []
    comment: str = ""


@router.post("/applications/{app_id}/request-documents")
async def request_documents(
    request: Request, app_id: str, body: RequestDocs, op: Operator = Depends(require("requests:send"))
) -> dict[str, Any]:
    pool, reg, settings = request.app.state.pool, request.app.state.registry, request.app.state.settings
    where, params = scoped_where(op, "a", 2)
    a = await pool.fetchrow(f"SELECT * FROM applications a WHERE a.id=$1 AND {where}", _uuid(app_id), *params)
    if not a:
        raise AppError("NOT_FOUND", 404)
    titles = [reg.documents[d].title for d in body.doc_types if d in reg.documents]
    comment = " ".join(body.comment.split())[:1000]
    if not titles and not comment:
        raise AppError("VALIDATION", 422, detail="выберите документы или напишите комментарий")
    text = (
        f"Здравствуйте, {a['full_name']}!\n\nОператор просит дослать документы по заявке {a['public_no']}:\n"
        + "".join(f"• {t}\n" for t in titles)
        + (f"\nКомментарий оператора: {comment}\n" if comment else "")
        + f"\nЗагрузите их по ссылке на заявку из первого письма ({settings.PUBLIC_BASE_URL}/).\n\nКадровое агентство"
    )
    ok = await send_mail(
        pool, settings, a, "request", f"Заявка {a['public_no']}: запрос документов", text, actor=op.name
    )
    return {"ok": ok}


@router.get("/files/{file_id}/preview")
async def preview(request: Request, file_id: str, op: Operator = Depends(require("files:read"))) -> Response:
    """Превью без ПДн: страница уменьшена и размыта целиком. Читаемый оригинал — /pages/1 (с записью в журнал)."""
    pool = request.app.state.pool
    where, params = scoped_where(op, "a", 2)
    row = await pool.fetchrow(
        f"SELECT f.page_key FROM files f JOIN applications a ON a.id=f.application_id "
        f"WHERE f.id=$1 AND {where}",
        _uuid(file_id),
        *params,
    )
    if not row or not row["page_key"]:
        raise AppError("NOT_FOUND", 404)
    data = await request.app.state.storage.get(row["page_key"])
    with Image.open(io.BytesIO(data)) as im:
        small = im.convert("RGB")
    small.thumbnail((600, 600))
    small = small.filter(ImageFilter.GaussianBlur(8))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=70)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/model-status")
async def model_status(request: Request, _: Operator = Depends(require("queue:read"))) -> dict[str, Any]:
    """Состояние модели по факту работы очереди: api не ходит наружу, судим по последним задачам."""
    pool = request.app.state.pool
    r = await pool.fetchrow(
        "SELECT count(*) FILTER (WHERE last_error IN ('UNAVAILABLE','TIMEOUT','RATE_LIMITED') "
        "  AND coalesce(finished_at, run_after, created_at) > now()-interval '10 minutes') AS bad, "
        "max(finished_at) FILTER (WHERE status='done') AS last_ok, "
        "max(coalesce(finished_at, run_after)) FILTER (WHERE last_error IN ('UNAVAILABLE','TIMEOUT','RATE_LIMITED')) AS last_bad "
        "FROM jobs"
    )
    paused = bool(r["bad"]) and (r["last_ok"] is None or (r["last_bad"] and r["last_bad"] > r["last_ok"]))
    return {
        "llm_ok": not paused,
        "ocr_ok": None,
        "paused": paused,
        "detail_ru": "Распознавание приостановлено: модель недоступна. Задачи ждут и будут разобраны автоматически."
        if paused
        else "Модель работает",
    }


# ---------- «Сотрудники»: Keycloak Admin API через сервисный клиент operator-admin ----------

ROLES = ("doc-reader", "doc-editor", "supervisor")


async def _kc(request: Request) -> tuple[httpx.AsyncClient, str]:
    s = request.app.state.settings
    secret = s.KC_ADMIN_CLIENT_SECRET.get_secret_value()
    if not secret:
        raise AppError("FORBIDDEN", 403)
    base = s.OIDC_INTERNAL_BASE.rsplit("/realms/", 1)[0]
    client = httpx.AsyncClient(timeout=15)
    r = await client.post(
        f"{s.OIDC_INTERNAL_BASE}/protocol/openid-connect/token",
        data={"grant_type": "client_credentials", "client_id": "operator-admin", "client_secret": secret},
    )
    if r.status_code != 200:
        await client.aclose()
        raise AppError("INTERNAL", 502)
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return client, f"{base}/admin/realms/hr"


@router.get("/staff")
async def staff_list(request: Request, _: Operator = Depends(require("staff:manage"))) -> dict[str, Any]:
    client, admin = await _kc(request)
    try:
        cid = (await client.get(f"{admin}/clients", params={"clientId": "operator-bff"})).json()[0]["id"]
        users = (
            await client.get(f"{admin}/users", params={"max": 200, "briefRepresentation": "true"})
        ).json()
        out = []
        for u in users:
            if u["username"].startswith("service-account"):
                continue
            roles = [
                r["name"]
                for r in (await client.get(f"{admin}/users/{u['id']}/role-mappings/clients/{cid}")).json()
            ]
            groups = [g["path"] for g in (await client.get(f"{admin}/users/{u['id']}/groups")).json()]
            out.append(
                {
                    "username": u["username"],
                    "name": f"{u.get('firstName', '')} {u.get('lastName', '')}".strip(),
                    "enabled": u.get("enabled", True),
                    "roles": [r for r in roles if r in ROLES],
                    "offices": [g.split("/")[-1] for g in groups if g.startswith("/offices/")],
                }
            )
        return {"items": out}
    finally:
        await client.aclose()


class NewStaff(BaseModel):
    username: str
    first_name: str
    last_name: str
    email: str
    role: str
    office: str = ""
    password: str


@router.post("/staff", status_code=201)
async def staff_create(
    request: Request, body: NewStaff, op: Operator = Depends(require("staff:manage"))
) -> dict[str, Any]:
    reg = request.app.state.registry
    if body.role not in ROLES:
        raise AppError("VALIDATION", 422, detail="роль: doc-reader, doc-editor или supervisor")
    if body.office and body.office not in {o.slug for o in reg.offices}:
        raise AppError("VALIDATION", 422, detail="неизвестный офис")
    if len(body.password) < 12:
        raise AppError("VALIDATION", 422, detail="пароль не короче 12 символов")
    client, admin = await _kc(request)
    try:
        r = await client.post(
            f"{admin}/users",
            json={
                "username": body.username,
                "firstName": body.first_name,
                "lastName": body.last_name,
                "email": body.email,
                "emailVerified": True,
                "enabled": True,
                "credentials": [{"type": "password", "value": body.password, "temporary": True}],
                "groups": [f"/offices/{body.office}"] if body.office else [],
            },
        )
        if r.status_code == 409:
            raise AppError("VALIDATION", 409, detail="такой логин или email уже есть")
        if r.status_code != 201:
            raise AppError("VALIDATION", 422, detail="Keycloak отклонил данные (проверьте email и пароль)")
        uid = r.headers["location"].rsplit("/", 1)[-1]
        cid = (await client.get(f"{admin}/clients", params={"clientId": "operator-bff"})).json()[0]["id"]
        role = (await client.get(f"{admin}/clients/{cid}/roles/{body.role}")).json()
        await client.post(f"{admin}/users/{uid}/role-mappings/clients/{cid}", json=[role])
    finally:
        await client.aclose()
    await audit(
        request.app.state.pool,
        op.name,
        "staff.created",
        f"{op.name} завёл сотрудника {body.username} (роль {body.role}, офис {body.office or '—'})",
    )
    return {"ok": True, "username": body.username}
