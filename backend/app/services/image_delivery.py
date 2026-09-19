"""Resolve catalogued private R2 images to short-lived browser download URLs."""

import threading

from botocore.exceptions import BotoCoreError

from app.services.embeddings import SearchError
from app.services.r2_catalogue import (
    object_key,
    r2_client,
    r2_destination,
    r2_object_url,
)

SIGNED_URL_SECONDS = 300


class ImageDelivery:
    def __init__(self, settings):
        self.settings = settings
        self._client = None
        self._lock = threading.Lock()

    def signed_url(self, image):
        if not image.r2_url or not self.settings.r2_endpoint_url:
            return None
        try:
            _endpoint, bucket = r2_destination(self.settings)
            key = object_key(image.relative_path, self.settings.r2_prefix)
            expected = r2_object_url(self.settings, image.relative_path)
            if image.r2_url != expected:
                raise ValueError(
                    "Stored R2 reference does not match configured storage."
                )
            with self._lock:
                if self._client is None:
                    self._client = r2_client(self.settings)
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=SIGNED_URL_SECONDS,
                HttpMethod="GET",
            )
        except (ValueError, BotoCoreError):
            raise SearchError(
                "Cloud image storage is not configured correctly.",
                "image_storage_unavailable",
                503,
            ) from None

    def close(self):
        if self._client is not None:
            self._client.close()
