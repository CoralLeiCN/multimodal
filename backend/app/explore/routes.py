import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from app.explore import auth
from app.explore.concurrency import blocking
from app.explore.contracts import Message, NewConversation, ToolCall

router = APIRouter(prefix="/explorer", tags=["explorer"])
User = Annotated[str, Depends(auth.principal)]
Writer = Annotated[str, Depends(auth.mutation)]


@router.get("/status")
def explorer_status(request: Request):
    settings = request.app.state.explorer.settings
    try:
        auth.principal(request)
        authenticated = True
    except HTTPException:
        authenticated = False
    return {
        "authenticated": authenticated,
        "ready": settings.ready,
        "auth_ready": settings.auth_ready,
    }


@router.get("/auth/login")
def explorer_login(request: Request):
    return auth.login(request.app.state.explorer.settings)


@router.get("/auth/callback")
async def explorer_callback(request: Request):
    return await auth.callback(request, request.app.state.explorer.settings)


@router.post("/auth/logout")
def explorer_logout(request: Request, owner: Writer):
    response = JSONResponse({"signed_out": True})
    response.delete_cookie(auth.COOKIE, path="/")
    return response


@router.get("/conversations")
def explorer_conversations(request: Request, owner: User):
    return request.app.state.explorer.list(owner)


@router.post("/conversations", status_code=201)
def explorer_create(request: Request, owner: Writer, body: NewConversation):
    return request.app.state.explorer.create(owner, body.title)


@router.get("/conversations/{conversation_id}")
def explorer_history(request: Request, owner: User, conversation_id: str):
    return request.app.state.explorer.history(owner, conversation_id)


@router.post("/conversations/{conversation_id}/messages", status_code=202)
async def explorer_submit(
    request: Request,
    owner: Writer,
    conversation_id: str,
    body: Message,
    key: Annotated[str, Header(alias="Idempotency-Key")],
):
    return await request.app.state.explorer.submit(owner, conversation_id, body, key)


@router.post("/runs/{run_id}/cancel")
async def explorer_cancel(request: Request, owner: Writer, run_id: str):
    return await request.app.state.explorer.cancel(owner, run_id)


@router.get("/runs/{run_id}/events")
async def explorer_events(request: Request, owner: User, run_id: str):
    service = request.app.state.explorer
    try:
        after = int(
            request.headers.get("last-event-id")
            or request.query_params.get("after", "0")
        )
        if not 0 <= after < 2**63:
            raise ValueError
    except ValueError:
        raise HTTPException(422, "Invalid event cursor.") from None
    await blocking(service.events, owner, run_id, after)

    async def stream():
        cursor = after
        while not await request.is_disconnected():
            records, complete = await blocking(service.events, owner, run_id, cursor)
            for item in records:
                cursor = item["id"]
                yield f"id: {cursor}\nevent: {item['kind']}\ndata: {json.dumps(item['data'])}\n\n"
            if complete and len(records) < 100:
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/uploads", status_code=201)
async def explorer_upload(
    request: Request, owner: Writer, image: Annotated[UploadFile, File()]
):
    service = request.app.state.explorer
    try:
        data = await image.read(service.search.settings.max_image_bytes + 1)
        return await blocking(service.save_upload, owner, data)
    finally:
        await image.close()


@router.post("/internal/{run_id}/tool")
def explorer_tool(
    request: Request,
    run_id: str,
    body: ToolCall,
    authorization: Annotated[str, Header()],
):
    if not authorization.startswith("Bearer "):
        raise HTTPException(403, "Invalid tool credential.")
    try:
        return request.app.state.explorer.tool(run_id, authorization[7:], body)
    except ValidationError:
        raise HTTPException(
            422, "Invalid collection tool arguments. Check IDs, filters and limits."
        ) from None
