"""Exercise the pinned Codex binary, Python SDK and MCP transport without paid calls."""

import asyncio
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from app.explore import runner
from app.explore.tools import preview
from PIL import Image


@pytest.mark.parametrize("repeat_usage", [False, True])
def test_real_codex_runtime_uses_collection_mcp_and_resumes(
    tmp_path, monkeypatch, repeat_usage
):
    from openai_codex.api import AsyncTurnHandle

    notifications = []
    original_stream = AsyncTurnHandle.stream

    async def inspect_stream(self):
        async for event in original_stream(self):
            if event.method in {"error", "turn/completed"}:
                notifications.append(event.payload.model_dump(mode="json"))
            yield event
            if repeat_usage and event.method == "thread/tokenUsage/updated":
                yield event

    monkeypatch.setattr(AsyncTurnHandle, "stream", inspect_stream)
    requests = []
    tools = []
    emitted = []
    tool_calls = []
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"data": []}')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tool":
                tool_calls.append(body)
                assert self.headers["Authorization"] == "Bearer tool-fixture"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"result":{"index_version":"fixture","results":[]}}')
                return
            requests.append(body)
            tools.extend(body.get("tools", []))
            (tmp_path / f"request-{len(requests)}.json").write_text(
                json.dumps(body, indent=2)
            )
            if len(requests) == 4:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(
                    b'event: response.created\ndata: {"type":"response.created","response":{"id":"stalled","status":"in_progress","output":[]}}\n\n'
                )
                self.wfile.flush()
                stop.wait(10)
                return
            if len(requests) == 1:
                namespace = next(
                    t["name"]
                    for t in body["tools"]
                    if t.get("type") == "namespace"
                    and any(x.get("name") == "search_text" for x in t.get("tools", []))
                )
                item = {
                    "id": "fc_fixture",
                    "type": "function_call",
                    "call_id": "call_fixture",
                    "name": "search_text",
                    "namespace": namespace,
                    "arguments": '{"query":"red"}',
                }
            else:
                item = {
                    "id": "msg_fixture",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"answer":"No matching indexed images.","image_ids":[]}',
                        }
                    ],
                }
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for message in [
                {
                    "type": "response.created",
                    "response": {
                        "id": "resp_fixture",
                        "status": "in_progress",
                        "output": [],
                    },
                },
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_fixture",
                        "status": "completed",
                        "output": [item],
                        "usage": {
                            "total_tokens": 20,
                            "input_tokens": 10,
                            "output_tokens": 10,
                            "input_tokens_details": {"cached_tokens": 4},
                        },
                    },
                },
            ]:
                self.wfile.write(
                    f"event: {message['type']}\ndata: {json.dumps(message)}\n\n".encode()
                )
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    original = runner.configuration

    def configuration(payload):
        return {
            **original(payload),
            "model_provider": "fixture",
            "model_providers.fixture": {
                "name": "Fixture",
                "base_url": origin + "/v1",
                "wire_api": "responses",
                "request_max_retries": 0,
                "stream_max_retries": 0,
            },
        }

    monkeypatch.setattr(runner, "configuration", configuration)
    monkeypatch.setattr(runner, "emit", emitted.append)
    monkeypatch.setenv(
        "PYTHONPATH", str(__import__("pathlib").Path(__file__).resolve().parents[1])
    )
    picture = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(picture, format="PNG")
    payload = {
        "upload_id": "a" * 32,
        "image": preview(picture.getvalue()),
        "api_key": "fixture",
        "model": "fixture",
        "content": "Find red collection images",
        "tool_url": origin + "/tool",
        "tool_token": "tool-fixture",
    }
    try:
        asyncio.run(asyncio.wait_for(runner.drive(payload, tmp_path), 60))
        first_thread = next(e["id"] for e in emitted if e["kind"] == "thread")
        asyncio.run(
            asyncio.wait_for(
                runner.drive(
                    {
                        **payload,
                        "thread_id": first_thread,
                        "content": "Summarize the result",
                    },
                    tmp_path,
                ),
                60,
            )
        )
        assert [e["usage"] for e in emitted if e["kind"] == "result"] == [
            {"input_tokens": 20, "output_tokens": 20, "cached_input_tokens": 8},
            {"input_tokens": 10, "output_tokens": 10, "cached_input_tokens": 4},
        ]
        assert any(
            c.get("type") == "input_image"
            for i in requests[0]["input"]
            for c in i.get("content", [])
            if isinstance(c, dict)
        )

        async def interrupted_turn():
            task = asyncio.create_task(
                runner.drive({**payload, "thread_id": first_thread}, tmp_path)
            )
            try:
                async with asyncio.timeout(10):
                    while len(requests) < 4:
                        await asyncio.sleep(0.01)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
            finally:
                stop.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        asyncio.run(interrupted_turn())
        assert len([e for e in emitted if e["kind"] == "result"]) == 2
        assert tool_calls, [
            (t.get("type"), t.get("name"), [x.get("name") for x in t.get("tools", [])])
            for t in tools
        ]
        assert tool_calls[0]["name"] == "search_text"
        assert {e["id"] for e in emitted if e["kind"] == "thread"} == {first_thread}
        names = {t.get("name", t.get("type")) for t in tools}
        assert all(
            name
            in {
                "mcp__collection",
                "update_plan",
                "list_mcp_resources",
                "list_mcp_resource_templates",
                "read_mcp_resource",
                "request_user_input",
            }
            for name in names
        ), names
    except RuntimeError as error:
        raise AssertionError(
            {"notifications": notifications, "requests": len(requests)}
        ) from error
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join()
