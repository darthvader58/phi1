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
a real (throwaway) database -- and it is why it reaches for subprocess.Popen
and os signals instead of calling run_forever() in-process or patching out
its collaborators: patching process_one or the queue would prove only that a
while loop stops, not that a job in flight survives being interrupted.

API side: a replica shutting down still holds live WebSockets for whichever
races this process happens to be spectating. drain_sockets() closes them so
those clients get a clean close frame and reconnect elsewhere, instead of
sitting on a socket to a process that is already gone until a TCP timeout
notices.
"""

import asyncio
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from backend.db import crud
from backend.determinism.manifest import Participant, build_manifest
from backend.jobs.queue import STREAM, MatchJobQueue
from backend.observability.health import _mongo_is_reachable
from backend.state.redis_client import get_redis, redis_is_reachable

PIWALL_DIR = Path(__file__).resolve().parents[2]

PLAYER_CODE = (
    "def my_strategy(state, my_car):\n"
    "    return {'pit': my_car.tyre_age > 20, 'compound': 'MEDIUM'}\n"
)


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
    url = f"mongodb://127.0.0.1:27017/{name}"
    client = MongoClient(url)
    database = client[name]
    init_db(database)
    session = MongoSession(database)
    yield session, url
    session.close()
    client.drop_database(name)
    client.close()


@pytest.mark.skipif(
    not (redis_is_reachable() and _mongo_is_reachable()),
    reason="needs a reachable Redis and a reachable database",
)
def test_a_real_sigterm_mid_job_still_finishes_and_acks_the_claimed_match(throwaway_db):
    """The drain assertion.

    A real match_job (one player's own submitted code plus a house bot, so
    a worker that could only replay house bots would still fail this) is
    enqueued on the real Redis stream. A worker subprocess is started
    against a throwaway Mongo database, and this test polls the queue's own
    pending count -- not a timer -- until it reports the job as delivered
    (claimed but not yet acked), which is exactly the window during which
    the job is "in hand". SIGTERM is sent the instant that window opens.

    Delete `signal.signal(signal.SIGTERM, _request_shutdown)` from
    run_forever (or make the handler force an exit instead of only setting
    the flag) and this goes red for a concrete, observable reason: the
    default SIGTERM disposition kills the process wherever it happens to be
    -- mid sandboxed run, mid Mongo write -- so the job is never acked and
    nothing is ever persisted for it. This does not merely assert a flag
    flipped; it asserts the queue and the database ended up in the states a
    finished match actually leaves behind.
    """
    session, mongo_url = throwaway_db

    player = crud.create_player(session, f"wp_sigterm_{uuid.uuid4().hex[:8]}", "T")
    race = crud.create_race(session, "bahrain", "quick", owner_id=player.id)
    match_id = race.id

    job = {
        "match_id": match_id, "track": "bahrain", "seed": 1000,
        "participants": [
            {"slot": 0, "player_id": player.id, "car_id": "USR-01",
             "code": PLAYER_CODE},
            {"slot": 1, "house_bot": "NXS-07"},
        ],
    }
    manifest = build_manifest(
        match_id=match_id, seed=1000, track="bahrain",
        participants=[
            Participant(slot=0, player_id=player.id, bot_version_id=None,
                       code_sha256="sha256:" + "a" * 64, house_bot=None),
            Participant(slot=1, player_id=None, bot_version_id=None,
                       code_sha256=None, house_bot="NXS-07"),
        ],
    )
    crud.save_manifest(session, manifest)

    redis_client = get_redis()
    redis_client.delete(STREAM)
    queue = MatchJobQueue()
    queue.enqueue(job)

    consumer = f"sigterm-test-{uuid.uuid4().hex[:8]}"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PIWALL_DIR)
    env["MONGODB_URI"] = mongo_url

    proc = subprocess.Popen(
        [sys.executable, "-c",
         f"import backend.worker as w; w.run_forever(consumer={consumer!r})"],
        cwd=str(PIWALL_DIR), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        # Poll the real queue state -- not a sleep -- until the job has
        # actually been delivered to this consumer. That is precisely the
        # window in which the job is claimed but not yet acked: "in hand".
        claim_deadline = time.time() + 15
        while queue.pending_count() == 0:
            if time.time() > claim_deadline:
                pytest.fail("worker subprocess never claimed the job")
            if proc.poll() is not None:
                pytest.fail(
                    f"worker subprocess exited early ({proc.returncode}) "
                    f"before claiming the job: {proc.stdout.read().decode(errors='replace')}"
                )
            time.sleep(0.002)

        signalled_at = time.time()
        proc.send_signal(signal.SIGTERM)

        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            pytest.fail("worker did not exit within 15s of SIGTERM")
        elapsed = time.time() - signalled_at

        output = proc.stdout.read().decode(errors="replace")
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
            "lands while it is in hand"
        )
        assert queue.reclaim_stalled("audit-worker", min_idle_ms=0) == [], (
            "nothing must be left in a state that needs reclaim_stalled's "
            "recovery path -- the job was acked, not merely abandoned"
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
        redis_client.delete(STREAM)


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
    """Delete the try/except around `await ws.close()` and this goes red:
    the exploding socket's RuntimeError propagates out of drain_sockets
    (asyncio.run re-raises it) instead of being swallowed, and the socket
    that closes cleanly is never reached.
    """
    import backend.main as main

    exploding = _FakeSocket(raise_on_close=True)
    fine = _FakeSocket()
    main.SOCKETS["r_drain_partial"] = {exploding, fine}
    try:
        closed = asyncio.run(main.drain_sockets())
        assert closed == 2
        assert fine.closed, "one bad socket must not strand the rest"
    finally:
        main.SOCKETS.pop("r_drain_partial", None)


@pytest.mark.skipif(
    not _mongo_is_reachable(), reason="needs a reachable database"
)
def test_lifespan_shutdown_actually_calls_drain_sockets():
    """Proves drain_sockets is wired into shutdown, not just defined.

    Delete the `await drain_sockets()` call from lifespan and this goes red
    for a concrete reason: the socket this test plants in SOCKETS is still
    open (`socket.closed` is False) after the app's shutdown has run.
    """
    from fastapi.testclient import TestClient

    import backend.main as main

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
