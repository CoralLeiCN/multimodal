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
        ["--scan-limit", "10001"],
        ["--resume", "example", "--limit", "50"],
    ],
)
def test_invalid_cli_options_exit_two(args):
    with pytest.raises(SystemExit) as error:
        index_images.main(args)
    assert error.value.code == 2
