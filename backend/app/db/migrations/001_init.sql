-- Схема БД: заявки, документы, файлы, поля, замечания, журнал аудита, очередь задач, сессии операторов.
-- Применяется при старте api под ролью hr_owner (app/db/migrate.py). Права hr_app — в конце файла:
-- на audit_events только SELECT и INSERT, журнал нельзя ни изменить, ни удалить.

CREATE TABLE applications (
    id            uuid PRIMARY KEY,
    public_no     text UNIQUE NOT NULL,
    office_slug   text NOT NULL,
    country_code  text NOT NULL,
    full_name     text NOT NULL,
    email         text NOT NULL,
    token_hash    text NOT NULL,
    token_expires timestamptz NOT NULL,
    consent_version text NOT NULL,
    consent_at    timestamptz NOT NULL,
    consent_ip    text,
    status        text NOT NULL DEFAULT 'draft',   -- draft | submitted | processing | ready | needs_review
    created_at    timestamptz NOT NULL DEFAULT now(),
    submitted_at  timestamptz,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE documents (
    id             uuid PRIMARY KEY,
    application_id uuid NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    doc_type       text NOT NULL,
    status         text NOT NULL DEFAULT 'uploaded', -- uploaded | processing | ready | needs_review | unreadable | failed
    stage          text,
    message_ru     text,
    expires_on     date,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (application_id, doc_type)
);

CREATE TABLE files (
    id             uuid PRIMARY KEY,
    application_id uuid NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    document_id    uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    original_name  text NOT NULL,                  -- только для показа человеку, в пайплайн не передаётся
    mime           text NOT NULL,
    size_bytes     bigint NOT NULL,
    sha256         text NOT NULL,
    s3_key         text NOT NULL,
    page_key       text,                           -- нормализованная страница PNG в S3
    status         text NOT NULL DEFAULT 'queued',  -- queued | processing | done | unreadable | failed
    stage          text NOT NULL DEFAULT 'queued',
    error_message_ru text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (application_id, sha256)
);

CREATE TABLE extraction_runs (
    id          bigserial PRIMARY KEY,
    file_id     uuid NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    provider    text NOT NULL,
    base_url_host text NOT NULL,
    model       text NOT NULL,
    prompt_version text NOT NULL,
    schema_hash text NOT NULL,
    duration_ms integer NOT NULL,
    tokens_in   integer,
    tokens_out  integer,
    doc_type_detected text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE fields (
    id          uuid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    file_id     uuid REFERENCES files(id) ON DELETE SET NULL,
    key         text NOT NULL,
    value       text,
    raw         text,
    status      text NOT NULL,                     -- ok | unreadable | absent
    kind        text,
    bbox        jsonb,
    confidence  text NOT NULL,                     -- green | yellow | red | grey
    signals     jsonb NOT NULL DEFAULT '[]',
    source      text NOT NULL DEFAULT 'model',     -- model | operator
    edited_by   text,
    edited_at   timestamptz,
    UNIQUE (document_id, key)
);

CREATE TABLE issues (
    id          bigserial PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    field_key   text,
    rule_id     text,
    severity    text NOT NULL,                     -- error | warning | info
    message     text NOT NULL,
    applicant_message text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_events (
    id             bigserial PRIMARY KEY,
    at             timestamptz NOT NULL DEFAULT now(),
    actor          text NOT NULL,                  -- «соискатель», «Олег Правкин», «worker-1», «система»
    action         text NOT NULL,                  -- file.accepted, file.rejected, parse.started, field.edited ...
    application_id uuid,
    object_id      text,
    message_ru     text NOT NULL,
    details        jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX audit_events_app ON audit_events (application_id, at);

CREATE TABLE jobs (
    id           bigserial PRIMARY KEY,
    kind         text NOT NULL,                    -- process_file
    payload      jsonb NOT NULL,
    status       text NOT NULL DEFAULT 'queued',   -- queued | running | done | failed
    attempts     integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 5,
    run_after    timestamptz NOT NULL DEFAULT now(),
    locked_by    text,
    locked_until timestamptz,
    stage        text,
    last_error   text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz
);
CREATE INDEX jobs_pick ON jobs (status, run_after, id);

CREATE TABLE sessions (
    id_hash     text PRIMARY KEY,                  -- sha256(session_id); сам id есть только в cookie
    sub         text NOT NULL,
    name        text NOT NULL,
    roles       text[] NOT NULL,
    offices     text[] NOT NULL,
    id_token    text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE auth_login_states (
    state       text PRIMARY KEY,
    nonce       text NOT NULL,
    verifier    text NOT NULL,
    return_to   text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT, UPDATE, DELETE ON applications, documents, files, extraction_runs, fields, issues,
    jobs, sessions, auth_login_states TO hr_app;
GRANT SELECT, INSERT ON audit_events TO hr_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO hr_app;
