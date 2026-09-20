"""The Phase 2 gate, from spec section 11.

Two properties:

  1. Two API replicas serve all traffic correctly.
  2. A worker restart mid-match loses no match.

Both are properties of *processes*, so both are proven with real processes.
That is not pedantry, it is the whole point of this file. The obvious cheap
version of property 1 -- build two `LobbyStore()` objects and check they see
each other's writes -- proves nothing at all: `LobbyStore.__init__` calls
`get_redis()`, which is a module-level singleton
(`backend/state/redis_client.py`), so the two "replicas" are two thin wrappers
around one connection object in one interpreter. A `LobbyStore` backed by a
module-global dict would pass that test exactly as happily as the Redis-backed
one, and a module-global dict is precisely the bug this phase exists to remove.

So the replicas here are two real `uvicorn backend.main:app` processes on two
ports, driven over HTTP, and the workers are real subprocesses that get real
signals. Nothing below shares a Python object with the thing it is testing;
the only thing the processes share is Redis and Mongo, which is exactly the
deployment being asserted.

Each test names, in its docstring, the production line whose deletion turns it
red -- because sixteen findings in this phase have been tests that could not
fail, and this is the file everyone will trust without re-deriving it.

Cost and cleanup: these spawn processes, run real sandboxed matches, and talk
to a real Redis. Every test owns a throwaway Mongo database that is dropped on
teardown (never the shared `phi1`), and the shared Redis stream and lobby keys
are deleted before and after each test.

One environment constraint, which is a property of the whole suite rather than
of this file: do not run these tests against a Redis that a live worker is also
draining. `backend/jobs/queue.py`'s STREAM is one fixed name that every worker
and every API replica must agree on in production, so a running worker will
happily claim a job this suite enqueued -- and, being pointed at the real
database rather than at a test's throwaway one, will fail to persist it. That
hits tests/jobs/test_worker.py exactly as hard as it hits the end-to-end test
below. In practice this only arises when running the suite INSIDE the compose
network while the `worker` service is up, so stop it first:

    docker compose stop worker
    docker compose run --rm -e REDIS_URL=redis://redis:6379/0 backend \
        sh -c "PYTHONPATH=. python -m pytest -q"

CI has no worker process, and a developer's host Redis is not the (unpublished)
compose one, so neither of those paths is affected.
"""

import asyncio
import json
import os
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx
import pytest

from backend.db import crud
from backend.jobs.queue import STREAM, MatchJobQueue
from backend.observability.health import _mongo_is_reachable
from backend.state.lobby import KEY_PREFIX
from backend.state.redis_client import get_redis, redis_is_reachable
from tests.subprocess_env import child_env as _child_env

PIWALL_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not (redis_is_reachable() and _mongo_is_reachable()),
    reason="needs a reachable Redis and a reachable database",
)

# A player's own submitted source, not a house bot. A gate built only from
# house bots would still pass against a worker that could only ever replay
# `BUILTIN_BOTS` -- see tests/jobs/test_worker.py's JOB, which makes the same
# point for the same reason.
PLAYER_CODE = (
    "def my_strategy(state, my_car):\n"
    "    return {'pit': my_car.tyre_age > 20, 'compound': 'MEDIUM'}\n"
)

# Player car_ids, deliberately NOT house-bot ids. "VEL-01" and "NXS-07" are in
# RESERVED_CAR_IDS (backend/state/car_ids.py, derived from BUILTIN_BOTS): a
# player claiming one is refused, and correctly so -- every race appends the
# house bots, so honouring the claim would put two cars called VEL-01 on the
# grid. They appear below only as `house_bot` participants, which is the one
# place they belong.
PLAYER_CAR_ID = "USR-01"
OTHER_PLAYER_CAR_ID = "USR-02"
HOUSE_BOT = "NXS-07"


# ─── Process plumbing ────────────────────────────────────────────────


def _free_ports(count: int) -> list:
    """`count` ports nothing is listening on, guaranteed distinct.

    Every socket is held open until all of them have been bound, and only
    then are they all released. Binding and closing one at a time -- which is
    what a single `_free_port()` called twice in a row did -- leaves nothing
    holding the first port while the second is chosen, so the kernel is free
    to hand the same ephemeral port back and replica B then fails to bind.
    Low probability, but this is the file that must not flake.

    A hardcoded port would be worse in a way that is not probabilistic at
    all: it fails outright whenever two copies of the suite share a machine,
    which CI and a developer's laptop both do.

    The window between releasing these and the children binding them is not
    closable from here -- uvicorn is given the port on its command line -- but
    it is a window against the rest of the machine, not against ourselves,
    which is the collision that was actually reachable.
    """
    holders = []
    try:
        for _ in range(count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            holders.append(sock)
        ports = [sock.getsockname()[1] for sock in holders]
        assert len(set(ports)) == count, f"duplicate ports handed out: {ports}"
        return ports
    finally:
        for sock in holders:
            sock.close()


def _throwaway_mongo_url(name: str) -> str:
    """A URL for a disposable database on whatever host MONGODB_URI names.

    Derived from `mongo_url()` rather than hardcoding 127.0.0.1:27017, so this
    and the `_mongo_is_reachable()` skip gate can never end up pointing at
    different servers -- the exact shape a CI service container introduces.
    Mirrors tests/jobs/test_shutdown.py, which needs the same guarantee.
    """
    from backend.db.models import mongo_url

    parsed = urlparse(mongo_url())
    return urlunparse(parsed._replace(path=f"/{name}"))


class _Child:
    """A spawned process whose output is captured to a file.

    Not subprocess.PIPE: these children are left running for seconds while the
    test talks to them over HTTP, and a pipe nobody drains fills its buffer and
    blocks the child mid-write. A file never blocks, and `output()` reads it
    back when a failure needs explaining.
    """

    def __init__(self, argv, env, label):
        self._log = tempfile.NamedTemporaryFile(
            mode="w+", suffix=f".{label}.log", delete=False
        )
        self.label = label
        self.proc = subprocess.Popen(
            argv,
            cwd=str(PIWALL_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            text=True,
        )

    @property
    def pid(self) -> int:
        return self.proc.pid

    def output(self) -> str:
        try:
            self._log.flush()
            with open(self._log.name) as handle:
                return handle.read()
        except Exception:
            return "<no output captured>"

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        try:
            self._log.close()
        except Exception:
            pass
        try:
            os.unlink(self._log.name)
        except OSError:
            pass


def _start_replica(port: int, mongo_uri: str, label: str) -> _Child:
    return _Child(
        [
            sys.executable, "-m", "uvicorn", "backend.main:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ],
        _child_env(mongo_uri),
        label,
    )


def _wait_until_serving(child: _Child, port: int, timeout: float = 45.0) -> str:
    """Block until the replica answers /health, and return its base URL.

    /health, not /ready: liveness touches no dependency, so this waits for the
    HTTP server to be up rather than for Redis and Mongo to be up as well. The
    skip gate at the top of this module already established those.
    """
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if child.proc.poll() is not None:
            pytest.fail(
                f"replica {child.label} exited with code {child.proc.returncode} "
                f"before serving:\n{child.output()}"
            )
        try:
            if httpx.get(f"{base}/health", timeout=1.0).status_code == 200:
                return base
        except Exception:
            time.sleep(0.1)
    pytest.fail(
        f"replica {child.label} never answered /health within {timeout}s:\n"
        f"{child.output()}"
    )


def _read_line_by(stream, deadline: float):
    """One line from `stream`, or None once `deadline` passes.

    A bare readline() on a child that hung before writing anything would hang
    this test with it. select bounds the wait without a background thread.
    """
    remaining = deadline - time.time()
    if remaining <= 0:
        return None
    ready, _, _ = select.select([stream], [], [], remaining)
    if not ready:
        return None
    return stream.readline()


# ─── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_shared_redis():
    """The job stream and this file's lobby keys, gone before and after.

    STREAM and the lobby keyspace are shared with every other test file and
    with whatever else is using this developer's Redis, so each test starts
    from a known stream and leaves nothing behind. Lobby keys are deleted by
    the id each test created rather than by wildcard, which would blow away a
    concurrently running suite's lobbies.
    """
    client = get_redis()
    client.delete(STREAM)
    created = []
    yield created
    for race_id in created:
        client.delete(f"{KEY_PREFIX}{race_id}")
    client.delete(STREAM)


@pytest.fixture
def throwaway_db():
    """A disposable database, never the shared phi1 one.

    Yields (session, uri). The replicas and workers spawned below are pointed
    at `uri` through MONGODB_URI, exactly as the real deployment points them at
    Mongo, and the assertions read back through the same crud functions
    production reads.
    """
    from pymongo import MongoClient

    from backend.db.models import MongoSession, init_db

    name = f"piwall_test_gate_{uuid.uuid4().hex[:8]}"
    uri = _throwaway_mongo_url(name)
    client = MongoClient(uri)
    database = client[name]
    init_db(database)
    session = MongoSession(database)
    yield session, uri
    session.close()
    client.drop_database(name)
    client.close()


@pytest.fixture
def two_replicas(throwaway_db):
    """Two real API processes on two ports, sharing one Redis and one Mongo.

    Returns (base_url_a, base_url_b). They are separate OS processes with
    separate interpreters, separate module globals and separate connection
    pools -- which is what makes any state they agree on necessarily shared
    state rather than shared memory.
    """
    _session, uri = throwaway_db
    port_a, port_b = _free_ports(2)
    child_a = _start_replica(port_a, uri, "replica-a")
    child_b = _start_replica(port_b, uri, "replica-b")
    try:
        base_a = _wait_until_serving(child_a, port_a)
        base_b = _wait_until_serving(child_b, port_b)
        assert len({child_a.pid, child_b.pid, os.getpid()}) == 3, (
            "the two replicas must be distinct OS processes, and neither may "
            "be this test process -- two objects in one interpreter are not "
            "two replicas"
        )
        yield base_a, base_b
    finally:
        child_a.stop()
        child_b.stop()


def _player(session, prefix: str):
    return crud.create_player(session, f"{prefix}_{uuid.uuid4().hex[:8]}", "T")


def _post(base: str, path: str, api_key: str, body: dict) -> httpx.Response:
    return httpx.post(
        f"{base}{path}", json=body, headers={"x-api-key": api_key}, timeout=30.0
    )


def _get(base: str, path: str) -> httpx.Response:
    return httpx.get(f"{base}{path}", timeout=30.0)


def _lobby_view(base: str, race_id: str) -> dict:
    """GET /api/race/{id} and insist the LOBBY was what answered.

    This distinction is load-bearing, and leaving it implicit made an earlier
    draft of this file green under a mutation that should have killed it.
    `get_race` has two branches: a live lobby read from Redis, and a fallback
    that reads the race document from Mongo for a terminal or expired lobby.
    They return different keys -- the lobby branch returns "players", the Mongo
    branch returns "results" -- and the API writes every status change to BOTH
    stores. So a replica whose lobby state was process-local would still report
    the right `status`, having quietly fallen through to a database row its
    sibling wrote, and an assertion on `status` alone would never notice.

    Asserting "players" is present pins the answer to the shared lobby.
    """
    payload = _get(base, f"/api/race/{race_id}").json()
    assert "players" in payload, (
        f"{base} did not serve this race from the shared lobby -- it fell "
        f"through to the database instead, which means it could not see the "
        f"lobby at all: {payload}"
    )
    return payload


# ─── Gate part 1: two API replicas serve all traffic correctly ───────


def test_two_replicas_serve_the_same_lobby(two_replicas, throwaway_db,
                                           _clean_shared_redis):
    """Gate part 1, over HTTP, across two real API processes.

    A lobby created against replica A is complete when read from replica B; a
    join accepted by B is visible to A; and B refuses a car_id that a player
    who joined through A already holds.

    The production line whose deletion turns this red: `LOBBIES = LobbyStore()`
    in backend/main.py (and the Redis-backed storage behind it in
    backend/state/lobby.py). Replace it with the module-global dict this phase
    removed and replica B's very first GET returns 404 "Race not found",
    because the lobby only ever existed in replica A's memory. The last
    assertion additionally pins the atomic half: move the car_id collision
    check out of `_JOIN_SCRIPT` into the `join_race` handler and two replicas
    stop agreeing on who holds which engine identity.
    """
    base_a, base_b = two_replicas
    session, _uri = throwaway_db
    created = _clean_shared_redis

    alex = _player(session, "gate_alex")
    sam = _player(session, "gate_sam")

    made = _post(base_a, "/api/race/create", alex.api_key,
                 {"track": "bahrain", "race_type": "quick", "speed": 5.0})
    assert made.status_code == 200, made.text
    race_id = made.json()["race_id"]
    created.append(race_id)

    # Read it from the OTHER process. Nothing in replica B's memory has ever
    # heard of this race, and _lobby_view insists the answer came from the
    # shared lobby rather than from the race document in Mongo.
    seen = _lobby_view(base_b, race_id)
    assert seen["track"] == "bahrain"
    assert seen["status"] == "lobby"

    # And the listing, which is a different read path (LobbyStore.list_open's
    # SCAN) than the single-key GET above.
    listed = _get(base_b, "/api/races").json()
    assert race_id in {r["race_id"] for r in listed}, (
        "replica B's race list must include a lobby opened on replica A"
    )

    # A join accepted by A.
    joined_a = _post(base_a, f"/api/race/{race_id}/join", alex.api_key,
                     {"car_id": PLAYER_CAR_ID, "starting_compound": "MEDIUM"})
    assert joined_a.status_code == 200, joined_a.text
    assert joined_a.json()["car_id"] == PLAYER_CAR_ID

    # A join accepted by B -- writes must flow both ways, or only one replica
    # can ever accept traffic.
    joined_b = _post(base_b, f"/api/race/{race_id}/join", sam.api_key,
                     {"car_id": OTHER_PLAYER_CAR_ID, "starting_compound": "SOFT"})
    assert joined_b.status_code == 200, joined_b.text

    back_on_a = _lobby_view(base_a, race_id)
    assert set(back_on_a["players"]) == {alex.id, sam.id}, (
        "a join accepted by replica B must be visible on replica A"
    )
    assert {p["car_id"] for p in back_on_a["players"].values()} == {
        PLAYER_CAR_ID, OTHER_PLAYER_CAR_ID
    }

    # Cross-replica uniqueness: B must refuse an identity a player who joined
    # through A already holds. Two replicas that disagree here hand one
    # player's bot both cars (engine/race.py keys strategies and belief_models
    # by car_id).
    clash = _post(base_b, f"/api/race/{race_id}/join", sam.api_key,
                  {"car_id": PLAYER_CAR_ID})
    assert clash.status_code == 400, (
        "replica B accepted a car_id already held by a player who joined "
        f"through replica A: {clash.text}"
    )

    # And a house bot's identity is refused through the API too -- these are
    # appended to every race, so a player holding one is the same collision by
    # another route (backend/state/car_ids.py).
    reserved = _post(base_b, f"/api/race/{race_id}/join", sam.api_key,
                     {"car_id": HOUSE_BOT})
    assert reserved.status_code == 400, (
        f"a reserved house-bot car_id must be refused: {reserved.text}"
    )


def test_a_status_change_on_one_replica_is_immediately_visible_on_the_other(
    two_replicas, throwaway_db, _clean_shared_redis
):
    """Gate part 1, for a write that is not a join.

    Starting a race is the transition that mattered most before this phase: it
    was written into one process's dict, so every spectator routed to the other
    replica saw a race that never started.

    The production line whose deletion turns this red: `LOBBIES.set_status(
    race_id, "countdown")` in backend/main.py's `start_race`. With it removed
    (or made process-local) replica B keeps reporting "lobby" forever.
    """
    base_a, base_b = two_replicas
    session, _uri = throwaway_db
    created = _clean_shared_redis

    owner = _player(session, "gate_owner")
    race_id = _post(base_a, "/api/race/create", owner.api_key,
                    {"track": "bahrain"}).json()["race_id"]
    created.append(race_id)
    assert _post(base_a, f"/api/race/{race_id}/join", owner.api_key,
                 {"car_id": PLAYER_CAR_ID}).status_code == 200

    before = _lobby_view(base_b, race_id)
    assert before["status"] == "lobby"
    assert set(before["players"]) == {owner.id}

    started = _post(base_a, f"/api/race/{race_id}/start", owner.api_key, {})
    assert started.status_code == 200, started.text

    # start_race sets the status synchronously and only then spawns the
    # five-second countdown, so this read races nothing.
    after = _lobby_view(base_b, race_id)
    assert after["status"] == "countdown", (
        "a race started on replica A must be reported as started by replica B"
    )
    assert set(after["players"]) == {owner.id}


def test_a_race_started_on_one_replica_is_run_by_a_worker_and_served_by_the_other(
    two_replicas, throwaway_db, _clean_shared_redis
):
    """The whole phase, end to end, in one sequence.

    Replica A opens a lobby and starts the race. A separate worker process --
    which has never spoken to either replica and shares no memory with them --
    claims the job off Redis, runs the match and persists it. Replica B, which
    did not create the race, start it, or run it, serves the finished result.

    Three processes, no shared memory, one race. That is the deployment spec
    section 11 describes, and nothing smaller than this proves it.

    The production line whose deletion turns this red: `_get_jobs().enqueue(
    job)` at the end of backend/main.py's `_run_race`. Without it the API has
    computed a manifest and told nobody, the worker sits idle, and the poll
    below times out with the race stuck at "running".
    """
    base_a, base_b = two_replicas
    session, uri = throwaway_db
    created = _clean_shared_redis

    owner = _player(session, "gate_e2e")
    race_id = _post(base_a, "/api/race/create", owner.api_key,
                    {"track": "bahrain"}).json()["race_id"]
    created.append(race_id)
    assert _post(base_a, f"/api/race/{race_id}/join", owner.api_key,
                 {"car_id": PLAYER_CAR_ID}).status_code == 200
    # Submitted through replica B, started through replica A: the code the
    # worker eventually runs was written by a process that never starts the
    # race, which is only possible because the lobby is shared state.
    submitted = _post(base_b, f"/api/race/{race_id}/submit-bot", owner.api_key,
                      {"code": PLAYER_CODE})
    assert submitted.status_code == 200, submitted.text

    worker = _Child(
        [sys.executable, "-c",
         "import backend.worker as w; w.run_forever(consumer='gate-e2e')"],
        _child_env(uri),
        "worker-e2e",
    )
    try:
        assert _post(base_a, f"/api/race/{race_id}/start", owner.api_key,
                     {}).status_code == 200

        # The countdown is five seconds before the job is even enqueued; the
        # match itself is fast. 90s is generous enough that a loaded CI runner
        # is never the reason this fails.
        deadline = time.time() + 90
        payload = None
        while time.time() < deadline:
            if worker.proc.poll() is not None:
                pytest.fail(
                    f"the worker exited early (code {worker.proc.returncode}):\n"
                    f"{worker.output()}"
                )
            payload = _get(base_b, f"/api/race/{race_id}").json()
            if payload.get("status") == "finished":
                break
            time.sleep(0.5)

        assert payload is not None and payload["status"] == "finished", (
            "replica B never served the finished race started on replica A; "
            f"last status was {payload and payload.get('status')!r}\n"
            f"worker output:\n{worker.output()}"
        )
        # Not just a status flag: the actual grid, served by the replica that
        # neither started nor ran the race.
        assert {r["car_id"] for r in payload["results"]} == {
            PLAYER_CAR_ID, *_house_bot_ids_for(session, race_id, PLAYER_CAR_ID)
        }, f"replica B served an incomplete grid: {payload['results']}"
        assert crud.get_replay_hash(session, race_id), (
            "a finished match must have left a replay hash against its manifest"
        )
    finally:
        worker.stop()


def _house_bot_ids_for(session, race_id: str, *player_car_ids: str) -> set:
    """The house-bot ids the API actually appended to this race.

    Read back from the manifest the API wrote rather than hardcoded: which
    bots fill a grid is `_build_job_and_manifest`'s decision, and a test that
    restated it would be asserting its own copy of that choice instead of the
    one production made.
    """
    manifest = crud.get_manifest(session, race_id)
    assert manifest is not None, "the API must have written a manifest"
    return {
        p.house_bot for p in manifest.participants if p.house_bot
    } - set(player_car_ids)


# ─── Gate part 2: a worker restart mid-match loses no match ──────────


# Run verbatim by the worker-1 subprocess below. It wraps run_match_isolated
# -- the call process_one makes only AFTER queue.claim() has already succeeded
# -- so "JOB_CLAIMED" is proof the job is genuinely in hand, and the readline()
# after it hands the parent control of exactly when the match would run. The
# parent never releases it; it sends SIGKILL instead. That makes the ordering
# structural rather than probabilistic: the worker cannot possibly have acked
# the job before it died, because the match had not started.
#
# Placeholders are substituted with str.replace rather than str.format so the
# braces in the surrounding file need no escaping. Mirrors the handshake in
# tests/jobs/test_shutdown.py, which exists for the same reason.
_GATED_WORKER_SOURCE = '''
import sys
import backend.worker as w

_real = w.run_match_isolated

def _gated(spec):
    print("JOB_CLAIMED", flush=True)
    sys.stdin.readline()
    return _real(spec)

w.run_match_isolated = _gated
w.run_forever(consumer="__CONSUMER__")
'''

# A baseline run: claim one job, run it, print the replay hash, persist
# nothing. Its only job is to establish what race this manifest produces, so
# the recovered run below can be checked against it.
_BASELINE_SOURCE = '''
from backend.jobs.events import MatchEvents
from backend.jobs.queue import MatchJobQueue
from backend.worker import process_one

out = []
process_one(MatchJobQueue(), MatchEvents(channel="__CHANNEL__"),
            persist=out.append, consumer="gate-baseline", block_ms=5000)
print("BASELINE " + out[0]["replay_sha256"], flush=True)
'''

# The replacement worker. min_idle_ms=0 so the abandoned job is reclaimed now
# rather than after the 30s production threshold -- the threshold is a timing
# policy, not the recovery mechanism, and waiting it out would only make this
# test slower. persist is the real _persist_result against the throwaway
# database, so what is asserted afterwards is what a real worker writes.
_REPLACEMENT_SOURCE = '''
import backend.worker as w
from backend.db.models import create_db_engine, init_db, mongo_url
from backend.jobs.events import MatchEvents
from backend.jobs.queue import MatchJobQueue

factory = init_db(create_db_engine(mongo_url()))

def persist(result):
    db = factory()
    try:
        w._persist_result(db, result)
    finally:
        db.close()

match_id = w.process_one(MatchJobQueue(), MatchEvents(channel="__CHANNEL__"),
                         persist, consumer="gate-worker-2", block_ms=1000,
                         min_idle_ms=0)
print("RECOVERED " + str(match_id), flush=True)
'''


def _run_child_for_line(source: str, uri: str, prefix: str, label: str,
                        timeout: float = 90.0, channel: str = "") -> str:
    """Run a child to completion and return the line starting with `prefix`.

    `channel` replaces the __CHANNEL__ placeholder in `source`. These children
    publish a real `match_finished` event, and CHANNEL is one fixed name every
    worker and API replica must agree on in production -- so publishing a
    throwaway match id onto it is exactly the cross-delivery
    test_nothing_is_lost_when_the_worker_dies_before_persisting isolates
    itself against, a few tests down in this same file. Isolating both means
    the file does not argue with itself.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", source.replace("__CHANNEL__", channel)],
        cwd=str(PIWALL_DIR), env=_child_env(uri),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    lines = []
    try:
        deadline = time.time() + timeout
        while True:
            line = _read_line_by(proc.stdout, deadline)
            if line is None or line == "":
                break
            lines.append(line)
            if line.startswith(prefix):
                return line.strip()
        pytest.fail(
            f"{label} never printed a {prefix!r} line:\n" + "".join(lines)
        )
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        try:
            proc.stdout.close()
        except Exception:
            pass


def test_a_worker_restart_mid_match_loses_no_match(throwaway_db,
                                                   _clean_shared_redis):
    """Gate part 2, with real processes and a real kill.

    A worker claims the job and is SIGKILLed while holding it -- no handler
    runs, no ack happens, nothing is persisted. A second worker process takes
    over. The match must complete, and its result must be byte-identical to a
    run that was never interrupted: "lost no match" would otherwise be
    satisfied by finishing some other race under the same id.

    SIGKILL rather than SIGTERM on purpose. SIGTERM is the graceful path and is
    already covered by tests/jobs/test_shutdown.py, where the worker finishes
    the job in hand. This is the ungraceful one: the process simply ceases,
    mid-flight, which is what a crashed container or an evicted pod actually
    does.

    The production lines whose deletion turns this red: `queue.reclaim_stalled(
    consumer, min_idle_ms=min_idle_ms)` at the top of `process_one`, and the
    consumer group itself in backend/jobs/queue.py. Remove either -- make the
    queue an LPOP list, say -- and the killed worker takes the match with it:
    the replacement claims nothing, pending_count stays 1, and no result is
    ever written. Moving `queue.ack(entry_id)` above `persist(...)` also turns
    it red, via the same route on the next test down.
    """
    session, uri = throwaway_db
    client = get_redis()

    owner = _player(session, "gate_restart")
    race = crud.create_race(session, "bahrain", "quick", owner_id=owner.id)
    match_id = race.id

    job = {
        "match_id": match_id, "track": "bahrain", "seed": 1000,
        "participants": [
            {"slot": 0, "player_id": owner.id, "car_id": PLAYER_CAR_ID,
             "code": PLAYER_CODE},
            {"slot": 1, "house_bot": HOUSE_BOT},
        ],
    }
    # The same manifest the worker derives from the job, so what is saved here
    # and what _persist_result updates are one document, not two views of one.
    from backend.worker import _manifest_from_job

    crud.save_manifest(session, _manifest_from_job(job))

    queue = MatchJobQueue()

    # 1. An uninterrupted run, to establish what this manifest produces.
    queue.enqueue(job)
    channel = f"piwall:events:match:gate-{uuid.uuid4().hex}"
    baseline = _run_child_for_line(
        _BASELINE_SOURCE, uri, "BASELINE", "the baseline worker",
        channel=channel,
    ).split(" ", 1)[1]
    assert baseline.startswith("sha256:")
    assert queue.pending_count() == 0, "the baseline run must have acked"

    # 2. The failure. worker-1 claims the job and is killed holding it.
    queue.enqueue(job)
    consumer = f"gate-worker-1-{uuid.uuid4().hex[:8]}"
    worker1 = subprocess.Popen(
        [sys.executable, "-c",
         _GATED_WORKER_SOURCE.replace("__CONSUMER__", consumer)],
        cwd=str(PIWALL_DIR), env=_child_env(uri),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    try:
        deadline = time.time() + 60
        lines = []
        claimed = False
        while True:
            line = _read_line_by(worker1.stdout, deadline)
            if line is None or line == "":
                break
            lines.append(line)
            if line.strip() == "JOB_CLAIMED":
                claimed = True
                break
        if not claimed:
            pytest.fail(
                "worker-1 never reached a claimed job:\n" + "".join(lines)
            )

        # Corroborated against Redis rather than trusted from the child's own
        # self-report: the marker and the real claim must not have come apart.
        assert queue.pending_count() == 1, (
            "worker-1 printed its marker but Redis does not show the job as "
            "delivered -- the handshake and the real claim have diverged"
        )

        worker1.send_signal(signal.SIGKILL)
        worker1.wait(timeout=15)
        assert worker1.returncode in (-signal.SIGKILL, 137), (
            f"worker-1 was supposed to die uncleanly, not exit "
            f"{worker1.returncode}"
        )
    finally:
        if worker1.poll() is None:
            worker1.kill()
            worker1.wait(timeout=5)
        for stream in (worker1.stdin, worker1.stdout):
            try:
                stream.close()
            except Exception:
                pass

    # Nothing was acked, and nothing was written. The match exists only as an
    # abandoned entry in the consumer group's pending list.
    assert queue.pending_count() == 1, (
        "a killed worker's claimed job must stay pending, not vanish"
    )
    assert crud.get_replay_hash(session, match_id) is None
    assert crud.get_race(session, match_id).status != "finished"

    # 3. The replacement worker takes over.
    recovered = _run_child_for_line(
        _REPLACEMENT_SOURCE, uri, "RECOVERED", "the replacement worker",
        channel=channel,
    ).split(" ", 1)[1]
    assert recovered == match_id, (
        f"the abandoned match was not recovered; worker-2 reported {recovered!r}"
    )

    assert queue.pending_count() == 0, "the recovered job was never acked"
    assert crud.get_replay_hash(session, match_id) == baseline, (
        "the recovered match produced a different race than an uninterrupted "
        "run of the same manifest"
    )
    persisted = crud.get_race(session, match_id)
    assert persisted.status == "finished"
    assert {r.car_id for r in crud.get_race_results(session, match_id)} == {
        PLAYER_CAR_ID, HOUSE_BOT
    }, "every car's result must be persisted, not just the replay hash"

    client.delete(STREAM)


def test_nothing_is_lost_when_the_worker_dies_before_persisting(throwaway_db,
                                                                _clean_shared_redis):
    """A crash between running and saving must leave the job recoverable.

    In-process on purpose: this one is about the ORDER of two calls inside
    `process_one`, and the cheapest way to sit between them is to make
    `persist` raise. The cross-process claim is the test above.

    The production line whose deletion turns this red: the placement of
    `queue.ack(entry_id)` AFTER `persist(...)` in backend/worker.py's
    `process_one`. Move the ack above persist and the first assertion fails --
    pending_count is 0, the job is gone, and the match that was never saved is
    lost with no record that it existed.
    """
    from backend.jobs.events import MatchEvents
    from backend.worker import process_one

    session, _uri = throwaway_db
    owner = _player(session, "gate_crash")
    race = crud.create_race(session, "bahrain", "quick", owner_id=owner.id)

    job = {
        "match_id": race.id, "track": "bahrain", "seed": 1000,
        "participants": [
            {"slot": 0, "player_id": owner.id, "car_id": PLAYER_CAR_ID,
             "code": PLAYER_CODE},
            {"slot": 1, "house_bot": HOUSE_BOT},
        ],
    }
    queue = MatchJobQueue()
    # An isolated channel, as tests/jobs/test_worker.py does: CHANNEL is one
    # fixed name every replica must share in production, which means two
    # concurrent suites against one Redis would cross-deliver.
    events = MatchEvents(channel=f"piwall:events:match:gate-{uuid.uuid4().hex}")
    queue.enqueue(job)

    def explode(_result):
        raise RuntimeError("crash between running and saving")

    with pytest.raises(RuntimeError):
        process_one(queue, events, persist=explode, consumer="gate-crasher")

    assert queue.pending_count() == 1, (
        "a job whose persist raised must stay pending and recoverable"
    )

    saved = []
    process_one(queue, events, persist=saved.append,
                consumer="gate-crasher-2", min_idle_ms=0)
    assert saved and saved[0]["match_id"] == race.id
    assert queue.pending_count() == 0


# ─── Gate part 1, continued: the one traffic type not backed by Redis ──


async def _finished_event_on(ws_url: str, start_race, timeout: float) -> dict:
    """Connect, THEN start the race, then wait for the finished event.

    The ordering is the whole point and is why this is one coroutine rather
    than a connect helper called between two HTTP calls. Match events are
    Redis pub/sub, which is deliberately fire-and-forget (see
    backend/jobs/events.py): a replica that is not subscribed when the worker
    publishes simply never learns. Connecting first, and only then starting
    the race from inside the connected context, means this test cannot pass
    or fail on whether it won a race against a five-second countdown.
    """
    from websockets.asyncio.client import connect

    async with connect(ws_url) as socket:
        await asyncio.to_thread(start_race)
        deadline = time.time() + timeout
        seen = []
        while time.time() < deadline:
            raw = await asyncio.wait_for(
                socket.recv(), timeout=max(0.1, deadline - time.time())
            )
            message = json.loads(raw)
            seen.append(message.get("type") or message)
            if message.get("type") == "finished":
                return message
        raise AssertionError(
            f"no finished event on this socket within {timeout}s; saw {seen}"
        )


def test_a_spectator_on_one_replica_sees_a_match_the_other_replica_started(
    two_replicas, throwaway_db, _clean_shared_redis
):
    """Gate part 1 for the WebSocket -- the only cross-replica path whose
    state is deliberately NOT in Redis.

    `SOCKETS` (backend/main.py:84) is a per-process dict and has to stay one:
    a live socket is an object owned by one process and cannot be handed to
    another through Redis. That makes "two replicas serve all traffic
    correctly" a genuinely non-trivial claim here, in a way it is not for any
    HTTP path in this file -- it holds only because the worker publishes
    `match_finished` to a channel EVERY replica subscribes to, and each
    replica then writes to whichever sockets it happens to hold.

    So: a spectator connects to replica B. The race is created, joined and
    started on replica A, and run by a worker process that has spoken to
    neither replica. B's socket must receive the finished event, with the
    standings its client needs to render anything at all.

    The production lines whose deletion turns this red: the
    `asyncio.create_task(_relay_match_events())` in backend/main.py's
    `lifespan`, and `events.publish(...)` in worker.py's `process_one`.
    Remove either and this socket receives nothing but the 30s keepalive
    until the deadline -- while every other assertion in this file still
    passes, because the HTTP path keeps reporting the race finished off the
    durable write. That asymmetry is exactly the silent regression this
    closes, and it is a live risk: four review rounds went into keeping
    SOCKETS per-process, so the pub/sub fan-out is the load-bearing half and
    nothing else here touches it.
    """
    base_a, base_b = two_replicas
    session, uri = throwaway_db
    created = _clean_shared_redis

    owner = _player(session, "gate_ws")
    race_id = _post(base_a, "/api/race/create", owner.api_key,
                    {"track": "bahrain"}).json()["race_id"]
    created.append(race_id)
    assert _post(base_a, f"/api/race/{race_id}/join", owner.api_key,
                 {"car_id": PLAYER_CAR_ID}).status_code == 200
    assert _post(base_a, f"/api/race/{race_id}/submit-bot", owner.api_key,
                 {"code": PLAYER_CODE}).status_code == 200

    worker = _Child(
        [sys.executable, "-c",
         "import backend.worker as w; w.run_forever(consumer='gate-ws')"],
        _child_env(uri),
        "worker-ws",
    )
    try:
        ws_url = base_b.replace("http://", "ws://") + f"/ws/race/{race_id}"

        def start():
            started = _post(base_a, f"/api/race/{race_id}/start",
                            owner.api_key, {})
            assert started.status_code == 200, started.text

        event = asyncio.run(_finished_event_on(ws_url, start, timeout=90.0))

        assert event["result"]["race_id"] == race_id, (
            f"replica B's socket got a finished event for the wrong race: "
            f"{event}"
        )
        # Not merely "an event arrived": the payload a client actually
        # renders. frontend/src/lib/websocket.ts does `if (msg.result)
        # setResult(msg.result)` and the race page then spreads
        # result.standings, so an event carrying no standings takes the page
        # down for every spectator -- the regression _stream_stored_replay's
        # docstring records as already having shipped once.
        assert event["result"]["standings"], (
            f"the finished event reached replica B's socket with no "
            f"standings to render: {event}"
        )
        assert PLAYER_CAR_ID in {
            car["car_id"] for car in event["result"]["standings"]
        }
    finally:
        worker.stop()


# ─── Gate part 2, continued: surviving the loss of the job stream ───────


def test_a_worker_survives_losing_the_job_stream_and_drains_the_next_job(
    throwaway_db, _clean_shared_redis
):
    """A Redis restart must not wedge the fleet, silently, forever.

    The job stream and its consumer group live only in Redis. Before the fix
    that accompanies this test, `_ensure_group` ran in `MatchJobQueue.__init__`
    and nowhere else, while `run_forever` builds one queue at startup and
    keeps it for the life of the process -- so a stream key that disappeared
    afterwards (a Redis restart with no persistence, an eviction under
    maxmemory, an operator DEL) made every subsequent group-scoped command
    raise NOGROUP. `run_forever` logged each one as a job failure and backed
    off to its 30s ceiling; the process never exited, so
    `restart: unless-stopped` never fired.

    It was silent on both sides. XADD does not fail, so the API went on
    accepting races and stacking up jobs nothing could read, and `/ready`
    reported healthy throughout because Redis and Mongo genuinely were. The
    only symptom was that matches stopped finishing.

    This is the reproduction, with a real worker process: wedge it, then
    enqueue and require the match to actually run and persist. Revert
    `_with_group`'s recovery in backend/jobs/queue.py and this goes red by
    timing out with the race still unfinished -- which is precisely what a
    production worker did, except without a deadline.
    """
    session, uri = throwaway_db
    client = get_redis()

    owner = _player(session, "gate_wedge")
    race = crud.create_race(session, "bahrain", "quick", owner_id=owner.id)
    match_id = race.id
    job = {
        "match_id": match_id, "track": "bahrain", "seed": 1000,
        "participants": [
            {"slot": 0, "player_id": owner.id, "car_id": PLAYER_CAR_ID,
             "code": PLAYER_CODE},
            {"slot": 1, "house_bot": HOUSE_BOT},
        ],
    }
    from backend.worker import _manifest_from_job

    crud.save_manifest(session, _manifest_from_job(job))

    # Built BEFORE the outage, and reused across it. This is not incidental
    # -- it is what makes the reproduction faithful, and getting it wrong
    # made an earlier draft of this test pass with the fix reverted.
    # `MatchJobQueue.__init__` calls `_ensure_group()`, so a queue constructed
    # *after* the stream key was deleted silently heals the very wedge this
    # test exists to reproduce, and the worker is then never asked to recover
    # from anything. Production does not get that accident: backend/main.py's
    # `_get_jobs()` memoises one queue per API process and `run_forever`
    # builds one per worker, both at startup, so in a real outage every
    # surviving process is holding a queue object built before the key
    # vanished. This holds one too.
    queue = MatchJobQueue()

    worker = _Child(
        [sys.executable, "-c",
         "import backend.worker as w; w.run_forever(consumer='gate-wedge')"],
        _child_env(uri),
        "worker-wedge",
    )
    try:
        deadline = time.time() + 45
        while time.time() < deadline and "worker ready" not in worker.output():
            time.sleep(0.2)
        assert "worker ready" in worker.output(), (
            f"the worker never started:\n{worker.output()}"
        )

        # The outage. Everything the group knows about goes with the key.
        client.delete(STREAM)

        # Long enough for the worker's in-flight XREADGROUP (block_ms=2000)
        # to expire and for it to go round the loop into the NOGROUP path at
        # least once -- so this is genuinely testing recovery from the wedged
        # state rather than a worker that never noticed.
        time.sleep(4)

        # XADD rebuilds the key with no consumer group -- note this goes
        # through the queue built above, so nothing here re-creates the
        # group on the worker's behalf. Before the fix this call succeeded
        # exactly as it does now, and the job was simply unreachable
        # forever: that silent success is half of what made the wedge so
        # hard to notice.
        queue.enqueue(job)
        assert client.exists(STREAM), "the enqueue must have rebuilt the key"

        deadline = time.time() + 90
        status = None
        while time.time() < deadline:
            if worker.proc.poll() is not None:
                pytest.fail(
                    f"the worker exited (code {worker.proc.returncode}) "
                    f"instead of recovering:\n{worker.output()}"
                )
            persisted = crud.get_race(session, match_id)
            status = persisted.status if persisted else None
            if status == "finished":
                break
            time.sleep(0.5)

        assert status == "finished", (
            f"the worker never drained a job enqueued after the stream key "
            f"was lost; the race is still {status!r}. The worker is wedged "
            f"on NOGROUP.\n{worker.output()[-3000:]}"
        )
        assert crud.get_replay_hash(session, match_id), (
            "the recovered job must have been persisted, not merely acked"
        )
    finally:
        worker.stop()
