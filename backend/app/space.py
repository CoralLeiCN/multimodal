"""Supervise the separate Image Studio prototype API and optional worker."""

import argparse
import os
import signal
import socket
import subprocess
import sys
import threading
import time

from app.services.agent.config import AgentSettings


def supervise(api_command, worker_command, *, env, port, startup_timeout):
    stopping = threading.Event()
    previous = {}
    children = []
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, lambda *_: stopping.set())
    try:
        api = subprocess.Popen(api_command, env=env)
        children.append(api)
        if worker_command:
            deadline = time.monotonic() + startup_timeout
            while not stopping.is_set():
                if api.poll() is not None or time.monotonic() >= deadline:
                    return 1
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    stopping.wait(0.2)
            if not stopping.is_set():
                children.append(subprocess.Popen(worker_command, env=env))
        while not stopping.wait(0.2):
            if any(child.poll() is not None for child in children):
                # A live API without its worker would accept work it cannot run.
                return 1
        return 0
    finally:
        # Stop the worker before the API. Both children get a bounded grace period.
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + 10
        for child in reversed(children):
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--agent-only", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    settings = AgentSettings()
    env = {**os.environ, "AGENT_GATEWAY_URL": f"http://127.0.0.1:{args.port}"}
    if settings.enabled:
        settings.gateway_url = env["AGENT_GATEWAY_URL"]
        settings.validate_enabled()
        if settings.blockers():
            parser.error("Configure the harness and image provider before starting the agent worker")
    target = (
        ["app.services.agent.application:create_agent_app", "--factory"]
        if args.agent_only
        else ["app.studio:app"]
    )
    api = [
        sys.executable,
        "-m",
        "uvicorn",
        *target,
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    worker = (
        [sys.executable, "-m", "app.services.agent.worker"]
        if settings.enabled
        else None
    )
    return supervise(
        api, worker, env=env, port=args.port, startup_timeout=settings.startup_timeout
    )


if __name__ == "__main__":
    raise SystemExit(main())
