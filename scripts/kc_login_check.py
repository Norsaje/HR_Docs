"""Проверка Keycloak после импорта realm: вход тестового оператора через форму логина.

Проходит настоящий поток Authorization Code + PKCE (как BFF, но без него):
страница логина → POST формы → код → обмен кода с секретом клиента → роли и группы из токенов.
Успешный обмен доказывает, что плейсхолдеры ${KC_BFF_CLIENT_SECRET}, ${KC_DEMO_PASSWORD}
и ${PUBLIC_BASE_URL} подставились. Секреты читаются из .env и в вывод не попадают.
Запуск: cd backend && uv run python ../scripts/kc_login_check.py [username]
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if m:
            env[m.group(1)] = re.split(r"\s+#", m.group(2), maxsplit=1)[0].strip()
    return env


def jwt_claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def main() -> int:
    username = sys.argv[1] if len(sys.argv) > 1 else "editor.msk"
    env = read_env()
    base = env["PUBLIC_BASE_URL"]
    realm = f"{base}/kc/realms/hr/protocol/openid-connect"
    redirect_uri = f"{base}/api/auth/callback"
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    # verify=False — только для стенда с самоподписанным сертификатом (CADDY_TLS=internal).
    with httpx.Client(verify=False, follow_redirects=False, timeout=20) as c:
        r = c.get(
            f"{realm}/auth",
            params={
                "client_id": "operator-bff",
                "response_type": "code",
                "scope": "openid profile email",
                "redirect_uri": redirect_uri,
                "state": "s",
                "nonce": "n",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        if r.status_code != 200:
            print(f"FAIL: страница логина вернула {r.status_code} (redirect_uri не подставился?)")
            return 1
        action = re.search(r'action="([^"]+)"', r.text)
        if not action:
            print("FAIL: на странице нет формы логина")
            return 1
        r = c.post(
            action.group(1).replace("&amp;", "&"),
            data={"username": username, "password": env["KC_DEMO_PASSWORD"], "credentialId": ""},
        )
        location = r.headers.get("location", "")
        if r.status_code != 302 or not location.startswith(redirect_uri):
            print(f"FAIL: вход {username} не удался (HTTP {r.status_code}); пароль или обязательные действия")
            return 1
        code = parse_qs(urlparse(location).query)["code"][0]
        r = c.post(
            f"{realm}/token",
            auth=("operator-bff", env["KC_BFF_CLIENT_SECRET"]),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
        if r.status_code != 200:
            print(f"FAIL: обмен кода вернул {r.status_code} (секрет клиента не подставился?)")
            return 1
        tokens = r.json()
    idt, at = jwt_claims(tokens["id_token"]), jwt_claims(tokens["access_token"])
    roles = at.get("resource_access", {}).get("operator-bff", {}).get("roles", [])
    print(
        f"OK: {username} вошёл; iss={idt['iss']}; aud={idt['aud']}; "
        f"roles={roles}; groups={idt.get('groups')}; name={idt.get('name')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
