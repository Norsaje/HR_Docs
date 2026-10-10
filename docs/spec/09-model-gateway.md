# 09. Шлюз модели, Qwen3.8, olmOCR, локальная инфраструктура

## Ресурсы

| Что | Адрес (из `.env`) | Роль |
|---|---|---|
| Qwen/Qwen3.8-27B — мультимодальная, контекст 262K | `LLM_BASE_URL=https://llm.ilons.ru/v1` (проверить, `/v1` или корень) | Основная модель: классификация, извлечение (проход A), арбитр (проход C). Развёрнута сотрудником в локальном контуре — это наша «локальная модель» |
| vLLM на нашей A10 (24 ГБ) | `LLM_BASE_URL=http://vllm:8000/v1` (профиль `llm-local`) | Запасной адрес и доказательство п. 5.3.5: «поднимается по инструкции» |
| olmOCR 2 (Qwen2.5-VL-7B, FP8) | `OCR_BASE_URL=https://ocr.ilons.ru` | Независимый дословный OCR (проход B) |

**Внешних облачных провайдеров нет** — это решение команды (ПДн не уходят за контур). Ответ судье по п. 5.3.4: слой модели принимает любой OpenAI-совместимый адрес. Переключение демонстрируется между двумя развёртываниями локальной модели, без правки кода.

## Интерфейс (`app/llm/gateway.py`) — единственная точка вызова моделей

```python
class LLMResult(BaseModel):
    data: dict | None            # распарсенный и провалидированный JSON
    raw_text: str
    finish_reason: str
    provider: str; base_url_host: str; model: str; prompt_version: str
    duration_ms: int; tokens_in: int | None; tokens_out: int | None

class ModelError(Exception):     # .code: TIMEOUT | UNAVAILABLE | RATE_LIMITED | BAD_RESPONSE | SCHEMA_INVALID | TRUNCATED
    retryable: bool

class Gateway:
    async def extract(self, image_png: bytes, doc_type: str, text_layer: str | None) -> LLMResult
    async def arbitrate(self, crop_png: bytes, field_label: str, field_format: str) -> LLMResult
    async def ocr(self, page_png: bytes) -> OCRResult      # markdown, duration, provider
    async def health(self) -> ModelHealth                  # GET /models у LLM и GET /health у OCR
```

Внутри — два провайдера в `app/llm/providers/`:

- **`openai_compat.py`** (`LLM_PROVIDER=openai_compat`) — `POST {LLM_BASE_URL}/chat/completions` через httpx. Покрывает llm.ilons.ru, vLLM на A10, llama-server и SGLang.
- **`olmocr_http.py`** (`OCR_PROVIDER=olmocr_http`) — `POST {OCR_BASE_URL}/ocr`, multipart поле `file`, заголовок `X-API-Token: {OCR_API_TOKEN}`, если задан. Ответ — markdown (`text/markdown`).
- `OCR_PROVIDER=none` — отключить проход B (поля станут жёлтыми вместо зелёных; это честно).

Выбор провайдера — словарь `PROVIDERS = {"openai_compat": OpenAICompatProvider, ...}` по значению из конфига. Новый провайдер — новый класс и строка в словаре.

## Запрос к Qwen3.8 (проход A)

```json
{
  "model": "${LLM_MODEL}",
  "messages": [
    {"role": "system", "content": "<system из prompts/extract.v1.md>"},
    {"role": "user", "content": [
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
      {"type": "text", "text": "<user из prompts/extract.v1.md с подстановками>"}
    ]}
  ],
  "temperature": 0, "top_p": 1, "presence_penalty": 0, "max_tokens": 2048,
  "response_format": {"type": "json_schema", "json_schema": {"name": "ru_passport_v1", "schema": {...}, "strict": true}},
  "chat_template_kwargs": {"enable_thinking": false}
}
```

- **Thinking выключен обязательно.** По умолчанию Qwen3.8 рассуждает на максимальном уровне, и один документ занимает минуты. Если сервер проигнорировал `chat_template_kwargs`, проверь, что `content` не содержит `<think>`; если содержит — вырежи блок и запиши предупреждение в лог. Рассуждение в `reasoning_content` игнорируем.
- **`temperature 0`, `presence_penalty 0`.** Рекомендованный Qwen для режима без рассуждений `presence_penalty 1.5` для извлечения вреден: штраф за повтор токенов искажает «000» и повторяющиеся цифры.
- **Изображение:** PNG, длинная сторона до `LLM_IMAGE_MAX_SIDE=1600` для страницы; вырезка арбитра — до 800.
- **Рамки** — относительные 0–1000 (так их выдают Qwen3-VL и Qwen3.8). Если эксперимент 4 покажет абсолютные пиксели, переведи их в 0–1000 в `schema_builder`/`reconcile` и запиши в decisions.
- **Если сервер не поддерживает `response_format` json_schema** (400 с упоминанием `response_format`):
  1. fallback на `{"type": "json_object"}`;
  2. затем на обычный текст с описанием схемы в промпте.

  Валидация Pydantic остаётся. Какой режим сработал, пишется в лог при старте (`gateway.health`) и в decisions.

## JSON-схема из реестра (`app/llm/schema_builder.py`)

```python
def field_schema(f: FieldDef) -> dict:
    raw = {"type": ["string", "null"], "maxLength": f.max_len or 64}
    if f.type == "enum":
        raw = {"type": ["string", "null"], "enum": [*f.values.keys(), "other", None]}
    return {"type": "object",
            "properties": {
                "raw": raw,
                "status": {"type": "string", "enum": ["ok", "unreadable", "absent"]},
                "kind": {"type": "string", "enum": ["printed", "handwritten", "stamp"]},
                "bbox": {"type": ["array", "null"], "items": {"type": "integer", "minimum": 0, "maximum": 1000},
                         "minItems": 4, "maxItems": 4}},
            "required": ["raw", "status", "kind", "bbox"], "additionalProperties": False}
```

Правила схемы:

- Верхний уровень: `doc_type` (enum восьми видов + `other`), `page_side`, `orientation` (enum 0, 90, 180, 270), `is_document` (bool), `mrz_lines` (array of string, maxItems 3, maxLength 50), `fields` (object со всеми полями вида, все required).
- **Порядок свойств:** `doc_type`, `page_side`, `orientation`, `is_document`, `mrz_lines`, `fields` — «сначала прочитать, потом разложить».
- **Никаких `pattern` и `format` в схеме декодера.** На смазанном фото они заставляют модель дорисовать цифры. Шаблоны проверяет код после генерации.
- **Схема плоская**, без `$ref` и `oneOf`. `schema_hash` (sha256 канонического JSON) пишется в `extraction_runs`.
- **Эксперимент 3** (фаза 0): прогнать схемы всех восьми видов через llm.ilons.ru. Если `["string","null"]` или `minItems` не поддержаны, заменить на `anyOf` или убрать `minItems`, а длину массива проверять в Pydantic.

## Ответ и проверки

1. HTTP-ошибки:

   | Ошибка | Код | retryable |
   |---|---|---|
   | `httpx.TimeoutException` | `TIMEOUT` | да |
   | `ConnectError`, 502, 503, 504 | `UNAVAILABLE` | да |
   | 429 | `RATE_LIMITED` | да, с `Retry-After` |
   | 400, 422 | `BAD_RESPONSE` | нет, но один повтор с упрощённой схемой, если это `response_format` |
   | 401, 403 | `UNAVAILABLE` | нет; в лог «проверьте LLM_API_KEY» |

2. `finish_reason == "length"` → `TRUNCATED` (один повтор с `max_tokens*2` на уровне шлюза, затем ошибка).
3. `json.loads`. Ошибка → `BAD_RESPONSE`.
4. Pydantic-модель, построенная из той же схемы. Ошибка → `SCHEMA_INVALID`: один немедленный повтор с секцией `retry_on_schema_error`, затем ошибка.
5. Шлюз **не** «чинит» значения и не подставляет дефолтов.

## Параллельность, таймауты, повторы

- `asyncio.Semaphore(LLM_MAX_CONCURRENCY=6)` на процесс воркера для Qwen и `OCR_MAX_CONCURRENCY=4` для olmOCR. Сервер olmOCR общий: не перегружать.
- Таймауты httpx: `connect 5 с`, `read LLM_TIMEOUT_S=90`, `OCR_TIMEOUT_S=150` (у olmOCR дедлайн 240 с на запрос).
- Повторы внутри шлюза — только описанные выше одиночные. Остальное повторяет очередь (backoff в `jobs`), чтобы повтор был виден в «В работе» и переживал перезапуск.
- Логи на каждый вызов: `provider`, `base_url_host`, `model`, `prompt_version`, `duration_ms`, `tokens`, `finish_reason`, `status`. Без изображений и без текста ответа.

## Конфигурация (`.env`)

```
LLM_PROVIDER=openai_compat
LLM_BASE_URL=https://llm.ilons.ru/v1        # запасной: http://vllm:8000/v1 (docker compose --profile llm-local)
LLM_MODEL=Qwen/Qwen3.8-27B                  # должен точно совпадать с GET {LLM_BASE_URL}/models
LLM_API_KEY=                                # значение ILONS_API_KEY; для vLLM — VLLM_API_KEY
LLM_TIMEOUT_S=90
LLM_MAX_CONCURRENCY=6
LLM_IMAGE_MAX_SIDE=1600
LLM_JSON_MODE=json_schema                   # json_schema | json_object | prompt — как поддерживает сервер
OCR_PROVIDER=olmocr_http                    # olmocr_http | none
OCR_BASE_URL=https://ocr.ilons.ru
OCR_API_TOKEN=                              # X-API-Token, если на сервисе включён OCR_CLIENT_TOKEN
OCR_TIMEOUT_S=150
OCR_MAX_CONCURRENCY=4
OCR_VOTES_CYRILLIC=true                     # false, если эксперимент 1 покажет плохую кириллицу
ARBITER_ENABLED=true
ARBITER_MAX_FIELDS=6
```

## Локальная модель на A10: профиль `llm-local` (`infra/llm/`)

Железо стенда: NVIDIA A10 24 ГБ, 16 CPU, 64 ГБ RAM, 160 ГБ SSD, Ubuntu с драйверами CUDA и NVIDIA Container Toolkit.

FP8-сборка 27B (~31 ГБ) в карту не помещается, поэтому только 4 бита (AWQ ≈ 19 ГБ).

```yaml
# infra/llm/compose.llm-local.yml (подключается: docker compose -f docker-compose.yml -f infra/llm/compose.llm-local.yml --profile llm-local up -d)
services:
  vllm:
    profiles: ["llm-local"]
    image: vllm/vllm-openai:<закреплённый тег с поддержкой Qwen3.8>
    command: >
      --model ${LOCAL_LLM_MODEL}
      --served-model-name ${LOCAL_LLM_SERVED_NAME}
      --max-model-len 16384 --max-num-seqs 4
      --gpu-memory-utilization 0.92
      --limit-mm-per-prompt.image 2
      --enable-prefix-caching
      --default-chat-template-kwargs '{"enable_thinking": false}'
      --api-key ${VLLM_API_KEY}
    environment: [HF_TOKEN=${HF_TOKEN:-}]
    volumes: [hf_cache:/root/.cache/huggingface]
    deploy: {resources: {reservations: {devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]}}}
    networks: [backend, egress]
    healthcheck: {test: ["CMD", "curl", "-fsS", "http://localhost:8000/health"], interval: 30s, start_period: 600s}
```

- **`LOCAL_LLM_MODEL`:**
  - кандидаты для 24 ГБ — сообщественные AWQ-INT4 сборки Qwen3.8-27B, например `cyankiwi/Qwen3.8-27B-AWQ-INT4` или `mattbucci/Qwen3.8-27B-AWQ` (визуальный энкодер в BF16);
  - перед использованием проверь карточку на Hugging Face: размер, поддержка vision в vLLM, лицензия.
  - Если 27B не стартует или выдаёт мусор (`!!!!!` — известная проблема неверного разбиения слоёв DeltaNet при квантовании), возьми меньшую мультимодальную модель Qwen (например, `Qwen/Qwen3-VL-8B-Instruct`). Кейс разрешает маленькую модель для демонстрации переключения.
  - Выбор и замер — в `docs/local-model.md` и `docs/decisions.md`.
- **Первый тест после старта:** `make llm-smoke` — один синтетический документ через шлюз с `LLM_BASE_URL=http://vllm:8000/v1`.
- vLLM слушает только внутри сети `backend`. Наружу порт не публикуется: к модели ходит только воркер.

### Переключение на защите (п. 5.3.6)

```bash
# было: LLM_BASE_URL=https://llm.ilons.ru/v1
docker compose -f docker-compose.yml -f infra/llm/compose.llm-local.yml --profile llm-local up -d vllm   # заранее, веса уже скачаны
sed -i 's#^LLM_BASE_URL=.*#LLM_BASE_URL=http://vllm:8000/v1#; s#^LLM_MODEL=.*#LLM_MODEL=local-qwen#; s#^LLM_API_KEY=.*#LLM_API_KEY='"$VLLM_API_KEY"'#' .env
docker compose up -d worker        # пересоздать воркер с новым env
# загрузить документ → в «Технические детали» и в журнале видно base_url_host=vllm, model=local-qwen
```

Для удобства — `make switch-llm TARGET=local|ilons`: скрипт делает то же самое и печатает, что изменилось.

## olmOCR: что важно знать

- Принимает PDF, PNG, JPEG, HEIC. Возвращает markdown (front matter отрезан).
- Рендер 1288 px по длинной стороне, страницы ретраит сам, температура 0.
- Нет координат и уверенности. Обучен на англоязычных документах, кириллица не заявлена — проверяется экспериментом 1.
- Шлём нормализованную PNG-страницу (не оригинал), чтобы EXIF-поворот и HEIC уже были учтены.
- 400 — непригодный файл (на нашей стороне такого быть не должно), 413 — слишком большой, 502/504 — недоступен или таймаут (`retryable`).
- Кэш по SHA-256 PNG страницы.
- P2: прямой вызов vLLM olmOCR (`olmocr_vllm`) даёт logprobs и возможность слать вырезки. Нужен ключ от организаторов; без него не делаем.

## Эксперименты фазы 0 (скрипты в `eval/experiments/`, отчёты в `eval/reports/`)

| № | Что | Решение по результату |
|---|---|---|
| 1 | olmOCR на 10 файлах РФ: доля верных цифр и ФИО | Если ФИО на кириллице часто неверны, `OCR_VOTES_CYRILLIC=false` |
| 2 | Qwen3.8 на llm.ilons.ru, 20 документов: p50/p95 секунд, доля валидных ответов | p95 > 60 с → уменьшить `LLM_IMAGE_MAX_SIDE` до 1280, проверить, что thinking реально выключен |
| 3 | Схемы всех восьми видов через structured outputs | Неподдержанное упростить; выбрать `LLM_JSON_MODE` |
| 4 | Рамки на 20 документах: попадают ли в поле | Если промахи частые, арбитр берёт расширенную область (отступ 40%) |

Если у тебя нет доступа к файлам открытого набора, сгенерируй синтетические документы: бланк, нарисованный Pillow, с вымышленными данными. Запиши в отчёт, что это синтетика.
