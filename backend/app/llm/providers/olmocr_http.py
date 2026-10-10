"""Провайдер olmOCR: `POST {OCR_BASE_URL}/ocr`, multipart-поле `file`, ответ — markdown.

olmOCR — независимый дословный OCR (проход B). Шлём нормализованную PNG-страницу
(EXIF-поворот и HEIC уже учтены), под нейтральным именем `page.png`: исходное имя файла
соискателя в модели не попадает. Токен `X-API-Token` — только если задан в конфиге.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.llm.errors import ModelError


@dataclass(frozen=True)
class OCRResponse:
    markdown: str


class OlmOcrHttpProvider:
    name = "olmocr_http"

    def __init__(self, base_url: str, token: str, timeout_s: float, client: httpx.AsyncClient) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {"X-API-Token": token} if token else {}
        self._timeout = httpx.Timeout(timeout_s, connect=5.0)
        self._client = client

    async def ocr(self, page_png: bytes) -> OCRResponse:
        try:
            r = await self._client.post(
                f"{self.base_url}/ocr",
                files={"file": ("page.png", page_png, "image/png")},
                headers=self._headers,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as e:
            raise ModelError(
                "TIMEOUT", f"OCR не ответил за {self._timeout.read} с", retryable=True, provider=self.name
            ) from e
        except httpx.TransportError as e:
            raise ModelError(
                "UNAVAILABLE", f"OCR: нет соединения: {type(e).__name__}", retryable=True, provider=self.name
            ) from e

        if r.status_code in (400, 413):
            # 400 — файл непригоден, 413 — слишком большой: повтор не поможет.
            raise ModelError(
                "BAD_RESPONSE", f"OCR отклонил файл ({r.status_code})", retryable=False, provider=self.name
            )
        if r.status_code in (401, 403):
            raise ModelError(
                "UNAVAILABLE",
                "OCR: доступ запрещён, проверьте OCR_API_TOKEN",
                retryable=False,
                provider=self.name,
            )
        if r.status_code == 429 or r.status_code >= 500:
            raise ModelError(
                "UNAVAILABLE", f"OCR недоступен ({r.status_code})", retryable=True, provider=self.name
            )
        if r.status_code >= 400:
            raise ModelError(
                "BAD_RESPONSE",
                f"OCR: неожиданный ответ ({r.status_code})",
                retryable=False,
                provider=self.name,
            )
        return OCRResponse(markdown=r.text)

    async def health(self) -> bool:
        r = await self._client.get(
            f"{self.base_url}/health", headers=self._headers, timeout=httpx.Timeout(10.0)
        )
        return r.status_code == 200
