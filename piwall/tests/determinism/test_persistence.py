import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("MONGODB_URI"),
    reason="needs a database; the rest of the determinism suite is hermetic",
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
    session.manifests.delete_many({"match_id": {"$regex": "^t_"}})
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
