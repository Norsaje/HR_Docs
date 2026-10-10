"""Воркер очереди `jobs` в PostgreSQL (`python -m app.queue.worker`).

Захват задачи — `FOR UPDATE SKIP LOCKED`: несколько воркеров и несколько задач в одном воркере
не берут одну задачу дважды. Задача арендуется на JOB_LEASE_S и продлевается heartbeat-ом,
пока идёт разбор. Если воркер убит, аренда истекает, и reaper возвращает задачу в очередь —
работа не теряется. Повторяемые ошибки модели → повтор с экспоненциальной задержкой;
попытки кончились или ошибка неповторяемая → файл failed с понятной причиной.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
import uuid
from typing import Any

import asyncpg

from app.audit.log import audit
from app.config import Settings, get_settings
from app.db.core import create_pool
from app.llm.errors import ModelError
from app.llm.gateway import Gateway
from app.logging import get_logger, setup_logging
from app.messages import MODEL_FAILURE_RU
from app.notify.email import notify_problems
from app.pipeline.run import process_file, recompute_application
from app.registry.loader import Registry, get_registry
from app.storage.s3 import Storage

log = get_logger(__name__)

CLAIM_SQL = """
UPDATE jobs SET status='running', attempts=attempts+1, locked_by=$1,
       locked_until=now() + make_interval(secs => $2), started_at=now(), stage='queued'
WHERE id = (SELECT id FROM jobs WHERE status='queued' AND run_after <= now()
            ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING *
"""


class Worker:
    def __init__(self, settings: Settings, registry: Registry, pool: asyncpg.Pool, name: str) -> None:
        self.settings, self.registry, self.pool, self.name = settings, registry, pool, name
        self.storage = Storage(settings)
        self.gw = Gateway(settings, registry)
        self.stopping = asyncio.Event()

    async def run(self) -> None:
        loops = [asyncio.create_task(self.loop(i)) for i in range(self.settings.WORKER_CONCURRENCY)]
        reaper = asyncio.create_task(self.reaper())
        await self.stopping.wait()
        log.info("worker_stopping", worker=self.name)
        await asyncio.gather(*loops, return_exceptions=True)  # даём текущим задачам дойти
        reaper.cancel()
        await self.gw.aclose()

    async def loop(self, slot: int) -> None:
        while not self.stopping.is_set():
            job = await self.pool.fetchrow(CLAIM_SQL, self.name, self.settings.JOB_LEASE_S)
            if job is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.stopping.wait(), timeout=1.0)
                continue
            await self.handle(job)

    async def handle(self, job: asyncpg.Record) -> None:
        payload: dict[str, Any] = json.loads(job["payload"])
        file_id = uuid.UUID(payload["file_id"])
        app_id = await self.pool.fetchval("SELECT application_id FROM files WHERE id=$1", file_id)
        await self.pool.execute("UPDATE files SET status='processing' WHERE id=$1", file_id)
        await self.pool.execute(
            "UPDATE documents SET status='processing', updated_at=now() "
            "WHERE id=(SELECT document_id FROM files WHERE id=$1)",
            file_id,
        )
        heartbeat = asyncio.create_task(self.heartbeat(job["id"]))

        async def set_stage(stage: str) -> None:
            await self.pool.execute("UPDATE jobs SET stage=$2 WHERE id=$1", job["id"], stage)
            await self.pool.execute("UPDATE files SET stage=$2 WHERE id=$1", file_id, stage)
            await self.pool.execute(
                "UPDATE documents SET stage=$2 WHERE id=(SELECT document_id FROM files WHERE id=$1)",
                file_id,
                stage,
            )

        try:
            await process_file(
                self.pool, self.storage, self.gw, self.registry, self.settings, file_id, self.name, set_stage
            )
            await self.pool.execute(
                "UPDATE jobs SET status='done', finished_at=now(), stage='done', locked_by=NULL WHERE id=$1",
                job["id"],
            )
        except ModelError as e:
            await self.fail_or_retry(
                job,
                file_id,
                app_id,
                e.code,
                MODEL_FAILURE_RU.get(e.code, e.detail),
                e.retryable,
                e.retry_after_s,
            )
        except Exception as e:  # noqa: BLE001 — любая другая ошибка тоже не должна терять задачу
            log.exception("job_crashed", job_id=job["id"])
            await self.fail_or_retry(
                job,
                file_id,
                app_id,
                type(e).__name__,
                "Внутренняя ошибка разбора. Мы повторим автоматически.",
                True,
                None,
            )
        finally:
            heartbeat.cancel()
        if app_id:
            try:  # письмо «не хватает / не прочиталось» — после разбора, не чаще раза в 10 минут
                await notify_problems(self.pool, self.settings, self.registry, app_id)
            except Exception:  # noqa: BLE001 — сбой почты не должен ронять воркер
                log.exception("notify_failed")

    async def fail_or_retry(
        self,
        job: asyncpg.Record,
        file_id: uuid.UUID,
        app_id: Any,
        code: str,
        text: str,
        retryable: bool,
        retry_after: float | None,
    ) -> None:
        attempt = job["attempts"]
        if retryable and attempt < job["max_attempts"]:
            delay = retry_after or min(30 * 2 ** (attempt - 1), 300)
            async with self.pool.acquire() as conn, conn.transaction():
                await conn.execute(
                    "UPDATE jobs SET status='queued', run_after=now()+make_interval(secs=>$2), "
                    "last_error=$3, locked_by=NULL WHERE id=$1",
                    job["id"],
                    float(delay),
                    code,
                )
                await conn.execute(
                    "UPDATE files SET status='queued', error_message_ru=$2 WHERE id=$1", file_id, text
                )
                await audit(
                    conn,
                    self.name,
                    "parse.retry",
                    f"{text.split('.')[0]} (попытка {attempt}) — повтор через {int(delay)} с",
                    application_id=app_id,
                    object_id=file_id,
                    details={"code": code},
                )
            return
        final = text.replace("Мы повторим автоматически.", "Оператор может запустить разбор повторно.")
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE jobs SET status='failed', finished_at=now(), last_error=$2, locked_by=NULL "
                "WHERE id=$1",
                job["id"],
                code,
            )
            await conn.execute(
                "UPDATE files SET status='failed', stage='done', error_message_ru=$2 WHERE id=$1",
                file_id,
                final,
            )
            await conn.execute(
                "UPDATE documents SET status='failed', stage='done', message_ru=$2, updated_at=now() "
                "WHERE id=(SELECT document_id FROM files WHERE id=$1) AND status <> 'ready'",
                file_id,
                final,
            )
            await audit(
                conn,
                self.name,
                "parse.failed",
                f"Разбор не удался после {attempt} попыток: {final}",
                application_id=app_id,
                object_id=file_id,
                details={"code": code},
            )
            if app_id:
                await recompute_application(conn, app_id)

    async def heartbeat(self, job_id: int) -> None:
        while True:
            await asyncio.sleep(self.settings.JOB_HEARTBEAT_S)
            await self.pool.execute(
                "UPDATE jobs SET locked_until=now()+make_interval(secs=>$2) WHERE id=$1",
                job_id,
                self.settings.JOB_LEASE_S,
            )

    async def reaper(self) -> None:
        """Возвращает в очередь задачи с истёкшей арендой (воркер убит или завис)."""
        while True:
            rows = await self.pool.fetch(
                "UPDATE jobs SET status='queued', locked_by=NULL, run_after=now() "
                "WHERE status='running' AND locked_until < now() RETURNING id, payload, locked_by"
            )
            for r in rows:
                fid = json.loads(r["payload"]).get("file_id")
                app_id = await self.pool.fetchval("SELECT application_id FROM files WHERE id=$1::uuid", fid)
                await self.pool.execute("UPDATE files SET status='queued' WHERE id=$1::uuid", fid)
                await audit(
                    self.pool,
                    "система",
                    "job.reaped",
                    f"Задача {r['id']} возвращена в очередь: воркер перестал отвечать",
                    application_id=app_id,
                    object_id=fid,
                )
            await asyncio.sleep(self.settings.REAPER_INTERVAL_S)


async def main() -> None:
    setup_logging()
    settings = get_settings()
    pool = await create_pool(settings)
    name = os.environ.get("WORKER_NAME") or f"worker-{socket.gethostname()[:8]}"
    worker = Worker(settings, get_registry(), pool, name)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, worker.stopping.set)  # мягкая остановка: дорабатываем текущие задачи
        except NotImplementedError:
            pass
    log.info("worker_started", worker=name, concurrency=settings.WORKER_CONCURRENCY)
    await worker.run()
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
