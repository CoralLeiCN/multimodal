import os
import signal
import socket
import subprocess
import sys
import time

import pytest
from app.core.config import ROOT


@pytest.mark.parametrize("failure", ["worker", "api", "startup_timeout", "shutdown"])
def test_supervisor_stops_children_on_failure_or_shutdown(tmp_path, failure):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    api_pid = tmp_path / "api.pid"
    worker_started = tmp_path / "worker-started"
    api_code = f"import os,time; from pathlib import Path; Path({str(api_pid)!r}).write_text(str(os.getpid())); "
    if failure == "api":
        api_code += "raise SystemExit(7)"
    elif failure == "startup_timeout":
        api_code += "time.sleep(30)"
    else:
        api_code += f"from http.server import HTTPServer,BaseHTTPRequestHandler; HTTPServer(('127.0.0.1',{port}),BaseHTTPRequestHandler).serve_forever()"
    worker_code = f"from pathlib import Path; Path({str(worker_started)!r}).touch(); raise SystemExit(7)"
    api = [sys.executable, "-c", api_code]
    worker = None if failure == "shutdown" else [sys.executable, "-c", worker_code]
    code = (
        "import os; from app.space import supervise; "
        f"raise SystemExit(supervise({api!r}, {worker!r}, env=os.environ.copy(), port={port}, startup_timeout=1))"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code], env={"PYTHONPATH": str(ROOT / "backend")}
    )
    try:
        if failure == "shutdown":
            deadline = time.monotonic() + 5
            while not api_pid.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert api_pid.exists()
            process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == (0 if failure == "shutdown" else 1)
        assert worker_started.exists() is (failure == "worker")
        with pytest.raises(ProcessLookupError):
            os.kill(int(api_pid.read_text()), 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
