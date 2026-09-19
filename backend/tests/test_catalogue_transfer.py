import pytest
from app.services.catalogue_transfer import TABLES, fingerprint, transfer_catalogue
from sqlalchemy import event, func, select


@pytest.fixture
def destination(database_factory):
    return database_factory()[1]


def test_transfer_preserves_all_rows_and_refuses_overwrite(setup, destination):
    with setup[1].connect() as source:
        counts = transfer_catalogue(source, destination)
        assert counts["images"] == 3
        with destination.connect() as target:
            for table in TABLES:
                assert fingerprint(source, table) == fingerprint(target, table)
        with pytest.raises(ValueError, match="not empty"):
            transfer_catalogue(source, destination)


def test_transfer_rolls_back_every_table_on_failure(setup, destination):
    @event.listens_for(destination, "before_cursor_execute")
    def fail(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("INSERT INTO image_associations"):
            raise RuntimeError("simulated interruption")

    with (
        setup[1].connect() as source,
        pytest.raises(RuntimeError, match="simulated interruption"),
    ):
        transfer_catalogue(source, destination)
    with destination.connect() as target:
        assert all(
            target.scalar(select(func.count()).select_from(t)) == 0 for t in TABLES
        )
