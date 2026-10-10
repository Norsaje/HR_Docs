# Переход на собственную локальную модель

> **Статус:** см. раздел «Проверка на A10» внизу — там реальные команды, время и результат.

## Что сейчас

Все обращения к модели идут через `backend/app/llm/gateway.py`. Адрес, модель и ключ задаются в `.env`:

- `LLM_PROVIDER=openai_compat` — провайдер OpenAI-совместимого API (`backend/app/llm/providers/openai_compat.py`);
- `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` — куда и с какой моделью ходит воркер;
- `LLM_JSON_MODE=json_schema` — режим structured outputs (запасные `json_object`, `prompt` включаются автоматически, если сервер не поддержал схему).

По умолчанию модель — Qwen3.8-27B на сервере в локальном контуре (`llm.ilons.ru`). vLLM отдаёт тот же OpenAI-совместимый API, поэтому переход — смена трёх переменных и пересоздание воркера (`make switch-llm`), код не меняется.

## Требования к железу

| Вариант | GPU | Память под веса | Контекст / параллельность |
|---|---|---|---|
| Qwen3.8-27B FP8 (прод) | 1× 80 ГБ (H100/A100) | ~31 ГБ | 32K / десятки запросов |
| Qwen3.8-27B AWQ-INT4 | 1× 24 ГБ (A10, L4, RTX 4090) | ~19 ГБ | 16K / 2–4 запроса |
| **Qwen/Qwen3-VL-4B-Instruct (выбрана для демонстрации)** | 1× 24 ГБ (A10) | 8,9 ГБ BF16 | 16K / 4 запроса |

FP8-сборка 27B в 24 ГБ не помещается. Для демонстрации переключения взята маленькая мультимодальная модель Qwen3-VL-4B-Instruct: открытая (Apache-2.0, без токена HF), поддерживается vLLM с v0.11.0, скачивается за минуты. Кейс разрешает маленькую модель для показа переключения. Образ `vllm/vllm-openai:v0.11.0` — 12,2 ГБ, веса — 8,9 ГБ.

Нужны драйвер NVIDIA, Docker, NVIDIA Container Toolkit. Проверка: `nvidia-smi` и
```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

## Вариант 1. Модель рядом со стендом (одна машина с GPU)

1. В `.env`:
   ```
   LOCAL_LLM_MODEL=Qwen/Qwen3-VL-4B-Instruct
   LOCAL_LLM_SERVED_NAME=local-qwen
   VLLM_API_KEY=            # make secrets сгенерирует
   ```
2. Поднять модель (первый раз скачиваются образ и веса):
   ```bash
   docker compose -f docker-compose.yml -f infra/llm/compose.llm-local.yml --profile llm-local up -d vllm
   docker compose -f docker-compose.yml -f infra/llm/compose.llm-local.yml logs -f vllm   # ждать "Application startup complete"
   ```
3. Переключить воркер и проверить:
   ```bash
   make switch-llm TARGET=local     # LLM_BASE_URL=http://vllm:8000/v1, LLM_MODEL=local-qwen, ключ = VLLM_API_KEY
   make llm-smoke                   # синтетический документ через шлюз из контейнера worker
   ```

## Вариант 2. Модель на отдельном GPU-сервере, стенд на другой машине

На сервере (каталог `~/hr_llm`, свой compose-проект, порт только на `127.0.0.1` сервера):
```bash
mkdir -p ~/hr_llm && cd ~/hr_llm            # сюда кладём infra/llm/compose.llm-local.yml
cat > .env <<EOF                             # chmod 600; ключ тот же, что VLLM_API_KEY в .env стенда
LOCAL_LLM_MODEL=Qwen/Qwen3-VL-4B-Instruct
LOCAL_LLM_SERVED_NAME=local-qwen
VLLM_API_KEY=<ключ>
EOF
docker compose -p hr_llm -f compose.llm-local.yml --profile llm-local up -d
docker compose -p hr_llm -f compose.llm-local.yml logs -f vllm
```
На машине стенда — SSH-туннель и переключение:
```bash
ssh -N -L 8000:127.0.0.1:8000 -i <ключ> <пользователь>@<сервер> &     # модель видна как localhost:8000
# в .env стенда: LOCAL_LLM_URL=http://host.docker.internal:8000/v1   (Docker Desktop)
make switch-llm TARGET=local
make llm-smoke
make switch-llm TARGET=ilons                                          # вернуть сервер в контуре
```
Если стенд и vLLM на одном сервере, но в разных проектах: `docker network connect hr_docs_backend hr_llm-vllm-1` и `LOCAL_LLM_URL=http://hr_llm-vllm-1:8000/v1`.

## Как работает `make switch-llm`

`scripts/switch_llm.py` правит только `.env`:
- `TARGET=local` — при первом переключении сохраняет текущие `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` в `ILONS_LLM_*` (ключ сервера в контуре не теряется), затем ставит `LLM_BASE_URL=${LOCAL_LLM_URL:-http://vllm:8000/v1}`, `LLM_MODEL=${LOCAL_LLM_SERVED_NAME}`, `LLM_API_KEY=${VLLM_API_KEY}`;
- `TARGET=ilons` — восстанавливает значения из `ILONS_LLM_*`;
- печатает, что изменилось (ключи — только «изменён», без значений), и пересоздаёт воркер `docker compose up -d worker`.

В журнале разбора (вкладка «История» карточки) у каждого документа видно, какая модель и какой адрес его разобрали: `local-qwen @ host.docker.internal` или `Qwen/Qwen3.8-27B @ llm.ilons.ru`.

## OCR локально

olmOCR — отдельный сервис (`OCR_BASE_URL`). Свой olmOCR-сервер: vLLM с `allenai/olmOCR-2-7B-1025-FP8` (вторая GPU ≥ 12 ГБ или очередь на одной) и `OCR_BASE_URL=http://olmocr:8080`; либо `OCR_PROVIDER=none` — поля станут жёлтыми вместо зелёных.

## Что меняется при переходе (п. 5.3.9)

| | Сервер в контуре (llm.ilons.ru) | Своя GPU (A10) |
|---|---|---|
| Модель | Qwen3.8-27B | Qwen3-VL-4B-Instruct (демонстрация); в проде — 27B AWQ-INT4 или FP8 на 80 ГБ |
| Скорость, p50/p95 на документ | 13,0 с / 18,0 с (эксперимент 2) | см. «Проверка на A10» |
| Качество | 20/20 ответов по схеме, вид документа верно 20/20 | 4B заметно слабее на мелком тексте — для прода не годится, только для показа переключения |
| Приватность | данные не покидают контур | данные не покидают машину |

## Известные проблемы

- Thinking у Qwen3.8 включён по умолчанию — шлюз передаёт `chat_template_kwargs.enable_thinking=false` в каждом запросе и вырезает `<think>`, если сервер его всё же вернул. Qwen3-VL-Instruct рассуждения не включает.
- Квантованные сборки 27B могут выдавать мусор (`!!!!!`) из-за неверного разбиения слоёв при квантовании — берите другую сборку или меньшую модель.
- `presence_penalty` должен быть 0: штраф за повтор искажает повторяющиеся цифры («000»).

## Проверка на A10

Ожидает запуска на GPU-сервере.
