import pytest

from cronjob import index_images


def test_prepare_only_saves_selection_without_external_calls(setup, monkeypatch):
    settings, _engine, selected, report, *_ = setup
    monkeypatch.setattr(index_images, "Settings", lambda: settings)
    monkeypatch.setattr(index_images, "select_images", lambda *args: (selected, report))

    def forbidden(*_args):
        raise AssertionError("prepare-only must not construct a vector client")

    monkeypatch.setattr(index_images, "VectorStore", forbidden)
    assert index_images.main(["--prepare-only", "--limit", "3"]) == 0


@pytest.mark.parametrize(
    "args",
    [
        ["--limit", "0"],
        ["--limit", "-1"],
        ["--limit", "1", "--scan-limit", "0"],
        ["--limit", "1", "--scan-limit", "-1"],
        ["--dry-run"],
        ["--prepare-only"],
        ["--seed", "7"],
        ["--scan-limit", "500"],
        ["--metadata", "source.json"],
        ["--batch-size", "0"],
        ["--batch-size", "101"],
        ["--workers", "0"],
        ["--workers", "11"],
        ["--resume", "example", "--limit", "50"],
    ],
)
def test_invalid_cli_options_exit_two(args):
    with pytest.raises(SystemExit) as error:
        index_images.main(args)
    assert error.value.code == 2


def test_large_selection_limits_reach_sampler(monkeypatch):
    calls = []

    def select_sample(settings, metadata, limit, scan_limit, seed):
        calls.append((limit, scan_limit, seed))
        return [{"title": "Sample image"}], {"selected": 1}

    from contextlib import nullcontext
    from unittest.mock import Mock

    monkeypatch.setattr(index_images, "Settings", Mock())
    monkeypatch.setattr(index_images, "configure_telemetry", Mock())
    monkeypatch.setattr(index_images, "GeminiEmbeddings", Mock())
    monkeypatch.setattr(index_images, "ingestion_lock", lambda *a, **k: nullcontext())
    monkeypatch.setattr(index_images, "select_images", select_sample)
    assert (
        index_images.main(
            ["--limit", "5000", "--scan-limit", "50000", "--dry-run"]
        )
        == 0
    )
    assert calls == [(5000, 50000, 42)]


def test_resume_accepts_batch_size_and_uses_it_for_requests(setup, monkeypatch):
    settings, engine, _, _, generation_id, vectors, embeddings = setup
    monkeypatch.setattr(index_images, "Settings", lambda: settings)
    monkeypatch.setattr(index_images, "make_engine", lambda _: engine)
    monkeypatch.setattr(index_images, "migrate", lambda _: None)
    monkeypatch.setattr(index_images, "GeminiEmbeddings", lambda _: embeddings)
    monkeypatch.setattr(index_images, "VectorStore", lambda _: vectors)
    monkeypatch.setattr(vectors, "close", lambda: None)
    sizes = []
    embed_images = embeddings.embed_images

    def capture(images):
        sizes.append(len(images))
        return embed_images(images)

    monkeypatch.setattr(embeddings, "embed_images", capture)
    assert (
        index_images.main(
            [
                "--resume",
                generation_id,
                "--batch-size",
                "2",
                "--workers",
                "1",
            ]
        )
        == 0
    )
    assert sizes == [2, 1]


@pytest.fixture
def saved_cli(setup, monkeypatch):
    settings, engine, _, _, _, vectors, embeddings = setup
    monkeypatch.setattr(index_images, "Settings", lambda: settings)
    monkeypatch.setattr(index_images, "make_engine", lambda _: engine)
    monkeypatch.setattr(index_images, "migrate", lambda _: None)
    monkeypatch.setattr(index_images, "GeminiEmbeddings", lambda _: embeddings)
    monkeypatch.setattr(index_images, "VectorStore", lambda _: vectors)
    monkeypatch.setattr(vectors, "close", lambda: None)
    return setup


@pytest.mark.parametrize("already_ready", [False, True])
@pytest.mark.parametrize("configured_collection", [False, True])
def test_no_limit_reuses_saved_selection_without_sampling(
    saved_cli,
    monkeypatch,
    already_ready,
    configured_collection,
):
    from app.models import Generation, Image
    from app.services.ingestion import run_ingestion
    from sqlmodel import Session, select

    settings, engine, selected, _, generation_id, vectors, embeddings = saved_cli
    if configured_collection:
        with Session(engine) as session:
            settings.qdrant_collection_name = session.get(
                Generation, generation_id
            ).collection
    if already_ready:
        run_ingestion(engine, settings, generation_id, embeddings, vectors)

    def forbidden(*args, **kwargs):
        pytest.fail("A run without --limit must reuse the saved catalogue")

    monkeypatch.setattr(index_images, "select_images", forbidden)
    monkeypatch.setattr(index_images, "create_generation", forbidden)
    if already_ready:
        monkeypatch.setattr(embeddings, "embed_images", forbidden)
        monkeypatch.setattr(vectors, "upsert", forbidden)
        monkeypatch.setattr("app.services.ingestion.safe_path", forbidden)
    assert index_images.main([]) == 0
    assert embeddings.calls == 3
    with Session(engine) as session:
        assert session.exec(select(Generation.id)).all() == [generation_id]
        assert session.get(Generation, generation_id).status == "ready"
        assert set(session.exec(select(Image.image_id)).all()) == {
            image["image_id"] for image in selected
        }


def test_no_limit_rejects_missing_or_ambiguous_selection(
    saved_cli, monkeypatch, capsys
):
    from app.services.ingestion import create_generation

    settings, engine, selected, report, _, _, _ = saved_cli
    create_generation(engine, settings, selected, report)

    def forbidden(*args, **kwargs):
        pytest.fail("An unresolved selection must not sample or contact Qdrant")

    monkeypatch.setattr(index_images, "select_images", forbidden)
    monkeypatch.setattr(index_images, "VectorStore", forbidden)
    assert index_images.main([]) == 2
    assert "Multiple saved selections" in capsys.readouterr().err
    settings.qdrant_collection_name = "no-saved-selection"
    assert index_images.main([]) == 2
    assert "make index LIMIT=50" in capsys.readouterr().err


def test_no_limit_prefers_configured_collection_over_other_generations(
    saved_cli, monkeypatch
):
    from app.models import Generation
    from app.services.ingestion import create_generation
    from sqlmodel import Session

    settings, engine, selected, report, generation_id, _, _ = saved_cli
    other = create_generation(engine, settings, selected, report)
    with Session(engine) as session:
        settings.qdrant_collection_name = session.get(
            Generation, generation_id
        ).collection
    assert index_images.main([]) == 0
    with Session(engine) as session:
        assert session.get(Generation, generation_id).status == "ready"
        assert session.get(Generation, other).status == "building"


def test_explicit_limit_still_selects_images(saved_cli, monkeypatch):
    _, engine, selected, report, original_id, _, _ = saved_cli
    calls = []

    def select_sample(settings, metadata, limit, scan_limit, seed):
        calls.append((limit, scan_limit, seed))
        return selected[:limit], report

    monkeypatch.setattr(index_images, "select_images", select_sample)
    assert index_images.main(["--limit", "2"]) == 0
    assert calls == [(2, 1000, 42)]
    from app.models import Generation
    from sqlmodel import Session, select

    with Session(engine) as session:
        generations = session.exec(select(Generation)).all()
        assert len(generations) == 2
        assert next(g for g in generations if g.id != original_id).count == 2


def test_make_omits_selection_options_without_limit():
    import os
    import shlex
    import subprocess

    from app.core.config import ROOT

    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "LIMIT",
            "SCAN_LIMIT",
            "INDEX_ARGS",
            "MAKEFLAGS",
            "MFLAGS",
            "MAKEOVERRIDES",
        }
    }

    def command(*args):
        result = subprocess.run(
            ["make", "-n", *args],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        return shlex.split(result.stdout)

    plain = command("index")
    assert "--limit" not in plain and "--scan-limit" not in plain
    explicit = command("index", "LIMIT=12", "SCAN_LIMIT=500")
    assert explicit[explicit.index("--limit") + 1] == "12"
    assert explicit[explicit.index("--scan-limit") + 1] == "500"
    preview = command("preview-index")
    assert preview[preview.index("--limit") + 1] == "50"
    assert "--dry-run" in preview
