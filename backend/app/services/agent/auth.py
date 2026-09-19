import base64
import hashlib
import hmac
import json
import time

from app.services.agent.storage import AgentError


def sign(settings, claims):
    payload = (
        base64.urlsafe_b64encode(json.dumps(claims, sort_keys=True).encode())
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        settings.access_token.get_secret_value().encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    return payload + "." + signature


def verify(settings, token, kind):
    try:
        payload, supplied = token.rsplit(".", 1)
        expected = hmac.new(
            settings.access_token.get_secret_value().encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            raise ValueError()
        claims = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
        if (
            claims["kind"] != kind
            or claims["workspace"] != settings.workspace
            or claims["exp"] <= time.time()
        ):
            raise ValueError()
        return claims
    except (ValueError, KeyError, TypeError):
        raise AgentError(
            "unauthorized", "Sign in to your creation workspace.", 401
        ) from None


def task_token(settings, run):
    return sign(
        settings,
        {
            "kind": "task",
            "workspace": run.workspace,
            "run_id": run.id,
            "attempt_id": run.attempt_id,
            "exp": run.deadline,
        },
    )
