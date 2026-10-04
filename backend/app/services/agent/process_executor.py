"""Run the checked-in agent runtime as a child process in the Space container."""

import hashlib
import os
import subprocess
import sys
import tempfile
import time
import uuid

from app.core.config import ROOT
from app.services.agent.auth import task_token


def runtime_version():
    digest = hashlib.sha256((ROOT / "uv.lock").read_bytes())
    for path in sorted((ROOT / "backend/app/agent_runtime").glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class ProcessExecutor:
    """Own child handles; unknown IDs are lost attempts, never arbitrary OS PIDs.

    This manages lifecycles, not security isolation. Only the packaged runtime
    executes; provider responses remain data. Use one worker per workspace.
    """

    def __init__(self, settings):
        self.settings = settings
        self.processes = {}
        self.names = {}

    def create(self, run):
        if self.settings.blockers():
            raise ValueError("Agent configuration is incomplete")
        if run.sandbox_name in self.names:
            return self.names[run.sandbox_name]
        directory = tempfile.TemporaryDirectory(prefix="image-agent-")
        execution_id = "process-" + uuid.uuid4().hex
        # Ignore inherited Python paths and execute only trusted code.
        command = (
            f"import sys; sys.path.insert(0, {str(ROOT / 'backend')!r}); "
            "from app.agent_runtime.process import main; raise SystemExit(main())"
        )
        env = {
            "AGENT_GATEWAY_URL": self.settings.gateway_url,
            "AGENT_TASK_TOKEN": task_token(self.settings, run),
            "AGENT_TRACEPARENT": run.traceparent or "",
            "AGENT_PARENT_PID": str(os.getpid()),
            "AGENT_PROCESS_DEADLINE": str(
                min(run.deadline, time.time() + self.settings.run_timeout)
            ),
            "HOME": directory.name,
            "TMPDIR": directory.name,
            "LANG": "C.UTF-8",
        }
        try:
            process = subprocess.Popen(
                [sys.executable, "-I", "-u", "-c", command],
                cwd=directory.name,
                env=env,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except BaseException:
            directory.cleanup()
            raise
        self.processes[execution_id] = (process, directory, run.sandbox_name)
        self.names[run.sandbox_name] = execution_id
        return execution_id

    def find(self, name):
        return self.names.get(name)

    def poll(self, execution_id):
        entry = self.processes.get(execution_id)
        # Interrupted attempts fail after a restart; never replay a paid call.
        return entry[0].poll() if entry else 1

    def terminate(self, execution_id):
        entry = self.processes.get(execution_id)
        if entry is None:
            return
        process, directory, name = entry
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        process.wait(timeout=2)
        directory.cleanup()
        self.processes.pop(execution_id)
        self.names.pop(name, None)

    def close(self):
        for execution_id in list(self.processes):
            self.terminate(execution_id)
