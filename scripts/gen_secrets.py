"""Заполняет пустые секреты в .env случайными значениями (цель `make secrets`).

Существующие значения не трогает, сами секреты в вывод не печатает — только имена.
Если .env нет, создаёт его из .env.example; переменные, которых нет в .env,
дописывает из .env.example. Только стандартная библиотека, чтобы работать до `uv sync`.
"""

from __future__ import annotations

import base64
import re
import secrets
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"


def _hex(n: int = 24) -> str:
    return secrets.token_hex(n)


def _b64key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def _fernet() -> str:
    # Ключ Fernet — 32 байта в urlsafe-base64; генерируем без зависимости от cryptography.
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def _demo_password() -> str:
    # Судьи вводят его руками: только буквы и цифры, 16 символов (политика realm — от 12).
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(16))


GENERATORS = {
    "POSTGRES_PASSWORD": _hex,
    "HR_OWNER_PASSWORD": _hex,
    "HR_APP_PASSWORD": _hex,
    "KC_DB_PASSWORD": _hex,
    "S3_ACCESS_KEY": lambda: _hex(10),
    "S3_SECRET_KEY": _hex,
    "FILE_ENC_MASTER_KEY": _b64key,
    "KC_BOOTSTRAP_ADMIN_USERNAME": lambda: "kcadmin",
    "KC_BOOTSTRAP_ADMIN_PASSWORD": _hex,
    "KC_BFF_CLIENT_SECRET": _hex,
    "KC_ADMIN_CLIENT_SECRET": _hex,
    "KC_DEMO_PASSWORD": _demo_password,
    "SESSION_ENC_KEY": _fernet,
    "VLLM_API_KEY": _hex,
}

# Не секреты, а разумные значения по умолчанию для стенда на https://localhost.
DEFAULTS = {
    "PUBLIC_BASE_URL": "https://localhost",
    "SITE_ADDRESS": "localhost",
    "MAIL_FROM": "noreply@hr-agency.local",
}

LINE = re.compile(r"^([A-Z0-9_]+)=(.*)$")


def _value(rest: str) -> str:
    """Значение без хвостового комментария «  # ...»."""
    return re.split(r"\s+#", rest, maxsplit=1)[0].strip()


def main() -> int:
    if not ENV.exists():
        shutil.copyfile(EXAMPLE, ENV)
        print("Создан .env из .env.example")

    lines = ENV.read_text(encoding="utf-8").splitlines()
    present: dict[str, str] = {}
    for line in lines:
        m = LINE.match(line)
        if m:
            present[m.group(1)] = _value(m.group(2))

    # Дописать переменные, появившиеся в .env.example позже.
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = LINE.match(line)
        if m and m.group(1) not in present:
            lines.append(line)
            present[m.group(1)] = _value(m.group(2))
            print(f"Добавлена переменная {m.group(1)}")

    base_url = present.get("PUBLIC_BASE_URL") or DEFAULTS["PUBLIC_BASE_URL"]
    fill = {**DEFAULTS, "OIDC_ISSUER": f"{base_url}/kc/realms/hr"}
    filled: list[str] = []
    out: list[str] = []
    for line in lines:
        m = LINE.match(line)
        if m and not _value(m.group(2)):
            key = m.group(1)
            new = GENERATORS[key]() if key in GENERATORS else fill.get(key)
            if new:
                line = f"{key}={new}"
                filled.append(key)
        out.append(line)

    ENV.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    try:
        ENV.chmod(0o600)
    except OSError:
        pass
    print("Заполнены: " + (", ".join(filled) if filled else "ничего, всё уже задано"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
