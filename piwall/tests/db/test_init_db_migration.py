"""init_db() must not brick a boot over data a migration should fix.

Review round 2, F15: elo_history's unique index on (player_id, race_id)
(backend/db/models.py) is created with create_index(unique=True), which
raises DuplicateKeyError if the collection already holds a duplicate pair
-- exactly the row shape the bug this index exists to prevent could have
already written on a live deployment, before this index existed. init_db()
runs from both the API's lifespan and the worker's run_forever, so an
uncaught raise there means neither process can start at all over a data
problem the boot itself cannot fix.

Uses a disposable database (never the shared piwall/phi1 one) so an
intentionally-duplicated collection never touches real data.
"""

import uuid

import pytest
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

from backend.db.models import init_db


def _mongo_is_reachable() -> bool:
    try:
        MongoClient(
            "mongodb://127.0.0.1:27017/", serverSelectionTimeoutMS=500
        ).admin.command("ping")
        return True
    except ServerSelectionTimeoutError:
        return False


pytestmark = pytest.mark.skipif(
    not _mongo_is_reachable(), reason="needs a reachable database"
)


@pytest.fixture
def throwaway_db():
    name = f"piwall_test_migration_{uuid.uuid4().hex[:8]}"
    client = MongoClient("mongodb://127.0.0.1:27017/")
    db = client[name]
    yield db
    client.drop_database(name)
    client.close()


def test_init_db_survives_pre_existing_elo_history_duplicates(throwaway_db):
    """The regression itself: a duplicate (player_id, race_id) pair,
    written before this index existed, must not stop init_db() -- and
    therefore the whole process -- from starting.
    """
    throwaway_db.elo_history.insert_many([
        {"id": "a", "player_id": "p1", "race_id": "r1",
         "elo_before": 1200, "elo_after": 1210},
        {"id": "b", "player_id": "p1", "race_id": "r1",
         "elo_before": 1200, "elo_after": 1220},
    ])

    init_db(throwaway_db)  # must not raise

    # And the index genuinely was not created, rather than silently having
    # succeeded with the duplicates still in place (which would mean the
    # exception path was never exercised at all).
    names = {ix["name"] for ix in throwaway_db.elo_history.list_indexes()}
    assert "player_id_1_race_id_1" not in names


def test_init_db_creates_the_index_on_a_clean_collection(throwaway_db):
    """The happy path, so the test above cannot pass merely because
    create_index is never called at all."""
    init_db(throwaway_db)

    names = {ix["name"] for ix in throwaway_db.elo_history.list_indexes()}
    assert "player_id_1_race_id_1" in names


def test_init_db_survives_pre_existing_race_results_duplicates(throwaway_db):
    """The same protection for the newer (race_id, player_id) index on
    race_results (review round 3, N6). Round 1 of that fix persisted
    results insert-only, so a live deployment redelivered a match before
    round 2 landed is holding exactly the duplicate pair this index
    forbids -- and init_db() runs from both the API's lifespan and the
    worker's run_forever, so a raise here stops BOTH processes booting
    over data the boot itself cannot fix.

    The behaviour was verified correct in round 3 but nothing pinned it
    (re-review of round 3, NEW-3).

    Red line: `except DuplicateKeyError:` in models.py's
    _create_unique_index_or_log -- the helper both unique indexes now go
    through. (Deleting the race_results CALL to that helper instead is
    caught by test_the_unique_index_is_what_prevents_a_double_insert in
    tests/db/test_race_results_persistence.py; between them, neither
    half can be removed unnoticed.)
    """
    throwaway_db.race_results.insert_many([
        {"id": "a", "race_id": "r1", "player_id": "p1", "car_id": "P01",
         "position": 1, "points": 25, "retired": False},
        {"id": "b", "race_id": "r1", "player_id": "p1", "car_id": "P01",
         "position": 1, "points": 25, "retired": False},
    ])

    init_db(throwaway_db)  # must not raise

    names = {ix["name"] for ix in throwaway_db.race_results.list_indexes()}
    assert "race_id_1_player_id_1" not in names
