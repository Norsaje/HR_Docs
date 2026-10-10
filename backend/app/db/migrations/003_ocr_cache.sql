-- Кэш прохода B (olmOCR) по sha256 PNG страницы: повторный разбор не гоняет OCR заново.
-- Текст OCR — данные документа, живёт в той же БД внутри периметра, в технические логи не пишется.
CREATE TABLE IF NOT EXISTS ocr_cache (
    page_sha256 text PRIMARY KEY,
    markdown    text NOT NULL,
    provider    text NOT NULL,
    duration_ms integer NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE, DELETE ON ocr_cache TO hr_app;
