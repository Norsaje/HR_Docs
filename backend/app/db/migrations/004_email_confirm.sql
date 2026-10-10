-- Подтверждение email соискателя: письмо со ссылкой; в БД — только sha256 кода.
ALTER TABLE applications ADD COLUMN IF NOT EXISTS email_confirm_hash text;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS email_confirmed_at timestamptz;
