"""CPU text/image embeddings using pinned, locally cached SigLIP 2 weights."""

import io
from contextlib import ExitStack
from itertools import batched
from threading import Lock

import logfire
from PIL import Image, ImageOps

from app.core.config import Settings
from app.services.embeddings import SearchError, normalize


class SiglipEmbeddings:
    def __init__(self, settings: Settings):
        if settings.embedding_dimensions != 768:
            raise ValueError("SigLIP 2 Base requires EMBEDDING_DIMENSIONS=768.")
        self.settings = settings
        self._model = None
        self._processor = None
        self._lock = Lock()

    def _load(self):
        if self._model is not None:
            return
        import torch
        from transformers import AutoModel, AutoProcessor

        torch.set_num_threads(self.settings.embedding_cpu_threads)
        options = {
            "revision": self.settings.embedding_revision,
            "cache_dir": str(
                self.settings.absolute(self.settings.embedding_model_cache)
            ),
            "trust_remote_code": False,
        }
        try:
            processor = AutoProcessor.from_pretrained(
                self.settings.embedding_model, **options
            )
            model = (
                AutoModel.from_pretrained(
                    self.settings.embedding_model, use_safetensors=True, **options
                )
                .to("cpu")
                .eval()
            )
        except (OSError, ValueError, RuntimeError):
            raise SearchError(
                "The search model could not be loaded. Check its local cache and download access.",
                "embedding_configuration",
            ) from None
        self._processor, self._model = processor, model

    def embed(self, *, text=None, image=None, mime_type="image/jpeg"):
        if (text is None) == (image is None):
            raise ValueError("Provide exactly one text query or image.")
        if image is not None:
            return self.embed_images([(image, mime_type)])[0]
        return self._encode(text=text)[0]

    def embed_images(self, images):
        vectors = []
        for batch in batched(images, self.settings.embedding_batch_size):
            vectors.extend(self._encode(images=batch))
        return vectors

    def _encode(self, *, text=None, images=None):
        import torch

        # One model and one bounded inference batch per process. Parallel API or
        # indexer calls cannot oversubscribe CPU memory with model forwards.
        with (
            self._lock,
            torch.inference_mode(),
            logfire.span(
                "siglip.embed",
                model=self.settings.embedding_model,
                revision=self.settings.embedding_revision,
                dimensions=self.settings.embedding_dimensions,
                input_kind="text" if text is not None else "image",
                batch_size=1 if images is None else len(images),
            ),
        ):
            self._load()
            try:
                if text is not None:
                    inputs = self._processor(
                        text=[text],
                        padding="max_length",
                        max_length=64,
                        truncation=True,
                        return_tensors="pt",
                    )
                    features = self._model.get_text_features(**inputs)
                else:
                    with ExitStack() as resources:
                        pictures = []
                        for data, _mime in images:
                            source = resources.enter_context(
                                Image.open(io.BytesIO(data))
                            )
                            oriented = ImageOps.exif_transpose(source)
                            resources.callback(oriented.close)
                            picture = oriented.convert("RGB")
                            resources.callback(picture.close)
                            pictures.append(picture)
                        inputs = self._processor(images=pictures, return_tensors="pt")
                        features = self._model.get_image_features(**inputs)
                # Transformers 5 returns a pooled model output from these helpers.
                rows = features.pooler_output.float().tolist()
                expected = 1 if images is None else len(images)
                if len(rows) != expected:
                    raise SearchError(
                        "Unexpected embedding count.", "invalid_embedding"
                    )
                return [normalize(row, 768) for row in rows]
            except SearchError:
                raise
            except (OSError, ValueError, RuntimeError):
                raise SearchError(
                    "The local embedding model could not process this search.",
                    "embedding_unavailable",
                ) from None

    def close(self):
        with self._lock:
            self._model = self._processor = None
