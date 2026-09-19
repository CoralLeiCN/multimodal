import pytest
from app.models import EmbeddingCache, Generation, Image, Ingestion, ServiceState
from app.services.embeddings import SearchError
from app.services.ingestion import create_generation, run_ingestion
from sqlmodel import Session, select


def test_completed_run_publishes_and_reuses_embeddings(setup):
    settings, engine, selected, report, generation_id, vectors, embeddings = setup
    result = run_ingestion(engine, settings, generation_id, embeddings, vectors)
    assert result["indexed"] == result["embedded"] == 3
    replacement = create_generation(engine, settings, selected, report)
    result = run_ingestion(engine, settings, replacement, embeddings, vectors)
    assert result["embedded"] == 0 and result["reused"] == 3
    assert embeddings.calls == 3
    with Session(engine) as session:
        assert session.get(ServiceState, 1).active_generation == replacement
        assert all(
            row.status == "indexed" and row.indexed_at
            for row in session.exec(select(Ingestion)).all()
        )


def test_resume_recovers_write_before_sqlite_update(setup):
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


def test_failed_generation_keeps_previous_active_index(setup):
    settings, engine, selected, report, first, vectors, embeddings = setup
    run_ingestion(engine, settings, first, embeddings, vectors)
    second = create_generation(engine, settings, selected, report)
    with Session(engine) as session:
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
