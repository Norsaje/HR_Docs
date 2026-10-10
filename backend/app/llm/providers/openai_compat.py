"""Провайдер OpenAI-совместимого API: `POST {base_url}/chat/completions` через httpx.

Покрывает Qwen3.8 на llm.ilons.ru, vLLM на нашей A10, llama-server и SGLang — адрес,
модель и ключ приходят из конфига. Здесь только транспорт и перевод HTTP-ошибок
в ModelError; разбор JSON, схема и повторы — в gateway.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.llm.errors import ModelError

_THINK = re.compile(r"<think>.*?</think>\s*", re.S)


@dataclass(frozen=True)
class ChatResponse:
    text: str
    finish_reason: str
    tokens_in: int | None
    tokens_out: int | None
    think_stripped: bool  # сервер проигнорировал enable_thinking=false и вернул <think>


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str, timeout_s: float, client: httpx.AsyncClient) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = httpx.Timeout(timeout_s, connect=5.0)
        self._client = client

    async def chat(self, body: dict[str, Any]) -> ChatResponse:
        try:
            r = await self._client.post(
                f"{self.base_url}/chat/completions", json=body, headers=self._headers, timeout=self._timeout
            )
        except httpx.TimeoutException as e:
            raise ModelError(
                "TIMEOUT", f"нет ответа за {self._timeout.read} с", retryable=True, provider=self.name
            ) from e
        except httpx.TransportError as e:
            raise ModelError(
                "UNAVAILABLE", f"нет соединения: {type(e).__name__}", retryable=True, provider=self.name
            ) from e

        _raise_for_status(r, self.name)
        try:
            payload = r.json()
            choice = payload["choices"][0]
            text = choice["message"].get("content") or ""
            finish_reason = str(choice.get("finish_reason") or "")
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise ModelError(
                "BAD_RESPONSE",
                "ответ сервера не в формате chat/completions",
                retryable=True,
                provider=self.name,
            ) from e

        stripped = _THINK.sub("", text)
        usage = payload.get("usage") or {}
        return ChatResponse(
            text=stripped.strip(),
            finish_reason=finish_reason,
            tokens_in=usage.get("prompt_tokens"),
            tokens_out=usage.get("completion_tokens"),
            think_stripped=stripped != text,
        )

    async def list_models(self) -> list[str]:
        r = await self._client.get(
            f"{self.base_url}/models", headers=self._headers, timeout=httpx.Timeout(10.0)
        )
        _raise_for_status(r, self.name)
        return [str(m["id"]) for m in r.json().get("data", [])]


def _raise_for_status(r: httpx.Response, provider: str) -> None:
    """HTTP-код → ModelError по таблице из docs/spec/09-model-gateway.md."""
    code = r.status_code
    if code < 400:
        return
    if code == 429:
        retry_after = r.headers.get("retry-after")
        raise ModelError(
            "RATE_LIMITED",
            "сервер модели просит подождать (429)",
            retryable=True,
            retry_after_s=float(retry_after) if retry_after and retry_after.isdigit() else None,
            provider=provider,
        )
    if code in (401, 403):
        raise ModelError(
            "UNAVAILABLE",
            f"доступ запрещён ({code}): проверьте LLM_API_KEY",
            retryable=False,
            provider=provider,
        )
    if code in (400, 422):
        # Текст ошибки сервера нужен шлюзу, чтобы понять, что не поддержан response_format.
        raise ModelError("BAD_RESPONSE", f"{code}: {r.text[:300]}", retryable=False, provider=provider)
    if code >= 500:
        raise ModelError(
            "UNAVAILABLE", f"сервер модели недоступен ({code})", retryable=True, provider=provider
        )
    raise ModelError(
        "BAD_RESPONSE", f"неожиданный ответ сервера ({code})", retryable=False, provider=provider
    )
