import json

from google import genai
from google.genai import types

from app.agent_runtime.contracts import Evaluation, Plan
from app.services.agent.prompts import brand_brief, load_prompts
from app.services.agent.storage import AgentError


class GeminiProvider:
    def __init__(self, settings):
        self.settings = settings
        self.prompts = load_prompts()
        self.client = genai.Client(
            api_key=settings.gemini_api_key.get_secret_value(),
            http_options=types.HttpOptions(
                timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1)
            ),
        )

    def close(self):
        self.client.close()

    @staticmethod
    def images(assets):
        parts = []
        for role, asset, content in assets:
            parts.extend(
                [
                    types.Part.from_text(text=f"{role}; asset_id={asset.id}"),
                    types.Part.from_bytes(data=content, mime_type=asset.mime),
                ]
            )
        return parts

    @staticmethod
    def usage(response):
        return (
            response.usage_metadata.model_dump(mode="json")
            if response.usage_metadata
            else None
        )

    def plan(self, brief, brand, answers, assets):
        content = [
            types.Part.from_text(
                text=json.dumps(
                    {
                        "request": brief,
                        "brand": brand,
                        "brand_design_brief": brand_brief(brand, self.prompts),
                        "user_answers": answers,
                        "instruction": self.prompts["plan"],
                    }
                )
            )
        ]
        response = self.client.models.generate_content(
            model=self.settings.model,
            contents=content + self.images(assets),
            config=types.GenerateContentConfig(
                system_instruction=self.prompts["system"],
                response_mime_type="application/json",
                response_json_schema=Plan.model_json_schema(),
            ),
        )
        if not response.text:
            raise AgentError(
                "provider_no_plan", "The model did not return a creative plan.", 502
            )
        return Plan.model_validate_json(response.text).model_dump(), self.usage(
            response
        )

    def generate(self, brief, brand, prompt, assets):
        content = [
            types.Part.from_text(
                text=json.dumps(
                    {
                        "request": brief,
                        "brand": brand,
                        "brand_design_brief": brand_brief(brand, self.prompts),
                        "image_prompt": prompt,
                        "instruction": self.prompts["generate"],
                    }
                )
            )
        ]
        response = self.client.models.generate_content(
            model=self.settings.image_model,
            contents=content + self.images(assets),
            config=types.GenerateContentConfig(
                system_instruction=self.prompts["system"],
                response_modalities=["TEXT", "IMAGE"],
                image_config=types.ImageConfig(aspect_ratio=brief["aspect_ratio"]),
            ),
        )
        for candidate in response.candidates or []:
            if candidate.content:
                for part in candidate.content.parts or []:
                    if (
                        part.inline_data
                        and part.inline_data.mime_type.startswith("image/")
                        and not part.thought
                    ):
                        return part.inline_data.data, self.usage(response)
        raise AgentError(
            "provider_no_image",
            "No image was returned. The request may have been declined.",
            502,
        )

    def evaluate(self, brief, brand, assets):
        response = self.client.models.generate_content(
            model=self.settings.evaluation_model,
            contents=[
                types.Part.from_text(
                    text=json.dumps(
                        {
                            "request": brief,
                            "brand": brand,
                            "brand_design_brief": brand_brief(brand, self.prompts),
                            "instruction": self.prompts["evaluate"],
                        }
                    )
                )
            ]
            + self.images(assets),
            config=types.GenerateContentConfig(
                system_instruction=self.prompts["system"],
                response_mime_type="application/json",
                response_json_schema=Evaluation.model_json_schema(),
            ),
        )
        if not response.text:
            raise AgentError(
                "provider_no_evaluation", "The model did not return an evaluation.", 502
            )
        return Evaluation.model_validate_json(response.text).model_dump(), self.usage(
            response
        )

    def chat(self, conversation, brand):
        from app.agent_runtime.contracts import ChatDecision

        # The locked SDK normalizes attempts=0 to 1, then interprets that as a
        # retry count for Interactions. Disable that resource's retry config
        # explicitly; a transport test guards this version-specific adapter.
        with genai.Client(
            api_key=self.settings.gemini_api_key.get_secret_value(),
            http_options=types.HttpOptions(
                timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1)
            ),
        ) as client:
            client.interactions.sdk_configuration.retry_config = None
            response = client.interactions.create(
                model=self.settings.chat_model,
                store=False,
                system_instruction=self.prompts["system"] + "\n" + self.prompts["chat"],
                input=json.dumps(
                    {
                        "brand": brand,
                        "brand_design_brief": brand_brief(brand, self.prompts),
                        "conversation": conversation,
                    }
                ),
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": ChatDecision.model_json_schema(),
                },
            )
        if not response.output_text:
            raise AgentError(
                "provider_no_reply", "The chat model did not return a decision.", 502
            )
        usage = response.usage.model_dump(mode="json") if response.usage else None
        return ChatDecision.model_validate_json(
            response.output_text
        ).model_dump(), usage
