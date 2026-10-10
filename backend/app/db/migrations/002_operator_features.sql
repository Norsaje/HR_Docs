-- Этап 2: подсказки и варианты поля, отметка «проверено», письма соискателю.

ALTER TABLE fields ADD COLUMN IF NOT EXISTS candidates jsonb NOT NULL DEFAULT '{}';
ALTER TABLE fields ADD COLUMN IF NOT EXISTS hint text;

ALTER TABLE documents ADD COLUMN IF NOT EXISTS verified_by text;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS verified_at timestamptz;

-- Письма соискателю: не чаще раза в NOTIFY_MIN_INTERVAL_MIN для автоматических уведомлений.
CREATE TABLE IF NOT EXISTS notifications (
    id             bigserial PRIMARY KEY,
    application_id uuid NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    kind           text NOT NULL,          -- link | missing | request
    subject        text NOT NULL,
    status         text NOT NULL,          -- sent | failed
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS notifications_app ON notifications (application_id, created_at);

GRANT SELECT, INSERT, UPDATE, DELETE ON notifications TO hr_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO hr_app;
