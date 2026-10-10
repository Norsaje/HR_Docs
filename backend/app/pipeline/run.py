"""Разбор одного файла (задача process_file в воркере): preprocess → extract → verify.

Поля читает модель (один вызов Qwen через app/llm по JSON-схеме из реестра); детерминированный
код нормализует их, проверяет правилами реестра и красит:
  зелёный — прочитано и подтверждено «твёрдой» проверкой (контрольная сумма, текстовый слой PDF);
  жёлтый — прочитано, правила пройдены, но подтвердить нечем;
  красный — не прочитано обязательное поле или нарушено правило; серый — необязательного поля нет.
Пайплайн получает байты и UUID файла; исходное имя файла сюда не передаётся.
Ошибки модели не превращаются в данные: ModelError уходит в очередь (повтор или failed).
"""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg
import pypdfium2 as pdfium
from PIL import Image, ImageOps, ImageStat

from app.audit.log import audit
from app.config import Settings
from app.llm.gateway import Gateway
from app.llm.schema_builder import Extraction
from app.registry.loader import Registry
from app.registry.rules import evaluate, normalize
from app.storage.s3 import Storage

COLOR_RANK = {"green": 3, "yellow": 2, "grey": 1, "red": 0}
_HARD_CHECKS = {"checksum_inn12", "checksum_snils"}


def preprocess(data: bytes, mime: str, max_side: int) -> tuple[bytes, str | None]:
    """Страница → PNG (EXIF-поворот, уменьшение до max_side) и текстовый слой PDF, если он есть."""
    text: str | None = None
    if mime == "application/pdf":
        pdf = pdfium.PdfDocument(data)
        page = pdf[0]
        layer = page.get_textpage().get_text_range()
        if sum(ch.isalnum() for ch in layer) >= 30:  # «цифровой» PDF: текст точнее картинки
            text = layer
        im = page.render(scale=2000 / max(page.get_size())).to_pil().convert("RGB")
    else:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue(), text


def too_dark(png: bytes, threshold: float) -> bool:
    with Image.open(io.BytesIO(png)) as im:
        return float(ImageStat.Stat(im.convert("L")).mean[0]) < threshold


async def process_file(
    pool: asyncpg.Pool,
    storage: Storage,
    gw: Gateway,
    registry: Registry,
    settings: Settings,
    file_id: uuid.UUID,
    worker: str,
    set_stage: Callable[[str], Awaitable[None]],
) -> None:
    f = await pool.fetchrow(
        "SELECT f.*, d.doc_type, d.application_id AS app_id FROM files f JOIN documents d ON d.id=f.document_id "
        "WHERE f.id=$1",
        file_id,
    )
    if f is None:
        return
    doc_type: str = f["doc_type"]
    doc = registry.documents[doc_type]

    await set_stage("preprocess")
    data = await storage.get(f["s3_key"])
    png, text_layer = preprocess(data, f["mime"], settings.NORMALIZE_MAX_SIDE)
    page_key = f"derived/{file_id}/page-1.png"
    await storage.put(page_key, png, "image/png")
    await pool.execute("UPDATE files SET page_key=$2 WHERE id=$1", file_id, page_key)
    if not text_layer and too_dark(png, 35):
        await finish_unreadable(
            pool, f, "Фото слишком тёмное. Сфотографируйте документ при хорошем освещении."
        )
        return

    await set_stage("extract")
    await audit(
        pool,
        worker,
        "parse.started",
        f"Воркер {worker} начал разбор файла документа «{doc.title}» ({gw.settings.LLM_MODEL} @ {gw.llm_host})",
        application_id=f["app_id"],
        object_id=file_id,
    )
    result = await gw.extract(png, doc_type, text_layer)
    ext = Extraction.model_validate(result.data)
    await pool.execute(
        "INSERT INTO extraction_runs (file_id, provider, base_url_host, model, prompt_version, schema_hash, "
        "duration_ms, tokens_in, tokens_out, doc_type_detected) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
        file_id,
        result.provider,
        result.base_url_host,
        result.model,
        result.prompt_version,
        result.schema_hash,
        result.duration_ms,
        result.tokens_in,
        result.tokens_out,
        ext.doc_type,
    )

    await set_stage("verify")
    if not ext.is_document:
        await finish_unreadable(pool, f, "На фото не найден документ. Сфотографируйте документ целиком.")
        return

    values = {
        k: normalize(doc.fields[k], v.raw if v.status == "ok" else None)
        for k, v in ext.fields.items()
        if k in doc.fields
    }
    broken = {
        r.field: msg
        for r, msg in evaluate(doc, values, registry, settings.EXPIRY_WARNING_DAYS)
        if r.severity == "error"
    }
    passed_hard = {
        r.field
        for r in doc.rules
        if r.check in _HARD_CHECKS and r.field not in broken and values.get(r.field)
    }
    async with pool.acquire() as conn, conn.transaction():
        for key, fv in ext.fields.items():
            if key not in doc.fields:
                continue
            value = values.get(key)
            signals: list[str] = []
            if fv.status != "ok":
                color = "red" if key in doc.min_fields or fv.status == "unreadable" else "grey"
                signals.append(fv.status)
            elif key in broken:
                color, signals = "red", ["rule_fail"]
            elif key in passed_hard:
                color, signals = "green", ["checksum_ok"]
            elif text_layer and value and value.replace("-", "") in "".join(text_layer.split()).upper():
                color, signals = "green", ["text_layer_match"]
            else:
                color, signals = "yellow", ["single_source"]
            if fv.kind != "printed" and color == "green":
                color = "yellow"
            # Слияние файлов документа: лучший цвет побеждает, правка оператора неприкосновенна.
            await conn.execute(
                """INSERT INTO fields (id, document_id, file_id, key, value, raw, status, kind, bbox, confidence, signals)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11::jsonb)
                   ON CONFLICT (document_id, key) DO UPDATE SET file_id=EXCLUDED.file_id, value=EXCLUDED.value,
                     raw=EXCLUDED.raw, status=EXCLUDED.status, kind=EXCLUDED.kind, bbox=EXCLUDED.bbox,
                     confidence=EXCLUDED.confidence, signals=EXCLUDED.signals
                   WHERE fields.source <> 'operator' AND (fields.file_id = EXCLUDED.file_id OR
                     CASE fields.confidence WHEN 'green' THEN 3 WHEN 'yellow' THEN 2 WHEN 'grey' THEN 1 ELSE 0 END
                     <= CASE EXCLUDED.confidence WHEN 'green' THEN 3 WHEN 'yellow' THEN 2 WHEN 'grey' THEN 1 ELSE 0 END)""",
                uuid.uuid4(),
                f["document_id"],
                file_id,
                key,
                value,
                fv.raw,
                fv.status,
                fv.kind,
                json.dumps(fv.bbox),
                color,
                json.dumps(signals),
            )
        mismatch = None
        if ext.doc_type != doc_type:
            other = (
                registry.documents[ext.doc_type].title
                if ext.doc_type in registry.documents
                else "другой документ"
            )
            mismatch = f"В слот «{doc.title}» загружен другой документ (похоже на «{other}»)"
        await conn.execute(
            "UPDATE files SET status='done', stage='done', error_message_ru=NULL WHERE id=$1", file_id
        )
        await recompute_document(conn, registry, settings, f["document_id"], extra_issue=mismatch)
        summary = await conn.fetchrow(
            "SELECT count(*) AS n, count(*) FILTER (WHERE confidence='green') AS g, "
            "count(*) FILTER (WHERE confidence='yellow') AS y, count(*) FILTER (WHERE confidence='red') AS r "
            "FROM fields WHERE document_id=$1",
            f["document_id"],
        )
        await audit(
            conn,
            worker,
            "parse.finished",
            f"Разбор завершён за {result.duration_ms / 1000:.1f} с: {summary['n']} полей, {summary['g']} зелёных, "
            f"{summary['y']} жёлтых, {summary['r']} красных ({result.model} @ {result.base_url_host})",
            application_id=f["app_id"],
            object_id=file_id,
            details={"duration_ms": result.duration_ms, "model": result.model, "provider": result.provider},
        )


async def finish_unreadable(pool: asyncpg.Pool, f: asyncpg.Record, reason: str) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE files SET status='unreadable', stage='done', error_message_ru=$2 WHERE id=$1",
            f["id"],
            reason,
        )
        await conn.execute(
            "UPDATE documents SET status='unreadable', stage='done', message_ru=$2, updated_at=now() "
            "WHERE id=$1 AND NOT EXISTS (SELECT 1 FROM fields WHERE document_id=$1)",
            f["document_id"],
            reason,
        )
        await audit(
            conn,
            "система",
            "parse.unreadable",
            f"Файл не прочитан: {reason}",
            application_id=f["app_id"],
            object_id=f["id"],
        )
        await recompute_application(conn, f["app_id"])


async def recompute_document(
    conn: asyncpg.Connection,
    registry: Registry,
    settings: Settings,
    document_id: uuid.UUID,
    extra_issue: str | None = None,
) -> None:
    """Правила по текущим (слитым, с правками оператора) значениям → замечания, цвета, статус документа."""
    d = await conn.fetchrow("SELECT * FROM documents WHERE id=$1", document_id)
    doc = registry.documents[d["doc_type"]]
    rows = await conn.fetch("SELECT key, value, status, source FROM fields WHERE document_id=$1", document_id)
    values: dict[str, Any] = {r["key"]: r["value"] for r in rows}
    issues = evaluate(doc, values, registry, settings.EXPIRY_WARNING_DAYS)
    await conn.execute("DELETE FROM issues WHERE document_id=$1", document_id)
    for rule, msg in issues:
        app_msg = rule.applicant_message or (
            "Документ требует проверки оператором." if rule.severity == "error" else None
        )
        await conn.execute(
            "INSERT INTO issues (document_id, field_key, rule_id, severity, message, applicant_message) "
            "VALUES ($1,$2,$3,$4,$5,$6)",
            document_id,
            rule.field,
            rule.id,
            rule.severity,
            msg,
            app_msg,
        )
        if rule.severity == "error":
            await conn.execute(
                "UPDATE fields SET confidence='red', signals=signals || '[\"rule_fail\"]'::jsonb "
                "WHERE document_id=$1 AND key=$2 AND source='model'",
                document_id,
                rule.field,
            )
    missing = [doc.fields[k].label for k in doc.min_fields if not values.get(k)]
    if missing:
        await conn.execute(
            "INSERT INTO issues (document_id, severity, message, applicant_message) VALUES ($1,'error',$2,$3)",
            document_id,
            "Не удалось прочитать: " + ", ".join(missing),
            "Часть данных не прочиталась. Загрузите более чёткое фото.",
        )
    if extra_issue:
        await conn.execute(
            "INSERT INTO issues (document_id, severity, message) VALUES ($1,'warning',$2)",
            document_id,
            extra_issue,
        )
    has_error = any(r.severity == "error" for r, _ in issues) or bool(missing)
    expires = values.get(doc.expiry_field) if doc.expiry_field else None
    await conn.execute(
        "UPDATE documents SET status=$2, stage='done', message_ru=NULL, expires_on=$3::text::date, updated_at=now() WHERE id=$1",
        document_id,
        "needs_review" if has_error or extra_issue else "ready",
        expires if expires and len(expires) == 10 and expires[4] == "-" else None,
    )
    await recompute_application(conn, d["application_id"])


async def recompute_application(conn: asyncpg.Connection, app_id: uuid.UUID) -> None:
    statuses = [
        r["status"] for r in await conn.fetch("SELECT status FROM documents WHERE application_id=$1", app_id)
    ]
    app_status = await conn.fetchval("SELECT status FROM applications WHERE id=$1", app_id)
    if app_status == "draft":
        return
    if any(s in ("uploaded", "processing") for s in statuses):
        new = "processing"
    elif any(s in ("needs_review", "unreadable", "failed") for s in statuses):
        new = "needs_review"
    else:
        new = "ready"
    await conn.execute("UPDATE applications SET status=$2, updated_at=now() WHERE id=$1", app_id, new)
