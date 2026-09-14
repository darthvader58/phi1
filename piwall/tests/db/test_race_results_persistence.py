"""save_race_results must never leave a finished race's results collection
empty, and its unique index must be what actually prevents a double-insert.

Review round 3, N6: the first version of this fix (round 2) made
save_race_results idempotent by deleting all of a race's rows before
re-inserting them. That closed the duplicate-row problem but opened a new
one: a redelivered persist now briefly leaves the collection with ZERO
rows for a race that GET /api/race/{id} and /api/season both read live.
This version replaces delete-then-insert with a per-row upsert keyed by
(race_id, player_id), which never removes a row before its replacement
exists, backed by a unique index on that same key so a concurrent
double-persist (two workers racing on the same match) cannot double a
race's rows -- and therefore its championship points.
"""

import uuid

import pytest
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, ServerSelectionTimeoutError

from backend.db import crud
from backend.db.models import create_db_engine, init_db, mongo_url


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


class _Namespace:
    def __init__(self, d):
        self.__dict__.update(d)


def _standings(suffix: str):
    return [
        {"player_id": f"p1_{suffix}", "car_id": "P01", "position": 1,
         "retired": False, "total_time": 100.0, "pit_laps": [],
         "compounds_used": ["MEDIUM"]},
        {"player_id": f"p2_{suffix}", "car_id": "P02", "position": 2,
         "retired": False, "total_time": 110.0, "pit_laps": [],
         "compounds_used": ["MEDIUM"]},
    ]


@pytest.fixture
def db():
    # The shared database, but every test below only ever touches rows it
    # creates itself under a fresh, unique race_id, and cleans them up --
    # unlike the index-dropping test further down, nothing here mutates
    # shared, persistent database state (an index), only scoped rows.
    engine = create_db_engine(mongo_url())
    factory = init_db(engine)
    session = factory()
    yield session
    session.close()


@pytest.fixture
def throwaway_db():
    """A disposable database, never the shared piwall/phi1 one.

    Review round 3, N3: an earlier version of a sibling test in this round
    called drop_index directly against the shared database (mongo_url()),
    so an interrupted run (Ctrl-C, -x, a crash) could leave the real
    database without its protective index, silently, for however long it
    took someone to notice. Dropping and recreating an index that
    everything else depends on has no business happening anywhere but a
    database built and torn down for exactly this test.
    """
    name = f"piwall_test_race_results_{uuid.uuid4().hex[:8]}"
    client = MongoClient("mongodb://127.0.0.1:27017/")
    database = client[name]
    yield database
    client.drop_database(name)
    client.close()


def test_save_race_results_never_calls_delete_many(db, monkeypatch):
    """The structural guarantee: this function must never empty the
    collection before repopulating it. Patching delete_many to raise is a
    direct check that the code path simply does not exist any more,
    rather than trying to catch a timing window.
    """
    race_id = f"r_no_delete_{uuid.uuid4().hex[:8]}"
    standings = [_Namespace(s) for s in _standings(race_id)]

    def exploding_delete_many(self, *args, **kwargs):
        raise AssertionError(
            "save_race_results must not call delete_many -- that is "
            "exactly the window this fix closes"
        )

    # db.db.race_results is a fresh pymongo Collection wrapper on every
    # attribute access (pymongo.Database.__getattr__ does not cache), so
    # patching one instance's bound method leaves every other access --
    # including the one inside crud.save_race_results -- untouched. The
    # class method is the only thing actually shared across accesses.
    from pymongo.collection import Collection
    monkeypatch.setattr(Collection, "delete_many", exploding_delete_many)

    try:
        crud.save_race_results(db, race_id, standings)
        assert db.db.race_results.count_documents({"race_id": race_id}) == 2

        # Redelivery: the same standings, persisted again. Still no
        # delete_many, and the row count must never have touched zero.
        crud.save_race_results(db, race_id, standings)
        assert db.db.race_results.count_documents({"race_id": race_id}) == 2
    finally:
        monkeypatch.undo()
        db.db.race_results.delete_many({"race_id": race_id})


def test_a_redelivery_leaves_ids_stable(db):
    """Upserting in place, not delete-then-insert, means a result row's
    own id survives a redelivery -- anything that cached a result by id
    would not see it vanish and reappear under a new one."""
    race_id = f"r_stable_id_{uuid.uuid4().hex[:8]}"
    standings = [_Namespace(s) for s in _standings(race_id)]

    try:
        first = crud.save_race_results(db, race_id, standings)
        ids_after_first = {r.id for r in first}

        second = crud.save_race_results(db, race_id, standings)
        ids_after_second = {r.id for r in second}

        assert ids_after_first == ids_after_second
    finally:
        db.db.race_results.delete_many({"race_id": race_id})


def test_the_unique_index_is_what_prevents_a_double_insert(throwaway_db):
    """The other half of N6: two workers persisting the same match at
    once (a stalled reclaim firing while the first worker is still
    running) must not double a race's rows.

    A real thread race against MongoDB's own server-side atomicity is not
    a reliable way to prove this from a client test -- find_one_and_update
    is a single atomic server operation, and two pymongo threads racing to
    call it do not reliably lose against the server's own document-level
    locking within the timing budget a unit test can afford, index or no
    index. This instead does what the sibling elo_history test does: on a
    throwaway database, create the collection with NO unique index, show
    MongoDB genuinely accepts two rows with the same (race_id,
    player_id), then add the index and show the identical second insert
    is refused.
    """
    row = {
        "id": str(uuid.uuid4()), "race_id": "r1", "player_id": "p1",
        "car_id": "P01", "position": 1, "points": 25, "total_time": 100.0,
        "pit_laps": [], "compounds_used": ["MEDIUM"], "strategy_json": None,
        "retired": False,
    }

    throwaway_db.race_results.insert_one({**row, "id": str(uuid.uuid4())})
    throwaway_db.race_results.insert_one({**row, "id": str(uuid.uuid4())})
    assert throwaway_db.race_results.count_documents(
        {"race_id": "r1", "player_id": "p1"}
    ) == 2, (
        "without the index, MongoDB must actually accept the second "
        "insert -- this is the vulnerability the index exists to close, "
        "reproduced directly rather than assumed"
    )

    throwaway_db.race_results.delete_many({})
    throwaway_db.race_results.create_index(
        [("race_id", 1), ("player_id", 1)], unique=True
    )

    throwaway_db.race_results.insert_one({**row, "id": str(uuid.uuid4())})
    with pytest.raises(DuplicateKeyError):
        throwaway_db.race_results.insert_one({**row, "id": str(uuid.uuid4())})
    assert throwaway_db.race_results.count_documents(
        {"race_id": "r1", "player_id": "p1"}
    ) == 1, "with the index in place, the second insert must be refused"


def test_init_db_creates_the_race_results_unique_index(throwaway_db):
    """The index this whole file is about is actually the one init_db()
    creates -- not a hand-rolled stand-in with a different key."""
    init_db(throwaway_db)
    names = {ix["name"] for ix in throwaway_db.race_results.list_indexes()}
    assert "race_id_1_player_id_1" in names
