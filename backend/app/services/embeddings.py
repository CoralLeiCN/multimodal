import math

from app.core.config import SIGLIP_MODEL, Settings


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


def create_embeddings(settings: Settings):
    if settings.embedding_model == SIGLIP_MODEL:
        from app.services.siglip_embeddings import SiglipEmbeddings

        return SiglipEmbeddings(settings)
    raise ValueError("Unsupported EMBEDDING_MODEL. Use SigLIP 2 Base.")
