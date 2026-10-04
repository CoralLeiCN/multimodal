"""HF authorization-code login with PKCE and signed, expiring app cookies."""

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

COOKIE = "explorer_session"
FLOW_COOKIE = "explorer_login"


def sign(settings, payload, audience, lifetime):
    return jwt.encode(
        {**payload, "aud": audience, "exp": int(time.time()) + lifetime},
        settings.session_secret.get_secret_value(),
        algorithm="HS256",
    )


def verify(settings, token, audience):
    if not settings.auth_ready:
        raise HTTPException(503, "Hugging Face sign-in is not configured.")
    try:
        return jwt.decode(
            token or "",
            settings.session_secret.get_secret_value(),
            algorithms=["HS256"],
            audience=audience,
            options={"require": ["exp", "aud"]},
        )
    except (jwt.InvalidTokenError, AttributeError):
        raise HTTPException(401, "Sign in with Hugging Face to continue.") from None


def principal(request: Request):
    settings = request.app.state.explorer.settings
    claims = verify(settings, request.cookies.get(COOKIE), "explorer")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise HTTPException(401, "Invalid session.")
    return claims["sub"]


def mutation(request: Request):
    if request.headers.get("origin") != request.app.state.explorer.settings.public_url:
        raise HTTPException(403, "Invalid request origin.")
    return principal(request)


def cookie(response, settings, name, value, lifetime):
    response.set_cookie(
        name,
        value,
        max_age=lifetime,
        httponly=True,
        secure=settings.public_url.startswith("https://"),
        samesite="lax",
        path="/",
    )


def login(settings):
    if not settings.auth_ready:
        raise HTTPException(503, "Hugging Face sign-in is not configured.")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    response = RedirectResponse(
        "https://huggingface.co/oauth/authorize?"
        + urlencode(
            {
                "client_id": settings.oauth_client_id,
                "redirect_uri": settings.public_url + "/api/v1/explorer/auth/callback",
                "response_type": "code",
                "scope": "openid profile",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )
    cookie(
        response,
        settings,
        FLOW_COOKIE,
        sign(settings, {"state": state, "verifier": verifier}, "oauth", 300),
        300,
    )
    return response


async def callback(request, settings, *, transport=None):
    flow = verify(settings, request.cookies.get(FLOW_COOKIE), "oauth")
    state, code = (
        request.query_params.get("state", ""),
        request.query_params.get("code", ""),
    )
    if (
        not secrets.compare_digest(flow.get("state", ""), state)
        or not code
        or len(code) > 4096
    ):
        raise HTTPException(400, "Invalid sign-in response.")
    try:
        async with httpx.AsyncClient(
            timeout=15, follow_redirects=False, transport=transport
        ) as client:
            auth = (
                (
                    settings.oauth_client_id,
                    settings.oauth_client_secret.get_secret_value(),
                )
                if settings.oauth_client_secret
                else None
            )
            token = await client.post(
                "https://huggingface.co/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": settings.oauth_client_id,
                    "redirect_uri": settings.public_url
                    + "/api/v1/explorer/auth/callback",
                    "code_verifier": flow["verifier"],
                },
                auth=auth,
            )
            token.raise_for_status()
            user = await client.get(
                "https://huggingface.co/oauth/userinfo",
                headers={"Authorization": "Bearer " + token.json()["access_token"]},
            )
            user.raise_for_status()
            subject = user.json()["sub"]
            if not isinstance(subject, str) or not 1 <= len(subject) <= 200:
                raise ValueError("Invalid subject")
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise HTTPException(
            502, "Hugging Face sign-in failed. Please try again."
        ) from None
    response = RedirectResponse(settings.public_url + "/", status_code=303)
    cookie(
        response,
        settings,
        COOKIE,
        sign(settings, {"sub": subject}, "explorer", 8 * 3600),
        8 * 3600,
    )
    response.delete_cookie(FLOW_COOKIE, path="/")
    return response
