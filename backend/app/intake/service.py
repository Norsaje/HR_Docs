"""Приём файла соискателя (api, синхронно): все проверки на сервере, затем одна транзакция.

Порядок: лимиты → размер → тип ПО СИГНАТУРЕ (расширение и Content-Type игнорируем) →
файл открывается → SHA-256 и дубликат в заявке → объект в S3 → в ОДНОЙ транзакции строка
files, статус документа, задача process_file и запись журнала. Если транзакция упала,
объект в S3 удаляется. Каждый отказ — русский текст из каталога и запись file.rejected.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from dataclasses import dataclass

import asyncpg
import pypdfium2 as pdfium
from PIL import Image

from app.audit.log import audit
from app.config import Settings
from app.messages import AppError
from app.registry.loader import Registry
from app.storage.s3 import Storage

_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"}


def sniff(data: bytes) -> str | None:
    """MIME по сигнатуре файла; None — тип не разрешён (SVG, GIF, TIFF, exe и всё прочее)."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b"%PDF-" in data[:1024]:
        return "application/pdf"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS:
        return "image/heic"
    return None


def check_opens(data: bytes, mime: str, settings: Settings) -> None:
    """Файл реально открывается; для изображения — не меньше MIN_IMAGE_SIDE, для PDF — не больше MAX_PDF_PAGES."""
    if mime == "application/pdf":
        try:
            pdf = pdfium.PdfDocument(data)
            pages = len(pdf)
        except Exception as e:  # pypdfium2 бросает свой PdfiumError на битых/зашифрованных PDF
            raise AppError("FILE_CORRUPTED", 422) from e
        if pages > settings.MAX_PDF_PAGES:
            raise AppError("PDF_TOO_MANY_PAGES", 422, pages=settings.MAX_PDF_PAGES)
        return
    if mime == "image/heic":
        return  # pillow-heif не ставим ради скорости; HEIC принимаем, разбор конвертирует при наличии
    Image.MAX_IMAGE_PIXELS = settings.MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            size = im.size
    except Exception as e:  # Pillow бросает разные исключения на битых файлах
        raise AppError("FILE_CORRUPTED", 422) from e
    if min(size) < settings.MIN_IMAGE_SIDE:
        raise AppError("IMAGE_TOO_SMALL", 422, px=settings.MIN_IMAGE_SIDE)


@dataclass
class Accepted:
    file_id: uuid.UUID
    document_id: uuid.UUID


async def accept_file(
    pool: asyncpg.Pool,
    storage: Storage,
    settings: Settings,
    registry: Registry,
    app_row: asyncpg.Record,
    doc_type: str,
    name: str,
    data: bytes,
    request_id: str,
) -> Accepted:
    app_id = app_row["id"]
    doc_title = registry.documents[doc_type].title if doc_type in registry.documents else doc_type
    try:
        group = registry.group_for_country(app_row["country_code"])
        if not group or doc_type not in group.documents:
            raise AppError("DOC_TYPE_NOT_ALLOWED", 422)
        doc_def = registry.documents[doc_type]
        if not data:
            raise AppError("FILE_EMPTY", 422)
        if len(data) > settings.MAX_FILE_MB * 1024 * 1024:
            raise AppError("FILE_TOO_LARGE", 413, mb=settings.MAX_FILE_MB)
        stats = await pool.fetchrow(
            "SELECT count(*) AS n, coalesce(sum(size_bytes),0) AS total, "
            "count(*) FILTER (WHERE d.doc_type = $2) AS n_doc "
            "FROM files f JOIN documents d ON d.id = f.document_id WHERE f.application_id = $1",
            app_id,
            doc_type,
        )
        if stats["n"] >= settings.MAX_FILES_PER_APPLICATION:
            raise AppError("TOO_MANY_FILES", 422, n=settings.MAX_FILES_PER_APPLICATION)
        if stats["n_doc"] >= doc_def.max_files:
            raise AppError("TOO_MANY_FILES", 422, n=doc_def.max_files)
        if stats["total"] + len(data) > settings.MAX_APPLICATION_MB * 1024 * 1024:
            raise AppError("APPLICATION_TOO_LARGE", 413, mb=settings.MAX_APPLICATION_MB)
        mime = sniff(data)
        if mime is None:
            raise AppError("FILE_TYPE_NOT_ALLOWED", 415)
        check_opens(data, mime, settings)
        sha = hashlib.sha256(data).hexdigest()
        if await pool.fetchval("SELECT 1 FROM files WHERE application_id = $1 AND sha256 = $2", app_id, sha):
            raise AppError("DUPLICATE_FILE", 409)
    except AppError as e:
        await audit(
            pool,
            "соискатель",
            "file.rejected",
            f"Соискатель загрузил файл в документ «{doc_title}» — отклонён: {e.message}",
            application_id=app_id,
            details={"code": e.code, "request_id": request_id},
        )
        raise

    file_id = uuid.uuid4()
    key = f"orig/{app_id}/{file_id}"
    await storage.put(key, data, mime)
    try:
        async with pool.acquire() as conn, conn.transaction():
            doc_id = await conn.fetchval(
                "INSERT INTO documents (id, application_id, doc_type, status, stage) VALUES ($1,$2,$3,'uploaded','queued') "
                "ON CONFLICT (application_id, doc_type) DO UPDATE SET status='uploaded', stage='queued', "
                "message_ru=NULL, updated_at=now() RETURNING id",
                uuid.uuid4(),
                app_id,
                doc_type,
            )
            await conn.execute(
                "INSERT INTO files (id, application_id, document_id, original_name, mime, size_bytes, sha256, s3_key) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                file_id,
                app_id,
                doc_id,
                name[:200],
                mime,
                len(data),
                sha,
                key,
            )
            # Задача ставится в той же транзакции: файл без задачи или задача без файла невозможны.
            await conn.execute(
                "INSERT INTO jobs (kind, payload, max_attempts) VALUES ('process_file', $1::jsonb, $2)",
                json.dumps({"file_id": str(file_id), "request_id": request_id}),
                settings.JOB_MAX_ATTEMPTS,
            )
            await conn.execute("NOTIFY jobs_new")
            await audit(
                conn,
                "соискатель",
                "file.accepted",
                f"Соискатель загрузил файл в документ «{doc_title}» — принят",
                application_id=app_id,
                object_id=file_id,
                details={"request_id": request_id},
            )
    except Exception:
        await storage.delete(key)
        raise
    return Accepted(file_id, doc_id)
