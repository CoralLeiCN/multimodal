import hashlib
import io
import os
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from app.core.config import Settings, postgres_url
from app.core.db import make_cache_engine, make_engine, migrate
from app.services.embeddings import SearchError
from app.services.ingestion import create_generation
from app.services.qdrant_store import VectorStore
from PIL import Image as PillowImage
from pydantic import SecretStr
from qdrant_client import QdrantClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

# Set before test modules import app.main, even on a configured developer machine.
os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"


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

    def embed_images(self, images):
        return [self.embed(image=data, mime_type=mime) for data, mime in images]

    def close(self):
        pass


@pytest.fixture(scope="session")
def postgres_admin():
    raw = os.environ.get("TEST_POSTGRES_URL")
    if not raw:
        pytest.fail(
            "Set TEST_POSTGRES_URL to a disposable PostgreSQL database's direct URL.",
            pytrace=False,
        )
    url = make_url(postgres_url(SecretStr(raw)))
    if url.host and "-pooler." in url.host:
        pytest.fail(
            "TEST_POSTGRES_URL must use a direct connection, not the Neon pooler.",
            pytrace=False,
        )
    engine = create_engine(url, hide_parameters=True)
    yield engine
    engine.dispose()


@pytest.fixture
def database_factory(postgres_admin, tmp_path):
    databases = []

    def create():
        schema = "test_catalogue_" + uuid4().hex
        with postgres_admin.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        url = postgres_admin.url.update_query_dict(
            {"options": f"-csearch_path={schema}"}
        ).render_as_string(hide_password=False)
        settings = Settings(
            _env_file=None,
            DATABASE_URL=url,
            DATABASE_URL_UNPOOLED=url,
            search_data_dir=tmp_path / schema,
            embedding_dimensions=3,
            gemini_api_key="fake-for-tests",
        )
        engine = make_engine(settings)
        databases.append((schema, engine))
        migrate(settings)
        return settings, engine

    yield create
    for schema, engine in reversed(databases):
        engine.dispose()
        with postgres_admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture
def setup(tmp_path, database_factory):
    image_root = tmp_path / "images"
    image_root.mkdir()
    settings, engine = database_factory()
    settings.image_root = image_root
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


@pytest.fixture
def cache_engine(setup):
    engine = make_cache_engine(setup[0])
    yield engine
    engine.dispose()
