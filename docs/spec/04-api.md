# 04. API

Все маршруты начинаются с `/api`. Формат — JSON. OpenAPI генерирует FastAPI, из неё фронтенды получают TypeScript-типы (`make api-types`). Swagger UI в проде выключен (`DOCS_ENABLED=false`); на стенде он доступен только через тот же вход оператора.

## Формат ошибки

```json
{"error": {"code": "FILE_TOO_LARGE", "message": "Файл больше 20 МБ. Сожмите фото или загрузите PDF меньшего размера.", "support_id": "a1b2c3"}}
```

- `message` — для человека, всегда по-русски, с советом, что делать. Берётся из каталога `app/messages.py`.
- `code` — для фронтенда и тестов, человеку не показывается.
- `support_id` — короткий `request_id`.
- Глобальный обработчик превращает любое неожиданное исключение в `500 INTERNAL` с текстом «Что-то пошло не так. Попробуйте ещё раз; если повторится — сообщите код {support_id}». Стектрейс уходит только в лог.

## Каталог сообщений (минимум)

| code | HTTP | message |
|---|---|---|
| CONSENT_REQUIRED | 422 | Чтобы отправить документы, подтвердите согласие на обработку персональных данных. |
| CONSENT_VERSION_OUTDATED | 409 | Текст согласия обновился. Обновите страницу и подтвердите согласие ещё раз. |
| COUNTRY_NOT_SUPPORTED | 422 | Для этой страны приём документов пока не настроен. Обратитесь в агентство. |
| OFFICE_NOT_FOUND | 404 | Ссылка устарела или указана неверно. Попросите у агентства актуальную ссылку. |
| EMAIL_INVALID | 422 | Проверьте адрес электронной почты — на него придёт ссылка на заявку. |
| FILE_TYPE_NOT_ALLOWED | 415 | Этот тип файла не подходит. Загрузите фото (JPG, PNG, HEIC, WebP) или PDF. |
| FILE_EMPTY | 422 | Файл пустой. Выберите другой файл. |
| FILE_TOO_LARGE | 413 | Файл больше {max_mb} МБ. Сожмите фото или загрузите PDF меньшего размера. |
| FILE_CORRUPTED | 422 | Файл повреждён и не открывается. Сделайте новое фото или сохраните PDF заново. |
| IMAGE_TOO_SMALL | 422 | Снимок слишком маленький ({w}×{h}). Сфотографируйте документ ближе, чтобы он занимал весь кадр. |
| IMAGE_TOO_LARGE | 422 | Изображение слишком большое по количеству пикселей. Уменьшите его или загрузите обычное фото с телефона. |
| PDF_TOO_MANY_PAGES | 422 | В PDF больше {max_pages} страниц. Загрузите только нужные страницы документа. |
| PDF_ENCRYPTED | 422 | PDF защищён паролем. Загрузите файл без пароля. |
| DUPLICATE_FILE | 409 | Этот файл уже загружен в заявку (документ «{doc_title}», {time}). Повторно загружать не нужно. |
| TOO_MANY_FILES | 422 | В заявке уже {n} файлов — это максимум. Если нужно заменить файл, свяжитесь с агентством. |
| TOO_MANY_FILES_FOR_DOCUMENT | 422 | Для документа «{doc_title}» уже загружено {n} файлов — это максимум. |
| APPLICATION_TOTAL_TOO_LARGE | 422 | Общий объём файлов заявки превышает {max_mb} МБ. |
| DOC_TYPE_NOT_IN_SET | 422 | Документ «{doc_title}» не требуется для выбранной страны. |
| TOKEN_INVALID | 401 | Ссылка на заявку недействительна или устарела. Откройте ссылку из письма ещё раз. |
| RATE_LIMITED | 429 | Слишком много попыток. Подождите минуту и попробуйте снова. |
| AUTH_REQUIRED | 401 | Нужно войти в систему. |
| FORBIDDEN | 403 | Недостаточно прав для этого действия. Обратитесь к руководителю. |
| NOT_FOUND | 404 | Не найдено. Возможно, заявка удалена или относится к другому офису. |
| FIELD_VERSION_CONFLICT | 409 | Поле уже изменил другой сотрудник. Обновите карточку. |
| FIELD_UNKNOWN | 422 | Такого поля нет у этого документа. |
| MODEL_UNAVAILABLE | 503 | Сервис распознавания временно недоступен. Документы ждут в очереди и будут разобраны автоматически. |
| CSRF_FAILED | 403 | Запрос отклонён из соображений безопасности. Обновите страницу. |

В тексте отказа на защите судья может попросить что-то поменять. Правка — одна строка в `messages.py`.

## Публичная часть (соискатель)

**Авторизация соискателя.**

- При создании заявки генерируется токен (32 байта, base64url). В ответе он возвращается один раз, в БД хранится только SHA-256.
- Ссылка в письме: `{PUBLIC_BASE_URL}/a/{application_id}#t={token}`. Токен стоит во фрагменте, поэтому не уходит на сервер в URL и не попадает в логи.
- SPA читает фрагмент, кладёт токен в `localStorage` (ключ по `application_id`) и шлёт в заголовке `X-Application-Token`.
- Сравнение — постоянного времени (`hmac.compare_digest` по хешам).

| Метод и путь | Что делает |
|---|---|
| `GET /api/public/offices/{slug}` | Название офиса; 404 `OFFICE_NOT_FOUND` |
| `GET /api/public/countries` | Список стран из реестра: `code`, `name_ru`, `group` |
| `GET /api/public/requirements?country=UZ` | Набор документов: `doc_type`, `title`, `hint`, `max_files`, `required`, а также лимиты приёма |
| `GET /api/public/consent` | Текущие версии согласия и политики, их URL (`/legal/consent-v1.html`, `/legal/policy-v1.html` — статика фронтенда) |
| `POST /api/public/applications` | Тело: `{office_slug, country_code, full_name, email, consent: {accepted: true, consent_version, policy_version}}`. Ответ 201: `{application_id, public_no, token, documents:[...]}`. Одна транзакция: заявка, согласие, слоты, журнал. Rate limit по IP: 10 в час |
| `POST /api/public/applications/{id}/files` | multipart: `doc_type` и `files[]` (до `MAX_FILES_PER_REQUEST`). Каждый файл проверяется отдельно. Ответ 207-подобный (код 200): `{"results": [{"name": "...", "accepted": true, "file_id": "...", "warning": null}, {"name": "...", "accepted": false, "error": {code, message}}]}`. Принятые файлы ставятся в очередь сразу, в той же транзакции. Работает и до, и после отправки комплекта (дозагрузка) |
| `POST /api/public/applications/{id}/submit` | Комплект отправлен: статус `submitted`, запись в журнал, письмо со ссылкой. Ответ сразу: `{public_no, submitted_at, missing:[...]}`. Повторный вызов идемпотентен |
| `GET /api/public/applications/{id}` | Состояние для соискателя: офис, страна, статус, документы (`status`, `stage`, файлы со статусами, безопасные замечания `message_applicant_ru`), `missing`. Извлечённых значений полей здесь нет — утёкшая ссылка не раскрывает паспортные данные |
| `GET /api/public/applications/{id}/events` | SSE: события `document.updated`, `file.updated`, `application.updated` с тем же содержимым, что в GET. Каждые 15 секунд — `ping`. Клиент — `fetch-event-source` с заголовком токена |
| `GET /api/public/files/{file_id}/thumb` | Миниатюра своего файла (с размытым лицом) для экрана загрузки. Только с токеном своей заявки |

## Аутентификация оператора (BFF)

| Метод и путь | Что делает |
|---|---|
| `GET /api/auth/login?return_to=/operator/...` | Создаёт `state`, `nonce` и PKCE-verifier (S256) в `auth_login_states` и делает 302 на `authorization_endpoint`. `return_to` — только относительный путь, начинающийся с `/operator` |
| `GET /api/auth/callback?code&state` | Проверяет `state`, меняет код на токены (confidential client, `client_secret_basic`), проверяет `id_token`: подпись по JWKS, `iss`, `aud`, `exp`, `nonce`. Берёт роли из `resource_access.operator-bff.roles`, офисы — из `groups` (`/offices/<slug>`). Создаёт сессию, ставит cookie и делает 302 на `return_to`. Пользователь без единой из трёх ролей → страница «Нет доступа» и запись `auth.login_failed` |
| `GET /api/auth/me` | `{name, username, roles, offices, permissions:[...]}` или 401 |
| `POST /api/auth/logout` | Удаляет сессию, очищает cookie и возвращает `{redirect: end_session_endpoint?id_token_hint=…&post_logout_redirect_uri=…}` |
| `POST /api/auth/backchannel-logout` | P2: принимает `logout_token` от Keycloak, проверяет его и удаляет сессии по `sid` или `sub` |

Cookie: `__Host-Http-hr_session=<random>`; `Secure; HttpOnly; SameSite=Strict; Path=/`, без `Domain`.

CSRF: все небезопасные методы под `/api/operator/*` и `/api/auth/logout` требуют заголовок `X-Requested-With: hr-operator` и `Origin`, равный `PUBLIC_BASE_URL`.

## Операторская часть

Все маршруты требуют сессию (иначе 401) и права (иначе 403). Объекты чужого офиса отдают 404. Права перечислены в `07-auth-security.md`.

| Метод и путь | Право | Что делает |
|---|---|---|
| `GET /api/operator/applications` | applications:read | Фильтры: `status`, `country_group`, `country`, `office` (только для supervisor), `has_errors`, `expiring_within_days`, `q` (номер заявки или ФИО), `created_from`, `created_to`; `sort`, `page`, `page_size`. В строке: номер, дата, ФИО (по документам, иначе из анкеты), страна, офис, статус, чипы документов с цветом, число ошибок и предупреждений, ближайшее истечение, email замаскирован (`iv***@mail.ru`) |
| `GET /api/operator/applications/{id}` | applications:read | Карточка: заявка, документы, файлы (id, страницы, размеры), поля (значение, raw, цвет, сигналы, кандидаты, подсказка, рамка, источник, кто правил), замечания, сверка между документами. Просмотр карточки пишется в журнал (`application.viewed`) |
| `GET /api/operator/files/{file_id}/pages/{n}?variant=page\|preview` | files:read | PNG/JPEG нормализованной страницы. `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`. Журнал `file.viewed` пишется не чаще раза в 5 минут на пару оператор–файл |
| `GET /api/operator/files/{file_id}/original` | files:read | Оригинал как вложение (`Content-Disposition: attachment`). Запись в журнал обязательна |
| `PATCH /api/operator/fields/{field_id}` | fields:edit | `{value, version}` → нормализация, правила документа заново, сверка заявки заново. Журнал: было → стало. Ответ — обновлённый документ |
| `POST /api/operator/documents/{id}/reprocess` | documents:reprocess | Задачи `process_file` с priority 10 на все файлы документа. Правки оператора сохраняются |
| `POST /api/operator/documents/{id}/verify` | documents:verify | Отметка «проверен». Если есть красные поля, нужно тело `{"confirm_red": true}` |
| `POST /api/operator/documents/{id}/change-type` | fields:edit | P1: перенести файлы в другой слот, если соискатель ошибся слотом, и разобрать заново |
| `POST /api/operator/applications/{id}/request-documents` | requests:send | Письмо соискателю: «пришлите…» со списком недостающих и непрочитанных документов и комментарием оператора |
| `GET /api/operator/applications/{id}/history` | history:read | События журнала по заявке |
| `GET /api/operator/applications/{id}/export?format=xlsx\|json` | exports | Карточка для кадров. В журнал |
| `GET /api/operator/expiring?days=30&format=json\|xlsx\|csv` | exports (json — applications:read) | Документы, истекающие в ближайшие N дней или уже истёкшие |
| `GET /api/operator/queue` | queue:read | `{waiting, processing, failed_last_hour, done_last_hour, avg_duration_s, p95_duration_s, oldest_waiting_s, model: {state, last_ok_at, last_error}, in_progress: [{file_id, application_no, doc_title, stage, worker, started_at, attempt}], failed: [{..., error_message_ru}]}` — всё из БД |
| `GET /api/operator/events` | applications:read | SSE для оператора: `application.updated`, `queue.updated` (с учётом офиса) |
| `GET /api/operator/audit` | audit:all | Журнал всей системы с фильтрами |
| `GET/POST /api/operator/staff` | staff:manage | P2: список и создание сотрудников через Keycloak Admin API |
| `DELETE /api/operator/applications/{id}` | applications:delete | P2: удаление данных соискателя (БД и S3). В журнале остаётся запись без ПДн |
| `GET /api/operator/runs/{file_id}` | audit:all | Технические детали разбора: `extraction_runs` по файлу |

## Служебные

`GET /api/healthz`, `GET /api/readyz`. Метрики Prometheus (P2) — на отдельном порту, только во внутренней сети.

## События SSE и LISTEN/NOTIFY

- Воркер после каждого изменения статуса делает `NOTIFY app_events, '{"application_id": "...", "kind": "document.updated"}'`. Полезная нагрузка — только идентификаторы, без ПДн.
- api держит одно соединение `LISTEN app_events` на процесс и раздаёт события подписчикам (asyncio-очереди по `application_id` и по офису).
- По событию клиент перезапрашивает состояние обычным GET. Так SSE не дублирует логику прав, а потерянное событие не ломает экран.
- При переподключении клиент делает полный GET.
