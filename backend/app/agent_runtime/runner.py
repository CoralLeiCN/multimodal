"""Entry point for Modal. All decisions happen here; paid tools use the gateway."""

import os
import threading
import time

import httpx
import logfire
from opentelemetry import context, propagate
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor


class GatewayClient:
    def __init__(self, url, token):
        self.client = httpx.Client(
            base_url=url + "/api/v1/agent/internal/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=150,
        )

    def get(self, path):
        response = self.client.get(path)
        response.raise_for_status()
        return response.json()

    def tool(self, step_id, operation, arguments=None):
        headers = {}
        propagate.inject(headers)
        response = self.client.post(
            "tools",
            headers=headers,
            json={
                "step_id": step_id,
                "operation": operation,
                "arguments": arguments or {},
            },
        )
        response.raise_for_status()
        return response.json()


def run_agent(client):
    manifest = client.get("manifest")
    if manifest["request"].get("mode") == "execution":
        from .execution import run_execution

        return run_execution(client, manifest)
    checkpoint = manifest["checkpoint"]
    plan_step = "plan_" + str(len(checkpoint.get("answers", [])))
    with logfire.span("agent.plan"):
        plan = client.tool(plan_step, "plan")
    if plan.get("question"):
        logfire.force_flush()
        client.tool("wait", "wait", {"question": plan["question"]})
        return
    candidates = []
    evaluations = []
    for number in range(manifest["request"]["candidate_count"]):
        with logfire.span("tool.generate_image"):
            result = client.tool(
                f"generate_{number}",
                "generate",
                {
                    "prompt": plan["prompt"]
                    + f"\nCreate distinct candidate {number + 1}.",
                    "reference_asset_ids": plan["reference_asset_ids"],
                },
            )
        candidates.append(result["asset_id"])
        with logfire.span("tool.evaluate_image"):
            evaluation = client.tool(
                f"evaluate_{number}", "evaluate", {"asset_id": result["asset_id"]}
            )
        evaluations.append(evaluation)
    # One bounded agent decision: revise the candidate with the weakest alignment.
    choices = [
        i
        for i, evaluation in enumerate(evaluations)
        if evaluation["action"] == "revise" and evaluation["revision_prompt"]
    ]
    if choices:
        worst = min(
            choices,
            key=lambda i: min(
                evaluations[i][k]
                for k in ("brand_score", "subject_score", "request_score")
            ),
        )
        with logfire.span("tool.generate_image"):
            revision = client.tool(
                "revision",
                "generate",
                {
                    "prompt": evaluations[worst]["revision_prompt"],
                    "reference_asset_ids": plan["reference_asset_ids"],
                    "revision_asset_id": candidates[worst],
                },
            )
        with logfire.span("tool.evaluate_image"):
            client.tool(
                "evaluate_revision", "evaluate", {"asset_id": revision["asset_id"]}
            )
        candidates[worst] = revision["asset_id"]
    with logfire.span("tool.publish_result"):
        client.tool("finish", "finish", {"asset_ids": candidates})


def main():
    url, token = os.environ["AGENT_GATEWAY_URL"], os.environ["AGENT_TASK_TOKEN"]
    exporter = OTLPSpanExporter(
        endpoint=url + "/api/v1/agent/internal/traces",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    logfire.configure(
        send_to_logfire=False,
        service_name="image-agent-sandbox",
        console=False,
        inspect_arguments=False,
        additional_span_processors=[
            BatchSpanProcessor(exporter, max_queue_size=128, max_export_batch_size=64)
        ],
    )
    ctx = context.attach(
        propagate.extract({"traceparent": os.environ["AGENT_TRACEPARENT"]})
    )
    client = GatewayClient(url, token)
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(20):
            try:
                response = client.client.post("heartbeat")
                if response.status_code in {401, 403}:
                    return
            except httpx.HTTPError:
                pass

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        # The worker must persist the sandbox ID before its first authorized call.
        for _ in range(60):
            try:
                client.get("manifest")
                break
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 403:
                    raise
                time.sleep(1)
        with logfire.span("agent.run"):
            # Swallow raw exception messages before span recording: SDK errors can contain content.
            try:
                run_agent(client)
            except Exception:  # noqa: BLE001 -- do not expose provider or credential details
                print(
                    "Agent execution stopped; inspect the persisted run status.",
                    flush=True,
                )
                return 1
        return 0
    finally:
        stop.set()
        thread.join(timeout=1)
        logfire.force_flush(timeout_millis=5000)
        client.client.close()
        context.detach(ctx)


if __name__ == "__main__":
    raise SystemExit(main())
