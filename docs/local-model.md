# Переход на собственную локальную модель

> **Статус:** профиль `llm-local` описан и подготовлен (`infra/llm/compose.llm-local.yml`), но **на A10 не запускался**: сдаваемый стенд — машина разработчика без GPU. Команды ниже — по спецификации vLLM; замеров скорости и качества на своей GPU пока нет.

## Что сейчас

Все обращения к модели идут через `backend/app/llm/gateway.py`. Адрес, модель и ключ задаются в `.env`:

- `LLM_PROVIDER=openai_compat` — провайдер OpenAI-совместимого API (`backend/app/llm/providers/openai_compat.py`);
- `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` — куда и с какой моделью ходит воркер;
- `LLM_JSON_MODE=json_schema` — режим structured outputs (запасные `json_object`, `prompt` включаются автоматически, если сервер не поддержал схему).

По умолчанию модель — Qwen3.8-27B на сервере в локальном контуре (`llm.ilons.ru`). vLLM отдаёт тот же OpenAI-совместимый API, поэтому переход — смена трёх переменных и пересоздание воркера, код не меняется.

## Требования к железу

| Вариант | GPU | Память под веса | Контекст / параллельность | Скорость |
|---|---|---|---|---|
| Qwen3.8-27B FP8 (прод) | 1× 80 ГБ (H100/A100) | ~31 ГБ | 32K / десятки запросов | не замеряли |
| Qwen3.8-27B AWQ-INT4 (стенд) | 1× 24 ГБ (A10, L4, RTX 4090) | ~19 ГБ | 16K / 2–4 запроса | не замеряли (A10 не проверена) |
| Меньшая модель для демонстрации переключения, например `Qwen/Qwen3-VL-8B-Instruct` | 16–24 ГБ | ~17 ГБ BF16 | 16K | не замеряли |
| Для сравнения: Qwen3.8-27B на llm.ilons.ru | сервер в контуре | — | — | p50 13,0 с, p95 18,0 с на документ (эксперимент 2) |

FP8-сборка 27B (~31 ГБ) в 24 ГБ не помещается, поэтому для A10 — только 4 бита.

Нужны драйвер NVIDIA, Docker, NVIDIA Container Toolkit. Проверка:
```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

## Шаги

1. **Выбрать веса** и вписать в `.env`:
   ```
   LOCAL_LLM_MODEL=<репозиторий весов на Hugging Face, например AWQ-INT4 сборка Qwen3.8-27B>
   LOCAL_LLM_SERVED_NAME=local-qwen
   VLLM_API_KEY=            # make secrets сгенерирует
   HF_TOKEN=                # только если веса закрытые
   ```
   Перед выбором проверьте карточку модели: размер, поддержку vision в vLLM, лицензию. Кандидаты для 24 ГБ — сообщественные AWQ-INT4 сборки Qwen3.8-27B (визуальный энкодер в BF16). Если 27B не стартует или выдаёт мусор (`!!!!!`), берите меньшую мультимодальную модель Qwen.
2. **Поднять модель** (первый раз скачиваются веса, ~20 ГБ; vLLM слушает только во внутренней сети `backend`):
   ```bash
   docker compose -f docker-compose.yml -f infra/llm/compose.llm-local.yml --profile llm-local up -d vllm
   docker compose -f docker-compose.yml -f infra/llm/compose.llm-local.yml logs -f vllm   # ждать "Application startup complete"
   ```
   Тег образа `vllm/vllm-openai` в `compose.llm-local.yml` закреплён, но `docker pull` на стенде не проверялся — при необходимости поставьте свежий стабильный тег с поддержкой Qwen3.8.
3. **Проверить, что модель отвечает** (из контейнера api — он в той же сети); после шага 4 то же самое через шлюз приложения — `make llm-smoke`:
   ```bash
   docker compose exec api python -c "import urllib.request,os; r=urllib.request.Request('http://vllm:8000/v1/models', headers={'Authorization':'Bearer '+os.environ.get('VLLM_API_KEY','')}); print(urllib.request.urlopen(r).read()[:300])"
   ```
   В ответе должен быть `id` = `LOCAL_LLM_SERVED_NAME`.
4. **Переключить приложение** — в `.env`:
   ```
   LLM_BASE_URL=http://vllm:8000/v1
   LLM_MODEL=local-qwen          # = LOCAL_LLM_SERVED_NAME
   LLM_API_KEY=<значение VLLM_API_KEY>
   ```
   и пересоздать воркер с новым окружением:
   ```bash
   docker compose up -d worker
   ```
   Цель `make switch-llm` пока не реализована — переключение делается правкой `.env`.
5. **Проверить в интерфейсе:** загрузить документ; в журнале разбора (вкладка «История» карточки) видно `vllm` и `local-qwen`.
6. **Вернуться** на сервер в контуре: вернуть `LLM_BASE_URL=https://llm.ilons.ru/v1`, `LLM_MODEL=Qwen/Qwen3.8-27B`, ключ — и `docker compose up -d worker`.

## OCR локально

В сдаваемой версии olmOCR в пайплайне разбора не используется (только эксперимент 1). Если подключать: собственный olmOCR-сервер (vLLM с `allenai/olmOCR-2-7B-1025-FP8`, нужна вторая GPU ≥ 12 ГБ или очередь на одной) и `OCR_BASE_URL=http://olmocr:8080`; либо `OCR_PROVIDER=none`.

## Что меняется при переходе (п. 5.3.9)

| | Сервер в контуре (llm.ilons.ru) | Своя GPU (A10, 4 бита) |
|---|---|---|
| Железо | сервер сотрудника, Qwen3.8-27B | 1× A10 24 ГБ, AWQ-INT4 |
| Скорость, p50/p95 на документ | 13,0 с / 18,0 с (эксперимент 2, 4 параллельно) | не замеряли |
| Качество | 20/20 ответов по схеме, вид документа верно 20/20 | не замеряли; 4 бита, ожидаемо хуже на мелком тексте |
| Стоимость | инфраструктура заказчика | фиксированная стоимость GPU |
| Приватность | данные не покидают контур | данные не покидают машину |

## Известные проблемы

- Thinking у Qwen включён по умолчанию — в профиле стоит `--default-chat-template-kwargs '{"enable_thinking": false}'`, и шлюз дополнительно передаёт `chat_template_kwargs.enable_thinking=false` в каждом запросе; если сервер всё же вернул `<think>`, шлюз вырезает блок.
- Квантованные сборки 27B могут выдавать мусор (`!!!!!`) из-за неверного разбиения слоёв при квантовании — берите другую сборку или меньшую модель.
- `presence_penalty` должен быть 0: штраф за повтор искажает повторяющиеся цифры («000»).
