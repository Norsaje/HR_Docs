# 03. Модель данных, статусы, очередь

Все таблицы описаны в SQLAlchemy 2 и создаются миграциями Alembic. Первичные ключи — UUID (кроме `jobs` и `audit_events`, где `bigserial`). Время — `timestamptz` в UTC, «сегодня» для правил считается в `APP_TIMEZONE` (по умолчанию Europe/Moscow).

## Таблицы

### applications — заявка соискателя
| Поле | Тип | Примечание |
|---|---|---|
| id | uuid | |
| public_no | text unique | человекочитаемый номер `A-2610-0042` для соискателя и оператора |
| office_slug | text | офис из ссылки `/apply/<slug>`; по нему работает видимость для операторов |
| country_code | text | ISO 3166-1 alpha-2 |
| country_group | text | `ru` / `uz` / `eu` из реестра |
| full_name_entered | text | ФИО, как ввёл соискатель (для сверки с документами) |
| email | text | только для обратной связи |
| token_hash | bytea | SHA-256 токена доступа соискателя (сам токен не храним) |
| token_expires_at | timestamptz | по умолчанию +30 дней |
| status | enum | `draft` → `submitted` → `processing` → `needs_attention` / `ready` → `verified`; вычисляется из документов (см. ниже) |
| submitted_at, created_at, updated_at | timestamptz | |
| last_notified_at | timestamptz null | антиспам для писем |
| deleted_at | timestamptz null | P2: удаление данных по запросу |

### consents — согласие на обработку ПДн
`id, application_id, consent_version, consent_sha256, policy_version, accepted_at, ip, user_agent`. Без строки в этой таблице заявка не создаётся (одна транзакция).

### documents — слот документа в заявке
| Поле | Тип | Примечание |
|---|---|---|
| id | uuid | |
| application_id | uuid fk | |
| doc_type | text | ключ из реестра (`ru_passport`, …) |
| required | bool | из реестра на момент создания |
| status | enum | см. «Статусы документа» |
| detected_type | text null | что увидела модель (если не совпало со слотом — замечание) |
| verified_by, verified_at | null | отметка «проверен оператором» |
| expires_on | date null | нормализованный срок действия для списка «истекающие» |
| updated_at | timestamptz | |

Слоты создаются при создании заявки — по списку документов для страны.

### files — загруженный файл
| Поле | Тип | Примечание |
|---|---|---|
| id | uuid | он же ключ объекта в S3: `orig/{application_id}/{file_id}` |
| document_id, application_id | uuid fk | |
| original_name | text | только для показа; в пайплайн не передаётся |
| mime | text | по сигнатуре, не по расширению |
| size_bytes | int | |
| sha256 | bytea | дубликаты в заявке — уникальный индекс `(application_id, sha256)` |
| phash | bigint null | перцептивный хеш первой страницы |
| pages | int | |
| status | enum | `queued`, `processing`, `done`, `unreadable`, `failed` |
| stage | enum null | `preprocess`, `ocr`, `extract`, `verify` — для «этап 2 из 4» |
| quality | jsonb | резкость, пересвет, разрешение по страницам |
| enc | jsonb | P1: параметры конвертного шифрования (wrapped key, nonce) |
| error_code, error_message_ru | text null | для `unreadable` и `failed` |
| uploaded_by | enum | `applicant` / `operator` |
| created_at | timestamptz | |

Производные изображения хранятся в S3 под `derived/{file_id}/page-{n}.png` (нормализованная страница) и `derived/{file_id}/preview-{n}.jpg` (превью с размытым лицом).

### extraction_runs — попытка разбора файла
`id, file_id, job_id, attempt, page, pass (A|B|C), provider, base_url_host, model, prompt_version, schema_hash, started_at, finished_at, duration_ms, status (ok|error), error_code, finish_reason, tokens_in, tokens_out, response jsonb (сырой ответ модели), ocr_text text (для прохода B)`.

Это доказательство для судей, что разбор делала модель: провайдер, модель, длительность и ответ по каждой странице. В UI оператора результаты показываются на вкладке «Технические детали разбора», только для `supervisor`.

### fields — текущее значение поля документа
| Поле | Тип | Примечание |
|---|---|---|
| id | uuid | |
| document_id | uuid fk | уникальность `(document_id, key)` |
| key | text | из реестра |
| value | text null | нормализованное значение (даты ISO, цифры без пробелов) |
| raw | text null | как прочитано |
| status | enum | `ok`, `unreadable`, `absent` |
| source | enum | `model` / `operator` |
| confidence | enum | `green`, `yellow`, `red`, `grey` (`grey` — необязательное поле, на документе его нет) |
| signals | jsonb | сигналы уверенности (`05-pipeline.md`) |
| candidates | jsonb | `{"qwen": "...", "ocr": "...", "arbiter": "...", "mrz": "..."}` |
| hint | text null | подсказка по контрольной сумме («возможно, 8 вместо 3») |
| file_id, page, bbox | | где поле на изображении; bbox — `[x1,y1,x2,y2]` в шкале 0–1000 |
| edited_by, edited_at | null | для правок оператора |
| version | int | оптимистичная блокировка правок |

Правка оператора не теряется при повторном разборе. Если `source=operator`, новый разбор пишет свой результат в `candidates.qwen` и т. д., но значение не перетирает. Конфликт показывается оператору как «модель теперь читает иначе».

### issues — замечания
`id, application_id, document_id null, field_key null, severity (error|warning|info), code, message_ru, message_applicant_ru null, audience (operator|both), created_by (system|operator), resolved_at, created_at`.

При каждом пересчёте документа замечания системы по этому документу пересоздаются, ручные не трогаются. Тексты берутся из каталога `app/messages.py` с подстановкой параметров.

### audit_events — журнал (только INSERT и SELECT для `hr_app`)
| Поле | Примечание |
|---|---|
| id bigserial, ts | |
| actor_type | `applicant`, `operator`, `system` |
| actor_id, actor_name | `sub` и ФИО оператора; для соискателя — «соискатель по ссылке», для системы — `worker-<host>` |
| action | код: `application.created`, `file.accepted`, `file.rejected`, `job.started`, `job.finished`, `job.failed`, `job.requeued`, `field.edited`, `document.reprocess`, `document.verified`, `auth.login`, `auth.login_failed`, `auth.logout`, `access.denied`, `file.viewed`, `export.created`, `notification.sent`, `application.deleted`… |
| message_ru | человекочитаемо: «Оператор Иванова И. исправила поле «Номер» в документе «Паспорт РФ»: 123456 → 128456» |
| application_id, object_type, object_id | |
| result | `ok`, `denied`, `error` |
| reason | причина отказа или ошибки, по-русски |
| before, after | jsonb для правок (значения номеров маскируются в общем журнале и видны полностью в истории заявки с правом) |
| request_id, ip | |
| prev_hash, hash | P2: хеш-цепочка |

### notifications — исходящие письма (outbox)
`id, application_id, kind (link|missing|unreadable|expired|request_from_operator), to_email, subject, body, status (pending|sent|failed), attempts, sent_at, error`. Отправляет воркер задачей `send_notification`.

### auth_login_states и operator_sessions
- `auth_login_states`: `state (pk), nonce, code_verifier, return_to, created_at`. Срок жизни — 10 минут, использованная запись удаляется.
- `operator_sessions`:
  - `id_hash (pk, sha256 от случайного 32-байтного id в cookie), sub, username, display_name`;
  - `roles text[], offices text[]`;
  - `refresh_token_enc bytea` (Fernet), `id_token_hint text, sid text`;
  - `created_at, last_seen_at, idle_expires_at, abs_expires_at, roles_refreshed_at`.

### jobs — очередь задач
| Поле | Тип | Примечание |
|---|---|---|
| id | bigserial | |
| kind | text | `process_file`, `recompute_application`, `send_notification`, `retention_sweep` |
| payload | jsonb | `{"file_id": "...", "request_id": "..."}` — без ПДн |
| status | enum | `queued`, `running`, `done`, `failed`, `dead` |
| priority | int | 0 по умолчанию; повторный разбор оператором — 10 |
| attempts, max_attempts | int | по умолчанию 0 и 5 |
| run_after | timestamptz | для отложенного повтора (backoff) |
| locked_by, locked_until, heartbeat_at | | аренда задачи воркером |
| last_error | text | для раздела «В работе» (по-русски и коротко) |
| dedupe_key | text null | уникальный частичный индекс `WHERE status IN ('queued','running')` — нельзя поставить два одинаковых разбора одного файла |
| created_at, started_at, finished_at | | |

Индексы: `(status, run_after, priority DESC, id)`, `(locked_until) WHERE status='running'`.

## Очередь: протокол

**Постановка — в той же транзакции, что и бизнес-запись:**

```sql
INSERT INTO files (...) VALUES (...);
INSERT INTO jobs (kind, payload, dedupe_key) VALUES ('process_file', '{"file_id": "..."}', 'process_file:<file_id>');
-- после COMMIT:
NOTIFY jobs_new;   -- или pg_notify в триггере на INSERT в jobs
```

**Захват задачи воркером:**

```sql
UPDATE jobs SET status='running', locked_by=:worker, locked_until=now() + interval '60 seconds',
               heartbeat_at=now(), attempts=attempts+1, started_at=now()
WHERE id = (
  SELECT id FROM jobs
  WHERE status='queued' AND run_after <= now()
  ORDER BY priority DESC, id
  FOR UPDATE SKIP LOCKED
  LIMIT 1)
RETURNING *;
```

**Воркер:**

- Один процесс держит `WORKER_CONCURRENCY` асинхронных слотов (по умолчанию 8). Реплик может быть несколько (`docker compose up --scale worker=2`).
- Ждёт `LISTEN jobs_new` с fallback-опросом раз в 2 секунды.
- Раз в 10 секунд продлевает `locked_until` у своих задач (heartbeat).
- На SIGTERM перестаёт брать новые задачи и ждёт текущие до 25 секунд. Незавершённые подберёт reaper.

**Reaper** — фоновая корутина в каждом воркере, раз в 15 секунд:

```sql
UPDATE jobs SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'queued' END,
               locked_by = NULL, run_after = now(), last_error = 'Обработчик перезапущен, задача возвращена в очередь'
WHERE status='running' AND locked_until < now()
RETURNING id, status;
```

Каждая возвращённая задача пишется в журнал (`job.requeued`). Задачи `dead` дают файлу статус `failed` с понятной причиной.

**Повторы:**

- Временная ошибка (модель или OCR недоступны, таймаут, 5xx, ответ не по схеме) → `status='queued'`, `run_after = now() + backoff` (`10s, 30s, 90s, 270s` + джиттер).
- Постоянная ошибка (файл повреждён, непригоден) → документ получает итоговый статус без повторов.

**Circuit breaker (P2):** при 5 подряд недоступностях модели воркер 60 секунд не берёт задачи `process_file`, и `attempts` не растут. Раздел «В работе» показывает баннер «Распознавание приостановлено: модель недоступна, задачи ждут».

**Идемпотентность.** Разбор файла пишет результаты по `(file_id, attempt)` и в конце одной транзакцией заменяет текущие поля документа. Повтор перезаписывает, а не дублирует.

## Статусы документа

```
           ┌──────────── дозагрузка / повторный разбор ─────────────┐
           ▼                                                         │
 missing ──► uploaded ──► processing ──► ready ─────────────────────► verified
 (нет файлов)  (ждёт)      (этап N/4)  ├► needs_review (есть замечания) ─┘
                                       ├► unreadable (причина + совет)
                                       └► failed (модель недоступна после повторов)
```

| Статус | Кто ставит | Соискатель видит | Оператор видит |
|---|---|---|---|
| `missing` | система | «Не загружен» | серый |
| `uploaded` | api | «Файл получен, ждёт разбора» | «Ждёт разбора» |
| `processing` | воркер | «Читаем документ (этап 2 из 4)» | этап и воркер |
| `ready` | воркер | «Разобран» | зелёный |
| `needs_review` | воркер | «Разобран, есть замечания: …» (только безопасные тексты) | список замечаний |
| `unreadable` | воркер | «Не удалось прочитать: размыто. Переснимите при хорошем свете без вспышки» | причина и метрики |
| `failed` | воркер | «Сервис распознавания временно недоступен. Мы повторим автоматически» | причина и кнопка «Разобрать повторно» |
| `verified` | оператор | «Проверен» | кто и когда |

**Документ из нескольких файлов.** Статус документа выводится из его файлов:

- хоть один файл `queued` или `processing` → `processing`;
- иначе поля сливаются со всех страниц, для каждого поля берётся лучшее по уверенности значение, и запускаются правила.

**Переходы.** Переходы разрешены только из таблицы `ALLOWED_TRANSITIONS` в `app/pipeline/merge.py`. Попытка недопустимого перехода (например, `ready` без записи `extraction_runs`) поднимает исключение и пишется в журнал.

**Статус заявки** вычисляется:

| Условие | Статус заявки |
|---|---|
| Есть документ в `processing` или `uploaded` | `processing` |
| Есть обязательный документ в `missing`, `unreadable`, `failed` или `needs_review` | `needs_attention` |
| Все обязательные документы `ready` или `verified` | `ready` |
| Все обязательные документы `verified` | `verified` |
