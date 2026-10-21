import io
import warnings
from pathlib import Path

from PIL import Image as PillowImage

from app.core.config import Settings
from app.services.embeddings import SearchError


def validate_image(data: bytes, settings: Settings) -> tuple[str, int, int]:
    if len(data) > settings.max_image_bytes:
        raise SearchError(
            "Choose an image smaller than 10 MiB.", "image_too_large", 413
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", PillowImage.DecompressionBombWarning)
            with PillowImage.open(io.BytesIO(data)) as image:
                if image.format not in {"JPEG", "PNG"}:
                    raise SearchError(
                        "Choose a JPEG or PNG image.", "unsupported_image", 415
                    )
                if image.width * image.height > settings.max_image_pixels:
                    raise SearchError(
                        "Choose an image under 20 million pixels.",
                        "image_too_large",
                        413,
                    )
                image.load()
                return (
                    "image/jpeg" if image.format == "JPEG" else "image/png",
                    image.width,
                    image.height,
                )
    except SearchError:
        raise
    except (PillowImage.DecompressionBombWarning, PillowImage.DecompressionBombError):
        raise SearchError(
            "The image dimensions exceed the supported limit.", "image_too_large", 413
        ) from None
    except (OSError, ValueError, SyntaxError):
        raise SearchError(
            "This file could not be decoded as an image.", "invalid_image", 422
        ) from None


def safe_path(settings: Settings, relative: str, *, must_exist: bool = True) -> Path:
    root = settings.absolute(settings.image_root)
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or (must_exist and not path.is_file()):
        raise SearchError("This collection image is unavailable.", "image_missing", 404)
    return path
