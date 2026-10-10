"""Код подтверждения email из Mailpit — для тестовых скриптов (e2e-smoke, open-set, demo-seed).

Скрипты работают как настоящий соискатель: создают заявку, берут код из письма в Mailpit
(UI и API Mailpit доступны только на 127.0.0.1 стенда) и подтверждают email через API.
"""

from __future__ import annotations

import re
import time

import httpx

MAILPIT = "http://127.0.0.1:8025"


def code_for(public_no: str, timeout_s: float = 20) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        found = httpx.get(f"{MAILPIT}/api/v1/search", params={"query": f'subject:"{public_no}"'}, timeout=10).json()
        for msg in found.get("messages", []):
            if "Код подтверждения" in msg["Subject"]:
                text = httpx.get(f"{MAILPIT}/api/v1/message/{msg['ID']}", timeout=10).json()["Text"]
                m = re.search(rf"{re.escape(public_no)}: (\d{{6}})", text)
                if m:
                    return m.group(1)
        time.sleep(1)
    raise RuntimeError(f"письмо с кодом для {public_no} не пришло в Mailpit")


def confirm(client: httpx.Client, app: dict) -> None:
    """Подтвердить email заявки кодом из письма (как сделал бы соискатель)."""
    code = code_for(app["public_no"])
    r = client.post(f"/api/public/applications/{app['application_id']}/confirm-email", json={"code": code},
                    headers={"X-Application-Token": app["token"]})
    r.raise_for_status()
