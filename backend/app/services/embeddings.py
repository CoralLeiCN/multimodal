import math

import httpx
import logfire
from google import genai
from google.genai import types
from google.genai.errors import APIError

from app.core.config import Settings


class SearchError(Exception):
    def __init__(
        self, message: str, code: str = "search_unavailable", status: int = 503
    ):
        super().__init__(message)
        self.code, self.status = code, status


def normalize(vector: list[float], dimensions: int) -> list[float]:
    if len(vector) != dimensions or not all(math.isfinite(value) for value in vector):
        raise SearchError(
            "The embedding has invalid dimensions or values.", "invalid_embedding"
        )
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        raise SearchError("The embedding vector is empty.", "invalid_embedding")
    return [value / norm for value in vector]


class GeminiEmbeddings:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None

    def close(self):
        if self.client:
            self.client.close()

    def embed(
        self,
        *,
        text: str | None = None,
        image: bytes | None = None,
        mime_type: str = "image/jpeg",
    ) -> list[float]:
        with logfire.span(
            "gemini.embed",
            model=self.settings.embedding_model,
            dimensions=self.settings.embedding_dimensions,
            input_kind="image" if image is not None else "text",
        ):
            return self._embed(text=text, image=image, mime_type=mime_type)

    def _embed(
        self,
        *,
        text: str | None = None,
        image: bytes | None = None,
        mime_type: str = "image/jpeg",
    ) -> list[float]:
        if not self.settings.gemini_api_key:
            raise SearchError(
                "Configure GEMINI_API_KEY in the backend environment.",
                "missing_api_key",
            )
        if self.client is None:
            self.client = genai.Client(
                api_key=self.settings.gemini_api_key.get_secret_value(),
                http_options=types.HttpOptions(
                    timeout=20000, retry_options=types.HttpRetryOptions(attempts=1)
                ),
            )
        content = (
            types.Content(
                parts=[types.Part.from_bytes(data=image, mime_type=mime_type)]
            )
            if image is not None
            else f"task: search result | query: {text}"
        )
        try:
            result = self.client.models.embed_content(
                model=self.settings.embedding_model,
                contents=content,
                config=types.EmbedContentConfig(
                    output_dimensionality=self.settings.embedding_dimensions
                ),
            )
        except (APIError, httpx.HTTPError, OSError) as error:
            code = getattr(error, "code", None)
            if code == 429:
                raise SearchError(
                    "Gemini quota is temporarily exhausted. Try again later.",
                    "embedding_quota",
                    429,
                ) from None
            if "timeout" in type(error).__name__.lower():
                raise SearchError(
                    "The embedding request timed out. Try again.",
                    "embedding_timeout",
                    504,
                ) from None
            if code in (400, 401, 403, 404):
                raise SearchError(
                    "Gemini rejected the embedding request. Check the key, model access, and billing.",
                    "embedding_configuration",
                ) from None
            raise SearchError(
                "The Gemini embedding service is unavailable. Try again.",
                "embedding_unavailable",
            ) from None
        if not result.embeddings or len(result.embeddings) != 1:
            raise SearchError(
                "Gemini returned an unexpected number of embeddings.",
                "invalid_embedding",
            )
        return normalize(
            result.embeddings[0].values, self.settings.embedding_dimensions
        )
