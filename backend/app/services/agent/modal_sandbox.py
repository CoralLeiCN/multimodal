import hashlib
from urllib.parse import urlparse

import modal

from app.core.config import ROOT
from app.services.agent.auth import task_token

RUNTIME = ROOT / "backend/app/agent_runtime"
REQUIREMENTS = ROOT / "deploy/agent-requirements.txt"


def image_version():
    digest = hashlib.sha256(REQUIREMENTS.read_bytes())
    for path in sorted(RUNTIME.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def runtime_image():
    return (
        modal.Image.debian_slim(python_version="3.12")
        .pip_install_from_requirements(str(REQUIREMENTS))
        .add_local_dir(
            str(RUNTIME),
            remote_path="/runtime/agent_runtime",
            copy=True,
            ignore=["__pycache__/**", "*.pyc"],
        )
        .workdir("/runtime")
    )


class ModalSandboxManager:
    def __init__(self, settings):
        self.settings = settings

    def create(self, run):
        hostname = urlparse(self.settings.gateway_url).hostname
        if self.settings.blockers() or not hostname:
            raise ValueError("Agent cloud configuration is incomplete")
        app = modal.App.lookup(self.settings.modal_app, create_if_missing=False)
        sandbox = modal.Sandbox.create(
            "python",
            "-m",
            "agent_runtime.runner",
            app=app,
            name=run.sandbox_name,
            tags={
                "run_id": run.id,
                "attempt_id": run.attempt_id,
                "workspace": run.workspace,
            },
            image=runtime_image(),
            cpu=(1, 1),
            memory=(1024, 1024),
            timeout=self.settings.run_timeout,
            outbound_cidr_allowlist=[],
            outbound_domain_allowlist=[hostname],
            inbound_cidr_allowlist=[],
            secrets=[
                modal.Secret.from_dict(
                    {"AGENT_TASK_TOKEN": task_token(self.settings, run)}
                )
            ],
            env={
                "AGENT_GATEWAY_URL": self.settings.gateway_url,
                "AGENT_TRACEPARENT": run.traceparent,
            },
        )
        return sandbox.object_id

    def find(self, name):
        try:
            return modal.Sandbox.from_name(self.settings.modal_app, name).object_id
        except modal.exception.NotFoundError:
            return None

    def poll(self, sandbox_id):
        return modal.Sandbox.from_id(sandbox_id).poll()

    def terminate(self, sandbox_id):
        try:
            modal.Sandbox.from_id(sandbox_id).terminate()
        except modal.exception.NotFoundError:
            pass
