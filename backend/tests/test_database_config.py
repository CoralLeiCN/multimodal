import pytest
from app.core.config import Settings
from app.core.db import make_cache_engine, make_engine
from app.models import EmbeddingCache
from sqlalchemy import inspect
from sqlmodel import Session


@pytest.mark.parametrize("url", [None, ""])
def test_missing_database_url_never_creates_a_catalogue(tmp_path, url):
    settings = Settings(
        _env_file=None, DATABASE_URL=url, search_data_dir=tmp_path / "search"
    )
    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        make_engine(settings)
    assert not settings.data_dir.exists()


@pytest.mark.parametrize(
    "url", ["sqlite:///catalog.sqlite3", "mysql://localhost/catalogue"]
)
def test_catalogue_requires_postgresql(url):
    with pytest.raises(ValueError, match="PostgreSQL"):
        make_engine(Settings(_env_file=None, DATABASE_URL=url))


def test_cache_stays_local_and_reopens_without_neon(tmp_path):
    settings = Settings(_env_file=None, search_data_dir=tmp_path / "search")
    engine = make_cache_engine(settings)
    with Session(engine) as session:
        session.add(
            EmbeddingCache(
                key="cached", checksum="image", config_hash="model", vector=[1.0, 0.0]
            )
        )
        session.commit()
    engine.dispose()
    reopened = make_cache_engine(settings)
    try:
        assert inspect(reopened).get_table_names() == ["embedding_cache"]
        with Session(reopened) as session:
            assert session.get(EmbeddingCache, "cached").vector == [1.0, 0.0]
        assert list(settings.data_dir.iterdir()) == [settings.embedding_cache_path]
    finally:
        reopened.dispose()
