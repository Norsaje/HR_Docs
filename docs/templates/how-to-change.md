# Как вносить типовые изменения (за 10–15 минут)

<!-- Для п. 5.6.2 «правка на защите». Агент: проверь каждый рецепт на реальном коде, укажи точные файлы и строки. -->

## Добавить поле в документ

1. `registry/documents.yaml` → `documents.<вид>.fields`: добавить строку, например
   `military_id: {label: Военный билет, type: code}`. Если поле обязательное — добавить в `min_fields`.
2. Перезапустить api и worker: `docker compose up -d --build api worker` (или `docker compose restart api worker`, если реестр примонтирован).
3. Новое поле само попадёт в JSON-схему модели, в описание полей в промпте, в карточку оператора и в выгрузку.
4. Нажать «Разобрать повторно» на документе — поле появится.

## Изменить правило проверки

1. `registry/documents.yaml` → `rules`: поменять `value`, `severity` или `message`, либо добавить правило из закрытого набора проверок (список — в шапке файла).
2. Если нужна новая проверка: функция в `backend/app/registry/rules.py`, регистрация в `CHECKS`, тест в `backend/tests/test_rules.py`.
3. Перезапуск, «Разобрать повторно».

## Поменять текст отказа

`backend/app/messages.py` — словарь `MESSAGES[code]`. Перезапуск api.

## Добавить фильтр в список заявок

1. Бэкенд: `backend/app/api/operator.py` → `list_applications`: параметр запроса и условие в запросе (через `scoped()`).
2. Фронтенд: `web/operator/src/pages/Applications/Filters.tsx`: элемент фильтра, значение в URL.
3. `make api-types`, пересборка caddy: `docker compose up -d --build caddy api`.

## Добавить страну или документ в набор

`registry/documents.yaml` → `country_groups.<группа>.countries` или `.documents`.

## Завести сотрудника

Раздел «Сотрудники» (supervisor) или админка Keycloak через SSH-туннель: Users → Add user → Credentials → Role mapping (клиент `operator-bff`) → Groups (`/offices/<офис>`).

## Поменять модель или адрес

`.env`: `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` → `docker compose up -d worker`. Или `make switch-llm TARGET=…`.
