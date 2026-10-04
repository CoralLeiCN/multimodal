import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from app.core.config import ROOT
from app.services.agent.application import install
from app.services.agent.config import AgentSettings
from app.services.agent.process_executor import ProcessExecutor
from app.services.agent.worker import Worker
from fastapi import FastAPI
from sqlalchemy.orm import Session
from test_agent import activate, new_run
from test_agent import agent as agent  # noqa: PLC0414 -- isolated shared fixture


def wait_for(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for process state")


@pytest.fixture
def gateway(agent):
    service, _, _, provider, _ = agent
    app = FastAPI()
    install(app, settings=service.settings)
    app.state.agent = service
    service.provider = provider
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    service.settings.gateway_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    try:
        wait_for(lambda: server.started)
        yield service
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


def test_real_process_completes_through_http_gateway(agent, gateway):
    service, _, _, provider, _ = agent
    run = new_run(agent, candidate_count=1)
    manager = ProcessExecutor(service.settings)
    worker = Worker(service, manager)
    try:
        worker.tick()
        execution_id = service.read_run(run["id"])["sandbox_id"]
        process, directory, _ = manager.processes[execution_id]
        wait_for(lambda: process.poll() is not None)
        worker.tick()
        result = service.read_run(run["id"])
        assert process.returncode == 0
        assert result["status"] == "succeeded"
        assert result["artifacts"]
        assert provider.generated == 2  # initial candidate and one revision
        assert not manager.processes
        assert not Path(directory.name).exists()
    finally:
        manager.close()


def test_cancellation_terminates_and_reaps_process(agent):
    service, _, _, _, _ = agent
    run = new_run(agent)
    manager = ProcessExecutor(service.settings)
    try:
        Worker(service, manager).tick()
        execution_id = service.read_run(run["id"])["sandbox_id"]
        process, directory, _ = manager.processes[execution_id]
        service.cancel(run["id"])
        Worker(service, manager).tick()
        assert process.poll() is not None
        assert service.read_run(run["id"])["status"] == "cancelled"
        assert not Path(directory.name).exists()
        assert manager.find("unknown") is None
    finally:
        manager.close()


def test_worker_restart_fails_interrupted_attempt_without_replay(agent):
    service, _, _, provider, fake = agent
    run = new_run(agent)
    activate(agent, run["id"])
    manager = ProcessExecutor(service.settings)
    Worker(service, manager).tick()
    result = service.read_run(run["id"])
    assert result["status"] == "failed"
    assert result["error_code"] == "execution_exit"
    assert manager.processes == {}
    assert fake.created == 1
    assert provider.generated == 0
    # Never interpret persisted IDs as PIDs belonging to another process.
    manager.terminate(str(os.getpid()))
    assert manager.poll(str(os.getpid())) == 1


def test_process_receives_only_scoped_environment(agent, monkeypatch):
    service, _, _, _, _ = agent
    run = new_run(agent)
    activate(agent, run["id"])
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-inherit")
    monkeypatch.setenv("HF_TOKEN", "must-not-inherit")
    captured = {}

    class Child:
        def poll(self):
            return 0

        def wait(self, **kwargs):
            return 0

    def spawn(command, **kwargs):
        captured.update(kwargs)
        captured["command"] = command
        return Child()

    monkeypatch.setattr(subprocess, "Popen", spawn)
    manager = ProcessExecutor(service.settings)
    try:
        with Session(service.engine) as session:
            row = service.run(session, run["id"])
            first = manager.create(row)
            assert manager.create(row) == first
        assert captured["command"][:3] == [sys.executable, "-I", "-u"]
        assert "must-not-inherit" not in str(captured)
        assert set(captured["env"]) == {
            "AGENT_GATEWAY_URL",
            "AGENT_TASK_TOKEN",
            "AGENT_TRACEPARENT",
            "AGENT_PARENT_PID",
            "AGENT_PROCESS_DEADLINE",
            "HOME",
            "TMPDIR",
            "LANG",
        }
        assert Path(captured["cwd"]).is_dir()
    finally:
        manager.close()
    assert not Path(captured["cwd"]).exists()


@pytest.mark.parametrize(
    "parent,deadline,exit_code", [(0, 60, 125), (os.getpid(), -1, 124)]
)
def test_runtime_enforces_parent_and_deadline_without_worker_ticks(
    parent, deadline, exit_code
):
    command = (
        f"import sys; sys.path.insert(0, {str(ROOT / 'backend')!r}); "
        "from app.agent_runtime.process import main; main()"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", command],
        env={
            "AGENT_PARENT_PID": str(parent),
            "AGENT_PROCESS_DEADLINE": str(time.time() + deadline),
        },
        timeout=5,
        check=False,
    )
    assert result.returncode == exit_code


@pytest.mark.parametrize(
    "origin,valid",
    [
        ("http://127.0.0.1:7860", True),
        ("http://localhost:8000", True),
        ("https://gateway.example", True),
        ("http://public.example", False),
        ("http://127.0.0.1:7860/path", False),
        ("http://user@localhost", False),
    ],
)
def test_gateway_origin_validation(origin, valid):
    settings = AgentSettings(
        _env_file=None,
        gemini_api_key="fixture",
        openai_api_key="fixture",
        model="fixture-model",
        gateway_url=origin,
    )
    assert (not settings.blockers()) is valid
