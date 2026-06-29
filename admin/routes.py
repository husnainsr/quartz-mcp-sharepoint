from __future__ import annotations

import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel

import db

router = APIRouter(prefix="/admin/api", tags=["admin"])

COOKIE_NAME = "quartz_admin"
TOKEN_TTL_HOURS = 12


def _jwt_secret() -> str:
    secret = os.environ.get("ADMIN_JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError("ADMIN_JWT_SECRET must be set to run the admin panel")
    return secret


def _issue_token(username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": username, "iat": now, "exp": now + timedelta(hours=TOKEN_TTL_HOURS)}
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def _require_admin(session: str | None) -> str:
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(session, _jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    return payload.get("sub", "admin")


class LoginBody(BaseModel):
    username: str
    password: str


class KeyBody(BaseModel):
    label: str = ""


class ServerBody(BaseModel):
    enabled: bool


@router.post("/login")
def login(body: LoginBody, response: Response):
    expected_user = os.environ.get("ADMIN_USERNAME", "").strip()
    expected_pass = os.environ.get("ADMIN_PASSWORD", "")
    if not expected_user or not expected_pass:
        raise HTTPException(status_code=500, detail="Admin credentials not configured")
    ok_user = hmac.compare_digest(body.username, expected_user)
    ok_pass = hmac.compare_digest(body.password, expected_pass)
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = _issue_token(body.username)
    response.set_cookie(
        COOKIE_NAME, token, httponly=True, samesite="lax",
        max_age=TOKEN_TTL_HOURS * 3600, path="/",
    )
    return {"ok": True, "username": body.username}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(quartz_admin: str | None = Cookie(default=None)):
    return {"username": _require_admin(quartz_admin)}


@router.get("/keys")
def get_keys(quartz_admin: str | None = Cookie(default=None)):
    _require_admin(quartz_admin)
    return {"keys": db.list_keys()}


@router.post("/keys")
def post_key(body: KeyBody, quartz_admin: str | None = Cookie(default=None)):
    _require_admin(quartz_admin)
    return db.create_key(body.label)


@router.post("/keys/{key_id}/revoke")
def post_revoke(key_id: int, quartz_admin: str | None = Cookie(default=None)):
    _require_admin(quartz_admin)
    if not db.revoke_key(key_id):
        raise HTTPException(status_code=404, detail="Key not found")
    return {"ok": True}


@router.delete("/keys/{key_id}")
def delete_key(key_id: int, quartz_admin: str | None = Cookie(default=None)):
    _require_admin(quartz_admin)
    if not db.delete_key(key_id):
        raise HTTPException(status_code=404, detail="Key not found")
    return {"ok": True}


@router.get("/server")
def get_server(quartz_admin: str | None = Cookie(default=None)):
    _require_admin(quartz_admin)
    return {"enabled": db.is_mcp_enabled()}


@router.post("/server")
def post_server(body: ServerBody, quartz_admin: str | None = Cookie(default=None)):
    _require_admin(quartz_admin)
    db.set_mcp_enabled(body.enabled)
    return {"enabled": db.is_mcp_enabled()}
