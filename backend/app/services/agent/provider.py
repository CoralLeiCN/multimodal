"""OpenAI-compatible harness with separately loaded image generation."""

import base64
import json

import logfire

from app.agent_runtime.contracts import ChatDecision, Evaluation, Plan
from app.services.agent.prompts import brand_brief, load_prompts
from app.services.agent.storage import AgentError


class AgentProvider:
    def __init__(self, settings, *, client=None):
        self.settings = settings
        self.prompts = load_prompts()
        self.image_provider = None
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                api_key=settings.openai_api_key.get_secret_value(),
                base_url=settings.openai_base_url,
                timeout=120,
                max_retries=0,
            )
        self.client = client

    def close(self):
        try:
            self.client.close()
        finally:
            if self.image_provider is not None:
                self.image_provider.close()

    @staticmethod
    def images(assets):
        parts = []
        for role, asset, content in assets:
            parts.extend(
                [
                    {"type": "text", "text": f"{role}; asset_id={asset.id}"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{asset.mime};base64,{base64.b64encode(content).decode()}"
                        },
                    },
                ]
            )
        return parts

    def structured(self, operation, model, schema, payload, assets=()):
        # Explicit metadata spans avoid SDK instrumentation recording prompts/images.
        with logfire.span("agent.model", operation=operation, model=model) as span:
            response = self.client.chat.completions.parse(
                model=model,
                store=False,
                max_completion_tokens=8192,
                messages=[
                    {
                        "role": "system",
                        "content": self.prompts["system"]
                        + "\n"
                        + self.prompts[operation],
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": json.dumps(payload)},
                            *self.images(assets),
                        ],
                    },
                ],
                response_format=schema,
            )
            if not response.choices:
                raise AgentError(
                    "invalid_model_output", "The model returned no decision.", 502
                )
            choice = response.choices[0]
            if choice.message.refusal:
                raise AgentError(
                    "provider_refused", "The model declined this request.", 502
                )
            if choice.finish_reason != "stop" or choice.message.parsed is None:
                raise AgentError(
                    "invalid_model_output",
                    "The model did not complete a valid decision.",
                    502,
                )
            usage = response.usage.model_dump(mode="json") if response.usage else None
            if response.usage:
                span.set_attribute("input_tokens", response.usage.prompt_tokens)
                span.set_attribute("output_tokens", response.usage.completion_tokens)
            return choice.message.parsed.model_dump(), usage

    def context(self, brand):
        return {"brand": brand, "brand_design_brief": brand_brief(brand, self.prompts)}

    def plan(self, brief, brand, answers, assets):
        return self.structured(
            "plan",
            self.settings.model,
            Plan,
            {**self.context(brand), "request": brief, "user_answers": answers},
            assets,
        )

    def evaluate(self, brief, brand, assets):
        return self.structured(
            "evaluate",
            self.settings.evaluation_model or self.settings.model,
            Evaluation,
            {**self.context(brand), "request": brief},
            assets,
        )

    def chat(self, conversation, brand):
        return self.structured(
            "chat",
            self.settings.chat_model or self.settings.model,
            ChatDecision,
            {**self.context(brand), "conversation": conversation},
        )

    def generate(self, brief, brand, prompt, assets):
        from app.services.agent.image_provider import GeminiImageProvider

        if self.image_provider is None:
            self.image_provider = GeminiImageProvider(self.settings)
        with logfire.span(
            "agent.model", operation="generate", model=self.settings.image_model
        ):
            return self.image_provider.generate(brief, brand, prompt, assets)
