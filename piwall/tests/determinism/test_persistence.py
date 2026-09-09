import os
import pytest

def _database_is_reachable() -> bool:
    """Reachable, not merely configured.

    Gating on MONGODB_URI being set gates on a proxy for the thing we need.
    Under compose the variable is always present and points at a service that
    may not be running, so `docker compose run --no-deps backend pytest`
    produced five errors and two and a half minutes of connection timeouts
    where it should have produced five skips.

    The short timeout matters: this runs at collection time, so a slow probe
    is paid by every test session whether or not a database exists.
    """
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        return False
    try:
        from pymongo import MongoClient

        MongoClient(uri, serverSelectionTimeoutMS=500).admin.command("ping")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_is_reachable(),
    reason="needs a reachable database; the rest of the determinism suite is hermetic",
)

from backend.db import crud
from backend.db.models import create_db_engine, init_db
from backend.determinism.manifest import Participant, build_manifest


@pytest.fixture
def db():
    engine = create_db_engine(os.environ["MONGODB_URI"])
    factory = init_db(engine)
    session = factory()
    yield session
    session.db.manifests.delete_many({"match_id": {"$regex": "^t_"}})
    session.close()


def _manifest(match_id="t_1", seed=7):
    return build_manifest(match_id=match_id, seed=seed, track="bahrain",
                          participants=[Participant(0, None, None, None, "VEL-01")])


def test_a_manifest_round_trips_through_the_database(db):
    crud.save_manifest(db, _manifest())
    assert crud.get_manifest(db, "t_1") == _manifest()


def test_manifests_are_immutable_once_written(db):
    """Rewriting a manifest would silently invalidate every replay of it."""
    crud.save_manifest(db, _manifest())
    with pytest.raises(ValueError):
        crud.save_manifest(db, _manifest(match_id="t_1", seed=999))


def test_resaving_identical_content_is_harmless(db):
    """Idempotent re-saves must not be treated as a mutation attempt."""
    crud.save_manifest(db, _manifest())
    crud.save_manifest(db, _manifest())
    assert crud.get_manifest(db, "t_1") == _manifest()


def test_an_absent_manifest_returns_none(db):
    assert crud.get_manifest(db, "t_nonexistent") is None


def test_the_replay_hash_round_trips(db):
    crud.save_manifest(db, _manifest())
    crud.save_replay_hash(db, "t_1", "sha256:" + "b" * 64)
    assert crud.get_replay_hash(db, "t_1") == "sha256:" + "b" * 64
