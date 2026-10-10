"""API рабочего места оператора: заявки, карточка, страницы файлов, правка, повторный разбор,
«В работе», история, выгрузки, истекающие документы.

Весь роутер закрыт зависимостью get_operator: без валидной сессии — 401 на любом маршруте.
Каждый запрос к заявке строится через scoped_where (фильтр по офису): объект чужого офиса — 404.
Изменяющие запросы требуют X-Requested-With (CSRF) и права роли (403 + запись в журнал).
"""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.audit.log import audit
from app.auth.deps import Operator, csrf_guard, get_operator, require, scoped_where
from app.messages import AppError
from app.pipeline.run import recompute_document
from app.registry.rules import normalize

router = APIRouter(prefix="/api/operator", dependencies=[Depends(get_operator), Depends(csrf_guard)])


def mask_email(email: str) -> str:
    user, _, domain = email.partition("@")
    return (user[:1] + "***@" + domain) if domain else "***"


def mask_number(v: str | None) -> str | None:
    """Номер документа в журнале и списках: видны только края («45** ***456»)."""
    if not v or len(v) < 6:
        return v
    return v[:2] + re.sub(r"[0-9A-Za-zА-Яа-я]", "*", v[2:-3]) + v[-3:]


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise AppError("NOT_FOUND", 404) from None


async def _app_or_404(request: Request, op: Operator, app_id: uuid.UUID) -> Any:
    where, params = scoped_where(op, "a", 2)
    row = await request.app.state.pool.fetchrow(
        f"SELECT * FROM applications a WHERE a.id=$1 AND {where}", app_id, *params
    )
    if not row:
        raise AppError("NOT_FOUND", 404)
    return row


@router.get("/applications")
async def list_applications(
    request: Request,
    status: str = "",
    country: str = "",
    q: str = "",
    op: Operator = Depends(require("applications:read")),
) -> dict[str, Any]:
    where, params = scoped_where(op, "a", 1)
    cond = [where, "a.status <> 'draft'"]
    args: list[Any] = list(params)
    if status:
        args.append(status)
        cond.append(f"a.status = ${len(args)}")
    if country:
        args.append(country.upper())
        cond.append(f"a.country_code = ${len(args)}")
    if q:
        args.append(f"%{q}%")
        cond.append(f"(a.full_name ILIKE ${len(args)} OR a.public_no ILIKE ${len(args)})")
    rows = await request.app.state.pool.fetch(
        f"""SELECT a.*, (SELECT count(*) FROM documents d WHERE d.application_id=a.id) AS documents_total,
              (SELECT count(*) FROM documents d WHERE d.application_id=a.id AND d.status='ready') AS documents_ready,
              EXISTS (SELECT 1 FROM issues i JOIN documents d ON d.id=i.document_id
                      WHERE d.application_id=a.id AND i.severity='error') AS has_errors
            FROM applications a WHERE {" AND ".join(cond)} ORDER BY a.created_at DESC LIMIT 200""",
        *args,
    )
    return {
        "items": [
            {
                "id": str(r["id"]),
                "public_no": r["public_no"],
                "full_name": r["full_name"],
                "email_masked": mask_email(r["email"]),
                "country_code": r["country_code"],
                "status": r["status"],
                "created_at": r["created_at"].isoformat(),
                "documents_total": r["documents_total"],
                "documents_ready": r["documents_ready"],
                "has_errors": r["has_errors"],
            }
            for r in rows
        ]
    }


def _norm_name(*parts: str | None) -> str:
    return " ".join(p for p in parts if p).upper().replace("Ё", "Е")


@router.get("/applications/{app_id}")
async def card(
    request: Request, app_id: str, op: Operator = Depends(require("applications:read"))
) -> dict[str, Any]:
    pool, reg = request.app.state.pool, request.app.state.registry
    a = await _app_or_404(request, op, _uuid(app_id))
    docs = await pool.fetch("SELECT * FROM documents WHERE application_id=$1 ORDER BY doc_type", a["id"])
    out = []
    by_doc_values: dict[str, dict[str, str | None]] = {}
    for d in docs:
        ddef = reg.documents.get(d["doc_type"])
        files = await pool.fetch(
            "SELECT id, status, error_message_ru FROM files WHERE document_id=$1 ORDER BY created_at", d["id"]
        )
        fields = await pool.fetch("SELECT * FROM fields WHERE document_id=$1", d["id"])
        issues = await pool.fetch(
            "SELECT severity, message FROM issues WHERE document_id=$1 ORDER BY severity", d["id"]
        )
        order = list(ddef.fields) if ddef else []
        by_doc_values[d["doc_type"]] = {f["key"]: f["value"] for f in fields}
        out.append(
            {
                "id": str(d["id"]),
                "doc_type": d["doc_type"],
                "title": ddef.title if ddef else d["doc_type"],
                "status": d["status"],
                "stage": d["stage"],
                "message": d["message_ru"],
                "expires_on": d["expires_on"].isoformat() if d["expires_on"] else None,
                "issues": [dict(i) for i in issues],
                "files": [
                    {"file_id": str(f["id"]), "status": f["status"], "error_message": f["error_message_ru"]}
                    for f in files
                ],
                "fields": sorted(
                    [
                        {
                            "id": str(f["id"]),
                            "key": f["key"],
                            "label": ddef.fields[f["key"]].label
                            if ddef and f["key"] in ddef.fields
                            else f["key"],
                            "value": f["value"],
                            "raw": f["raw"],
                            "status": f["status"],
                            "confidence": f["confidence"],
                            "signals": json.loads(f["signals"]),
                            "source": f["source"],
                            "edited_by": f["edited_by"],
                            "bbox": json.loads(f["bbox"]) if f["bbox"] else None,
                            "file_id": str(f["file_id"]) if f["file_id"] else None,
                        }
                        for f in fields
                    ],
                    key=lambda x: order.index(x["key"]) if x["key"] in order else 99,
                ),
            }
        )
    # Сверка между документами заявки: ФИО и дата рождения (замечания уровня warning/error).
    crosscheck = []
    names = {
        dt: _norm_name(v.get("surname"), v.get("given_name") or v.get("given_names"))
        for dt, v in by_doc_values.items()
        if v.get("surname")
    }
    births = {dt: v.get("birth_date") for dt, v in by_doc_values.items() if v.get("birth_date")}
    title = lambda dt: reg.documents[dt].title if dt in reg.documents else dt  # noqa: E731
    items = list(names.items())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (da, na), (db, nb) = items[i], items[j]
            if na and nb and na != nb and not (set(na.split()) & set(nb.split())):
                crosscheck.append(
                    {
                        "severity": "warning",
                        "message": f"ФИО различаются: «{na}» ({title(da)}) и «{nb}» ({title(db)})",
                    }
                )
    bitems = list(births.items())
    for i in range(len(bitems)):
        for j in range(i + 1, len(bitems)):
            if bitems[i][1] != bitems[j][1]:
                crosscheck.append(
                    {
                        "severity": "error",
                        "message": f"Дата рождения различается: {bitems[i][1]} "
                        f"({title(bitems[i][0])}) и {bitems[j][1]} ({title(bitems[j][0])})",
                    }
                )
    entered = _norm_name(a["full_name"])
    for dt, n in names.items():
        if n and not set(n.split()) <= set(entered.split()):
            crosscheck.append(
                {
                    "severity": "info",
                    "message": f"ФИО в анкете («{entered}») отличается от ФИО в документе "
                    f"«{title(dt)}» («{n}»)",
                }
            )
    return {
        "application": {
            "id": str(a["id"]),
            "public_no": a["public_no"],
            "full_name": a["full_name"],
            "email_masked": mask_email(a["email"]),
            "country_code": a["country_code"],
            "status": a["status"],
            "created_at": a["created_at"].isoformat(),
        },
        "documents": out,
        "crosscheck": crosscheck,
    }


@router.get("/files/{file_id}/pages/1")
async def page(request: Request, file_id: str, op: Operator = Depends(require("files:read"))) -> Response:
    pool = request.app.state.pool
    where, params = scoped_where(op, "a", 2)
    row = await pool.fetchrow(
        f"SELECT f.page_key, f.application_id FROM files f JOIN applications a ON a.id=f.application_id "
        f"WHERE f.id=$1 AND {where}",
        _uuid(file_id),
        *params,
    )
    if not row or not row["page_key"]:
        raise AppError("NOT_FOUND", 404)
    data = await request.app.state.storage.get(row["page_key"])
    await audit(
        pool,
        op.name,
        "file.viewed",
        f"{op.name} открыл изображение файла",
        application_id=row["application_id"],
        object_id=file_id,
    )
    return Response(
        data,
        media_type="image/png",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


class FieldPatch(BaseModel):
    value: str | None


@router.patch("/fields/{field_id}")
async def edit_field(
    request: Request, field_id: str, body: FieldPatch, op: Operator = Depends(require("fields:edit"))
) -> dict[str, Any]:
    pool, reg, settings = request.app.state.pool, request.app.state.registry, request.app.state.settings
    where, params = scoped_where(op, "a", 2)
    f = await pool.fetchrow(
        f"SELECT fl.*, d.doc_type, d.application_id FROM fields fl JOIN documents d ON d.id=fl.document_id "
        f"JOIN applications a ON a.id=d.application_id WHERE fl.id=$1 AND {where}",
        _uuid(field_id),
        *params,
    )
    if not f:
        raise AppError("NOT_FOUND", 404)
    fdef = reg.documents[f["doc_type"]].fields[f["key"]]
    new = normalize(fdef, body.value.strip()) if body.value and body.value.strip() else None
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE fields SET value=$2, status=CASE WHEN $2::text IS NULL THEN 'absent' ELSE 'ok' END, "
            "source='operator', edited_by=$3, edited_at=now(), confidence='green', "
            "signals='[\"operator_edit\"]'::jsonb WHERE id=$1",
            f["id"],
            new,
            op.name,
        )
        await recompute_document(conn, reg, settings, f["document_id"])
        await audit(
            conn,
            op.name,
            "field.edited",
            f"Оператор {op.name} исправил поле «{fdef.label}»: {f['value'] or '—'} → {new or '—'}",
            application_id=f["application_id"],
            object_id=f["id"],
            details={"field": f["key"], "old": f["value"], "new": new},
        )
    return {"ok": True, "value": new}


@router.post("/documents/{doc_id}/reprocess")
async def reprocess(
    request: Request, doc_id: str, op: Operator = Depends(require("documents:reprocess"))
) -> dict[str, Any]:
    pool = request.app.state.pool
    where, params = scoped_where(op, "a", 2)
    d = await pool.fetchrow(
        f"SELECT d.* FROM documents d JOIN applications a ON a.id=d.application_id WHERE d.id=$1 AND {where}",
        _uuid(doc_id),
        *params,
    )
    if not d:
        raise AppError("NOT_FOUND", 404)
    files = await pool.fetch("SELECT id FROM files WHERE document_id=$1", d["id"])
    async with pool.acquire() as conn, conn.transaction():
        for f in files:
            await conn.execute(
                "INSERT INTO jobs (kind, payload, max_attempts) VALUES ('process_file', $1::jsonb, $2)",
                json.dumps({"file_id": str(f["id"]), "request_id": request.state.request_id}),
                request.app.state.settings.JOB_MAX_ATTEMPTS,
            )
            await conn.execute(
                "UPDATE files SET status='queued', stage='queued', error_message_ru=NULL WHERE id=$1", f["id"]
            )
        await conn.execute(
            "UPDATE documents SET status='uploaded', stage='queued', message_ru=NULL WHERE id=$1", d["id"]
        )
        await audit(
            conn,
            op.name,
            "document.reprocess",
            f"{op.name} отправил документ на повторный разбор ({len(files)} файлов)",
            application_id=d["application_id"],
            object_id=d["id"],
        )
    return {"queued": len(files)}


@router.get("/queue")
async def queue(request: Request, _: Operator = Depends(require("queue:read"))) -> dict[str, Any]:
    pool = request.app.state.pool
    s = await pool.fetchrow("""SELECT
        count(*) FILTER (WHERE status='queued') AS waiting,
        count(*) FILTER (WHERE status='running') AS processing,
        count(*) FILTER (WHERE status='done' AND finished_at > now()-interval '1 hour') AS done_last_hour,
        count(*) FILTER (WHERE status='failed' AND finished_at > now()-interval '1 hour') AS failed_last_hour,
        avg(extract(epoch FROM finished_at-started_at)) FILTER (WHERE status='done' AND finished_at > now()-interval '1 hour') AS avg_s,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at-started_at))
            FILTER (WHERE status='done' AND finished_at > now()-interval '1 hour') AS p95_s,
        extract(epoch FROM now()-min(created_at) FILTER (WHERE status='queued')) AS oldest_s
        FROM jobs""")
    running = await pool.fetch(
        "SELECT id, payload, locked_by, stage, attempts, started_at FROM jobs WHERE status='running' ORDER BY id"
    )
    failed = await pool.fetch(
        "SELECT id, payload, attempts, last_error, finished_at FROM jobs WHERE status='failed' "
        "ORDER BY finished_at DESC LIMIT 20"
    )
    retrying = await pool.fetch(
        "SELECT id, payload, attempts, last_error, run_after FROM jobs WHERE status='queued' "
        "AND last_error IS NOT NULL ORDER BY id LIMIT 20"
    )
    return {
        "waiting": s["waiting"],
        "processing": s["processing"],
        "done_last_hour": s["done_last_hour"],
        "failed_last_hour": s["failed_last_hour"],
        "avg_duration_s": round(s["avg_s"] or 0, 1),
        "p95_duration_s": round(s["p95_s"] or 0, 1),
        "oldest_waiting_s": round(s["oldest_s"] or 0),
        "in_progress": [
            {
                "job_id": r["id"],
                "file_id": json.loads(r["payload"])["file_id"],
                "worker": r["locked_by"],
                "stage": r["stage"],
                "attempts": r["attempts"],
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
            }
            for r in running
        ],
        "failed": [
            {
                "job_id": r["id"],
                "file_id": json.loads(r["payload"])["file_id"],
                "attempts": r["attempts"],
                "error": r["last_error"],
                "at": r["finished_at"].isoformat() if r["finished_at"] else None,
            }
            for r in failed
        ],
        "retrying": [
            {
                "job_id": r["id"],
                "attempts": r["attempts"],
                "error": r["last_error"],
                "run_after": r["run_after"].isoformat(),
            }
            for r in retrying
        ],
    }


@router.get("/applications/{app_id}/history")
async def history(
    request: Request, app_id: str, op: Operator = Depends(require("history:read"))
) -> dict[str, Any]:
    a = await _app_or_404(request, op, _uuid(app_id))
    rows = await request.app.state.pool.fetch(
        "SELECT at, actor, action, message_ru FROM audit_events WHERE application_id=$1 ORDER BY at, id",
        a["id"],
    )
    return {
        "items": [
            {
                "at": r["at"].isoformat(),
                "actor": r["actor"],
                "action": r["action"],
                "message": r["message_ru"],
            }
            for r in rows
        ]
    }


@router.get("/applications/{app_id}/export")
async def export(
    request: Request, app_id: str, format: str = "json", op: Operator = Depends(require("exports"))
) -> Response:
    data = await card(request, app_id, op)
    await audit(
        request.app.state.pool,
        op.name,
        "export.card",
        f"{op.name} выгрузил карточку {data['application']['public_no']} ({format})",
        application_id=uuid.UUID(data["application"]["id"]),
    )
    no = data["application"]["public_no"]
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Заявка", "ФИО (анкета)", "Документ", "Поле", "Значение", "Уверенность", "Источник"])
        for d in data["documents"]:
            for f in d["fields"]:
                w.writerow(
                    [
                        no,
                        data["application"]["full_name"],
                        d["title"],
                        f["label"],
                        f["value"] or "",
                        f["confidence"],
                        "оператор" if f["source"] == "operator" else "модель",
                    ]
                )
        return Response(
            "﻿" + buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{no}.csv"'},
        )
    payload = {
        "application": data["application"],
        "documents": [
            {
                "doc_type": d["doc_type"],
                "title": d["title"],
                "status": d["status"],
                "issues": d["issues"],
                "fields": {
                    f["key"]: {"value": f["value"], "confidence": f["confidence"], "source": f["source"]}
                    for f in d["fields"]
                },
            }
            for d in data["documents"]
        ],
        "crosscheck": data["crosscheck"],
    }
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{no}.json"'},
    )


@router.get("/expiring")
async def expiring(
    request: Request,
    days: int = 30,
    format: str = "json",
    op: Operator = Depends(require("applications:read")),
) -> Any:
    reg = request.app.state.registry
    where, params = scoped_where(op, "a", 2)
    rows = await request.app.state.pool.fetch(
        f"SELECT a.id, a.public_no, a.full_name, d.doc_type, d.expires_on, (d.expires_on - current_date) AS days_left "
        f"FROM documents d JOIN applications a ON a.id=d.application_id "
        f"WHERE d.expires_on IS NOT NULL AND d.expires_on <= current_date + $1::int AND {where} ORDER BY d.expires_on",
        days,
        *params,
    )
    items = [
        {
            "application_id": str(r["id"]),
            "public_no": r["public_no"],
            "full_name": r["full_name"],
            "doc_type": r["doc_type"],
            "title": reg.documents[r["doc_type"]].title if r["doc_type"] in reg.documents else r["doc_type"],
            "expires_on": r["expires_on"].isoformat(),
            "days_left": r["days_left"],
        }
        for r in rows
    ]
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Заявка", "ФИО", "Документ", "Действует до", "Осталось дней (минус — истёк)"])
        for i in items:
            w.writerow([i["public_no"], i["full_name"], i["title"], i["expires_on"], i["days_left"]])
        return Response(
            "﻿" + buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="expiring.csv"'},
        )
    return {"items": items}
