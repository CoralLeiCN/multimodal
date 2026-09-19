import pytest
from app.models import EmbeddingCache, Generation, Image, Ingestion, ServiceState
from app.services.embeddings import SearchError
from app.services.ingestion import create_generation, run_ingestion
from sqlalchemy import inspect
from sqlmodel import Session, select


def test_completed_run_publishes_and_reuses_embeddings(setup, cache_engine):
    settings, engine, selected, report, generation_id, vectors, embeddings = setup
    result = run_ingestion(engine, settings, generation_id, embeddings, vectors)
    assert result["indexed"] == result["embedded"] == 3
    replacement = create_generation(engine, settings, selected, report)
    result = run_ingestion(engine, settings, replacement, embeddings, vectors)
    assert result["embedded"] == 0 and result["reused"] == 3
    assert embeddings.calls == 3
    assert not inspect(engine).has_table("embedding_cache")
    assert inspect(cache_engine).get_table_names() == ["embedding_cache"]
    with Session(cache_engine) as session:
        assert len(session.exec(select(EmbeddingCache)).all()) == 3
    with Session(engine) as session:
        assert session.get(ServiceState, 1).active_generation == replacement
        assert all(
            row.status == "indexed" and row.indexed_at
            for row in session.exec(select(Ingestion)).all()
        )


def test_resume_recovers_write_before_catalogue_update(setup):
    settings, engine, _selected, _report, generation_id, vectors, embeddings = setup
    with Session(engine) as session:
        generation = session.get(Generation, generation_id)
        image = session.exec(
            select(Image).where(Image.generation_id == generation_id)
        ).first()
        tracking = session.get(Ingestion, (generation_id, image.image_id))
        tracking.status = "upserting"
        session.add(tracking)
        session.commit()
        session.refresh(generation)
        session.refresh(image)
        vectors.ensure_collection(generation)
        vectors.upsert(generation, image, [1.0, 0.0, 0.0])
    result = run_ingestion(engine, settings, generation_id, embeddings, vectors)
    assert result["indexed"] == 3 and result["embedded"] == 2
    assert embeddings.calls == 2


def test_resume_reuses_cache_committed_before_catalogue_update(setup, monkeypatch):
    settings, engine, _selected, _report, generation_id, vectors, embeddings = setup
    commit = Session.commit

    def interrupt_catalogue_update(session):
        if session.bind is engine and any(
            isinstance(row, Ingestion) and row.status == "embedded"
            for row in session.dirty
        ):
            raise KeyboardInterrupt("interrupted after committing the cache")
        return commit(session)

    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", interrupt_catalogue_update)
        with pytest.raises(KeyboardInterrupt):
            run_ingestion(engine, settings, generation_id, embeddings, vectors)
    assert embeddings.calls == 1
    result = run_ingestion(engine, settings, generation_id, embeddings, vectors)
    assert result["indexed"] == 3
    assert result["reused"] == 1
    assert result["embedded"] == 2
    assert embeddings.calls == 3


def test_failed_generation_keeps_previous_active_index(setup, cache_engine):
    settings, engine, selected, report, first, vectors, embeddings = setup
    run_ingestion(engine, settings, first, embeddings, vectors)
    second = create_generation(engine, settings, selected, report)
    with Session(cache_engine) as session:
        for cache in session.exec(select(EmbeddingCache)).all():
            session.delete(cache)
        session.commit()
    embeddings.fail = True
    with pytest.raises(SearchError):
        run_ingestion(engine, settings, second, embeddings, vectors)
    with Session(engine) as session:
        assert session.get(ServiceState, 1).active_generation == first
        assert session.get(Generation, second).status == "failed"


def test_resume_rebuilds_missing_points_from_cache(setup):
    settings, engine, _selected, _report, generation_id, vectors, embeddings = setup
    run_ingestion(engine, settings, generation_id, embeddings, vectors)
    with Session(engine) as session:
        generation = session.get(Generation, generation_id)
        generation.status = "building"
        session.delete(session.get(ServiceState, 1))
        session.add(generation)
        session.commit()
        session.refresh(generation)
        vectors.client.delete_collection(generation.collection)
    result = run_ingestion(engine, settings, generation_id, embeddings, vectors)
    assert result["reused"] == 3 and result["embedded"] == 0
    assert embeddings.calls == 3


def test_permanent_collection_adds_images_and_reuses_existing(setup):
    from app.services.search import SearchService

    settings, engine, selected, report, first, vectors, embeddings = setup
    run_ingestion(engine, settings, first, embeddings, vectors)
    # Adopt an existing generation without copying or renaming its collection.
    with Session(engine) as session:
        settings.qdrant_collection_name = session.get(Generation, first).collection
    search = SearchService(engine, settings, vectors, embeddings)
    assert search.status().indexed_images == 3
    extra = dict(selected[0], image_id="00000000-0000-0000-0000-000000000001")
    repeated = create_generation(engine, settings, [selected[0], extra], report)
    assert repeated == first
    assert search.status().status == "empty"
    result = run_ingestion(engine, settings, repeated, embeddings, vectors)
    assert result["indexed"] == 4
    assert embeddings.calls == 3
    assert len(vectors.client.get_collections().collections) == 1
    assert search.status().indexed_images == 4
    assert len(search.browse(limit=10).items) == 4
    import json

    snapshot = settings.data_dir / "selections" / f"{first}.jsonl"
    assert len([json.loads(line) for line in snapshot.read_text().splitlines()]) == 4


def test_permanent_failure_can_resume_and_model_change_is_rejected(setup, cache_engine):
    settings, engine, selected, report, first, vectors, embeddings = setup
    run_ingestion(engine, settings, first, embeddings, vectors)
    with Session(engine) as session:
        settings.qdrant_collection_name = session.get(Generation, first).collection
    with Session(cache_engine) as session:
        for cache in session.exec(select(EmbeddingCache)).all():
            session.delete(cache)
        session.commit()
    extra = dict(selected[0], image_id="00000000-0000-0000-0000-000000000001")
    create_generation(engine, settings, [extra], report)
    embeddings.fail = True
    with pytest.raises(SearchError):
        run_ingestion(engine, settings, first, embeddings, vectors)
    embeddings.fail = False
    assert run_ingestion(engine, settings, first, embeddings, vectors)["indexed"] == 4
    settings.embedding_dimensions = 5
    with pytest.raises(ValueError, match="original embedding settings"):
        create_generation(engine, settings, selected, report)


def test_new_permanent_collection_and_foreign_collection_guard(setup):
    settings, engine, selected, report, first, vectors, embeddings = setup
    run_ingestion(engine, settings, first, embeddings, vectors)
    settings.qdrant_collection_name = "permanent_images"
    permanent = create_generation(engine, settings, selected[:1], report)
    run_ingestion(engine, settings, permanent, embeddings, vectors)
    with Session(engine) as session:
        generation = session.get(Generation, permanent)
        assert generation.collection == "permanent_images"
        generation.id = "unrelated-catalogue"
        with pytest.raises(SearchError, match="another catalogue"):
            vectors.ensure_collection(generation)


def test_permanent_update_replaces_vector_and_metadata_without_duplicates(setup):
    import hashlib
    from copy import deepcopy

    from PIL import Image as PillowImage

    settings, engine, selected, report, first, vectors, embeddings = setup
    run_ingestion(engine, settings, first, embeddings, vectors)
    with Session(engine) as session:
        settings.qdrant_collection_name = session.get(Generation, first).collection
    changed = deepcopy(selected[0])
    path = settings.image_root / changed["relative_path"]
    PillowImage.new("RGB", (16, 16), (0, 255, 0)).save(path)
    changed["checksum"] = hashlib.sha256(path.read_bytes()).hexdigest()
    changed["title"] = "updated"
    changed["associations"][0]["places"] = ["Berlin"]
    create_generation(engine, settings, [changed], report)
    result = run_ingestion(engine, settings, first, embeddings, vectors)
    assert result["indexed"] == 3
    with Session(engine) as session:
        generation = session.get(Generation, first)
        image = session.get(Image, (first, changed["image_id"]))
        assert image.title == "updated"
        assert vectors.matches(generation, image)
        assert vectors.vector(generation, image.image_id) == [0, 1, 0]
        assert image.filter_metadata[0]["place"] == ["berlin"]
