"""Общие фикстуры тестов: тестовые значения env, чтобы конфиг проходил проверку без .env."""

import base64
import os

_TEST_ENV = {
    "PUBLIC_BASE_URL": "https://localhost",
    "HR_OWNER_PASSWORD": "test",
    "HR_APP_PASSWORD": "test",
    "S3_ACCESS_KEY": "test",
    "S3_SECRET_KEY": "test",
    "OIDC_ISSUER": "https://localhost/kc/realms/hr",
    "KC_BFF_CLIENT_SECRET": "test",
    # Ключ Fernet строим в коде: литерал с высокой энтропией gitleaks принял бы за секрет.
    "SESSION_ENC_KEY": base64.urlsafe_b64encode(b"t" * 32).decode(),
    "LLM_BASE_URL": "http://llm.test/v1",
    "LLM_MODEL": "test-model",
    "OCR_BASE_URL": "http://ocr.test",
}
for key, value in _TEST_ENV.items():
    os.environ.setdefault(key, value)
