import json

import pytest
from app.models import EmbeddingCache, Generation, Image, Ingestion, ServiceState
from app.services.embeddings import SearchError
from app.services.ingestion import create_generation, run_ingestion
from sqlalchemy import inspect
from sqlmodel import Session, select


def test_r2_indexing_uses_local_bytes_and_links_new_and_appended_images(
    setup, monkeypatch
):
    settings, engine, selected, report, _, vectors, embeddings = setup
    settings.r2_endpoint_url = "https://" + "a" * 32 + ".r2.cloudflarestorage.com"
    settings.r2_bucket = "smg-images"
    settings.qdrant_collection_name = "r2_indexing_test"

    def forbid_r2(*args, **kwargs):
        pytest.fail("Indexing must not contact R2")

    monkeypatch.setattr("app.services.r2_catalogue.boto3.client", forbid_r2)
    local_bytes = {
        (settings.image_root / item["relative_path"]).read_bytes() for item in selected
    }
    embed = embeddings.embed

    def embed_local(*, image, mime_type):
        assert image in local_bytes
        return embed(image=image, mime_type=mime_type)

    monkeypatch.setattr(embeddings, "embed", embed_local)
    generation_id = create_generation(engine, settings, selected[:1], report)
    snapshot = settings.data_dir / "selections" / f"{generation_id}.jsonl"
    rows = [json.loads(line) for line in snapshot.read_text().splitlines()]
    assert rows[0]["r2_url"] == settings.r2_endpoint_url + "/smg-images/red.png"
    run_ingestion(engine, settings, generation_id, embeddings, vectors)
    assert create_generation(engine, settings, selected, report) == generation_id
    result = run_ingestion(engine, settings, generation_id, embeddings, vectors)
    assert result["indexed"] == 3
    assert embeddings.calls == 3
    with Session(engine) as session:
        images = session.exec(
            select(Image).where(Image.generation_id == generation_id)
        ).all()
        assert all(
            image.r2_url
            == settings.r2_endpoint_url + "/smg-images/" + image.relative_path
            for image in images
        )
    assert all(json.loads(line)["r2_url"] for line in snapshot.read_text().splitlines())


@pytest.mark.parametrize("already_ready", [False, True])
def test_resume_adds_r2_links_to_existing_generation(setup, monkeypatch, already_ready):
    settings, engine, _, _, generation_id, vectors, embeddings = setup
    if already_ready:
        run_ingestion(engine, settings, generation_id, embeddings, vectors)
    settings.r2_endpoint_url = "https://" + "a" * 32 + ".r2.cloudflarestorage.com"
    settings.r2_bucket = "smg-images"
    settings.r2_prefix = "archive"

    def forbid_r2(*args, **kwargs):
        pytest.fail("Resume must not contact R2")

    monkeypatch.setattr("app.services.r2_catalogue.boto3.client", forbid_r2)
    result = run_ingestion(engine, settings, generation_id, embeddings, vectors)
    assert result["embedded"] == (0 if already_ready else 3)
    assert embeddings.calls == 3
    with Session(engine) as session:
        images = session.exec(select(Image)).all()
        assert all(
            image.r2_url
            == settings.r2_endpoint_url + "/smg-images/archive/" + image.relative_path
            for image in images
        )
    snapshot = settings.data_dir / "selections" / f"{generation_id}.jsonl"
    assert all(json.loads(line)["r2_url"] for line in snapshot.read_text().splitlines())


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
    completed_embeddings = embeddings.calls
    assert 1 <= completed_embeddings <= 3
    result = run_ingestion(engine, settings, generation_id, embeddings, vectors)
    assert result["indexed"] == 3
    assert result["reused"] == completed_embeddings
    assert result["embedded"] == 3 - completed_embeddings
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


@pytest.mark.parametrize("workers", [1, 2, 10])
@pytest.mark.parametrize("batch_size", [1, 10])
def test_ingestion_overlaps_requests_within_worker_limit(
    setup, monkeypatch, workers, batch_size
):
    import hashlib
    from threading import Barrier, Lock, get_ident
    from uuid import uuid4

    from PIL import Image as PillowImage

    settings, engine, selected, report, _, vectors, embeddings = setup
    sample = []
    for number in range(workers * 2 * batch_size):
        path = settings.image_root / f"parallel-{number}.png"
        PillowImage.new("RGB", (16, 16), (number + 1, 20, 30)).save(path)
        sample.append(
            dict(
                selected[0],
                image_id=str(uuid4()),
                relative_path=path.name,
                location=path.name,
                checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
    generation_id = create_generation(engine, settings, sample, report)
    barrier = Barrier(workers, timeout=15)
    lock = Lock()
    active = peak = calls = 0

    batch_sizes = []

    def embed_images(images):
        nonlocal active, peak, calls
        with lock:
            active += 1
            calls += 1
            batch_sizes.append(len(images))
            peak = max(peak, active)
        try:
            barrier.wait()
            return [[1.0, 0.0, 0.0] for _ in images]
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(embeddings, "embed_images", embed_images)
    messages = []
    main_thread = get_ident()

    def progress(message):
        assert get_ident() == main_thread
        messages.append(message)

    # Omit workers for the 10-worker case to exercise the production default.
    options = {} if workers == 10 else {"workers": workers}
    if batch_size != 10:
        options["batch_size"] = batch_size
    result = run_ingestion(
        engine,
        settings,
        generation_id,
        embeddings,
        vectors,
        progress,
        **options,
    )
    assert peak == workers
    assert calls == workers * 2
    assert batch_sizes == [batch_size] * calls
    assert result["embedded"] == result["indexed"] == workers * 2 * batch_size
    assert result["reused"] == 0
    assert [message.split()[0] for message in messages] == [
        f"[{number}/{len(sample)}]" for number in range(1, len(sample) + 1)
    ]


@pytest.mark.parametrize("batch_size", [2, 10])
def test_concurrent_duplicate_content_embeds_once(setup, cache_engine, batch_size):
    from uuid import uuid4

    settings, engine, selected, report, _, vectors, embeddings = setup
    sample = [dict(selected[0], image_id=str(uuid4())) for _ in range(10)]
    generation_id = create_generation(engine, settings, sample, report)
    result = run_ingestion(
        engine, settings, generation_id, embeddings, vectors, batch_size=batch_size
    )
    assert result["indexed"] == 10
    assert result["embedded"] == embeddings.calls == 1
    assert result["reused"] == 9
    with Session(cache_engine) as session:
        assert len(session.exec(select(EmbeddingCache)).all()) == 1


def test_failure_drains_active_worker_and_leaves_remaining_images_for_resume(
    setup,
    monkeypatch,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event, Lock

    settings, engine, _, _, generation_id, vectors, embeddings = setup
    barrier = Barrier(2, timeout=15)
    release = Event()
    failed = Event()
    lock = Lock()
    calls = 0
    commit = Session.commit

    def commit_and_signal(session):
        failure = session.bind is engine and any(
            isinstance(row, Ingestion) and row.status == "failed"
            for row in session.dirty
        )
        result = commit(session)
        if failure:
            failed.set()
        return result

    def embed(**kwargs):
        nonlocal calls
        with lock:
            calls += 1
            call = calls
        barrier.wait()
        if call == 1:
            raise SearchError("bad credentials", "embedding_configuration")
        assert release.wait(15)
        return [1.0, 0.0, 0.0]

    with monkeypatch.context() as patch:
        patch.setattr(embeddings, "embed", embed)
        patch.setattr(Session, "commit", commit_and_signal)
        with ThreadPoolExecutor(max_workers=1) as runner:
            future = runner.submit(
                run_ingestion,
                engine,
                settings,
                generation_id,
                embeddings,
                vectors,
                workers=2,
                batch_size=1,
            )
            try:
                assert failed.wait(15)
                assert not future.done()
                with Session(engine) as session:
                    assert session.get(Generation, generation_id).status == "building"
                    assert session.get(ServiceState, 1) is None
            finally:
                release.set()
            with pytest.raises(SearchError, match="bad credentials"):
                future.result(timeout=15)
    assert calls == 2
    with Session(engine) as session:
        assert session.get(Generation, generation_id).status == "failed"
        assert sorted(row.status for row in session.exec(select(Ingestion)).all()) == [
            "failed",
            "indexed",
            "pending",
        ]
        assert session.get(ServiceState, 1) is None
    result = run_ingestion(engine, settings, generation_id, embeddings, vectors)
    assert result["indexed"] == 3
    assert result["embedded"] == 2
    assert result["reused"] == 1


def test_retry_after_vector_failure_reuses_embedding_without_double_counting(
    setup,
    monkeypatch,
):
    settings, engine, _, _, generation_id, vectors, embeddings = setup
    original = vectors.upsert
    attempted = set()

    def fail_once(generation, image, vector):
        if image.image_id not in attempted:
            attempted.add(image.image_id)
            raise SearchError("temporary vector failure", "vector_write_failed")
        return original(generation, image, vector)

    monkeypatch.setattr(vectors, "upsert", fail_once)
    monkeypatch.setattr("app.services.ingestion.time.sleep", lambda _: None)
    result = run_ingestion(engine, settings, generation_id, embeddings, vectors)
    assert result["indexed"] == result["embedded"] == embeddings.calls == 3
    assert result["reused"] == 0
    with Session(engine) as session:
        assert all(row.attempts == 2 for row in session.exec(select(Ingestion)).all())


def test_batches_keep_vector_order_and_flush_partial_batch(setup, monkeypatch):
    settings, engine, _, _, generation_id, vectors, embeddings = setup
    batches = []
    embed_images = embeddings.embed_images

    def capture(images):
        batches.append(images)
        return embed_images(images)

    monkeypatch.setattr(embeddings, "embed_images", capture)
    result = run_ingestion(
        engine, settings, generation_id, embeddings, vectors, batch_size=2, workers=1
    )
    assert [len(batch) for batch in batches] == [2, 1]
    assert result["embedded"] == result["indexed"] == 3
    with Session(engine) as session:
        generation = session.get(Generation, generation_id)
        for image in session.exec(select(Image)).all():
            expected = {"red": [1, 0, 0], "green": [0, 1, 0], "blue": [0, 0, 1]}[
                image.title
            ]
            assert vectors.vector(generation, image.image_id) == expected


def test_batch_only_sends_uncached_content(setup, monkeypatch):
    settings, engine, selected, report, generation_id, vectors, embeddings = setup
    warm = create_generation(engine, settings, selected[:1], report)
    run_ingestion(engine, settings, warm, embeddings, vectors)
    batches = []
    embed_images = embeddings.embed_images

    def capture(images):
        batches.append(images)
        return embed_images(images)

    monkeypatch.setattr(embeddings, "embed_images", capture)
    result = run_ingestion(engine, settings, generation_id, embeddings, vectors)
    assert [len(batch) for batch in batches] == [2]
    assert result["embedded"] == 2
    assert result["reused"] == 1
    cached_bytes = (settings.image_root / selected[0]["relative_path"]).read_bytes()
    assert all(data != cached_bytes for batch in batches for data, _ in batch)


@pytest.mark.parametrize("bad_vectors", [[[1, 0, 0]], [[1, 0, 0], [0, 1], [0, 0, 1]]])
def test_bad_batch_response_caches_nothing_and_can_resume(
    setup, cache_engine, monkeypatch, bad_vectors
):
    settings, engine, _, _, generation_id, vectors, embeddings = setup
    with monkeypatch.context() as patch:
        patch.setattr(embeddings, "embed_images", lambda images: bad_vectors)
        with pytest.raises(SearchError) as error:
            run_ingestion(engine, settings, generation_id, embeddings, vectors)
        assert error.value.code == "invalid_embedding"
    with Session(cache_engine) as session:
        assert session.exec(select(EmbeddingCache)).all() == []
    with Session(engine) as session:
        assert session.get(ServiceState, 1) is None
        assert all(
            row.status == "failed" and row.attempts == 1
            for row in session.exec(select(Ingestion)).all()
        )
    result = run_ingestion(
        engine, settings, generation_id, embeddings, vectors, batch_size=2
    )
    assert result["indexed"] == result["embedded"] == 3


def test_large_batch_splits_requests_and_retries_only_uncached_part(setup, monkeypatch):
    settings, engine, selected, _, generation_id, vectors, embeddings = setup
    sizes = [
        (settings.image_root / item["relative_path"]).stat().st_size
        for item in selected
    ]
    cap = max(sizes) * 2
    monkeypatch.setattr("app.services.ingestion.MAX_BATCH_IMAGE_BYTES", cap)
    monkeypatch.setattr("app.services.ingestion.time.sleep", lambda _: None)
    batches = []
    embed_images = embeddings.embed_images

    def fail_second_request_once(images):
        batches.append(images)
        assert sum(len(data) for data, _ in images) <= cap
        if len(batches) == 2:
            raise SearchError("temporary quota failure", "embedding_quota")
        return embed_images(images)

    monkeypatch.setattr(embeddings, "embed_images", fail_second_request_once)
    result = run_ingestion(engine, settings, generation_id, embeddings, vectors)
    assert [len(batch) for batch in batches] == [2, 1, 1]
    assert batches[1] == batches[2]
    assert result["embedded"] == embeddings.calls == result["indexed"] == 3
    assert result["reused"] == 0


def test_same_sample_skips_indexed_images_without_embedding_or_vector_writes(
    setup,
    cache_engine,
    monkeypatch,
):
    settings, engine, selected, report, generation_id, vectors, embeddings = setup
    run_ingestion(engine, settings, generation_id, embeddings, vectors)
    with Session(engine) as session:
        settings.qdrant_collection_name = session.get(
            Generation, generation_id
        ).collection
        attempts = {
            row.image_id: row.attempts for row in session.exec(select(Ingestion)).all()
        }
    # Confirm the skip depends on the verified index, not the local embedding cache.
    with Session(cache_engine) as session:
        for entry in session.exec(select(EmbeddingCache)).all():
            session.delete(entry)
        session.commit()
    repeated = create_generation(engine, settings, selected, report)
    assert repeated == generation_id

    def forbidden(*args, **kwargs):
        pytest.fail("Verified indexed images must not be read, embedded, or upserted")

    monkeypatch.setattr(embeddings, "embed_images", forbidden)
    monkeypatch.setattr(vectors, "upsert", forbidden)
    monkeypatch.setattr("app.services.ingestion.safe_path", forbidden)
    result = run_ingestion(engine, settings, repeated, embeddings, vectors)
    assert result["indexed"] == result["reused"] == len(selected)
    assert result["embedded"] == 0
    with Session(engine) as session:
        assert {image.image_id for image in session.exec(select(Image)).all()} == {
            image["image_id"] for image in selected
        }
        assert {
            row.image_id: row.attempts for row in session.exec(select(Ingestion)).all()
        } == attempts
        assert session.get(Generation, generation_id).status == "ready"
