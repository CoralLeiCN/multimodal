"""Bound request bodies before JSON or multipart parsing consumes them."""

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

JSON_BODY_LIMIT = 256 * 1024
MULTIPART_OVERHEAD = 1024 * 1024


class RequestBodyLimitMiddleware:
    def __init__(
        self, app: ASGIApp, max_image_bytes: int = 10 * 1024 * 1024, upload_paths=()
    ):
        self.app = app
        self.max_image_bytes = max_image_bytes
        self.upload_paths = {"/api/v1/search/image", *upload_paths}

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (
            scope["type"] != "http"
            or scope["method"] not in {"POST", "PUT", "PATCH"}
            or not scope["path"].startswith("/api/v1/")
        ):
            await self.app(scope, receive, send)
            return

        path = scope["path"].rstrip("/")
        limit = JSON_BODY_LIMIT
        if path in self.upload_paths:
            limit = self.max_image_bytes + MULTIPART_OVERHEAD

        error = JSONResponse(
            {
                "code": "body_too_large",
                "message": "The upload exceeds the request limit.",
            },
            status_code=413,
        )
        try:
            declared_size = int(Headers(scope=scope).get("content-length", "0"))
        except ValueError:
            declared_size = 0
        if declared_size > limit:
            await error(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                await error(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        # Replay through the public ASGI interface; later reads still see disconnects.
        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)
