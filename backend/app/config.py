"""Конфигурация приложения из переменных окружения (pydantic-settings).

Все настройки — только env (`.env` на стенде, `.env.example` в репозитории).
При старте api и worker значения валидируются: если обязательной переменной нет,
процесс останавливается с понятным сообщением, а не падает позже в случайном месте.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_root() -> Path:
    """Ближайший каталог с registry/documents.yaml: корень репозитория локально или /app в образе."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "registry" / "documents.yaml").is_file():
            return parent
    return Path(__file__).resolve().parents[1]


APP_ROOT = _find_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=True)

    # Стенд
    PUBLIC_BASE_URL: str
    APP_TIMEZONE: str = "Europe/Moscow"
    APP_ENV: Literal["prod", "dev"] = "prod"
    DOCS_ENABLED: bool = False

    # PostgreSQL: приложение работает под hr_app, миграции — под hr_owner.
    DB_HOST: str = "postgres"
    DB_PORT: int = 5432
    DB_NAME: str = "hr"
    HR_OWNER_PASSWORD: SecretStr
    HR_APP_PASSWORD: SecretStr

    # S3
    S3_ENDPOINT: str = "http://seaweedfs:8333"
    S3_BUCKET: str = "hr-documents"
    S3_ACCESS_KEY: SecretStr
    S3_SECRET_KEY: SecretStr
    FILE_ENCRYPTION: Literal["on", "off"] = "on"
    FILE_ENC_MASTER_KEY: SecretStr | None = None

    # OIDC
    OIDC_ISSUER: str
    OIDC_INTERNAL_BASE: str = "http://keycloak:8080/kc/realms/hr"
    OIDC_CLIENT_ID: str = "operator-bff"
    KC_BFF_CLIENT_SECRET: SecretStr
    KC_ADMIN_CLIENT_SECRET: SecretStr = SecretStr("")  # раздел «Сотрудники» (клиент operator-admin)
    OIDC_TOKEN_ENDPOINT: str = ""
    OIDC_JWKS_URI: str = ""
    SESSION_ENC_KEY: SecretStr
    SESSION_IDLE_MIN: int = 30
    SESSION_ABS_HOURS: int = 10

    # Модель и OCR (используются только в app/llm)
    LLM_PROVIDER: str = "openai_compat"
    LLM_BASE_URL: str
    LLM_MODEL: str
    LLM_API_KEY: SecretStr = SecretStr("")
    LLM_TIMEOUT_S: float = 90
    LLM_MAX_CONCURRENCY: int = 6
    LLM_IMAGE_MAX_SIDE: int = 1600
    LLM_JSON_MODE: Literal["json_schema", "json_object", "prompt"] = "json_schema"
    PROMPT_EXTRACT_VERSION: str = "v1"
    PROMPT_ARBITER_VERSION: str = "v1"
    ARBITER_ENABLED: bool = True
    ARBITER_MAX_FIELDS: int = 6
    OCR_PROVIDER: Literal["olmocr_http", "none"] = "olmocr_http"
    OCR_BASE_URL: str = ""
    OCR_API_TOKEN: SecretStr = SecretStr("")
    OCR_TIMEOUT_S: float = 150
    OCR_MAX_CONCURRENCY: int = 4
    OCR_VOTES_CYRILLIC: bool = True

    # Приём файлов
    MAX_FILE_MB: int = 20
    MAX_FILES_PER_REQUEST: int = 20
    MAX_FILES_PER_APPLICATION: int = 40
    MAX_APPLICATION_MB: int = 200
    MAX_PIXELS: int = 40_000_000
    MAX_PDF_PAGES: int = 10
    MIN_IMAGE_SIDE: int = 600
    NORMALIZE_MAX_SIDE: int = 2400

    # Воркер и очередь
    WORKER_CONCURRENCY: int = 8
    JOB_MAX_ATTEMPTS: int = 5
    JOB_LEASE_S: int = 60
    JOB_HEARTBEAT_S: int = 10
    REAPER_INTERVAL_S: int = 15

    # Правила, соискатель, почта
    EXPIRY_WARNING_DAYS: int = 30
    NAME_MATCH_THRESHOLD: float = 0.92
    APPLICANT_TOKEN_TTL_DAYS: int = 30
    CONSENT_VERSION: str = "v1"
    POLICY_VERSION: str = "v1"
    SMTP_HOST: str = "mailpit"
    SMTP_PORT: int = 1025
    MAIL_FROM: str = "noreply@hr-agency.local"

    @model_validator(mode="before")
    @classmethod
    def _drop_comment_values(cls, data: Any) -> Any:
        # docker compose env_file не вырезает комментарий у ПУСТОЙ переменной: «X=   # пояснение»
        # приходит как значение «# пояснение». Такие значения считаем пустыми.
        if isinstance(data, dict):
            return {
                k: ("" if isinstance(v, str) and v.strip().startswith("#") else v) for k, v in data.items()
            }
        return data

    # Пути к реестру и промптам (в образе — /app/registry, /app/prompts)
    REGISTRY_PATH: Path = Field(default=APP_ROOT / "registry" / "documents.yaml")
    PROMPTS_DIR: Path = Field(default=APP_ROOT / "prompts")


@lru_cache
def get_settings() -> Settings:
    """Прочитать и проверить настройки один раз на процесс."""
    try:
        return Settings()  # значения приходят из env
    except ValidationError as e:
        missing = [str(err["loc"][0]) for err in e.errors() if err["type"] == "missing"]
        invalid = [f"{err['loc'][0]}: {err['msg']}" for err in e.errors() if err["type"] != "missing"]
        lines = ["Ошибка конфигурации: проверьте .env (см. .env.example, `make secrets`)."]
        if missing:
            lines.append("Не заданы: " + ", ".join(missing))
        if invalid:
            lines.append("Неверные значения: " + "; ".join(invalid))
        print("\n".join(lines), file=sys.stderr)
        raise SystemExit(2) from None
