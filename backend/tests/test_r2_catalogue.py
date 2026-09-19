from copy import deepcopy

import pytest
from app.models import Image
from app.services.ingestion import extend_generation
from app.services.r2_catalogue import apply_r2_urls, object_key, plan_r2_urls
from sqlalchemy import select, update

ENDPOINT = "https://" + "a" * 32 + ".r2.cloudflarestorage.com"


def prepare(setup):
    settings, engine, selected, *_ = setup
    settings.r2_endpoint_url = ENDPOINT
    settings.r2_bucket = "smg-images"

    class FakeR2:
        def head_object(self, *, Bucket, Key):
            assert Bucket == "smg-images"
            row = next(item for item in selected if item["relative_path"] == Key)
            return {"Metadata": {"sha256": row["checksum"]}}

    return settings, engine, FakeR2()


def test_verified_urls_preserve_source_identity_and_are_repeatable(setup):
    settings, engine, client = prepare(setup)
    table = Image.__table__
    original_columns = [column for column in table.columns if column.name != "r2_url"]
    with engine.connect() as connection:
        before = connection.execute(
            select(*original_columns).order_by(table.c.image_id)
        ).all()
    plan = plan_r2_urls(engine, settings, client)
    assert len(plan) == 3
    assert all(row["new_r2_url"].startswith(ENDPOINT + "/smg-images/") for row in plan)
    assert apply_r2_urls(engine, plan) == 3
    assert apply_r2_urls(engine, plan_r2_urls(engine, settings, client)) == 0
    with engine.connect() as connection:
        assert (
            connection.execute(
                select(*original_columns).order_by(table.c.image_id)
            ).all()
            == before
        )


def test_changed_catalogue_rolls_back_all_urls(setup):
    settings, engine, client = prepare(setup)
    plan = plan_r2_urls(engine, settings, client)
    with engine.begin() as connection:
        connection.execute(
            update(Image)
            .where(Image.image_id == plan[-1]["image_id"])
            .values(checksum="changed")
        )
    with pytest.raises(ValueError, match="rolled back"):
        apply_r2_urls(engine, plan)
    with engine.connect() as connection:
        assert connection.execute(select(Image.r2_url)).scalars().all() == [None] * 3


def test_wrong_remote_checksum_leaves_catalogue_untouched(setup):
    settings, engine, _client = prepare(setup)

    class WrongR2:
        def head_object(self, **_kwargs):
            return {"Metadata": {"sha256": "wrong"}}

    with pytest.raises(ValueError, match="SHA-256"):
        plan_r2_urls(engine, settings, WrongR2())
    with engine.connect() as connection:
        assert connection.execute(select(Image.r2_url)).scalars().all() == [None] * 3


@pytest.mark.parametrize("r2_enabled", [True, False])
def test_reindexing_updates_links_using_current_configuration(setup, r2_enabled):
    settings, engine, client = prepare(setup)
    apply_r2_urls(engine, plan_r2_urls(engine, settings, client))
    selected = deepcopy(setup[2])
    selected[0]["checksum"] = "new-checksum"
    selected[0]["relative_path"] = "migrated/new image.jpg"
    selected[0]["r2_url"] = "https://stale.example/old.jpg"
    if not r2_enabled:
        settings.r2_endpoint_url = None
    extend_generation(engine, settings, setup[4], selected)
    with engine.connect() as connection:
        urls = dict(connection.execute(select(Image.image_id, Image.r2_url)).all())
    assert urls[selected[0]["image_id"]] == (
        ENDPOINT + "/smg-images/migrated/new%20image.jpg" if r2_enabled else None
    )
    assert all(urls[item["image_id"]] for item in selected[1:])


@pytest.mark.parametrize(
    "path", ["", "../a.jpg", "/a.jpg", "a/../b.jpg", "a\\b.jpg", "a//b.jpg"]
)
def test_rejects_unsafe_keys(path):
    with pytest.raises(ValueError, match="relative paths"):
        object_key(path)


def test_encodes_special_characters_in_stored_url(setup):
    settings, engine, _client = prepare(setup)
    with engine.begin() as connection:
        connection.execute(
            update(Image).values(relative_path="folder/a b#%.jpg", checksum="same")
        )

    class FakeR2:
        def head_object(self, *, Bucket, Key):
            assert Key == "folder/a b#%.jpg"
            return {"Metadata": {"sha256": "same"}}

    assert all(
        row["new_r2_url"].endswith("/folder/a%20b%23%25.jpg")
        for row in plan_r2_urls(engine, settings, FakeR2())
    )
