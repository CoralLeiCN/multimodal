"""HF asset writes, bounded reads, and workspace-scoped orphan cleanup."""

import hashlib
import io
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.services.agent.config import AgentSettings
from app.services.agent.storage import AgentError, Storage
from pydantic import SecretStr
from test_agent import picture


@pytest.fixture
def hf_storage(monkeypatch):
    hub = Mock()
    filesystem = Mock()
    api = Mock(return_value=hub)
    fs = Mock(return_value=filesystem)
    monkeypatch.setattr("huggingface_hub.HfApi", api)
    monkeypatch.setattr("huggingface_hub.HfFileSystem", fs)
    settings = AgentSettings(
        _env_file=None,
        storage="hf",
        hf_bucket="fixtures/assets",
        hf_token="fixture-token",
        access_token="x" * 32,
    )
    settings.validate_enabled()
    storage = Storage(settings)
    api.assert_called_once_with(token="fixture-token")
    fs.assert_called_once_with(token="fixture-token", skip_instance_cache=True)
    return storage, hub, filesystem


def test_hf_asset_round_trip_preserves_bytes_identity_and_bounds_reads(hf_storage):
    storage, hub, filesystem = hf_storage
    content = picture()
    asset = storage.save(content, "preview", "generated")
    assert asset.checksum == hashlib.sha256(content).hexdigest()
    hub.batch_bucket_files.assert_called_once_with(
        "fixtures/assets", add=[(content, asset.object_key)]
    )
    body = io.BytesIO(content)
    filesystem.open.return_value = body
    assert storage.get(asset) == content
    assert body.closed
    filesystem.open.assert_called_once_with(
        f"hf://buckets/fixtures/assets/{asset.object_key}", "rb", block_size=0
    )
    storage.settings.max_image_bytes = 4
    oversized = io.BytesIO(b"0123456789")
    filesystem.open.return_value = oversized
    assert storage.get(asset) == b"01234"
    assert oversized.closed


def test_failed_hf_upload_does_not_publish_an_asset(hf_storage):
    storage, hub, _ = hf_storage
    hub.batch_bucket_files.side_effect = RuntimeError("fixture upload failure")
    with pytest.raises(RuntimeError, match="upload failure"):
        storage.save(picture(), "preview", "generated")


@pytest.mark.parametrize(
    "key",
    [
        "../other/image",
        "/absolute/image",
        "abc/../../secret",
        "hf://buckets/other/image",
    ],
)
def test_hf_read_rejects_keys_outside_generated_namespace(hf_storage, key):
    storage, _, filesystem = hf_storage
    with pytest.raises(AgentError, match="Invalid asset"):
        storage.get(SimpleNamespace(object_key=key))
    filesystem.open.assert_not_called()


def test_hf_cleanup_only_deletes_old_unreferenced_files_in_this_workspace(hf_storage):
    storage, hub, _ = hf_storage
    prefix = hashlib.sha256(b"preview").hexdigest()[:24] + "/"

    def entry(key, timestamp, kind="file"):
        return SimpleNamespace(
            path=key,
            uploaded_at=datetime.fromtimestamp(timestamp, UTC)
            if timestamp is not None
            else None,
            type=kind,
            mtime=datetime.fromtimestamp(1, UTC),
        )

    orphan = prefix + "a" * 32
    retained = prefix + "b" * 32
    hub.list_bucket_tree.return_value = [
        entry(orphan, 1),
        entry(retained, 1),
        entry(prefix + "c" * 32, 200),
        entry(prefix + "d" * 32, None),
        entry("e" * 24 + "/" + "f" * 32, 1),
        entry(prefix + "../escape", 1),
        entry(prefix + "f" * 32, 1, "directory"),
    ]
    assert storage.prune_orphans("preview", {retained}, 100) == 1
    hub.list_bucket_tree.assert_called_once_with(
        "fixtures/assets", prefix=prefix, recursive=False
    )
    hub.batch_bucket_files.assert_called_once_with("fixtures/assets", delete=[orphan])


@pytest.mark.parametrize(
    "bucket,token", [("", "token"), ("../bucket", "token"), ("owner/bucket", "")]
)
def test_hf_storage_requires_bucket_and_explicit_token(bucket, token):
    settings = AgentSettings(
        _env_file=None,
        storage="hf",
        hf_bucket=bucket,
        hf_token=SecretStr(token),
        access_token="x" * 32,
    )
    with pytest.raises(ValueError, match="AGENT_HF_"):
        settings.validate_enabled()
