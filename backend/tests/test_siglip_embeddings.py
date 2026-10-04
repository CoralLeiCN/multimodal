import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import torch
from app.core.config import Settings
from app.services.embeddings import SearchError, create_embeddings
from app.services.siglip_embeddings import SiglipEmbeddings
from PIL import Image


def image_bytes(color):
    output = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    loads, batches = [], []

    def processor(*, images=None, text=None, **kwargs):
        colors = [p.getpixel((0, 0)) for p in images] if images else [(3, 4, 0)]
        batches.append(len(colors))
        return {"rows": colors}

    def features(rows):
        assert torch.is_inference_mode_enabled()
        values = torch.zeros(len(rows), 768)
        values[:, :3] = torch.tensor(rows)
        return SimpleNamespace(pooler_output=values)

    model = SimpleNamespace(
        to=lambda _: model,
        eval=lambda: model,
        get_image_features=features,
        get_text_features=features,
    )

    def load(*args, **kwargs):
        assert kwargs["trust_remote_code"] is False
        assert kwargs["use_safetensors"] is True
        loads.append(kwargs["revision"])
        return model

    monkeypatch.setattr("transformers.AutoModel.from_pretrained", load)
    monkeypatch.setattr(
        "transformers.AutoProcessor.from_pretrained", lambda *a, **k: processor
    )
    settings = Settings(
        _env_file=None, embedding_model_cache=tmp_path, embedding_batch_size=2
    )
    value = create_embeddings(settings)
    yield value, loads, batches
    value.close()


def test_batches_preserve_image_order_and_normalize_vectors(adapter):
    model, loads, batches = adapter
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (3, 4, 0), (1, 0, 0)]
    result = model.embed_images([(image_bytes(color), "image/png") for color in colors])
    assert batches == [2, 2, 1]
    assert len(loads) == 1
    assert [row[:3] for row in result] == [
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
        [0.6, 0.8, 0],
        [1, 0, 0],
    ]
    assert all(
        len(row) == 768 and sum(x * x for x in row) == pytest.approx(1)
        for row in result
    )


def test_concurrent_text_searches_load_one_model_without_api_key(adapter):
    model, loads, _ = adapter
    with ThreadPoolExecutor(max_workers=4) as executor:
        vectors = list(
            executor.map(lambda _: model.embed(text="a microscope"), range(8))
        )
    assert len(loads) == 1
    assert all(vector[:3] == [0.6, 0.8, 0] for vector in vectors)


def test_failed_model_load_is_sanitized_and_can_retry(adapter, monkeypatch):
    model, _, _ = adapter
    real_load = __import__("transformers").AutoModel.from_pretrained

    def fail(*args, **kwargs):
        raise OSError("private-cache-path-or-credential")

    monkeypatch.setattr("transformers.AutoModel.from_pretrained", fail)
    with pytest.raises(SearchError) as error:
        model.embed(text="query")
    assert error.value.code == "embedding_configuration"
    assert "private-cache" not in str(error.value)
    monkeypatch.setattr("transformers.AutoModel.from_pretrained", real_load)
    assert model.embed(text="query")[:3] == [0.6, 0.8, 0]


def test_model_revision_invalidates_cached_vectors_and_siglip_hash_is_unchanged():
    original = Settings(_env_file=None)
    updated = original.model_copy(update={"embedding_revision": "a" * 40})
    assert original.config_hash != updated.config_hash
    assert (
        original.config_hash
        == hashlib.sha256(
            f"{original.embedding_model}:{original.embedding_revision}:768:siglip-rgb224-text64-v1".encode()
        ).hexdigest()
    )


def test_retired_embedding_model_is_rejected():
    with pytest.raises(ValueError, match="Use SigLIP 2 Base"):
        create_embeddings(
            Settings(_env_file=None, embedding_model="gemini-embedding-2")
        )


def test_wrong_siglip_dimension_fails_before_loading_weights():
    with pytest.raises(ValueError, match="768"):
        SiglipEmbeddings(Settings(_env_file=None, embedding_dimensions=1536))


def test_invalid_local_vectors_are_rejected(adapter):
    model, _, _ = adapter
    with pytest.raises(SearchError) as error:
        model.embed(image=image_bytes((0, 0, 0)))
    assert error.value.code == "invalid_embedding"
