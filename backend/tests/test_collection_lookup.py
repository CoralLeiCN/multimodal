import pytest
from app.models import Generation, ServiceState
from app.services.agent.collection import read_collection_image, resolve_record_images
from app.services.agent.config import AgentSettings
from app.services.agent.storage import AgentError
from sqlmodel import Session


@pytest.fixture
def ready_catalogue(setup, monkeypatch):
    settings, engine, selected, _, generation_id, *_ = setup
    with Session(engine) as session:
        generation = session.get(Generation, generation_id)
        generation.status = "ready"
        session.add(generation)
        session.add(ServiceState(active_generation=generation_id))
        session.commit()
    monkeypatch.setattr("app.core.config.Settings", lambda: settings)
    agent = AgentSettings(_env_file=None, collection_image_root=settings.image_root)
    return agent, settings, engine, selected[0], generation_id


def test_chat_uuid_reads_shared_postgres_and_preserves_attribution(ready_catalogue):
    agent, settings, _, image, _ = ready_catalogue
    assert agent.collection_database is None
    data, source = read_collection_image(agent, image["image_id"])
    assert data == (settings.image_root / image["relative_path"]).read_bytes()
    assert source["image_id"] == image["image_id"]
    assert source["associations"][0]["record_uid"] == "co-red"
    assert source["associations"][0]["licence"] == "CC BY-NC-SA 4.0"
    matches = resolve_record_images("co-red")
    assert [match["image_id"] for match in matches] == [image["image_id"]]
    assert resolve_record_images("co-red' OR 1=1 --") == []


def test_chat_uuid_distinguishes_missing_image_and_unready_catalogue(ready_catalogue):
    agent, _, engine, image, generation_id = ready_catalogue
    with pytest.raises(AgentError) as missing:
        read_collection_image(agent, "unknown' OR 1=1 --")
    assert missing.value.code == "image_missing"
    with Session(engine) as session:
        generation = session.get(Generation, generation_id)
        generation.status = "building"
        session.add(generation)
        session.commit()
    with pytest.raises(AgentError) as unavailable:
        read_collection_image(agent, image["image_id"])
    assert unavailable.value.code == "collection_unavailable"


@pytest.mark.parametrize("outcome", ["changed", "too_large", "missing", "symlink"])
def test_chat_mounted_images_enforce_integrity_and_containment(
    ready_catalogue, tmp_path, outcome
):
    agent, settings, _, image, _ = ready_catalogue
    path = settings.image_root / image["relative_path"]
    if outcome == "changed":
        path.write_bytes(b"changed")
    elif outcome == "too_large":
        agent.max_image_bytes = path.stat().st_size - 1
    elif outcome == "missing":
        path.unlink()
    else:
        outside = tmp_path / "outside.png"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    with pytest.raises(AgentError) as error:
        read_collection_image(agent, image["image_id"])
    assert (
        error.value.code
        == {
            "changed": "image_changed",
            "too_large": "image_too_large",
            "missing": "image_unavailable",
            "symlink": "invalid_collection_path",
        }[outcome]
    )


def test_agent_uses_shared_image_root_unless_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGE_ROOT", str(tmp_path / "shared"))
    assert AgentSettings(_env_file=None).collection_image_root == tmp_path / "shared"
    monkeypatch.setenv("AGENT_COLLECTION_IMAGE_ROOT", str(tmp_path / "agent"))
    assert AgentSettings(_env_file=None).collection_image_root == tmp_path / "agent"
