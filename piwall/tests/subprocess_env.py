"""One definition of the environment a test subprocess runs in.

Two test files spawn child processes that talk to Mongo -- tests/jobs/
test_shutdown.py (a real worker taking a real SIGTERM) and tests/integration/
test_phase2_gate.py (two API replicas and several workers) -- and both had
re-derived the same setup by copy. The piece that must not be re-derived is
the MONGODB_DB pinning, because getting it wrong is silent in the worst way:
the child writes to a database the test never reads, the test's assertions
fail somewhere far from the cause, and with MONGODB_DB=phi1 exported the
child writes into the shared database the suite is forbidden to touch.

Not named test_*.py on purpose, so pytest does not try to collect it.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

PIWALL_DIR = Path(__file__).resolve().parents[1]


def child_env(mongo_uri: str) -> dict:
    """The environment for a subprocess this suite owns.

    `mongo_uri` must name a throwaway database in its path -- never the
    shared `phi1`.
    """
    from backend.state.redis_client import REDIS_URL

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PIWALL_DIR)
    env["MONGODB_URI"] = mongo_uri

    # MONGODB_DB OVERRIDES the database named in MONGODB_URI's path --
    # backend/db/models.py's _resolve_database_name reads the variable first
    # and only falls back to the path -- and both CI and docker compose set
    # it. Inheriting it unchanged sends the child to whatever database that
    # names while the test's assertions read the throwaway one: the child
    # then finds no manifest for its match, _persist_result refuses to treat
    # the result as persisted, and the job is never acked. Worse, a developer
    # with MONGODB_DB=phi1 exported would have the child writing into the
    # shared database this suite must never touch. Pinning it to the
    # throwaway database's own name means the two cannot disagree.
    database_name = urlparse(mongo_uri).path.lstrip("/")
    assert database_name, f"no database name in {mongo_uri!r}"
    env["MONGODB_DB"] = database_name

    # Passed explicitly rather than left to the child's own default: the
    # parent resolved REDIS_URL at import time, and a child that re-derived
    # it from a different environment would be talking to a different Redis
    # than the assertions read.
    env["REDIS_URL"] = REDIS_URL

    # What the runtime images pin (Dockerfile.backend / Dockerfile.worker).
    # These children compare replay hashes with each other across process
    # boundaries, so they must agree on the interpreter's hash seed the way
    # production does.
    env["PYTHONHASHSEED"] = "0"
    return env
