"""Shutdown must not abandon work that has been claimed, or leave a client
hanging on a socket to a replica that is already gone.

Worker side: a worker that exits holding an unacked job is recoverable --
that is what reclaim_stalled is for -- but only after an idle timeout during
which a client waits. Finishing the job in hand costs a fraction of a second
and avoids that wait entirely. The signal handler, the shutdown flag and the
backoff-that-checks-it already exist (backend/worker.py's _request_shutdown,
_shutting_down, _sleep_unless_shutting_down) and are exercised elsewhere.
What is not yet proven anywhere is the actual claim being made: a real
worker process, sent a real SIGTERM while it genuinely holds a claimed job,
still finishes and acks that job rather than abandoning it. That is what
test_a_real_sigterm_mid_job_still_finishes_and_acks_the_claimed_match runs --
a real subprocess, a real Redis consumer group, a real sandboxed match, and
a real (throwaway) database.

That test's window between "claimed" and "acked" is a real one -- tens of
milliseconds of real match execution -- and a first version of this file
raced it: poll Redis until the job looks claimed, then fire SIGTERM. That
passed, but only proved the drain works when the signal happens to land
before the ack; a worker whose handler abandoned the job and exited 0 also
passed the same test whenever the poller was slow to notice the claim,
because nothing checked that the signal actually preceded the ack rather
than following it. The fix is a handshake, not a faster poll: the child
process this file spawns is handed its own source (below), and that source
wraps run_match_isolated so the child prints a marker the instant the job is
claimed and then blocks -- on this test's own stdin pipe -- until the parent
releases it. The parent reads the marker (proof of claim), sends SIGTERM,
and only then writes the release line. That makes the ordering structural
rather than probabilistic: the signal cannot possibly land after the ack,
because the match has not even started running yet when it is sent.

API side: a replica shutting down still holds live WebSockets for whichever
races this process happens to be spectating. drain_sockets() closes them so
those clients get a clean close frame and reconnect elsewhere, instead of
sitting on a socket to a process that is already gone until a TCP timeout
notices.
"""

import asyncio
import os
import select
import hashlib
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest

from backend.db import crud
from backend.determinism.manifest import Participant, build_manifest
from backend.jobs.queue import STREAM, MatchJobQueue
from backend.observability.health import _mongo_is_reachable
from backend.state.redis_client import get_redis, redis_is_reachable
from tests.subprocess_env import child_env

PIWALL_DIR = Path(__file__).resolve().parents[2]

PLAYER_CODE = (
    "def my_strategy(state, my_car):\n"
    "    return {'pit': my_car.tyre_age > 20, 'compound': 'MEDIUM'}\n"
)

# The worker subprocess spawned below runs this verbatim. It wraps
# run_match_isolated -- the call process_one makes only after queue.claim()
# has already succeeded -- so the "JOB_CLAIMED" marker is proof the job is
# genuinely in hand, and the readline() after it hands control of exactly
# when the real match runs to the parent test process. Placeholders are
# substituted with str.replace rather than str.format/an f-string so
# nothing here has to worry about the braces in the rest of this file's
# own code.
_CHILD_SOURCE = '''
import sys
import backend.worker as w

_real_run_match_isolated = w.run_match_isolated

def _gated_run_match_isolated(spec):
    print("JOB_CLAIMED", flush=True)
    sys.stdin.readline()
    return _real_run_match_isolated(spec)

w.run_match_isolated = _gated_run_match_isolated
w.run_forever(consumer="__CONSUMER__")
'''


def _throwaway_mongo_url(name: str) -> str:
    """A URL for a disposable database, on whatever host MONGODB_URI (or
    its "mongodb://127.0.0.1:27017/phi1" default) actually names.

    Hardcoding 127.0.0.1:27017 here would let this fixture and the
    _mongo_is_reachable() skip gate point at different servers whenever
    MONGODB_URI is set to anything else -- exactly the shape a CI service
    container introduces. Deriving both from the same mongo_url() means
    they can never disagree.
    """
    from backend.db.models import mongo_url

    parsed = urlparse(mongo_url())
    return urlunparse(parsed._replace(path=f"/{name}"))


@pytest.fixture(autouse=True, scope="module")
def _stream_is_gone_when_this_file_is_done():
    """Belt-and-suspenders on top of the `queue` fixture's own teardown,
    mirroring tests/jobs/test_queue.py and test_worker.py: guarantees one
    more delete of the shared STREAM key after the *last* test in this
    file, so a failure that unwinds past a single test's teardown can
    never leave it behind for the next file to inherit.

    Unlike those siblings, this file is not entirely gated behind a single
    module-level Redis skip -- the drain_sockets tests need neither Redis
    nor Mongo, so they still run (and this autouse fixture's teardown still
    fires) when Redis is down. Guarded on redis_is_reachable() so that run
    skips cleanly instead of erroring on a dead connection with nothing to
    actually clean up.
    """
    yield
    if redis_is_reachable():
        get_redis().delete(STREAM)


@pytest.fixture
def queue():
    client = get_redis()
    client.delete(STREAM)
    q = MatchJobQueue()
    yield q
    client.delete(STREAM)


@pytest.fixture
def throwaway_db():
    """A disposable database, never the shared piwall/phi1 one.

    The worker subprocess this file spawns is pointed at this database via
    MONGODB_URI, exactly like the real deployment points a worker at Mongo --
    so what gets asserted afterward is read back through the same crud
    functions production reads, against a database this test owns outright.
    """
    from pymongo import MongoClient

    from backend.db.models import MongoSession, init_db

    name = f"piwall_test_shutdown_{uuid.uuid4().hex[:8]}"
    url = _throwaway_mongo_url(name)
    client = MongoClient(url)
    database = client[name]
    init_db(database)
    session = MongoSession(database)
    yield session, url
    session.close()
    client.drop_database(name)
    client.close()


def _read_line_by(stream, deadline: float):
    """One line from `stream`, or None if `deadline` passes first.

    A plain stream.readline() blocks with no timeout at all -- if the child
    hung before ever writing anything, this test would hang with it. select
    bounds the wait without needing a background thread.
    """
    remaining = deadline - time.time()
    if remaining <= 0:
        return None
    ready, _, _ = select.select([stream], [], [], remaining)
    if not ready:
        return None
    return stream.readline()


@pytest.mark.skipif(
    not (redis_is_reachable() and _mongo_is_reachable()),
    reason="needs a reachable Redis and a reachable database",
)
def test_a_real_sigterm_mid_job_still_finishes_and_acks_the_claimed_match(
    throwaway_db, queue
):
    """The drain assertion.

    A real match_job (one player's own submitted code plus a house bot, so
    a worker that could only replay house bots would still fail this) is
    enqueued on the real Redis stream. A worker subprocess -- running the
    handshake source in _CHILD_SOURCE, against a throwaway Mongo database --
    claims it for real, and this test waits for the "JOB_CLAIMED" marker
    that fires the instant process_one has actually claimed the job and is
    about to run the match. SIGTERM is sent immediately on seeing it, before
    the release line that lets the match proceed -- so the signal cannot
    land after the ack; the match has not even started yet.

    Delete `signal.signal(signal.SIGTERM, _request_shutdown)` from
    run_forever (or make the handler force an exit instead of only setting
    the flag) and this goes red for a concrete, observable reason: the
    default SIGTERM disposition kills the process wherever it happens to be
    -- here, blocked on the release line, having claimed the job and gone
    no further -- so the job is never acked and nothing is ever persisted
    for it. This does not merely assert a flag flipped; it asserts the
    queue and the database ended up in the states a finished match actually
    leaves behind.
    """
    session, mongo_url = throwaway_db

    player = crud.create_player(session, f"wp_sigterm_{uuid.uuid4().hex[:8]}", "T")
    race = crud.create_race(session, "bahrain", "quick", owner_id=player.id)
    match_id = race.id

    # The job and the manifest must describe the SAME match, the way
    # main._build_job_and_manifest builds them: the code_sha256 the manifest
    # declares is the one the job carries. A placeholder here meant the
    # stored manifest was not the one the worker rebuilds from this job,
    # which _persist_result now refuses.
    code_sha256 = "sha256:" + hashlib.sha256(PLAYER_CODE.encode()).hexdigest()
    job = {
        "match_id": match_id, "track": "bahrain", "seed": 1000,
        "participants": [
            {"slot": 0, "player_id": player.id, "car_id": "USR-01",
             "code": PLAYER_CODE, "code_sha256": code_sha256},
            {"slot": 1, "house_bot": "NXS-07"},
        ],
    }
    manifest = build_manifest(
        match_id=match_id, seed=1000, track="bahrain",
        participants=[
            Participant(slot=0, player_id=player.id, bot_version_id=None,
                       code_sha256=code_sha256, house_bot=None),
            Participant(slot=1, player_id=None, bot_version_id=None,
                       code_sha256=None, house_bot="NXS-07"),
        ],
    )
    crud.save_manifest(session, manifest)
    queue.enqueue(job)

    consumer = f"sigterm-test-{uuid.uuid4().hex[:8]}"
    env = child_env(mongo_url)

    child_source = _CHILD_SOURCE.replace("__CONSUMER__", consumer)

    proc = subprocess.Popen(
        [sys.executable, "-c", child_source],
        cwd=str(PIWALL_DIR), env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    output_lines = []
    try:
        marker_deadline = time.time() + 15
        marker_seen = False
        while True:
            line = _read_line_by(proc.stdout, marker_deadline)
            if line is None:
                break  # timed out waiting for the marker
            if line == "":
                break  # EOF: the child exited before ever claiming a job
            output_lines.append(line)
            if line.strip() == "JOB_CLAIMED":
                marker_seen = True
                break

        if not marker_seen:
            leftover = proc.stdout.read() if proc.poll() is not None else ""
            pytest.fail(
                "worker subprocess never reached a claimed job within 15s:\n"
                + "".join(output_lines) + leftover
            )

        # The marker fires only after queue.claim() has already succeeded
        # (process_one calls run_match_isolated afterward, never before) --
        # this corroborates that against Redis's own state directly, rather
        # than trusting the child's self-report alone.
        assert queue.pending_count() == 1, (
            "the marker fired but Redis does not yet show the job as "
            "delivered -- the marker and the real claim have come apart"
        )

        signalled_at = time.time()
        proc.send_signal(signal.SIGTERM)

        # Only now does the match get permission to actually run. If the
        # signal handler is broken in a way that exits the process outright,
        # it is already gone by this point and the write below is a no-op
        # (or a harmless BrokenPipeError) -- the assertions after proc.wait
        # are what catch that case, not this write succeeding or failing.
        try:
            proc.stdin.write("go\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            pytest.fail("worker did not exit within 15s of SIGTERM")
        elapsed = time.time() - signalled_at

        try:
            output_lines.append(proc.stdout.read())
        except Exception:
            pass
        output = "".join(output_lines)

        assert proc.returncode == 0, (
            f"worker exited abnormally (code {proc.returncode}) instead of "
            f"finishing its shutdown cleanly:\n{output}"
        )
        # reclaim_stalled's idle threshold is 30s -- the entire cost this
        # task exists to avoid. A prompt exit here, well under that, is
        # what proves shutdown did not fall back to waiting it out.
        assert elapsed < 10, (
            f"worker took {elapsed:.2f}s to exit after SIGTERM; that is far "
            f"too close to reclaim_stalled's 30s idle threshold for a single "
            f"already-claimed job to legitimately need"
        )

        assert queue.pending_count() == 0, (
            "the claimed job must be acked, not left pending, when SIGTERM "
            "lands while it is in hand -- and this signal was sent before "
            "the match was even allowed to start running"
        )

        persisted_race = crud.get_race(session, match_id)
        assert persisted_race is not None and persisted_race.status == "finished", (
            "the match must be durably marked finished, not lost to the signal"
        )
        results = crud.get_race_results(session, match_id)
        assert {r.car_id for r in results} == {"USR-01", "NXS-07"}, (
            "every car's result must be persisted, not just the replay hash"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        for stream in (proc.stdin, proc.stdout):
            try:
                stream.close()
            except Exception:
                pass


class _FakeSocket:
    def __init__(self, raise_on_close: bool = False):
        self.closed = False
        self._raise_on_close = raise_on_close

    async def close(self):
        if self._raise_on_close:
            raise RuntimeError("already gone")
        self.closed = True


def test_drain_sockets_closes_every_socket_and_reports_the_count():
    """Delete the `await ws.close()` call inside drain_sockets and this goes
    red on a real observable: the sockets stay open (`a.closed` is False),
    not on some flag saying draining "happened".
    """
    import backend.main as main

    a, b = _FakeSocket(), _FakeSocket()
    main.SOCKETS["r_drain"] = {a, b}
    try:
        closed = asyncio.run(main.drain_sockets())
        assert closed == 2
        assert a.closed and b.closed
        assert "r_drain" not in main.SOCKETS, (
            "a drained race's entry must be removed, not left behind empty"
        )
    finally:
        main.SOCKETS.pop("r_drain", None)


def test_drain_sockets_tolerates_a_socket_that_fails_to_close():
    """One bad socket must not strand the rest, and must not be counted as
    closed when it plainly was not.

    Delete the try/except around `await ws.close()` and this goes red
    because the exploding socket's RuntimeError propagates out of
    drain_sockets (asyncio.run re-raises it) instead of being swallowed,
    and the socket that closes cleanly is never reached. Change `closed`
    to count attempts rather than successes (drop the `except Exception:
    return False` / only count what actually returns True) and this also
    goes red, on `closed == 1` rather than 2 -- the exploding socket must
    not be reported as closed just because it was tried.
    """
    import backend.main as main

    exploding = _FakeSocket(raise_on_close=True)
    fine = _FakeSocket()
    main.SOCKETS["r_drain_partial"] = {exploding, fine}
    try:
        closed = asyncio.run(main.drain_sockets())
        assert fine.closed, "one bad socket must not strand the rest"
        assert closed == 1, (
            "a socket whose close() raised must not be counted as closed"
        )
    finally:
        main.SOCKETS.pop("r_drain_partial", None)


@pytest.mark.skipif(
    not _mongo_is_reachable(), reason="needs a reachable database"
)
def test_lifespan_shutdown_actually_calls_drain_sockets(monkeypatch):
    """Proves drain_sockets is wired into shutdown, not just defined.

    Runs the real FastAPI lifespan via TestClient, but against a throwaway
    database (main.db_engine is swapped for the duration of this test)
    rather than the shared phi1 -- lifespan's first act is init_db(), which
    ensures indexes against whatever database it is given.

    Delete the `await drain_sockets()` call from lifespan and this goes red
    for a concrete reason: the socket this test plants in SOCKETS is still
    open (`socket.closed` is False) after the app's shutdown has run.
    """
    from fastapi.testclient import TestClient
    from pymongo import MongoClient

    import backend.main as main

    name = f"piwall_test_shutdown_lifespan_{uuid.uuid4().hex[:8]}"
    client = MongoClient(_throwaway_mongo_url(name))
    database = client[name]
    monkeypatch.setattr(main, "db_engine", database)
    # lifespan's first act is `_session_factory = init_db(db_engine)`, which
    # binds the module-global factory to THIS throwaway database -- and the
    # client below is closed when the test ends. Without restoring it, every
    # later test in the session that calls main.SessionLocal() talks to a
    # closed MongoClient and fails somewhere unrelated to its own subject.
    # monkeypatch restores whatever is here now once this test is done.
    monkeypatch.setattr(main, "_session_factory", main._session_factory)

    socket = _FakeSocket()
    main.SOCKETS["r_lifespan_drain"] = {socket}
    try:
        with TestClient(main.app):
            pass
        assert socket.closed, (
            "a socket held by this replica must be closed once its "
            "shutdown has run"
        )
        assert "r_lifespan_drain" not in main.SOCKETS
    finally:
        main.SOCKETS.pop("r_lifespan_drain", None)
        client.drop_database(name)
        client.close()
