-- Подтверждение email кодом из письма: счётчик попыток и время отправки (повтор не чаще раза в минуту).
ALTER TABLE applications ADD COLUMN IF NOT EXISTS email_confirm_attempts integer NOT NULL DEFAULT 0;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS email_code_sent_at timestamptz;
-- Заявки, созданные до появления обязательного кода, считаем подтверждёнными, чтобы не заблокировать их.
UPDATE applications SET email_confirmed_at = created_at WHERE email_confirmed_at IS NULL;
