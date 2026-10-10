"""Шлюз модели — единственная точка, через которую приложение вызывает Qwen и olmOCR.

Здесь: сборка запроса (промпт из prompts/, схема из реестра), ограничение параллельности,
одиночные повторы (обрезка по длине, ответ не по схеме, неподдержанный response_format)
и строгая проверка ответа. Шлюз никогда не «чинит» значения и не подставляет дефолтов:
ответ либо прошёл схему, либо это ModelError. Остальные повторы делает очередь, чтобы они
были видны в «В работе» и переживали перезапуск воркера.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from PIL import Image

from app.config import Settings
from app.llm.errors import ModelError
from app.llm.prompts import Prompt, load_prompt
from app.llm.providers.olmocr_http import OlmOcrHttpProvider
from app.llm.providers.openai_compat import ChatResponse, OpenAICompatProvider
from app.llm.schema_builder import doc_type_list, extract_schema, fields_block, schema_errors, schema_hash
from app.logging import get_logger
from app.registry.loader import Registry

log = get_logger(__name__)

JsonMode = Literal["json_schema", "json_object", "prompt"]
_FALLBACK: dict[JsonMode, JsonMode | None] = {
    "json_schema": "json_object",
    "json_object": "prompt",
    "prompt": None,
}

# Новый провайдер — новый класс и строка в словаре; выбор — по LLM_PROVIDER / OCR_PROVIDER.
LLM_PROVIDERS: dict[str, type[OpenAICompatProvider]] = {"openai_compat": OpenAICompatProvider}
OCR_PROVIDERS: dict[str, type[OlmOcrHttpProvider]] = {"olmocr_http": OlmOcrHttpProvider}


@dataclass(frozen=True)
class LLMResult:
    data: dict[str, Any]  # распарсенный JSON, прошедший схему
    raw_text: str
    finish_reason: str
    provider: str
    base_url_host: str
    model: str
    prompt_version: str
    json_mode: str
    schema_hash: str
    duration_ms: int
    tokens_in: int | None
    tokens_out: int | None
    inner_retries: int  # сколько одиночных повторов понадобилось внутри шлюза


@dataclass(frozen=True)
class OCRResult:
    markdown: str
    provider: str
    base_url_host: str
    duration_ms: int


@dataclass(frozen=True)
class ModelHealth:
    llm_ok: bool
    llm_model_listed: bool
    ocr_ok: bool | None  # None — OCR выключен настройкой
    detail: str


def fit_png(png: bytes, max_side: int) -> bytes:
    """Уменьшить изображение до max_side по длинной стороне (модели больше не нужно)."""
    with Image.open(io.BytesIO(png)) as src:
        if max(src.size) <= max_side:
            return png
        im = src.convert("RGB")
    im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class Gateway:
    def __init__(
        self, settings: Settings, registry: Registry, client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings
        self.registry = registry
        self._client = client or httpx.AsyncClient()
        self.llm = LLM_PROVIDERS[settings.LLM_PROVIDER](
            settings.LLM_BASE_URL,
            settings.LLM_API_KEY.get_secret_value(),
            settings.LLM_TIMEOUT_S,
            self._client,
        )
        self.ocr_provider: OlmOcrHttpProvider | None = None
        if settings.OCR_PROVIDER != "none":
            self.ocr_provider = OCR_PROVIDERS[settings.OCR_PROVIDER](
                settings.OCR_BASE_URL,
                settings.OCR_API_TOKEN.get_secret_value(),
                settings.OCR_TIMEOUT_S,
                self._client,
            )
        # Семафоры на процесс: не перегружаем общий сервер модели и olmOCR.
        self._llm_sem = asyncio.Semaphore(settings.LLM_MAX_CONCURRENCY)
        self._ocr_sem = asyncio.Semaphore(settings.OCR_MAX_CONCURRENCY)
        # Режим JSON может понизиться один раз за процесс, если сервер не поддержал response_format.
        self.json_mode: JsonMode = settings.LLM_JSON_MODE
        self.llm_host = urlparse(settings.LLM_BASE_URL).hostname or ""
        self.ocr_host = urlparse(settings.OCR_BASE_URL).hostname or ""

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------- проход A: классификация и извлечение ----------

    async def extract(self, image_png: bytes, doc_type: str, text_layer: str | None) -> LLMResult:
        prompt = load_prompt(self.settings.PROMPTS_DIR, "extract", self.settings.PROMPT_EXTRACT_VERSION)
        doc = self.registry.documents[doc_type]
        schema = extract_schema(self.registry, doc_type)
        text_block = ""
        if text_layer:
            text_block = (
                "Ниже текст, извлечённый из PDF-файла этой страницы. Он точнее изображения для цифр и букв; "
                "используй его, но значения бери только те, что относятся к нужным полям.\n<<<\n"
                f"{text_layer[:8000]}\n>>>"
            )
        user_text = prompt.render(
            "user", doc_title=doc.title, fields_block=fields_block(doc), text_layer_block=text_block
        )
        system_text = prompt.render("system", doc_type_list=doc_type_list(self.registry))
        image = fit_png(image_png, self.settings.LLM_IMAGE_MAX_SIDE)
        return await self._structured_call(prompt, system_text, user_text, image, schema, f"{doc_type}_v1")

    # ---------- проход C: слепой арбитр по вырезке ----------

    async def arbitrate(self, crop_png: bytes, field_label: str, field_format: str) -> LLMResult:
        prompt = load_prompt(self.settings.PROMPTS_DIR, "arbiter", self.settings.PROMPT_ARBITER_VERSION)
        if not prompt.schema:
            raise ValueError("в промпте арбитра нет schema во frontmatter")
        user_text = prompt.render("user", field_label=field_label, field_format=field_format)
        image = fit_png(crop_png, 800)
        return await self._structured_call(
            prompt, prompt.render("system"), user_text, image, prompt.schema, "arbiter_v1"
        )

    # ---------- проход B: olmOCR ----------

    @property
    def ocr_enabled(self) -> bool:
        return self.ocr_provider is not None

    async def ocr(self, page_png: bytes) -> OCRResult:
        if self.ocr_provider is None:
            raise ModelError("UNAVAILABLE", "OCR выключен настройкой OCR_PROVIDER=none", retryable=False)
        started = time.monotonic()
        status = "ok"
        try:
            async with self._ocr_sem:
                resp = await self.ocr_provider.ocr(page_png)
        except ModelError as e:
            status = e.code
            raise
        finally:
            log.info(
                "ocr_call",
                provider=self.ocr_provider.name,
                base_url_host=self.ocr_host,
                duration_ms=int((time.monotonic() - started) * 1000),
                status=status,
            )
        return OCRResult(
            resp.markdown, self.ocr_provider.name, self.ocr_host, int((time.monotonic() - started) * 1000)
        )

    async def health(self) -> ModelHealth:
        llm_ok, listed, ocr_ok, detail = False, False, None, ""
        try:
            models = await self.llm.list_models()
            llm_ok, listed = True, self.settings.LLM_MODEL in models
            if not listed:
                detail = f"модель {self.settings.LLM_MODEL} не найдена в /models"
        except (ModelError, httpx.HTTPError) as e:
            detail = f"LLM: {type(e).__name__}"
        if self.ocr_provider is not None:
            try:
                ocr_ok = await self.ocr_provider.health()
            except httpx.HTTPError:
                ocr_ok = False
        return ModelHealth(llm_ok, listed, ocr_ok, detail)

    # ---------- общий структурированный вызов ----------

    async def _structured_call(
        self,
        prompt: Prompt,
        system_text: str,
        user_text: str,
        image_png: bytes,
        schema: dict[str, Any],
        schema_name: str,
    ) -> LLMResult:
        started = time.monotonic()
        max_tokens = int(prompt.model_params.get("max_tokens", 2048))
        image_part = {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + base64.b64encode(image_png).decode()},
        }
        history: list[dict[str, Any]] = []  # неудачный ответ + просьба исправить (один раз)
        retried_truncated = retried_schema = False
        inner_retries = 0

        while True:
            mode = self.json_mode
            body = self._body(
                prompt, system_text, user_text, image_part, schema, schema_name, mode, max_tokens, history
            )
            try:
                resp = await self._chat(body, prompt)
            except ModelError as e:
                fallback = _FALLBACK[mode]
                if e.code == "BAD_RESPONSE" and "response_format" in e.detail and fallback:
                    log.warning("json_mode_fallback", from_mode=mode, to_mode=fallback)
                    self.json_mode = fallback
                    inner_retries += 1
                    continue
                raise

            if resp.finish_reason == "length":
                if retried_truncated:
                    raise ModelError(
                        "TRUNCATED",
                        f"ответ обрезан на {max_tokens} токенах",
                        retryable=True,
                        provider=self.llm.name,
                    )
                retried_truncated, max_tokens, inner_retries = True, max_tokens * 2, inner_retries + 1
                continue
            if resp.finish_reason != "stop":
                raise ModelError(
                    "BAD_RESPONSE",
                    f"finish_reason={resp.finish_reason}",
                    retryable=True,
                    provider=self.llm.name,
                )

            problem, code, data = _parse(resp.text, schema)
            if problem is None and data is not None:
                return LLMResult(
                    data=data,
                    raw_text=resp.text,
                    finish_reason=resp.finish_reason,
                    provider=self.llm.name,
                    base_url_host=self.llm_host,
                    model=self.settings.LLM_MODEL,
                    prompt_version=prompt.version,
                    json_mode=mode,
                    schema_hash=schema_hash(schema),
                    duration_ms=int((time.monotonic() - started) * 1000),
                    tokens_in=resp.tokens_in,
                    tokens_out=resp.tokens_out,
                    inner_retries=inner_retries,
                )
            if retried_schema or "retry_on_schema_error" not in prompt.sections:
                raise ModelError(code, problem or "пустой ответ", retryable=True, provider=self.llm.name)
            retried_schema, inner_retries = True, inner_retries + 1
            history = [
                {"role": "assistant", "content": resp.text[:4000]},
                {"role": "user", "content": prompt.render("retry_on_schema_error", error=problem or "")},
            ]

    def _body(
        self,
        prompt: Prompt,
        system_text: str,
        user_text: str,
        image_part: dict[str, Any],
        schema: dict[str, Any],
        schema_name: str,
        mode: JsonMode,
        max_tokens: int,
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        params = {k: v for k, v in prompt.model_params.items() if k != "max_tokens"}
        if mode != "json_schema":
            # Без structured outputs схема уходит текстом; проверка ответа той же схемой остаётся.
            user_text += "\n\nСхема ответа (JSON Schema):\n" + json.dumps(schema, ensure_ascii=False)
        body: dict[str, Any] = {
            "model": self.settings.LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_text},
                {"role": "user", "content": [image_part, {"type": "text", "text": user_text}]},
                *history,
            ],
            **params,
            "max_tokens": max_tokens,
        }
        # Thinking выключаем всегда: с рассуждением один документ занимает минуты.
        body["chat_template_kwargs"] = {**params.get("chat_template_kwargs", {}), "enable_thinking": False}
        body.setdefault("temperature", 0)
        if mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
            }
        elif mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        return body

    async def _chat(self, body: dict[str, Any], prompt: Prompt) -> ChatResponse:
        started = time.monotonic()
        status, resp = "ok", None
        try:
            async with self._llm_sem:
                resp = await self.llm.chat(body)
            return resp
        except ModelError as e:
            status = e.code
            raise
        finally:
            # Только метаданные вызова: ни изображения, ни текста ответа в логах нет.
            log.info(
                "llm_call",
                provider=self.llm.name,
                base_url_host=self.llm_host,
                model=self.settings.LLM_MODEL,
                prompt_version=f"{prompt.id}.{prompt.version}",
                duration_ms=int((time.monotonic() - started) * 1000),
                tokens_in=resp.tokens_in if resp else None,
                tokens_out=resp.tokens_out if resp else None,
                finish_reason=resp.finish_reason if resp else None,
                status=status,
                think_stripped=resp.think_stripped if resp else None,
            )


def _parse(
    text: str, schema: dict[str, Any]
) -> tuple[str | None, Literal["BAD_RESPONSE", "SCHEMA_INVALID"], dict[str, Any] | None]:
    """(проблема, код ошибки, данные): проблема None — ответ годен."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Без structured outputs модели любят оборачивать JSON в ```json ... ```.
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return f"ответ не является JSON ({e.msg})", "BAD_RESPONSE", None
    if not isinstance(data, dict):
        return "ответ не является JSON-объектом", "BAD_RESPONSE", None
    errors = schema_errors(schema, data)
    if errors:
        return "; ".join(errors[:5]), "SCHEMA_INVALID", None
    return None, "SCHEMA_INVALID", data
