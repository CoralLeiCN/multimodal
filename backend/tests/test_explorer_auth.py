import asyncio
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from app.explore import auth
from app.explore.config import ExplorerSettings
from fastapi import HTTPException
from starlette.requests import Request


def test_hf_login_pkce_state_cookie_and_expiry():
    settings = ExplorerSettings(
        _env_file=None,
        public_url="https://collection.example",
        session_secret="s" * 48,
        oauth_client_id="test",
    )
    login = auth.login(settings)
    query = parse_qs(urlsplit(login.headers["location"]).query)
    cookie = login.headers["set-cookie"].split(";", 1)[0]
    claims = auth.verify(settings, cookie.split("=", 1)[1], "oauth")
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(claims["verifier"].encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert query["code_challenge"] == [expected]
    assert query["scope"] == ["openid profile"]
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "Secure" in login.headers["set-cookie"]
    calls = []

    def response(request):
        calls.append(request)
        if request.url.path == "/oauth/token":
            form = parse_qs(request.content.decode())
            assert form["code_verifier"] == [claims["verifier"]]
            return httpx.Response(200, json={"access_token": "private-token"})
        assert request.headers["Authorization"] == "Bearer private-token"
        return httpx.Response(200, json={"sub": "hf-user"})

    def request(state):
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "headers": [(b"cookie", cookie.encode())],
                "query_string": f"code=example&state={state}".encode(),
            }
        )

    with pytest.raises(HTTPException) as failure:
        asyncio.run(
            auth.callback(
                request("wrong-state"),
                settings,
                transport=httpx.MockTransport(response),
            )
        )
    assert failure.value.status_code == 400
    assert not calls
    result = asyncio.run(
        auth.callback(
            request(claims["state"]), settings, transport=httpx.MockTransport(response)
        )
    )
    session = next(
        c
        for c in result.headers.getlist("set-cookie")
        if c.startswith(auth.COOKIE + "=")
    )
    token = session.split(";", 1)[0].split("=", 1)[1]
    assert auth.verify(settings, token, "explorer")["sub"] == "hf-user"
    assert "private-token" not in session
    with pytest.raises(HTTPException):
        auth.verify(
            settings,
            auth.sign(settings, {"sub": "hf-user"}, "explorer", -1),
            "explorer",
        )
    with pytest.raises(HTTPException):
        auth.verify(settings, token, "oauth")


@pytest.mark.parametrize("secret", ["", "short"])
def test_unconfigured_auth_rejects_weak_or_missing_secrets(secret):
    settings = ExplorerSettings(
        _env_file=None, session_secret=secret, oauth_client_id="test"
    )
    token = (
        auth.sign(settings, {"sub": "forged"}, "explorer", 60) if secret else "invalid"
    )
    with pytest.raises(HTTPException) as failure:
        auth.verify(settings, token, "explorer")
    assert failure.value.status_code == 503
