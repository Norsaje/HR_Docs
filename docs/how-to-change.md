# Как вносить типовые изменения (за 10–15 минут)

Реестр `registry/documents.yaml` и промпты `prompts/` копируются в образ бэкенда при сборке, поэтому после их правки api и worker пересобираются: `docker compose up -d --build api worker`.

## Добавить поле в документ

1. `registry/documents.yaml` → `documents.<вид>.fields`: добавить строку, например
   `military_id: {label: Военный билет, type: code}`.
   Типы полей: `digits | date | name | latin_name | code | text | enum` (описание — в шапке файла). Если в подписи есть запятая или двоеточие — возьмите её в кавычки.
   Если поле обязательное для «документ разобран» — добавить его имя в `min_fields`.
2. Пересобрать и перезапустить: `docker compose up -d --build api worker`. При ошибке в YAML api не стартует, а в `docker compose logs api` будет номер строки и причина.
3. Поле само попадёт в JSON-схему модели (`backend/app/llm/schema_builder.py`), в описание полей в промпте и в карточку оператора.
4. В карточке оператора нажать «Разобрать повторно» — поле появится.

## Изменить или добавить правило проверки

1. `registry/documents.yaml` → `documents.<вид>.rules`: поменять `value`, `severity` (`error | warning | info`) или `message`, либо добавить правило из закрытого набора проверок (список `check` — в шапке файла), например
   `- {id: snils_len, field: number, check: length, value: 11, severity: error, message: "Номер СНИЛС должен состоять из 11 цифр"}`.
2. Новая проверка, которой нет в наборе: ветка с её именем в функции `check()` в `backend/app/registry/rules.py`, имя — в списке `RuleCheck` в `backend/app/registry/loader.py`, тест — в `backend/tests/`.
3. `docker compose up -d --build api worker`, затем «Разобрать повторно».

## Поменять текст ошибки для пользователя

`backend/app/messages.py` — каталог русских сообщений по коду ошибки. `docker compose up -d --build api`.

## Добавить фильтр в список заявок

1. Бэкенд: `backend/app/api/operator.py` → `list_applications`: новый параметр запроса (рядом с `status`, `country`, `q`) и условие в список `cond` (фильтр по офису `scoped_where` уже применён).
2. Фронтенд: `web/operator/src/pages/Applications.tsx` — элемент фильтра, значение передать в запрос (`web/operator/src/api.ts`).
3. Пересобрать: `docker compose up -d --build api caddy` (фронтенды собираются внутри образа caddy).

## Добавить страну или документ в набор страны

`registry/documents.yaml` → `country_groups.<группа>.countries` (код страны и название в `country_names`) или `.documents`. Страна может входить только в одну группу. Пересобрать api и worker.

## Завести сотрудника

Админка Keycloak (только с машины стенда: http://localhost:8081/kc/admin; на удалённом стенде — через SSH-туннель, `make kc-admin-tunnel-help`): realm `hr` → Users → Add user (имя, фамилия, email, «Email verified») → Credentials (пароль не короче 12 символов) → Role mapping → клиент `operator-bff`: `doc-reader`, `doc-editor` или `supervisor` → Groups: `/offices/moscow` или `/offices/spb`. Раздел «Сотрудники» в интерфейсе оператора не реализован.

## Поменять модель или адрес модели

`.env`: `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` → `docker compose up -d worker`. Подробно — `docs/local-model.md`.

## Поменять промпт

Новая версия файла `prompts/extract.v2.md` (frontmatter `version: v2`) и `PROMPT_EXTRACT_VERSION=v2` в `.env` → `docker compose up -d --build worker`. Старую версию не удалять: по ней можно откатиться.
