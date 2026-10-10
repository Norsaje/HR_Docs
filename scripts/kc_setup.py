"""Настройка Keycloak на работающем стенде через Admin API (realm уже импортирован, повторный импорт не нужен).

  make kc-user EMAIL=<email> FIRST=<Имя> LAST=<Фамилия> ROLE=supervisor [OFFICE=moscow]
      — завести (или обновить) сотрудника: вход по email, пароль = KC_DEMO_PASSWORD из .env.
  make kc-google
      — включить «Войти через Google» (кнопка на форме входа), если в .env заданы
        GOOGLE_CLIENT_ID и GOOGLE_CLIENT_SECRET. Существующий сотрудник с тем же email
        привязывает Google к своей учётке при первом входе; новому роль выдаёт руководитель.
Админ-доступ — KC_BOOTSTRAP_ADMIN_* из .env через 127.0.0.1:8081 (наружу админка не открыта).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ADMIN_BASE = "http://127.0.0.1:8081/kc"


def env() -> dict[str, str]:
    out = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if m:
            v = re.split(r"\s+#", m.group(2), maxsplit=1)[0].strip()
            out[m.group(1)] = "" if v.startswith("#") else v
    return out


def admin_client(e: dict[str, str]) -> httpx.Client:
    r = httpx.post(f"{ADMIN_BASE}/realms/master/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "admin-cli",
        "username": e["KC_BOOTSTRAP_ADMIN_USERNAME"], "password": e["KC_BOOTSTRAP_ADMIN_PASSWORD"]}, timeout=15)
    r.raise_for_status()
    return httpx.Client(base_url=f"{ADMIN_BASE}/admin/realms/hr", timeout=15,
                        headers={"Authorization": f"Bearer {r.json()['access_token']}"})


def ensure_user(e: dict[str, str], email: str, first: str, last: str, role: str, office: str) -> None:
    c = admin_client(e)
    users = c.get("/users", params={"email": email, "exact": "true"}).json()
    body = {"username": email, "email": email, "firstName": first, "lastName": last,
            "emailVerified": True, "enabled": True, "requiredActions": []}
    if users:
        uid = users[0]["id"]
        c.put(f"/users/{uid}", json={k: v for k, v in body.items() if k != "username"}).raise_for_status()
        print("сотрудник уже был — обновлён")
    else:
        r = c.post("/users", json=body)
        r.raise_for_status()
        uid = r.headers["location"].rsplit("/", 1)[-1]
        print("сотрудник создан")
    c.put(f"/users/{uid}/reset-password",
          json={"type": "password", "value": e["KC_DEMO_PASSWORD"], "temporary": False}).raise_for_status()
    cid = c.get("/clients", params={"clientId": "operator-bff"}).json()[0]["id"]
    current = c.get(f"/users/{uid}/role-mappings/clients/{cid}").json()
    if current:
        c.request("DELETE", f"/users/{uid}/role-mappings/clients/{cid}", json=current)
    c.post(f"/users/{uid}/role-mappings/clients/{cid}", json=[c.get(f"/clients/{cid}/roles/{role}").json()]).raise_for_status()
    if office:
        group = next(g for g in c.get("/groups", params={"search": office}).json()[0]["subGroups"] if g["name"] == office)
        c.put(f"/users/{uid}/groups/{group['id']}").raise_for_status()
    print(f"роль {role}, офис {office or '—'}; пароль — значение KC_DEMO_PASSWORD из .env")


def ensure_google(e: dict[str, str]) -> int:
    cid, secret = e.get("GOOGLE_CLIENT_ID", ""), e.get("GOOGLE_CLIENT_SECRET", "")
    if not cid or not secret:
        print("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET в .env не заданы.\n"
              "Google Cloud Console → APIs & Services → Credentials → OAuth client ID (Web application),\n"
              f"Authorized redirect URI: {e['PUBLIC_BASE_URL']}/kc/realms/hr/broker/google/endpoint\n"
              "Впишите оба значения в .env и повторите: make kc-google")
        return 1
    c = admin_client(e)
    idp = {"alias": "google", "providerId": "google", "displayName": "Google", "enabled": True,
           "trustEmail": True, "firstBrokerLoginFlowAlias": "first broker login",
           "config": {"clientId": cid, "clientSecret": secret, "syncMode": "IMPORT", "defaultScope": "openid profile email"}}
    exists = c.get("/identity-provider/instances/google").status_code == 200
    r = c.put("/identity-provider/instances/google", json=idp) if exists else c.post("/identity-provider/instances", json=idp)
    r.raise_for_status()
    print("«Войти через Google» включён на форме входа операторов")
    return 0


def main() -> int:
    e = env()
    if len(sys.argv) >= 2 and sys.argv[1] == "google":
        return ensure_google(e)
    if len(sys.argv) >= 6 and sys.argv[1] == "user":
        _, _, email, first, last, role, *rest = sys.argv
        ensure_user(e, email, first, last, role, rest[0] if rest else "")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
