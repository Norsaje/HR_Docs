"""Вход операторов: BFF поверх Keycloak (OIDC Authorization Code + PKCE), без Authlib.

FastAPI — конфиденциальный клиент: код меняется на токены на сервере, браузер получает
только HttpOnly-cookie сессии. id_token проверяется по ключам JWKS (RS256, iss, aud, exp, nonce).
Роли — клиентские роли operator-bff из access_token, офисы — группы /offices/<slug> из id_token.
Сессия хранится в БД (только sha256 от её id), поэтому переживает перезапуск api.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.audit.log import audit
from app.auth.deps import COOKIE, Operator, csrf_guard, get_operator
from app.messages import AppError

router = APIRouter(prefix="/api/auth")
ROLES = {"doc-reader", "doc-editor", "supervisor"}
_discovery: dict[str, Any] = {}
_jwks: dict[str, jwt.PyJWKClient] = {}


async def discovery(request: Request) -> dict[str, Any]:
    """Discovery по внутреннему адресу: issuer и authorization_endpoint публичные, token и jwks — внутренние."""
    if not _discovery:
        s = request.app.state.settings
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{s.OIDC_INTERNAL_BASE}/.well-known/openid-configuration")
            r.raise_for_status()
            _discovery.update(r.json())
        if s.OIDC_TOKEN_ENDPOINT:
            _discovery["token_endpoint"] = s.OIDC_TOKEN_ENDPOINT
        if s.OIDC_JWKS_URI:
            _discovery["jwks_uri"] = s.OIDC_JWKS_URI
    return _discovery


def _verify(token: str, jwks_uri: str, issuer: str, audience: str | None) -> dict[str, Any]:
    client = _jwks.setdefault(jwks_uri, jwt.PyJWKClient(jwks_uri, cache_keys=True))
    key = client.get_signing_key_from_jwt(token).key
    opts = {"verify_aud": audience is not None}
    return jwt.decode(
        token, key, algorithms=["RS256"], issuer=issuer, audience=audience, options=opts, leeway=30
    )


@router.get("/login")
async def login(request: Request, return_to: str = "/operator/") -> RedirectResponse:
    s, pool = request.app.state.settings, request.app.state.pool
    d = await discovery(request)
    if not return_to.startswith("/operator"):
        return_to = "/operator/"  # только свой адрес: защита от открытого редиректа
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    await pool.execute("DELETE FROM auth_login_states WHERE created_at < now() - interval '10 minutes'")
    await pool.execute(
        "INSERT INTO auth_login_states (state, nonce, verifier, return_to) VALUES ($1,$2,$3,$4)",
        state,
        nonce,
        verifier,
        return_to,
    )
    params = {
        "client_id": s.OIDC_CLIENT_ID,
        "response_type": "code",
        "scope": "openid profile email",
        "redirect_uri": f"{s.PUBLIC_BASE_URL}/api/auth/callback",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return RedirectResponse(f"{d['authorization_endpoint']}?{urlencode(params)}", status_code=302)


@router.get("/callback")
async def callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    s, pool = request.app.state.settings, request.app.state.pool
    d = await discovery(request)
    row = await pool.fetchrow(
        "DELETE FROM auth_login_states WHERE state=$1 AND created_at > now() - interval '10 minutes' "
        "RETURNING *",
        state,
    )
    if not row or not code:
        raise AppError("UNAUTHORIZED", 401)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            d["token_endpoint"],
            auth=(s.OIDC_CLIENT_ID, s.KC_BFF_CLIENT_SECRET.get_secret_value()),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": row["verifier"],
                "redirect_uri": f"{s.PUBLIC_BASE_URL}/api/auth/callback",
            },
        )
    if r.status_code != 200:
        raise AppError("UNAUTHORIZED", 401)
    tokens = r.json()
    idt = await asyncio.to_thread(_verify, tokens["id_token"], d["jwks_uri"], s.OIDC_ISSUER, s.OIDC_CLIENT_ID)
    at = await asyncio.to_thread(_verify, tokens["access_token"], d["jwks_uri"], s.OIDC_ISSUER, None)
    if idt.get("nonce") != row["nonce"]:
        raise AppError("UNAUTHORIZED", 401)
    roles = sorted(set(at.get("resource_access", {}).get(s.OIDC_CLIENT_ID, {}).get("roles", [])) & ROLES)
    offices = sorted(g.split("/")[-1] for g in (idt.get("groups") or []) if g.startswith("/offices/"))
    name = idt.get("name") or idt.get("preferred_username") or "оператор"
    if not roles:
        await audit(
            pool, name, "auth.login_failed", f"Вход отклонён: у пользователя {name} нет роли оператора"
        )
        raise AppError("FORBIDDEN", 403)
    sid = secrets.token_urlsafe(32)
    await pool.execute(
        "INSERT INTO sessions (id_hash, sub, name, roles, offices, id_token) VALUES ($1,$2,$3,$4,$5,$6)",
        hashlib.sha256(sid.encode()).hexdigest(),
        idt["sub"],
        name,
        roles,
        offices,
        tokens["id_token"],
    )
    await audit(pool, name, "auth.login", f"Вход оператора {name} ({', '.join(roles)})")
    resp = RedirectResponse(row["return_to"], status_code=302)
    resp.set_cookie(
        COOKIE, sid, httponly=True, secure=True, samesite="lax", path="/", max_age=s.SESSION_ABS_HOURS * 3600
    )
    return resp


@router.get("/me")
async def me(op: Operator = Depends(get_operator)) -> dict[str, Any]:
    return {"name": op.name, "roles": op.roles, "offices": op.offices}


@router.post("/logout", dependencies=[Depends(csrf_guard)])
async def logout(request: Request, op: Operator = Depends(get_operator)) -> JSONResponse:
    s, pool = request.app.state.settings, request.app.state.pool
    id_token = await pool.fetchval(
        "DELETE FROM sessions WHERE id_hash=$1 RETURNING id_token", op.session_hash
    )
    await audit(pool, op.name, "auth.logout", f"Выход оператора {op.name}")
    d = await discovery(request)
    url = f"{d['end_session_endpoint']}?" + urlencode(
        {
            "id_token_hint": id_token or "",
            "post_logout_redirect_uri": f"{s.PUBLIC_BASE_URL}/operator/",
            "client_id": s.OIDC_CLIENT_ID,
            "t": int(time.time()),
        }
    )
    resp = JSONResponse({"logout_url": url})
    resp.delete_cookie(COOKIE, path="/", secure=True, httponly=True)
    return resp
