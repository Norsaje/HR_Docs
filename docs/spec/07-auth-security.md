# 07. Вход, права, безопасность

## Оператор: Keycloak + BFF (OIDC Authorization Code + PKCE)

Схема по RFC 10017 (BCP 212, OAuth 2.0 for Browser-Based Applications), паттерн Backend-for-Frontend:

- FastAPI — конфиденциальный OIDC-клиент;
- токены не попадают в браузер;
- у браузера только HttpOnly-cookie сессии.

### Realm `hr` (`infra/keycloak/realm-hr.json`, импорт при старте, значения через `${ENV}`)

**Настройки realm:**

- `sslRequired: external`, `bruteForceProtected: true` (`failureFactor 5`, `waitIncrementSeconds 60`);
- `loginWithEmailAllowed: true`, `registrationAllowed: false`, `resetPasswordAllowed: false` (на стенде нет SMTP для Keycloak);
- `accessTokenLifespan: 300`, `ssoSessionIdleTimeout: 1800`, `ssoSessionMaxLifespan: 36000`;
- `passwordPolicy: "length(12) and notUsername"`; `internationalizationEnabled: true`, `defaultLocale: ru`.

**Клиент `operator-bff`:**

- `publicClient: false`, `clientAuthenticatorType: client-secret`, `secret: ${KC_BFF_CLIENT_SECRET}`;
- `standardFlowEnabled: true`, `directAccessGrantsEnabled: false`, `implicitFlowEnabled: false`, `serviceAccountsEnabled: false`;
- `redirectUris: ["${PUBLIC_BASE_URL}/api/auth/callback"]`, `webOrigins: ["${PUBLIC_BASE_URL}"]`;
- `attributes`: `pkce.code.challenge.method: S256`, `post.logout.redirect.uris: ${PUBLIC_BASE_URL}/operator/`, `backchannel.logout.url: http://api:8000/api/auth/backchannel-logout` (P2), `backchannel.logout.session.required: true`;
- роли клиента: `doc-reader`, `doc-editor`, `supervisor`;
- маппер `groups`: тип `oidc-group-membership-mapper`, `full.path: true`, в ID-токен и в userinfo;
- маппер клиентских ролей в токен — стандартный (`resource_access.operator-bff.roles`).

**Клиент `operator-admin`** (P2, для раздела «Сотрудники»): `serviceAccountsEnabled: true`, `standardFlowEnabled: false`, `secret: ${KC_ADMIN_CLIENT_SECRET}`. У сервисного аккаунта роли `realm-management`: `manage-users`, `view-users`, `query-groups`, `view-clients` — и только они. В realm-JSON это поле `serviceAccountClientRoles` у пользователя `service-account-operator-admin`.

**Группы-офисы:** `/offices/moscow`, `/offices/spb` (совпадают с `offices` в реестре).

**Тестовые пользователи** (пароль у всех `${KC_DEMO_PASSWORD}`; `firstName`, `lastName`, `email` и `emailVerified: true` обязательны, иначе Keycloak 26 при входе попросит дозаполнить профиль):

| username | ФИО | Роль | Группа |
|---|---|---|---|
| `reader.msk` | Ирина Читалова | doc-reader | /offices/moscow |
| `editor.msk` | Олег Правкин | doc-editor | /offices/moscow |
| `editor.spb` | Анна Невская | doc-editor | /offices/spb |
| `supervisor` | Сергей Главный | supervisor | — |

**MFA:**

- на стенде TOTP необязателен, чтобы судьи могли войти;
- в проде — required action `CONFIGURE_TOTP` или passkeys (WebAuthn) для всех операторов. Меняется настройкой realm, код BFF не меняется.

### Поток BFF (`app/auth/oidc.py`, ~150 строк, без Authlib — чтобы объяснить на защите)

1. `GET /api/auth/login`:
   - `state = token_urlsafe(32)`, `nonce = token_urlsafe(32)`, `code_verifier = token_urlsafe(64)`, `code_challenge = b64url(sha256(verifier))`;
   - запись в `auth_login_states`;
   - 302 на `authorization_endpoint` с `response_type=code`, `scope=openid profile email`, `code_challenge_method=S256`.
2. `GET /api/auth/callback`:
   - найти и удалить `state` (одноразовый; старше 10 минут — отказ);
   - POST на `token_endpoint` (внутренний адрес) с `code`, `code_verifier`, `redirect_uri`, Basic-аутентификацией клиента;
   - проверить `id_token` через PyJWT + `PyJWKClient(jwks_uri)` (кэш ключей): `RS256`, `iss == OIDC_ISSUER`, `aud` содержит `operator-bff`, `exp`, `iat`, `nonce`;
   - роли — из `access_token` (`resource_access.operator-bff.roles`; access token проверяется так же), группы — из `id_token.groups`;
   - нет ни одной из трёх ролей → «Нет доступа», журнал `auth.login_failed`, сессию не создаём.
3. **Сессия:**
   - `session_id = token_urlsafe(32)` → в БД `sha256(session_id)`;
   - `refresh_token` хранится зашифрованным (Fernet, ключ `SESSION_ENC_KEY`);
   - `idle 30 мин`, `abs 10 ч`;
   - cookie `__Host-Http-hr_session`.
4. **На каждом запросе** (`deps.get_operator`):
   - найти сессию по хешу, проверить сроки, обновить `last_seen_at` (не чаще раза в минуту);
   - раз в 5 минут обновлять роли через `refresh_token`: если Keycloak отказал (пользователь отключён, роль снята), сессия удаляется → 401.
5. **Выход:** удалить сессию; фронтенд уходит на `end_session_endpoint` с `id_token_hint` и `post_logout_redirect_uri`.

### Права (`app/auth/permissions.py`)

```python
PERMISSIONS = {
  "applications:read":   {"doc-reader", "doc-editor", "supervisor"},
  "files:read":          {"doc-reader", "doc-editor", "supervisor"},
  "queue:read":          {"doc-reader", "doc-editor", "supervisor"},
  "history:read":        {"doc-reader", "doc-editor", "supervisor"},
  "fields:edit":         {"doc-editor", "supervisor"},
  "documents:reprocess": {"doc-editor", "supervisor"},
  "documents:verify":    {"doc-editor", "supervisor"},
  "requests:send":       {"doc-editor", "supervisor"},
  "exports":             {"doc-editor", "supervisor"},
  "audit:all":           {"supervisor"},
  "staff:manage":        {"supervisor"},
  "applications:delete": {"supervisor"},
}
```

Зависимость FastAPI: `Depends(require("fields:edit"))`. Отказ даёт 403 и запись `access.denied` в журнал.

### Видимость по группам (защита от IDOR)

- Единственная функция `scoped(query, operator)` в `app/auth/deps.py` добавляет `WHERE applications.office_slug = ANY(:offices)` для всех, кроме `supervisor`.
- Любой запрос к заявке, документу, файлу, полю, истории или выгрузке строится от `applications` через неё. Файл ищется только джойном с заявкой.
- Объект вне видимости даёт **404**, а не 403: не подтверждаем существование.
- Оператор без офиса и без роли `supervisor` видит пустой список.

### Обязательные автотесты безопасности (`tests/test_security.py`)

1. Перебрать `app.routes`: каждый маршрут `/api/operator/*` без cookie → 401. Новый маршрут попадает в тест автоматически.
2. `editor.msk` запрашивает заявку, файл, поле и историю офиса `spb` → 404 на всё.
3. `doc-reader` делает `PATCH` поля → 403.
4. POST без `X-Requested-With` или с чужим `Origin` → 403.
5. Публичный GET заявки без токена, с чужим или с истёкшим → 401. Ответ публичного GET не содержит значений полей (проверить по ключам JSON).
6. Подделанная или просроченная cookie → 401.

Сессии в тестах создаются фикстурой напрямую в БД: Keycloak в юнит-тестах не нужен. Полный поток OIDC проверяет `make e2e-smoke` — headless-логин через форму Keycloak с помощью httpx (GET страницы логина, POST формы).

## Соискатель

- **Согласие.** Галочка по умолчанию не отмечена, рядом ссылки на «Согласие на обработку персональных данных» и «Политику обработки персональных данных». Это статические HTML-страницы в `web/applicant/public/legal/`, текст — шаблон с пометкой «согласовать с юристом».
  - Версии и SHA-256 текстов знает сервер (`CONSENT_VERSION`, `POLICY_VERSION`; хеш считается при старте из файлов, примонтированных в api).
  - Без `consent.accepted=true` и актуальной версии заявка не создаётся (422 или 409).
  - В `consents` записываются версии, хеш, время, IP и User-Agent.
- **Токен заявки** — 256 бит, в БД только SHA-256, срок 30 дней, передаётся во фрагменте ссылки (`04-api.md`).
- **Соискатель не видит значения извлечённых полей**, только статусы и безопасные тексты замечаний. Утёкшая ссылка не раскрывает паспортные данные.
- **Rate limit** (slowapi, по IP):
  - создание заявки — 10/час;
  - загрузка — 60/час на заявку;
  - публичный GET — 120/мин.

  В проде к этому добавляется капча.
- **Email** — только для писем. В списке оператора он маскируется. Вне Mailpit и SMTP заказчика никуда не уходит.

## Заголовки и браузер

- CSP без внешних источников и без inline-скриптов (`02-architecture.md`), `frame-ancestors 'none'`.
- `Cache-Control: no-store` на всех ответах `/api/operator/*` и `/api/public/applications/*`.
- `Referrer-Policy: no-referrer` (токен во фрагменте и так не уходит, но лишним не будет).
- Отдача изображений: `Content-Type` из нашей нормализации (PNG или JPEG), `X-Content-Type-Options: nosniff`. Оригиналы — только `attachment`: PDF соискателя никогда не рендерится браузером оператора в нашем origin.
- CORS выключен: всё на одном origin.

## Данные в покое

- **S3:** бакет без публичного доступа. Ключ объекта — UUID, имени файла в ключе нет.
- **P1 — шифрование файлов** (`app/storage/crypto.py`), конвертная схема:
  - на каждый файл случайный DEK (32 байта), AES-256-GCM, nonce 12 байт;
  - DEK шифруется мастер-ключом `FILE_ENC_MASTER_KEY` (32 байта, base64 в `.env`; в проде — Vault или KMS);
  - в `files.enc` лежат `{"v":1,"wrapped_dek": ..., "nonce": ..., "kid": "k1"}`;
  - производные изображения шифруются так же.
- **PostgreSQL:** volume на зашифрованном диске (прод, вне приложения). Резервные копии `pg_dump` и `weed` — `make backup` (P2).
- **Сроки хранения (P2):** `RETENTION_DAYS` после последнего изменения. Задача `retention_sweep` раз в сутки удаляет ПДн (файлы в S3 и строки) и оставляет в журнале запись «данные заявки A-… удалены по сроку». Кнопка `supervisor` «Удалить данные соискателя» делает то же самое сразу.

## Журнал аудита

- Пишется в той же транзакции, что и действие. Запись журнала не может «потеряться» отдельно от действия.
- У роли `hr_app` на `audit_events` только `SELECT` и `INSERT` — доказательство неизменяемости для судей.
- Отказы тоже пишутся: отклонённый файл с причиной, `access.denied`, `auth.login_failed`.
- Номера документов в `message_ru` общего журнала маскируются (`45** ***456`). В истории заявки для пользователя с правом видно «было → стало» полностью.

## Секреты

- `.env` (права 600 на сервере) — единственное место секретов. В репозитории лежит `.env.example` с описанием каждой переменной и без значений.
- `make secrets`: скрипт генерирует случайные значения для всех секретов (`openssl rand`) в `.env`. README говорит его запустить.
- В pre-commit — `gitleaks`. В CI (если будет) — `gitleaks detect`.
- В логах нет токенов, cookie, паролей и ПДн. Есть фильтр structlog по ключам `token`, `password`, `secret`, `authorization`, `cookie`.
