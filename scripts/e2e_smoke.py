"""Сквозная проверка стенда (`make e2e-smoke`): соискатель → очередь → разбор моделью → оператор.

1. Создать заявку (страна RU, согласие), загрузить файл паспорта, повторно — получить 409 «уже загружен»;
   без согласия — 422. 2. Отправить комплект, опрашивать статус до финального.
3. Войти оператором через настоящую форму Keycloak (BFF), открыть список и карточку, проверить поля.
4. Без cookie операторский API отдаёт 401. Файл берётся первым подходящим из data/open (или аргументом).
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def env() -> dict[str, str]:
    out = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if m:
            out[m.group(1)] = re.split(r"\s+#", m.group(2), maxsplit=1)[0].strip()
    return out


def ok(cond: bool, text: str) -> None:
    print(("OK   " if cond else "FAIL ") + text)
    if not cond:
        sys.exit(1)


def main() -> int:
    e = env()
    base = e["PUBLIC_BASE_URL"]
    sample = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else next((ROOT / "data" / "open").rglob("Паспорт РФ К*.jpg"))
    )
    c = httpx.Client(base_url=base, verify=False, timeout=30)  # стенд с самоподписанным сертификатом

    r = c.post(
        "/api/public/applications",
        json={
            "country_code": "RU",
            "full_name": "Смоук Тест",
            "email": "smoke@example.test",
            "consent_accepted": False,
        },
    )
    ok(
        r.status_code == 422 and "соглас" in r.json()["error"]["message"],
        "без согласия заявка не создаётся (422)",
    )
    r = c.post(
        "/api/public/applications",
        json={
            "country_code": "RU",
            "full_name": "Смоук Тест",
            "email": "smoke@example.test",
            "consent_accepted": True,
        },
    )
    ok(r.status_code == 201, f"заявка создана: {r.json().get('public_no')}")
    app = r.json()
    h = {"X-Application-Token": app["token"]}
    files = {"files": (sample.name, sample.read_bytes(), "image/jpeg")}
    r = c.post(
        f"/api/public/applications/{app['application_id']}/files",
        data={"doc_type": "ru_passport"},
        files=files,
        headers=h,
    )
    ok(r.status_code == 200 and r.json()["results"][0]["accepted"], "файл паспорта принят")
    r = c.post(
        f"/api/public/applications/{app['application_id']}/files",
        data={"doc_type": "ru_passport"},
        files=files,
        headers=h,
    )
    ok(r.status_code == 409, f"повторная загрузка того же файла → 409: {r.json()['error']['message']}")
    fake = {"files": ("photo.jpg", b"MZ\x90\x00" + b"\x00" * 2000, "image/jpeg")}
    r = c.post(
        f"/api/public/applications/{app['application_id']}/files",
        data={"doc_type": "ru_passport"},
        files=fake,
        headers=h,
    )
    ok(
        not r.json()["results"][0]["accepted"],
        f"переименованный exe отклонён: {r.json()['results'][0]['error']['message']}",
    )
    t0 = time.monotonic()
    r = c.post(f"/api/public/applications/{app['application_id']}/submit", headers=h)
    ok(
        r.status_code == 200 and time.monotonic() - t0 < 2,
        f"отправка мгновенная, не хватает: {r.json()['missing']}",
    )
    ok(
        c.get(f"/api/public/applications/{app['application_id']}").status_code == 401,
        "статус без токена → 401",
    )

    stages = set()
    for _ in range(90):
        st = c.get(f"/api/public/applications/{app['application_id']}", headers=h).json()
        doc = next(d for d in st["documents"] if d["doc_type"] == "ru_passport")
        stages.add(f"{doc['status']}/{doc['stage']}")
        if doc["status"] in ("ready", "needs_review", "unreadable", "failed"):
            break
        time.sleep(2)
    ok(
        doc["status"] in ("ready", "needs_review"),
        f"паспорт разобран: {doc['status']}; стадии: {sorted(stages)}",
    )
    ok("value" not in str(st) and "fields" not in st, "публичный ответ не содержит значений полей")

    ok(
        httpx.get(f"{base}/api/operator/applications", verify=False).status_code == 401,
        "оператор без cookie → 401",
    )
    op = httpx.Client(base_url=base, verify=False, timeout=30, follow_redirects=False)
    r = op.get("/api/auth/login?return_to=/operator/")
    r = op.get(r.headers["location"])
    action = re.search(r'action="([^"]+)"', r.text).group(1).replace("&amp;", "&")
    r = op.post(
        action, data={"username": "editor.msk", "password": e["KC_DEMO_PASSWORD"], "credentialId": ""}
    )
    r = op.get(r.headers["location"])  # callback BFF → cookie сессии
    ok(
        r.status_code == 302 and any(k.startswith("__Host-Http") for k in op.cookies),
        "вход editor.msk через Keycloak, cookie выдана",
    )
    me = op.get("/api/auth/me").json()
    ok("doc-editor" in me["roles"], f"оператор: {me['name']}, роли {me['roles']}")
    items = op.get("/api/operator/applications").json()["items"]
    ok(any(i["id"] == app["application_id"] for i in items), "заявка в списке оператора")
    card = op.get(f"/api/operator/applications/{app['application_id']}").json()
    fields = card["documents"][0]["fields"]
    colors = {f["confidence"] for f in fields}
    ok(len(fields) > 5, f"в карточке {len(fields)} полей, цвета: {sorted(colors)}")
    ok(
        op.get(f"/api/operator/files/{fields[0]['file_id']}/pages/1").headers["content-type"] == "image/png",
        "страница файла отдаётся с сессией",
    )
    ok(
        httpx.get(f"{base}/api/operator/files/{fields[0]['file_id']}/pages/1", verify=False).status_code
        == 401,
        "файл без сессии → 401",
    )
    f0 = next(f for f in fields if f["key"] == "birth_place")
    r = op.patch(
        f"/api/operator/fields/{f0['id']}",
        json={"value": "ПРАВКА СМОУК"},
        headers={"X-Requested-With": "fetch"},
    )
    ok(r.status_code == 200, "правка поля оператором")
    hist = op.get(f"/api/operator/applications/{app['application_id']}/history").json()["items"]
    ok(any(i["action"] == "field.edited" for i in hist), f"в истории {len(hist)} событий, правка записана")
    q = op.get("/api/operator/queue").json()
    ok(
        "waiting" in q,
        f"«В работе»: ждёт {q['waiting']}, за час {q['done_last_hour']}, среднее {q['avg_duration_s']} с",
    )
    print("e2e-smoke: всё зелёное")
    return 0


if __name__ == "__main__":
    sys.exit(main())
