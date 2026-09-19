import hashlib
import io
from uuid import NAMESPACE_URL, uuid5

import pytest
from app.core.config import Settings
from app.core.db import make_engine, migrate
from app.services.embeddings import SearchError
from app.services.ingestion import create_generation
from app.services.qdrant_store import VectorStore
from PIL import Image as PillowImage
from qdrant_client import QdrantClient


class FakeEmbeddings:
    calls = 0
    fail = False

    def embed(self, *, image=None, text=None, **_kwargs):
        self.calls += 1
        if self.fail:
            raise SearchError("Embedding fixture failed", "embedding_configuration")
        if image:
            with PillowImage.open(io.BytesIO(image)) as picture:
                red, green, blue = picture.getpixel((0, 0))
                return [float(red), float(green), float(blue)]
        return [1.0, 0.0, 0.0] if text == "red" else [0.0, 1.0, 0.0]

    def close(self):
        pass


@pytest.fixture
def setup(tmp_path):
    image_root = tmp_path / "images"
    image_root.mkdir()
    settings = Settings(
        _env_file=None,
        sqlite_path=tmp_path / "search/catalog.sqlite3",
        image_root=image_root,
        embedding_dimensions=3,
        gemini_api_key="fake-for-tests",
    )
    engine = make_engine(settings)
    migrate(settings)
    selected = []
    for name, color in (
        ("red", (255, 0, 0)),
        ("green", (0, 255, 0)),
        ("blue", (0, 0, 255)),
    ):
        path = image_root / f"{name}.png"
        PillowImage.new("RGB", (16, 16), color).save(path)
        selected.append(
            {
                "image_id": str(uuid5(NAMESPACE_URL, name)),
                "location": path.name,
                "relative_path": path.name,
                "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
                "mime_type": "image/png",
                "width": 16,
                "height": 16,
                "title": name,
                "associations": [
                    {
                        "record_uid": f"co-{name}",
                        "image_uid": f"i-{name}",
                        "source_json": "fixture.json",
                        "title": name,
                        "licence": "CC BY-NC-SA 4.0",
                        "description": "",
                        "credit": "Fixture credit",
                        "copyright": "Fixture copyright",
                        "places": ["Paris"] if name == "green" else ["London"],
                        "categories": ["Optics"] if name == "red" else ["Computing"],
                        "date_ranges": []
                        if name == "blue"
                        else [
                            {
                                "date_from": 1850 if name == "red" else 1900,
                                "date_to": 1870 if name == "red" else 1920,
                            }
                        ],
                    }
                ],
            }
        )
    report = {"scanned": 3, "selected": 3, "skipped": {}}
    generation_id = create_generation(engine, settings, selected, report)
    vectors = VectorStore(settings, QdrantClient(":memory:"))
    embeddings = FakeEmbeddings()
    yield settings, engine, selected, report, generation_id, vectors, embeddings
    vectors.close()
    engine.dispose()
