import hashlib
import io
import json
from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

import pytest
from app.core.config import Settings
from app.services.dataset import load_catalogue, select_images
from datasets import Dataset
from datasets import Image as HFImage
from PIL import Image


@pytest.fixture
def release(tmp_path):
    root = tmp_path / "package"
    (root / "catalogue").mkdir(parents=True)
    rows = []
    for index, color in enumerate(("red", "green", "blue")):
        stream = io.BytesIO()
        Image.new("RGB", (4, 4), color).save(stream, format="PNG")
        blob = stream.getvalue()
        digest = hashlib.sha256(blob).hexdigest()
        rows.append(
            {
                "image_id": str(
                    uuid5(NAMESPACE_URL, f"smg-collection-image:{color}.png")
                ),
                "location": f"{color}.png",
                "relative_path": f"hf/{digest}.png",
                "checksum": digest,
                "mime_type": "image/png",
                "width": 4,
                "height": 4,
                "title": color,
                "schema_version": "1.0.0",
                "image": {"bytes": blob, "path": None},
                "associations": [
                    {
                        "record_uid": f"co{index}",
                        "image_uid": f"i{index}",
                        "source_json": "source.json",
                        "title": color,
                        "date": "c.1993",
                        "places": ["London"],
                        "categories": ["Optics"],
                        "date_ranges": [{"date_from": 1993, "date_to": 1993}],
                        "licence": "CC BY-NC-SA 4.0",
                    }
                ],
                "filter_metadata": [
                    {
                        "record_uid": f"co{index}",
                        "place": ["london"],
                        "category": ["optics"],
                        "date_from": 1993,
                        "date_to": 1993,
                    }
                ],
            }
        )

    def write(records):
        path = root / "catalogue/train-00000.parquet"
        Dataset.from_list(records).cast_column("image", HFImage()).to_parquet(path)
        (root / "build_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": "3.0.0",
                    "files": [
                        {
                            "path": "catalogue/train-00000.parquet",
                            "bytes": path.stat().st_size,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        }
                    ],
                }
            )
        )

    write(rows)
    return (
        Settings(_env_file=None, hf_dataset_path=root, image_root=tmp_path / "cache"),
        rows,
        write,
    )


def test_selection_uses_prepared_rows_and_original_bytes_offline(release, monkeypatch):
    import socket

    settings, original, _ = release
    monkeypatch.setattr(
        socket.socket, "connect", lambda *args: pytest.fail("Network access")
    )
    first, report = select_images(settings, limit=1, scan_limit=2)
    second, _ = select_images(settings, limit=1, scan_limit=2)
    assert first == second and report["scanned"] == 2
    expected = next(row for row in original if row["image_id"] == first[0]["image_id"])
    assert first[0]["associations"] == expected["associations"]
    assert first[0]["filter_metadata"] == expected["filter_metadata"]
    assert (settings.image_root / first[0]["relative_path"]).read_bytes() == expected[
        "image"
    ]["bytes"]
    assert report["dataset"]["revision"]


def test_remote_loading_requires_a_pinned_commit_before_network(monkeypatch):
    import huggingface_hub

    monkeypatch.setattr(
        huggingface_hub,
        "snapshot_download",
        lambda *a, **kw: pytest.fail("Unpinned download"),
    )
    for revision in (None, "main", "v1", "YOUR_COMMIT_SHA"):
        with pytest.raises(ValueError, match="full commit SHA"):
            load_catalogue(
                Settings(
                    _env_file=None,
                    hf_dataset_id="owner/dataset",
                    hf_dataset_revision=revision,
                )
            )


def test_remote_loader_downloads_only_pinned_catalogue(release, monkeypatch):
    import huggingface_hub

    settings, _, _ = release
    root = settings.hf_dataset_path
    settings.hf_dataset_path = None
    settings.hf_dataset_id = "owner/dataset"
    settings.hf_dataset_revision = "a" * 40
    calls = []

    def download(repo, **kwargs):
        calls.append((repo, kwargs))
        return str(root)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    _, report = select_images(settings, 1, 2)
    assert calls == [
        (
            "owner/dataset",
            {
                "repo_type": "dataset",
                "revision": "a" * 40,
                "allow_patterns": ["build_manifest.json", "catalogue/*.parquet"],
            },
        )
    ]
    assert report["dataset"]["revision"] == "a" * 40


def test_changed_shard_is_rejected_before_caching(release):
    settings, _, _ = release
    (settings.hf_dataset_path / "catalogue/train-00000.parquet").write_bytes(
        b"tampered"
    )
    with pytest.raises(ValueError, match="manifest"):
        select_images(settings)
    assert not settings.image_root.exists()


@pytest.mark.parametrize("change", ["checksum", "path", "version"])
def test_invalid_prepared_rows_are_rejected(release, change):
    settings, original, write = release
    rows = deepcopy(original)
    if change == "checksum":
        rows[0]["image"]["bytes"] = b"changed"
    elif change == "path":
        rows[0]["relative_path"] = "../../outside.png"
    else:
        rows[0]["schema_version"] = "999"
    write(rows)
    with pytest.raises(ValueError):
        select_images(settings, 1, 1)
    assert not settings.image_root.exists()


def test_index_generation_cannot_mix_dataset_releases(setup):
    from app.models import Generation
    from conftest import create_generation
    from sqlmodel import Session

    settings, engine, selected, report, _, _, _ = setup
    settings.qdrant_collection_name = "pinned_dataset_test"
    report["dataset"] = {
        "repo_id": "owner/dataset",
        "revision": "a" * 40,
        "schema_version": "1.0.0",
    }
    generation = create_generation(engine, settings, selected, report)
    report["dataset"]["revision"] = "b" * 40
    with pytest.raises(ValueError, match="different dataset release"):
        create_generation(engine, settings, selected, report)
    with Session(engine) as session:
        assert session.get(Generation, generation).dataset_revision == "a" * 40
