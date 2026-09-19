import asyncio
import hmac
import json
import time
from typing import Annotated, Literal
from urllib.parse import urlparse

import logfire
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse, StreamingResponse
from opentelemetry import context, propagate
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent_models import TraceBatch
from app.agent_runtime.contracts import (
    BrandInput,
    ConversationInput,
    MessageInput,
    RunInput,
    ToolRequest,
)
from app.core.telemetry import accept_trace
from app.services.agent.auth import sign, verify
from app.services.agent.chat import ChatService
from app.services.agent.gateway import Gateway
from app.services.agent.provider import GeminiProvider
from app.services.agent.service import TERMINAL
from app.services.agent.storage import AgentError

router = APIRouter(prefix="/agent", tags=["agent"])


def service(request: Request):
    result = getattr(request.app.state, "agent", None)
    if result is None:
        raise AgentError("agent_disabled", "Image creation is not configured yet.", 503)
    return result


def same_origin(request):
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        raise AgentError("invalid_origin", "Use this site's workspace sign-in.", 403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise AgentError("invalid_origin", "Cross-site requests are not allowed.", 403)


def authenticated(request: Request, svc: Annotated[object, Depends(service)]):
    token = request.headers.get("authorization", "")
    if token.startswith("Bearer "):
        if hmac.compare_digest(
            token[7:].encode(), svc.settings.access_token.get_secret_value().encode()
        ):
            return svc
        raise AgentError("unauthorized", "Invalid workspace credential.", 401)
    same_origin(request)
    verify(svc.settings, request.cookies.get("agent_session", ""), "session")
    return svc


Service = Annotated[object, Depends(authenticated)]


def task_auth(request: Request, svc: Annotated[object, Depends(service)]):
    header = request.headers.get("authorization", "")
    claims = verify(svc.settings, header.removeprefix("Bearer "), "task")
    return svc, claims


Task = Annotated[tuple, Depends(task_auth)]


class Login(BaseModel):
    access_token: str = Field(min_length=1, max_length=512)


class Answer(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)


@router.get("/status")
def agent_status(request: Request):
    svc = getattr(request.app.state, "agent", None)
    if svc is None:
        return {
            "enabled": False,
            "authenticated": False,
            "ready": False,
            "message": "Image creation is not configured yet.",
        }
    try:
        authenticated(request, svc)
    except AgentError:
        return {
            "enabled": True,
            "authenticated": False,
            "ready": False,
            "message": "Sign in to your creation workspace.",
        }
    blockers = svc.settings.blockers()
    with Session(svc.engine) as session:
        backlog = session.scalar(select(func.count()).select_from(TraceBatch))
    return {
        "enabled": True,
        "authenticated": True,
        "ready": not blockers,
        "missing": blockers,
        "tracing": "not_configured"
        if not svc.settings.logfire_token
        else "backlog"
        if backlog
        else "configured",
        "message": "Ready to submit cloud generation tasks."
        if not blockers
        else "Generation setup is incomplete. You can prepare brands and references.",
    }


@router.post("/session")
def login(
    body: Login,
    request: Request,
    response: Response,
    svc: Annotated[object, Depends(service)],
):
    same_origin(request)
    if not hmac.compare_digest(
        body.access_token.encode(),
        svc.settings.access_token.get_secret_value().encode(),
    ):
        raise AgentError("unauthorized", "Invalid workspace access key.", 401)
    token = sign(
        svc.settings,
        {
            "kind": "session",
            "workspace": svc.settings.workspace,
            "exp": time.time() + 8 * 3600,
        },
    )
    response.set_cookie(
        "agent_session",
        token,
        httponly=True,
        secure=svc.settings.environment == "production"
        or request.url.scheme == "https",
        samesite="strict",
        max_age=8 * 3600,
        path="/api/v1/agent",
    )
    return {"authenticated": True}


@router.delete("/session")
def logout(response: Response, svc: Service):
    response.delete_cookie("agent_session", path="/api/v1/agent")
    return {"authenticated": False}


@router.post("/assets", status_code=201)
async def upload(
    svc: Service,
    file: Annotated[UploadFile, File()],
    kind: Annotated[Literal["reference", "subject"], Form()],
):
    content = await file.read(svc.settings.max_image_bytes + 1)
    # Decode and storage IO run in the threadpool, not the event loop.
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(svc.upload, content, kind)


@router.get("/assets/{asset_id}/file")
def asset_file(asset_id: str, svc: Service):
    with Session(svc.engine) as session:
        asset = svc.asset(session, asset_id)
        return Response(
            svc.storage.get(asset),
            media_type=asset.mime,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )


@router.post("/brands", status_code=201)
def create_brand(body: BrandInput, svc: Service):
    return svc.create_brand(body)


@router.post("/brands/{brand_id}/versions", status_code=201)
def version_brand(brand_id: str, body: BrandInput, svc: Service):
    return svc.create_brand(body, brand_id)


@router.get("/brands")
def brands(svc: Service):
    return svc.brands()


@router.post("/runs", status_code=202)
def create_run(
    body: RunInput,
    svc: Service,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=100)],
):
    return svc.create_run(body, idempotency_key)


@router.get("/runs")
def runs(svc: Service):
    return svc.runs()


@router.get("/runs/{run_id}")
def read_run(run_id: str, svc: Service):
    return svc.read_run(run_id)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, svc: Service):
    return svc.cancel(run_id)


@router.post("/runs/{run_id}/input")
def answer_run(run_id: str, body: Answer, svc: Service):
    return svc.provide_input(run_id, body.answer)


@router.get("/runs/{run_id}/events")
async def events(
    run_id: str,
    request: Request,
    svc: Service,
    last_event_id: Annotated[str | None, Header()] = None,
):
    from starlette.concurrency import run_in_threadpool

    try:
        after = int(last_event_id or request.query_params.get("after", "0"))
        if after < 0:
            raise ValueError()
    except ValueError:
        raise AgentError("invalid_cursor", "Invalid event cursor.", 422) from None
    await run_in_threadpool(svc.read_run, run_id)

    async def stream():
        cursor = after
        for _ in range(240):
            if await request.is_disconnected():
                return
            batch, status = await run_in_threadpool(svc.events, run_id, cursor)
            for event in batch:
                cursor = event["sequence"]
                yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
            if status in TERMINAL or status == "waiting_for_input":
                if len(batch) == 100:
                    continue
                yield "event: done\ndata: {}\n\n"
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/internal/manifest", include_in_schema=False)
def manifest(task: Task):
    svc, claims = task
    return svc.manifest(claims)


@router.post("/internal/heartbeat", include_in_schema=False)
def heartbeat(task: Task):
    svc, claims = task
    return svc.heartbeat(claims)


@router.post("/internal/tools", include_in_schema=False)
def tools(body: ToolRequest, request: Request, task: Task):
    svc, claims = task
    with Session(svc.engine) as session:
        run = svc.task(session, claims)
        parent = request.headers.get("traceparent", "")
        if parent.split("-")[1:2] != [run.trace_id]:
            parent = run.traceparent
    token = context.attach(propagate.extract({"traceparent": parent}))
    failure = None
    try:
        with logfire.span(
            "agent.gateway", operation=body.operation, run_id=claims["run_id"]
        ):
            try:
                with _provider(svc) as provider:
                    result = Gateway(svc, provider).execute(claims, body)
            except AgentError as error:
                failure = error
        if failure:
            raise failure
        return result
    finally:
        context.detach(token)


def _provider(svc):
    from contextlib import contextmanager

    @contextmanager
    def factory():
        provider = getattr(svc, "provider", None)
        owned = provider is None
        if owned:
            provider = GeminiProvider(svc.settings)
        try:
            yield provider
        finally:
            if owned:
                provider.close()

    return factory()


@router.post("/internal/traces", include_in_schema=False)
async def traces(request: Request, task: Task):
    from starlette.concurrency import run_in_threadpool

    svc, claims = task
    return await run_in_threadpool(accept_trace, svc, claims, await request.body())


async def agent_error(_request, error):
    return JSONResponse(
        status_code=error.status, content={"code": error.code, "message": error.message}
    )


# Conversations use the same workspace authentication and resumable run event stream.


@router.post("/conversations", status_code=201)
def create_conversation(body: ConversationInput, svc: Service):
    return ChatService(svc).create(body)


@router.get("/conversations")
def conversations(svc: Service):
    return ChatService(svc).list()


@router.get("/conversations/{conversation_id}")
def read_conversation(conversation_id: str, svc: Service):
    return ChatService(svc).read(conversation_id)


@router.post("/conversations/{conversation_id}/messages", status_code=202)
def send_message(
    conversation_id: str,
    body: MessageInput,
    svc: Service,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=100)],
):
    return ChatService(svc).submit(conversation_id, body, idempotency_key)
