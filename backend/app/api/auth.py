"""Sign-in endpoints for the optional console password (see app/auth.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import auth
from app.container import Container

from .deps import get_container

router = APIRouter(tags=["auth"])


class LoginBody(BaseModel):
    password: str = Field(..., min_length=1, max_length=200)


class PasswordBody(BaseModel):
    current: str = Field("", max_length=200)
    new: str = Field("", max_length=200)


def _set_cookie(response: Response, request: Request, c: Container) -> None:
    response.set_cookie(auth.COOKIE, auth.make_session(c.config), max_age=auth.SESSION_DAYS * 86400,
                        httponly=True, samesite="lax", secure=auth.is_https(request.scope), path="/")


@router.get("/api/auth/status", summary="Is a password set, and is this device signed in?")
async def status(request: Request, c: Container = Depends(get_container)):
    enabled = bool(c.settings.APP_PASSWORD_HASH)
    return {"enabled": enabled, "local": auth.is_local(request.scope),
            "signedIn": auth.signed_in(c.config, request.scope),
            # without a password: how many other devices used the console in the last 7 days (for the reminder)
            "remoteDevices": 0 if enabled else auth.remote_devices()}


@router.post("/api/auth/login", summary="Sign in this device")
async def login(body: LoginBody, request: Request, response: Response, c: Container = Depends(get_container)):
    who = auth.client_id(request.scope)
    if auth.too_many_failures(who):
        raise HTTPException(429, "Too many wrong passwords — wait a few minutes and try again")
    stored = c.settings.APP_PASSWORD_HASH
    if not stored or not auth.verify_password(body.password, stored):
        auth.record_failure(who)
        raise HTTPException(401, "Wrong password")
    auth.clear_failures(who)
    _set_cookie(response, request, c)
    return {"ok": True}


@router.post("/api/auth/logout", summary="Sign out this device")
async def logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.put("/api/auth/password", summary="Set, change or remove the console password")
async def set_password(body: PasswordBody, request: Request, response: Response, c: Container = Depends(get_container)):
    stored = c.settings.APP_PASSWORD_HASH
    # Changing or removing needs the current password, unless you are on this computer.
    if stored and not auth.is_local(request.scope) and not auth.verify_password(body.current, stored):
        auth.record_failure(auth.client_id(request.scope))
        raise HTTPException(403, "The current password is wrong")
    if body.new:
        if len(body.new) < 6:
            raise HTTPException(400, "Use at least 6 characters")
        c.config.update({"APP_PASSWORD_HASH": auth.hash_password(body.new)})
        _set_cookie(response, request, c)  # keep this device signed in (all others are signed out)
        return {"ok": True, "enabled": True}
    c.config.update({"CLEAR_APP_PASSWORD_HASH": True})
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True, "enabled": False}
