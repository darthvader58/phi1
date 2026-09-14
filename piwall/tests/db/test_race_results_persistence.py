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

    Round 3's version of this test hand-wrote a row dict, hand-created an
    index with a literal key pattern and called insert_one twice. It ran
    no piwall code at all, so it stayed green with the production index
    line deleted -- it asserted that MongoDB enforces unique indexes
    (re-review of round 3, NEW-2). This goes through the real path
    instead: init_db() builds the index, crud.save_race_results() writes
    the first row, and the duplicate that must be refused is the one the
    losing side of the concurrent-upsert race would insert.

    The interleaving is written out rather than raced for, deliberately:
    find_one_and_update is one atomic server operation, so two pymongo
    threads calling it do not reliably lose against MongoDB's own
    document-level locking inside a unit test's timing budget -- with or
    without the index. What the index actually has to refuse is the
    insert half of a "no match -> insert" decision another writer made
    before this one's row existed, which is exactly the write below.

    Red line: `_create_unique_index_or_log(db.race_results, [("race_id",
    ASCENDING), ("player_id", ASCENDING)], "race_results")` in
    backend/db/models.py. Delete it and the duplicate is accepted.
    """
    session = init_db(throwaway_db)()
    race_id = f"r_double_insert_{uuid.uuid4().hex[:8]}"
    standings = [_Namespace(s) for s in _standings(race_id)]

    written = crud.save_race_results(session, race_id, standings)
    assert len(written) == 2

    # The losing writer in the race the index exists to settle: it read
    # "no row for (race_id, p1_...)" before the write above landed, and
    # now goes on to insert one.
    duplicate = {
        "id": str(uuid.uuid4()),
        "race_id": race_id,
        "player_id": standings[0].player_id,
        "car_id": "P01", "position": 1, "points": 25, "total_time": 100.0,
        "pit_laps": [], "compounds_used": ["MEDIUM"], "strategy_json": None,
        "retired": False,
    }
    with pytest.raises(DuplicateKeyError):
        throwaway_db.race_results.insert_one(duplicate)

    assert throwaway_db.race_results.count_documents(
        {"race_id": race_id, "player_id": standings[0].player_id}
    ) == 1, "the index init_db creates must refuse the second row"
    assert throwaway_db.race_results.count_documents({"race_id": race_id}) == 2, (
        "and the race must still hold exactly one row per car, so its "
        "championship points cannot have been doubled"
    )


def test_a_losing_upsert_retries_instead_of_escaping_to_the_worker(db, monkeypatch):
    """NEW-6: with the unique index in place, an upsert that finds no
    matching row decides to insert -- and loses if another worker's
    insert for the same key lands first. pymongo surfaces that as
    DuplicateKeyError and leaves the retry to the caller. Unhandled, it
    escapes _persist_result and strands the whole job unacked until
    reclaim_stalled picks it up.

    The losing writer is simulated rather than raced for, and at the one
    layer that makes the simulation faithful: the FIRST find_one_and_update
    call has the competing row inserted underneath it and then raises
    DuplicateKeyError, which is exactly the sequence the real loser sees.

    Red line: the `except DuplicateKeyError:` retry in
    crud.save_race_results.
    """
    from pymongo.collection import Collection

    race_id = f"r_upsert_retry_{uuid.uuid4().hex[:8]}"
    standings = [_Namespace(s) for s in _standings(race_id)]
    real = Collection.find_one_and_update
    calls = []

    def losing_once(self, filter, *args, **kwargs):
        calls.append(filter)
        if len(calls) == 1:
            # The other worker's insert lands first...
            self.insert_one({
                "id": str(uuid.uuid4()), "race_id": race_id,
                "player_id": standings[0].player_id, "car_id": "P01",
                "position": 1, "points": 25, "total_time": 100.0,
                "pit_laps": [], "compounds_used": ["MEDIUM"],
                "strategy_json": None, "retired": False,
            })
            # ...and this one's own insert is refused by the index.
            raise DuplicateKeyError("simulated concurrent insert race")
        return real(self, filter, *args, **kwargs)

    monkeypatch.setattr(Collection, "find_one_and_update", losing_once)

    try:
        rows = crud.save_race_results(db, race_id, standings)

        assert len(rows) == 2
        assert len(calls) == 3, "the losing car must have been retried exactly once"
        assert db.db.race_results.count_documents({"race_id": race_id}) == 2, (
            "the retry must land on the row the winner inserted, not add "
            "a third"
        )
        winner = db.db.race_results.find_one(
            {"race_id": race_id, "player_id": standings[0].player_id}
        )
        assert winner["position"] == 1 and winner["car_id"] == "P01"
    finally:
        monkeypatch.undo()
        db.db.race_results.delete_many({"race_id": race_id})


def test_init_db_creates_the_race_results_unique_index(throwaway_db):
    """The index this whole file is about is actually the one init_db()
    creates -- not a hand-rolled stand-in with a different key."""
    init_db(throwaway_db)
    names = {ix["name"] for ix in throwaway_db.race_results.list_indexes()}
    assert "race_id_1_player_id_1" in names
