import gzip
import sqlite3
import subprocess
import sys

import pytest
from app.models import EmbeddingCache
from sqlmodel import Session

from scripts.export_catalogue import ROOT, export_catalogue


def test_export_preserves_local_cache_and_removes_vectors_from_snapshot(
    setup, tmp_path
):
    settings, engine, _selected, _report, generation_id, *_ = setup
    cache_key = "private-local-cache-marker"
    with Session(engine) as session:
        session.add(
            EmbeddingCache(
                key=cache_key, checksum="checksum", config_hash="config", vector=[1.0]
            )
        )
        session.commit()
    output = tmp_path / "catalog.sqlite3.gz"
    export_catalogue(settings.sqlite_path, output)
    with Session(engine) as session:
        assert session.get(EmbeddingCache, cache_key).vector == [1.0]
    contents = gzip.decompress(output.read_bytes())
    assert cache_key.encode() not in contents
    restored = tmp_path / "restored.sqlite3"
    restored.write_bytes(contents)
    with sqlite3.connect(restored) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT count(*) FROM embedding_cache").fetchone() == (0,)
        assert db.execute("SELECT id FROM index_generations").fetchall() == [
            (generation_id,)
        ]
        assert db.execute("SELECT count(*) FROM images").fetchone() == (3,)
        assert db.execute("SELECT count(*) FROM image_associations").fetchone() == (3,)


def test_export_failure_preserves_existing_snapshot(tmp_path):
    source = tmp_path / "missing.sqlite3"
    output = tmp_path / "catalog.sqlite3.gz"
    output.write_bytes(b"previous snapshot")
    with pytest.raises(sqlite3.OperationalError):
        export_catalogue(source, output)
    assert not source.exists()
    assert output.read_bytes() == b"previous snapshot"
    with pytest.raises(ValueError, match="different path"):
        export_catalogue(output, output)
    assert output.read_bytes() == b"previous snapshot"


def test_shared_snapshot_restores_without_cache_and_refuses_overwrite(tmp_path):
    output = tmp_path / "search/catalog.sqlite3"
    command = [
        sys.executable,
        str(ROOT / "scripts/restore_catalogue.py"),
        "--output",
        str(output),
    ]
    restored = subprocess.run(command, capture_output=True, text=True, check=False)
    assert restored.returncode == 0, restored.stderr
    before = output.read_bytes()
    with sqlite3.connect(output) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT count(*) FROM embedding_cache").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM images").fetchone()[0] > 0
    refused = subprocess.run(command, capture_output=True, text=True, check=False)
    assert refused.returncode == 1
    assert "already exists" in refused.stderr
    assert output.read_bytes() == before
