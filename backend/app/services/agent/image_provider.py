"""Optional Gemini image generation; the harness uses the OpenAI client."""

import json

from google import genai
from google.genai import types

from app.services.agent.prompts import brand_brief, load_prompts
from app.services.agent.storage import AgentError


class GeminiImageProvider:
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
