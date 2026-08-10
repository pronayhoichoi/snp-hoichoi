"""Microsoft Entra ID (Azure AD) OAuth 2.0 Authorization Code Flow.

Uses the `msal` library (Microsoft's official Python SDK).

Flow:
1.  User clicks "Sign in with Microsoft" -> GET /auth/microsoft/login
2.  We build the Microsoft authorize URL, store a CSRF state + PKCE flow
    dict in the session cookie, redirect to Microsoft.
3.  User signs in at login.microsoftonline.com/<tenant> and consents.
4.  Microsoft redirects to /auth/microsoft/callback?code=...&state=...
5.  We exchange the code for tokens, verify the id_token, look up (or
    auto-create) the User row by email, and set our own app session cookie.
"""
from __future__ import annotations

import base64
import json
import logging
import secrets

import msal
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeSerializer, BadSignature

from app.auth import (
    SESSION_COOKIE,
    hash_password,
    make_session,
)
from app.config import settings
from app.db import SessionLocal
from app.models.models import User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/microsoft", tags=["auth"])

# Signed cookie to carry the msal auth flow dict (contains PKCE verifier +
# state) between /login and /callback. Signed so it can't be forged.
FLOW_COOKIE = "snp_ms_flow"
_flow_serializer = URLSafeSerializer(settings.session_secret, salt="snp-ms-flow")


def _msal_app() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=settings.ms_client_id,
        client_credential=settings.ms_client_secret,
        authority=settings.ms_authority,
    )


def _scopes() -> list[str]:
    return [s for s in settings.ms_scopes.split() if s]


@router.get("/login")
def ms_login(request: Request):
    if not settings.ms_enabled:
        raise HTTPException(503, "Microsoft sign-in is not configured on this deployment.")

    app_ = _msal_app()
    flow = app_.initiate_auth_code_flow(
        scopes=_scopes(),
        redirect_uri=settings.ms_redirect_uri,
        state=secrets.token_urlsafe(16),
    )
    if "auth_uri" not in flow:
        log.error("msal: initiate_auth_code_flow returned %r", flow)
        raise HTTPException(500, "Failed to start Microsoft sign-in.")

    resp = RedirectResponse(flow["auth_uri"], status_code=302)
    resp.set_cookie(
        FLOW_COOKIE,
        _flow_serializer.dumps(flow),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=600,  # 10 min to complete the sign-in
    )
    return resp


@router.get("/callback")
def ms_callback(request: Request):
    if not settings.ms_enabled:
        raise HTTPException(503, "Microsoft sign-in is not configured on this deployment.")

    flow_tok = request.cookies.get(FLOW_COOKIE)
    if not flow_tok:
        raise HTTPException(400, "Sign-in state missing or expired. Please try again.")
    try:
        flow = _flow_serializer.loads(flow_tok)
    except BadSignature:
        raise HTTPException(400, "Sign-in state invalid. Please try again.")

    result = _msal_app().acquire_token_by_auth_code_flow(
        flow, dict(request.query_params)
    )
    if "error" in result:
        log.warning("msal callback error: %s — %s", result.get("error"), result.get("error_description"))
        raise HTTPException(400, f"Microsoft sign-in failed: {result.get('error_description') or result['error']}")

    claims = result.get("id_token_claims") or {}
    email = (claims.get("preferred_username") or claims.get("email") or "").lower().strip()
    oid = claims.get("oid") or ""
    if not email:
        raise HTTPException(400, "Microsoft did not return an email address for this account.")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            is_first = db.query(User).count() == 0
            user = User(
                email=email,
                # Password login won't be used for MS accounts; store a random
                # unusable hash so the column stays non-null.
                password_hash=hash_password(secrets.token_urlsafe(32)),
                is_admin=is_first,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            log.info("ms_auth: created user %s (admin=%s, oid=%s)", email, is_first, oid)
        else:
            log.info("ms_auth: logged in existing user %s", email)

        resp = RedirectResponse("/", status_code=302)
        resp.set_cookie(
            SESSION_COOKIE,
            make_session(user.id),
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
        resp.delete_cookie(FLOW_COOKIE)
        return resp
    finally:
        db.close()
