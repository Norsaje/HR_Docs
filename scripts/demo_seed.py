"""Демо-данные через настоящий API и настоящую модель (`make demo-seed`), без вставки в БД.

Создаёт три заявки — Россия (паспорт, СНИЛС, ИНН), Узбекистан (патент), Беларусь (паспорт) —
загружает файлы из data/test (сначала `make test-files`), отправляет и печатает ссылки соискателя.
Разбор идёт в фоне: через 1–2 минуты заявки готовы в рабочем месте оператора.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import mailpit_code

ROOT = Path(__file__).resolve().parent.parent
TEST = ROOT / "data" / "test"
SEED = [
    ("RU", "Демо Россия Тестовый", {"ru_passport": "*_ru_passport_1.*", "snils": "*_snils_1.*", "inn": "*_inn_1.*"}),
    ("UZ", "Демо Узбекистан Тестовый", {"work_patent": "*_work_patent_front_1.*"}),
    ("BY", "Демо Беларусь Тестовый", {"by_passport": "*_by_passport_1.*"}),
]


def main() -> int:
    if not TEST.exists():
        print("Сначала: make test-files")
        return 1
    base = "https://localhost"
    c = httpx.Client(base_url=base, verify=False, timeout=60)  # стенд с самоподписанным сертификатом
    for country, name, files in SEED:
        a = c.post("/api/public/applications", json={"country_code": country, "full_name": name,
                                                     "email": "demo@example.test", "consent_accepted": True}).json()
        h = {"X-Application-Token": a["token"]}
        mailpit_code.confirm(c, a)  # код подтверждения из письма в Mailpit
        for doc_type, pattern in files.items():
            path = next(TEST.glob(pattern), None)
            if path is None:
                continue
            mime = "application/pdf" if path.suffix == ".pdf" else "image/jpeg"
            r = c.post(f"/api/public/applications/{a['application_id']}/files", data={"doc_type": doc_type},
                       files={"files": (path.name, path.read_bytes(), mime)}, headers=h)
            res = r.json()["results"][0] if r.status_code == 200 else {"accepted": False}
            print(f"  {a['public_no']} {doc_type}: {'принят' if res['accepted'] else 'отклонён'} ({path.name})")
        c.post(f"/api/public/applications/{a['application_id']}/submit", headers=h)
        print(f"{a['public_no']} ({country}) — ссылка соискателя: {base}/#/a/{a['application_id']}/{a['token']}")
    print("Готово. Через 1–2 минуты заявки разобраны: https://localhost/operator/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
