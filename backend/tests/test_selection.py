import json

import pytest
from app.core.config import Settings
from app.services.embeddings import SearchError
from app.services.selection import safe_path, select_images, validate_image
from PIL import Image


@pytest.mark.parametrize("first", ["", "Untitled image"])
def test_shared_image_uses_first_nonempty_title_and_preserves_sources(tmp_path, first):
    Image.new("RGB", (4, 4)).save(tmp_path / "shared.png")
    media = {
        "@admin": {"uid": "i1"},
        "@processed": {"medium_thumbnail": {"location": "shared.png"}},
        "legal": {"rights": [{"licence": "CC BY 4.0"}]},
    }
    titles = [first, "Available source title", "Later title"]
    records = [
        {
            "@admin": {"uid": f"co{n}"},
            "title": [{"value": title}],
            "multimedia": [media],
        }
        for n, title in enumerate(titles)
    ]
    path = tmp_path / "records.json"
    path.write_text(json.dumps(records))
    images, _ = select_images(Settings(_env_file=None, image_root=tmp_path), [path])
    assert len(images) == 1
    assert images[0]["title"] == (first or titles[1])
    assert [a["title"] for a in images[0]["associations"]] == titles


def test_selection_is_bounded_reproducible_and_preserves_metadata(setup, tmp_path):
    settings, _engine, _selected, _report, _generation, _vectors, _embeddings = setup
    records = []
    for index, name in enumerate(("red", "green", "blue")):
        records.append(
            {
                "@admin": {"uid": f"co{index}"},
                "title": [{"value": name}],
                "multimedia": [
                    {
                        "@admin": {"uid": f"i{index}"},
                        "@processed": {"medium_thumbnail": {"location": f"{name}.png"}},
                        "legal": {"rights": [{"licence": "CC BY-NC-SA 4.0"}]},
                    }
                ],
            }
        )
    source = tmp_path / "source.json"
    source.write_text(json.dumps(records))
    first, report = select_images(settings, [source], limit=1, scan_limit=2)
    second, _ = select_images(settings, [source], limit=1, scan_limit=2)
    assert first == second and len(first) == 1 and report["scanned"] == 2
    assert first[0]["associations"][0]["description"] == ""
    assert first[0]["associations"][0]["licence"] == "CC BY-NC-SA 4.0"


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
