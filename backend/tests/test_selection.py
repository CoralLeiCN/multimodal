import pytest
from app.services.embeddings import SearchError
from app.services.image_files import safe_path, validate_image
from PIL import Image


def test_paths_and_image_limits(setup, tmp_path):
    settings, *_ = setup
    outside = tmp_path / "secret.png"
    Image.new("RGB", (10, 10)).save(outside)
    (settings.image_root / "escape.png").symlink_to(outside)
    for relative in ("../secret.png", "escape.png"):
        for must_exist in (True, False):
            with pytest.raises(SearchError, match="unavailable"):
                safe_path(settings, relative, must_exist=must_exist)
    with pytest.raises(SearchError) as error:
        validate_image(b"x" * (settings.max_image_bytes + 1), settings)
    assert error.value.status == 413
    with pytest.raises(SearchError):
        validate_image(b"bad image", settings)
