"""Sandbox executor: resolve the compiled source, generate, check, and return artifacts."""

import httpx
import logfire


def run_execution(client, manifest):
    task = manifest["execution"]
    if task.get("image_id"):
        try:
            with logfire.span("tool.read_collection"):
                client.tool("source", "execution_image")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {404, 409, 413, 422, 503}:
                client.tool(
                    "lookup_failure",
                    "execution_finish",
                    {"error_code": "source_unavailable"},
                )
                return
            raise
    with logfire.span("tool.generate_image"):
        result = client.tool(
            "generate",
            "generate",
            {
                "prompt": task["prompt"],
                "aspect_ratio": task["aspect_ratio"],
                "reference_asset_ids": task["brand"]["reference_asset_ids"],
            },
        )
    with logfire.span("tool.evaluate_image"):
        evaluation = client.tool(
            "evaluate", "evaluate", {"asset_id": result["asset_id"]}
        )
    if evaluation["action"] == "revise" and evaluation["revision_prompt"]:
        with logfire.span("tool.generate_image"):
            result = client.tool(
                "revision",
                "generate",
                {
                    "prompt": (
                        task["prompt"]
                        + "\nRefinement: "
                        + evaluation["revision_prompt"]
                    )[:6000],
                    "aspect_ratio": task["aspect_ratio"],
                    "reference_asset_ids": task["brand"]["reference_asset_ids"],
                    "revision_asset_id": result["asset_id"],
                },
            )
        with logfire.span("tool.evaluate_image"):
            client.tool(
                "evaluate_revision", "evaluate", {"asset_id": result["asset_id"]}
            )
    with logfire.span("tool.publish_result"):
        client.tool(
            "execution_finish", "execution_finish", {"asset_ids": [result["asset_id"]]}
        )
