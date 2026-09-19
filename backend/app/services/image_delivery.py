"""Resolve catalogued private R2 images to short-lived browser download URLs."""

import threading
from collections import OrderedDict
from time import monotonic

from botocore.exceptions import BotoCoreError

from app.services.embeddings import SearchError
from app.services.r2_catalogue import (
    object_key,
    r2_client,
    r2_destination,
    r2_object_url,
)

SIGNED_URL_SECONDS = 300
SIGNED_URL_CACHE_SECONDS = 240
SIGNED_URL_CACHE_SIZE = 2048
IMAGE_CACHE_CONTROL = "private, max-age=300"


class ImageDelivery:
    def __init__(self, settings):
        self.settings = settings
        self._client = None
        self._lock = threading.Lock()
        self._urls = OrderedDict()

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
                # Validate the reference even on hits. Keep versions separate when
                # indexing replaces an image or switches the active generation.
                cache_key = (expected, image.generation_id, image.checksum)
                now = monotonic()
                cached = self._urls.get(cache_key)
                if cached is not None:
                    url, expires_at = cached
                    if now < expires_at:
                        self._urls.move_to_end(cache_key)
                        return url
                    del self._urls[cache_key]
                if self._client is None:
                    self._client = r2_client(self.settings)
                url = self._client.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": bucket,
                        "Key": key,
                        "ResponseCacheControl": IMAGE_CACHE_CONTROL,
                    },
                    ExpiresIn=SIGNED_URL_SECONDS,
                    HttpMethod="GET",
                )
                # Start the TTL before signing so time spent signing cannot
                # extend reuse beyond the signature's safety margin.
                self._urls[cache_key] = (url, now + SIGNED_URL_CACHE_SECONDS)
                if len(self._urls) > SIGNED_URL_CACHE_SIZE:
                    self._urls.popitem(last=False)
                return url
        except (ValueError, BotoCoreError):
            raise SearchError(
                "Cloud image storage is not configured correctly.",
                "image_storage_unavailable",
                503,
            ) from None

    def close(self):
        with self._lock:
            self._urls.clear()
            if self._client is not None:
                self._client.close()
                self._client = None
