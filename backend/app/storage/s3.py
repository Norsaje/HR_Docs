"""Файлы в S3 (SeaweedFS): put / get / delete и создание бакета при старте.

boto3 синхронный — вызываем через asyncio.to_thread: просто и объяснимо.
Ключ объекта — UUID файла, имени файла соискателя в ключе нет. Бакет не публичный:
браузер в S3 не ходит, файлы отдаёт только api после проверки сессии.
"""

from __future__ import annotations

import asyncio
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import Settings


class Storage:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.S3_BUCKET
        self._s3: Any = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id=settings.S3_ACCESS_KEY.get_secret_value(),
            aws_secret_access_key=settings.S3_SECRET_KEY.get_secret_value(),
            region_name="us-east-1",
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )

    async def ensure_bucket(self) -> None:
        def _ensure() -> None:
            try:
                self._s3.head_bucket(Bucket=self.bucket)
            except ClientError:
                self._s3.create_bucket(Bucket=self.bucket)

        await asyncio.to_thread(_ensure)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._s3.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            return bytes(self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())

        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._s3.delete_object, Bucket=self.bucket, Key=key)
