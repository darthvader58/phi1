import uuid

import pytest

from backend.observability.health import _mongo_is_reachable

# The one definition of "a reachable database" this suite uses
# (backend/observability/health.py), which probes whatever mongo_url()
# resolves to.
#
# This file used to gate on MONGODB_URI being SET, which is a proxy for the
# thing we need rather than the thing itself, and it disagreed with every
# other gate in the suite in both directions. Under compose the variable is
# always present and points at a service that may not be running, which is
# what produced five errors and two and a half minutes of connection timeouts
# where five skips belonged. Locally it is absent while Mongo is running
# perfectly well on the default URL, so these five tests skipped for no
# reason -- which is why the local run reported "5 skipped" while CI reported
# "0 skipped" for the identical suite. A skip count that depends on which
# environment variables happen to be exported undermines the zero-skip floor
# CI now enforces: a real outage and a merely-unset variable were
# indistinguishable.
#
# The short timeout still matters, and _mongo_is_reachable keeps it: this
# runs at collection time, so a slow probe is paid by every session whether
# or not a database exists.
pytestmark = pytest.mark.skipif(
    not _mongo_is_reachable(),
    reason="needs a reachable database; the rest of the determinism suite is hermetic",
)

from backend.db import crud
from backend.db.models import init_db
from backend.determinism.manifest import Participant, build_manifest


@pytest.fixture
def db():
    """A throwaway database, dropped whole on teardown.

    Now that the gate above probes a reachable Mongo rather than a set
    environment variable, these tests RUN locally -- where mongo_url()
    resolves to the shared `phi1`. Writing manifests into it (and, as the
    previous teardown did, running a regex delete_many across its manifests
    collection) is precisely what this suite is forbidden to do. A database
    this fixture owns outright removes the question.

    Built with MongoClient(...)[name] rather than create_db_engine, and
    deliberately so: create_db_engine goes through _resolve_database_name,
    which reads MONGODB_DB FIRST and only then the URL's path -- so with that
    variable set (CI sets it, compose sets it) the "throwaway" name in the
    URL would be silently ignored and these tests would write to the
    configured database after all. That is the same override that had a
    worker subprocess writing to the wrong database in tests/jobs/
    test_shutdown.py; see tests/subprocess_env.py.
    """
    from pymongo import MongoClient

    from backend.db.models import MongoSession, mongo_url

    name = f"piwall_test_manifests_{uuid.uuid4().hex[:8]}"
    client = MongoClient(mongo_url())
    database = client[name]
    init_db(database)
    session = MongoSession(database)
    yield session
    session.close()
    client.drop_database(name)
    client.close()


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
