# 02. Архитектура, периметр, стек, структура репозитория

## Схема

```
 Телефон соискателя            ПК оператора
        │ HTTPS                     │ HTTPS
        ▼                           ▼
 ┌──────────────────── Caddy :443 (единственный открытый порт) ────────────────────┐
 │  /            → статика applicant        /operator/* → статика operator          │
 │  /api/*       → api:8000 (SSE без буферизации)                                    │
 │  /kc/*        → keycloak:8080 (админка и realm master снаружи → 404)              │
 └──────────────────────────────────────────────────────────────────────────────────┘
        │ сеть backend                        │
        ▼                                     ▼
 ┌──────────────┐    сеть backend (internal: true, без выхода в интернет)   ┌──────────────┐
 │ api (FastAPI)│──────────────┬──────────────┬──────────────┬────────────│ keycloak 26  │
 │  BFF + REST  │              │              │              │            └──────────────┘
 └──────────────┘        ┌───────────┐  ┌───────────┐  ┌───────────┐
                         │ postgres  │  │ seaweedfs │  │  mailpit  │
                         │ данные,   │  │ S3, файлы │  │ SMTP 1025 │
                         │ очередь,  │  │ (шифр.)   │  │ UI только │
                         │ журнал    │  └───────────┘  │ 127.0.0.1 │
                         └───────────┘                 └───────────┘
 ┌──────────────┐         ▲ та же сеть backend
 │ worker       │─────────┘
 │ пайплайн,    │── сеть egress ──► https://llm.ilons.ru  (Qwen3.8, локальная модель в контуре)
 │ шлюз модели  │                └► https://ocr.ilons.ru  (olmOCR)
 └──────────────┘                └► http://vllm:8000/v1   (профиль llm-local, запасной, на A10)
```

## Принципы периметра (прод)

- **Единственная точка входа — Caddy.** Наружу открыты только его 443 и 80 (редирект и ACME). Отладочные входы тоже идут через Caddy и слушают только `127.0.0.1` хоста, доступ к ним — через SSH-туннель:
  - `127.0.0.1:8081` — админка Keycloak;
  - `127.0.0.1:8025` — Mailpit UI.
- **Сети:**
  - `backend` с `internal: true`: api, worker, postgres, seaweedfs, keycloak, mailpit. Выхода в интернет из неё нет. Порты контейнеров этой сети Docker наружу не публикует — и это нам и нужно.
  - `edge` (обычный bridge): только caddy, для публикации портов и ACME. Caddy подключён к обеим сетям.
  - `egress` (обычный bridge): только worker. Ему нужен выход к `llm.ilons.ru` и `ocr.ilons.ru`.
- У api, Keycloak, PostgreSQL и S3 нет физического выхода в интернет.
- Браузер не ходит ни в S3, ни к модели. Файлы отдаёт только api после проверки сессии и группы.
- В проде worker выходит наружу через egress-прокси с allowlist из двух хостов (или модели живут в той же внутренней сети). На стенде достаточно сетевой изоляции.
- Все образы — с закреплёнными тегами (не `latest`) и `security_opt: [no-new-privileges:true]`. Приложение работает от непривилегированного пользователя.

## Сервисы docker compose

| Сервис | Образ | Сети | Порты на хосте | Volume | Примечание |
|---|---|---|---|---|---|
| caddy | сборка `infra/caddy/Dockerfile`: node собирает оба фронтенда, затем `caddy:2.x` | edge, backend | 80, 443; 127.0.0.1:8081, 127.0.0.1:8025 | caddy_data, caddy_config | Caddyfile в `infra/caddy/` |
| api | сборка `backend/Dockerfile` | backend | — | — | `uvicorn app.main:app --proxy-headers`, перед стартом `alembic upgrade head` (под `hr_owner`) |
| worker | тот же образ | backend, egress | — | — | `python -m app.queue.worker` |
| postgres | `postgres:17-alpine` | backend | — | pg_data | init-скрипт создаёт БД и роли (см. ниже) |
| seaweedfs | `chrislusf/seaweedfs:<тег>` | backend | — | s3_data | `weed server -dir=/data -s3 -s3.config=/etc/seaweedfs/s3.json`; s3.json генерирует `infra/seaweedfs/entrypoint.sh` из env |
| keycloak | `quay.io/keycloak/keycloak:26.<x>` | backend | — | — (БД в postgres) | `start --import-realm`, настройки ниже |
| mailpit | `axllent/mailpit:<тег>` | backend | — | — | SMTP 1025, UI 8025 через Caddy на 127.0.0.1 |
| vllm (профиль `llm-local`) | `vllm/vllm-openai:<тег>` | backend, egress (скачать веса) | — | hf_cache | GPU; worker ходит на `http://vllm:8000/v1`, см. `09-model-gateway.md` |
| postgres-test (профиль `test`) | `postgres:17-alpine` | отдельная | 127.0.0.1:55432 | tmpfs | для интеграционных тестов |

Тег каждого образа выбери при сборке: свежий стабильный, проверенный `docker pull`. Зафиксируй его в compose.

Healthchecks обязательны. api и worker стартуют после `postgres: service_healthy` и `seaweedfs: service_started`. api — также после `keycloak: service_healthy` (Keycloak с `KC_HEALTH_ENABLED=true`, проверка на порту управления 9000: `/health/ready`).

### PostgreSQL: роли

Init-скрипт `infra/postgres/init/01-init.sh` (bash и psql, значения из env) создаёт:

- БД `hr` с владельцем `hr_owner` — под ним только миграции;
- роль `hr_app` (LOGIN) — под ней работают api и worker: `SELECT, INSERT, UPDATE, DELETE` на все таблицы, кроме `audit_events`, где только `SELECT, INSERT`. Права выдаёт отдельная миграция Alembic;
- БД `keycloak` с владельцем `keycloak`.

Init-скрипты выполняются только на пустом volume. Это нужно записать в README.

### Keycloak: команда и hostname

```
start --import-realm
  --hostname=${PUBLIC_BASE_URL}/kc
  --hostname-admin=${KC_ADMIN_URL}            # http://localhost:8081/kc — Caddy слушает 8081 только на 127.0.0.1 хоста
  --http-enabled=true --http-relative-path=/kc
  --proxy-headers=xforwarded
  --hostname-backchannel-dynamic=true
  --health-enabled=true
env: KC_DB=postgres, KC_DB_URL=jdbc:postgresql://postgres:5432/keycloak, KC_DB_USERNAME, KC_DB_PASSWORD,
     KC_BOOTSTRAP_ADMIN_USERNAME, KC_BOOTSTRAP_ADMIN_PASSWORD,
     переменные для плейсхолдеров realm: PUBLIC_BASE_URL, KC_BFF_CLIENT_SECRET, KC_ADMIN_CLIENT_SECRET, KC_DEMO_PASSWORD
volume: ./infra/keycloak/realm-hr.json → /opt/keycloak/data/import/realm-hr.json:ro
```

- Импорт realm при старте подставляет `${VAR}` из переменных окружения. Если realm уже существует, импорт пропускается: чтобы переимпортировать, удалите realm или volume БД.
- `issuer` в токенах — всегда публичный `${PUBLIC_BASE_URL}/kc/realms/hr`.
- BFF берёт discovery по внутреннему адресу `http://keycloak:8080/kc/realms/hr/.well-known/openid-configuration`. Благодаря `hostname-backchannel-dynamic` `token_endpoint` и `jwks_uri` в ответе будут внутренними, а `authorization_endpoint` и `issuer` — публичными.
- Первым делом в фазе 1 проверь это на своей версии: запроси discovery изнутри контейнера api и посмотри все четыре поля.
- Если что-то не совпадает, задай `OIDC_TOKEN_ENDPOINT` и `OIDC_JWKS_URI` явно из env и запиши это в `docs/decisions.md`.

### Caddyfile (суть)

```
{$SITE_ADDRESS} {
    tls {$CADDY_TLS}                       # "internal" (стенд по IP или localhost) или email для Let's Encrypt
    encode zstd gzip
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "no-referrer"
        Permissions-Policy "camera=(self), microphone=(), geolocation=()"
        -Server
    }
    @kc_private path /kc/admin* /kc/realms/master*
    respond @kc_private 404                # админка снаружи не существует

    handle /kc/* { reverse_proxy keycloak:8080 }
    handle /api/* { reverse_proxy api:8000 { flush_interval -1 } }
    handle_path /operator* {
        root * /srv/operator
        header Content-Security-Policy "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        try_files {path} /index.html
        file_server
    }
    handle {
        root * /srv/applicant
        header Content-Security-Policy "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        try_files {path} /index.html
        file_server
    }
}

# Отладочные входы. В compose порты опубликованы как 127.0.0.1:8081:8081 и 127.0.0.1:8025:8025
http://:8081 {
    reverse_proxy keycloak:8080            # админка Keycloak: ssh -L 8081:127.0.0.1:8081 <стенд>, затем http://localhost:8081/kc/admin
}
http://:8025 {
    reverse_proxy mailpit:8025             # письма стенда: ssh -L 8025:127.0.0.1:8025 <стенд>
}
```

Сборка оператора идёт с `base: '/operator/'`.

Варианты адреса стенда задаются в `.env`, без правки кода:

1. Домен и Let's Encrypt (нужны открытые 80 и 443).
2. IP с `tls internal` (браузер покажет предупреждение).
3. `https://localhost` через SSH-туннель: `ssh -L 8443:localhost:443` и т. п.

Адрес и IP сервера в репозиторий не пишутся.

## Стек

| Слой | Выбор |
|---|---|
| Бэкенд | Python 3.12, FastAPI, Uvicorn, SQLAlchemy 2 (async) + asyncpg, Alembic, Pydantic v2 + pydantic-settings, httpx, PyJWT[crypto], cryptography, structlog, sse-starlette, slowapi |
| Изображения | Pillow, pillow-heif, opencv-python-headless (+ YuNet ONNX из opencv_zoo, скачивается при сборке образа), pypdfium2, imagehash, numpy |
| Тексты | rapidfuzz (Jaro–Winkler, Levenshtein), свой парсер МЧЗ |
| S3-клиент | boto3 через `asyncio.to_thread` (просто и объяснимо) |
| Выгрузки | openpyxl (XLSX), csv из стандартной библиотеки |
| Очередь | своя таблица `jobs` в PostgreSQL (`03-data-model.md`) |
| Зависимости | uv, `uv.lock` в репозитории |
| Фронтенд | React 18 + Vite + TypeScript, Mantine 7, TanStack Query, react-router, `@microsoft/fetch-event-source` (SSE с заголовками), openapi-typescript (типы из OpenAPI бэкенда) |
| Качество | ruff, mypy, pytest, pytest-asyncio, respx; eslint, tsc; pre-commit с gitleaks |

Фронтенды не грузят ничего с внешних CDN: шрифты системные, иконки из npm-пакета (`@tabler/icons-react`).

## Структура репозитория

```
.
├── AGENTS.md  CLAUDE.md  README.md  Makefile  docker-compose.yml  .env.example  .gitignore  .pre-commit-config.yaml
├── registry/documents.yaml              # реестр: страны → документы → поля → правила
├── prompts/extract.v1.md  arbiter.v1.md # промпты модели с версиями
├── backend/
│   ├── Dockerfile  pyproject.toml  uv.lock  alembic.ini
│   ├── app/
│   │   ├── main.py  config.py  messages.py  logging.py
│   │   ├── db/        models.py session.py migrations/
│   │   ├── api/       public.py operator.py auth.py events.py health.py errors.py
│   │   ├── auth/      oidc.py sessions.py deps.py permissions.py applicant_token.py keycloak_admin.py
│   │   ├── intake/    sniff.py limits.py service.py
│   │   ├── storage/   s3.py crypto.py
│   │   ├── queue/     jobs.py worker.py reaper.py notify.py
│   │   ├── pipeline/  run.py preprocess.py quality.py pdf.py crops.py reconcile.py confidence.py merge.py crosscheck.py faces.py
│   │   ├── llm/       gateway.py schema_builder.py prompts.py providers/openai_compat.py providers/olmocr_http.py
│   │   ├── registry/  loader.py rules.py checksums.py mrz.py dates.py names.py translit.py
│   │   ├── audit/     log.py
│   │   ├── notify/    email.py templates/*.txt
│   │   └── exports/   card.py expiring.py
│   └── tests/
├── web/
│   ├── applicant/   (Vite + React, mobile-first)
│   └── operator/    (Vite + React, desktop)
├── infra/
│   ├── caddy/Caddyfile  caddy/Dockerfile
│   ├── keycloak/realm-hr.json
│   ├── postgres/init/01-init.sh
│   ├── seaweedfs/entrypoint.sh
│   └── llm/compose.llm-local.yml  llm/README.md
├── eval/   run.py split.py distort.py metrics.py gold/ reports/
├── data/   (в .gitignore) открытый набор и прочие локальные файлы
└── docs/   architecture.md decisions.md local-model.md limitations.md ai-usage.md how-to-change.md spec/ handoff/
```

## Конфигурация

Вся конфигурация — переменные окружения, они перечислены в `.env.example` с комментариями. `app/config.py` (pydantic-settings) валидирует их при старте и падает с понятным сообщением, если обязательной переменной нет. `registry/documents.yaml` проверяется по Pydantic-модели при старте api и worker: ошибка в YAML останавливает запуск с указанием строки.

## Наблюдаемость

- JSON-логи (structlog) со сквозным `request_id`: заголовок `X-Request-ID` от Caddy или сгенерированный, передаётся в payload задачи и в логи воркера. ПДн, изображений и ответов модели в логах нет, номера документов маскируются.
- `/api/healthz` — процесс жив. `/api/readyz` — доступны БД, S3 и Keycloak; модель проверяется отдельно и показывается в UI оператора как статус, а не как падение readiness.
- Метрики — в разделе «В работе», считаются SQL-запросами по `jobs` и `extraction_runs`. Эндпоинт Prometheus — P2, только во внутренней сети.
