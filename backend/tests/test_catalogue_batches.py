import json
from copy import deepcopy
from uuid import UUID

import pytest
from app.models import Association, Generation, Image, Ingestion
from app.services.ingestion import create_generation, save_selection
from sqlalchemy import event
from sqlmodel import Session, select


def sample_images(template, count):
    return [
        dict(deepcopy(template), image_id=str(UUID(int=number + 1)))
        for number in range(count)
    ]


@pytest.mark.parametrize("extend", [False, True])
def test_large_catalogue_uses_bounded_queries_and_preserves_rows(setup, extend):
    settings, engine, selected, report, *_ = setup
    sample = sample_images(selected[0], 1001)
    # More associations than fit in one SQL batch; also retain images with none.
    sample[0]["associations"] = [
        dict(sample[0]["associations"][0], record_uid=f"co{number}")
        for number in range(501)
    ]
    sample[-1]["associations"] = []
    if extend:
        settings.qdrant_collection_name = "batch-test"
        previous = create_generation(engine, settings, sample[:2], report)
        with Session(engine) as session:
            tracking = session.get(Ingestion, (previous, sample[0]["image_id"]))
            tracking.status, tracking.attempts = "indexed", 3
            tracking.indexed_at = "2026-01-01T00:00:00+00:00"
            session.add(tracking)
            session.commit()
        sample[0]["title"] = "Updated title"
        sample[0]["associations"][0]["credit"] = "Updated credit"

    statements = []

    def count_sql(_conn, _cursor, statement, *_args):
        statements.append(statement)

    messages = []
    event.listen(engine, "before_cursor_execute", count_sql)
    try:
        generation_id = create_generation(
            engine, settings, sample, report, progress=messages.append
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_sql)
    # Includes both catalogue writes and snapshot reads. Per-image queries would
    # require thousands of statements for this workload.
    assert len(statements) < 50
    assert any("[1001/1001]" in message for message in messages)
    assert any("Catalogue committed" in message for message in messages)
    snapshot = settings.data_dir / "selections" / f"{generation_id}.jsonl"
    rows = [json.loads(line) for line in snapshot.read_text().splitlines()]
    assert len(rows) == 1001
    assert [row["image_id"] for row in rows] == sorted(
        item["image_id"] for item in sample
    )
    assert rows[0]["title"] == sample[0]["title"]
    assert len(rows[0]["associations"]) == 501
    assert (
        rows[0]["associations"][0]["credit"] == sample[0]["associations"][0]["credit"]
    )
    assert rows[0]["associations"][0]["description"] == ""
    assert rows[-1]["associations"] == []
    with Session(engine) as session:
        assert session.get(Generation, generation_id).count == 1001
        tracking = session.get(Ingestion, (generation_id, sample[0]["image_id"]))
        assert tracking.status == ("indexed" if extend else "pending")
        assert tracking.attempts == (3 if extend else 0)
        if extend:
            assert generation_id == previous
            assert tracking.indexed_at == "2026-01-01T00:00:00+00:00"


def catalogue_rows(engine):
    with Session(engine) as session:
        return {
            model.__tablename__: sorted(
                (row.model_dump_json() for row in session.exec(select(model))),
            )
            for model in (Generation, Image, Association, Ingestion)
        }


@pytest.mark.parametrize("extend", [False, True])
def test_later_batch_failure_rolls_back_catalogue_and_preserves_snapshot(setup, extend):
    settings, engine, selected, report, generation_id, *_ = setup
    sample = [deepcopy(selected[0]), *sample_images(selected[0], 500)]
    sample[0]["title"] = "Must roll back"
    if extend:
        with Session(engine) as session:
            generation = session.get(Generation, generation_id)
            settings.qdrant_collection_name = generation.collection
            generation.status = "ready"
            session.add(generation)
            session.commit()
    before = catalogue_rows(engine)
    folder = settings.data_dir / "selections"
    snapshots = {path.name: path.read_bytes() for path in folder.iterdir()}
    writes = 0

    def fail_later_batch(_conn, _cursor, statement, *_args):
        nonlocal writes
        if statement.startswith("INSERT INTO images "):
            writes += 1
            if writes == 2:
                raise RuntimeError("injected batch failure")

    event.listen(engine, "before_cursor_execute", fail_later_batch)
    try:
        with pytest.raises(RuntimeError, match="injected batch failure"):
            create_generation(engine, settings, sample, report)
    finally:
        event.remove(engine, "before_cursor_execute", fail_later_batch)
    assert writes == 2
    assert catalogue_rows(engine) == before
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == snapshots


def test_snapshot_read_failure_preserves_previous_file(setup, monkeypatch):
    settings, engine, _, _, generation_id, *_ = setup
    monkeypatch.setattr("app.services.ingestion.CATALOGUE_BATCH_SIZE", 2)
    snapshot = settings.data_dir / "selections" / f"{generation_id}.jsonl"
    before = snapshot.read_bytes()
    reads = 0

    def fail_later_read(_conn, _cursor, statement, *_args):
        nonlocal reads
        if statement.startswith("SELECT") and "FROM image_associations" in statement:
            reads += 1
            if reads == 2:
                raise RuntimeError("injected snapshot failure")

    event.listen(engine, "before_cursor_execute", fail_later_read)
    try:
        with pytest.raises(RuntimeError, match="injected snapshot failure"):
            save_selection(engine, settings, generation_id)
    finally:
        event.remove(engine, "before_cursor_execute", fail_later_read)
    assert snapshot.read_bytes() == before
    assert not snapshot.with_suffix(".tmp").exists()
