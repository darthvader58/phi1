"""The queue must not lose a job when a worker dies holding it.

A Redis list with LPOP deletes the job at the moment it is handed out, so a
worker killed one instruction later takes the match with it and nothing
anywhere records that a match was lost. A Stream consumer group keeps the
entry in a pending list until it is acked, which is what makes
reclaim_stalled possible.
"""

import pytest
from redis.exceptions import ResponseError

from backend.jobs.queue import GROUP, STREAM, MatchJobQueue
from backend.state.redis_client import get_redis, redis_is_reachable

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)

JOB = {"match_id": "m_test_1", "track": "bahrain", "seed": "1000"}


@pytest.fixture(autouse=True, scope="module")
def _stream_is_gone_when_this_file_is_done():
    """A module-level safety net on top of every per-test teardown below.

    Each test's own `queue` fixture (or, for the one test that skips it,
    its own try/finally) already deletes STREAM after itself. This adds one
    more delete after the *last* test in the file, so a stream created by
    this file can never survive past it even if some single test's own
    cleanup were ever skipped -- a raised BaseException that unwinds past a
    fixture's teardown, or this file run standalone against a Redis a
    concurrent process is also using and momentarily racing this module's
    own per-test deletes. Belt and suspenders: cheap, and it can't make a
    passing run behave any differently.
    """
    yield
    get_redis().delete(STREAM)


@pytest.fixture
def queue():
    client = get_redis()
    client.delete(STREAM)
    q = MatchJobQueue()
    yield q
    client.delete(STREAM)


def test_enqueue_returns_an_entry_id(queue):
    assert queue.enqueue(JOB)


def test_a_claimed_job_round_trips_its_payload(queue):
    queue.enqueue(JOB)
    entry_id, job = queue.claim("worker-1", block_ms=500)
    assert job["match_id"] == "m_test_1"
    assert job["track"] == "bahrain"


def test_claim_returns_none_on_an_empty_queue(queue):
    assert queue.claim("worker-1", block_ms=200) is None


def test_a_claimed_job_is_not_handed_to_a_second_worker(queue):
    queue.enqueue(JOB)
    queue.claim("worker-1", block_ms=500)
    assert queue.claim("worker-2", block_ms=200) is None


def test_an_unacked_job_stays_pending(queue):
    """This is the property that makes a lost worker recoverable."""
    queue.enqueue(JOB)
    queue.claim("worker-1", block_ms=500)
    assert queue.pending_count() == 1


def test_acking_clears_the_pending_entry(queue):
    queue.enqueue(JOB)
    entry_id, _ = queue.claim("worker-1", block_ms=500)
    queue.ack(entry_id)
    assert queue.pending_count() == 0


def test_a_job_abandoned_by_a_dead_worker_can_be_reclaimed(queue):
    """The phase gate in miniature: worker-1 dies holding the job."""
    queue.enqueue(JOB)
    queue.claim("worker-1", block_ms=500)  # worker-1 now dies, never acks
    reclaimed = queue.reclaim_stalled("worker-2", min_idle_ms=0)
    assert len(reclaimed) == 1
    assert reclaimed[0][1]["match_id"] == "m_test_1"


def test_reclaim_returns_nothing_when_no_job_is_stalled(queue):
    queue.enqueue(JOB)
    entry_id, _ = queue.claim("worker-1", block_ms=500)
    queue.ack(entry_id)
    assert queue.reclaim_stalled("worker-2", min_idle_ms=0) == []


def test_reclaim_respects_the_idle_threshold(queue):
    """A job claimed a moment ago is being worked on, not abandoned."""
    queue.enqueue(JOB)
    queue.claim("worker-1", block_ms=500)
    assert queue.reclaim_stalled("worker-2", min_idle_ms=60000) == []


def test_a_reclaimed_job_can_be_acked_by_its_new_owner(queue):
    queue.enqueue(JOB)
    queue.claim("worker-1", block_ms=500)
    reclaimed = queue.reclaim_stalled("worker-2", min_idle_ms=0)
    queue.ack(reclaimed[0][0])
    assert queue.pending_count() == 0


def test_depth_counts_enqueued_jobs(queue):
    queue.enqueue(JOB)
    queue.enqueue({**JOB, "match_id": "m_test_2"})
    assert queue.depth() == 2


def test_jobs_are_delivered_in_enqueue_order(queue):
    queue.enqueue({**JOB, "match_id": "m_first"})
    queue.enqueue({**JOB, "match_id": "m_second"})
    first = queue.claim("worker-1", block_ms=500)[1]["match_id"]
    second = queue.claim("worker-1", block_ms=500)[1]["match_id"]
    assert (first, second) == ("m_first", "m_second")


def test_creating_the_group_twice_is_harmless(queue):
    """Every worker calls this on startup; only the first one creates it."""
    MatchJobQueue()
    MatchJobQueue()
    assert get_redis().xinfo_groups(STREAM)[0]["name"] == GROUP


def test_ensure_group_does_not_swallow_a_non_busygroup_error():
    """The BUSYGROUP-only guard is not decorative.

    Widen it to a bare `except ResponseError: pass` and a stream key of the
    wrong type stops raising: construction "succeeds" and the queue is
    silently empty forever, instead of the worker crashing loudly on a
    genuinely broken stream at startup.
    """
    client = get_redis()
    client.delete(STREAM)
    client.set(STREAM, "not a stream")
    try:
        with pytest.raises(ResponseError, match="WRONGTYPE"):
            MatchJobQueue()
    finally:
        client.delete(STREAM)


# ─── Surviving the loss of the stream key or the group ───────────────
#
# `_ensure_group` used to run in `__init__` and nowhere else, and
# `run_forever` builds one queue at startup and keeps it for the life of the
# process. So a stream key that disappeared afterwards -- a Redis restart with
# no persistence, an eviction under maxmemory, an operator DEL, or this very
# suite's own `client.delete(STREAM)` teardown running against a Redis a
# worker is draining -- wedged that worker permanently. Every iteration raised
# NOGROUP, `run_forever` logged it as a job failure and backed off to its 30s
# ceiling, and the process never exited, so `restart: unless-stopped` never
# fired and no probe noticed: `/ready` checks only that Redis and Mongo are
# reachable, and both genuinely are.
#
# Meanwhile XADD does not fail. `enqueue` recreates the key without a group
# and keeps returning entry ids, so the API accepts races and stacks up jobs
# nothing can read. These tests pin both halves.


def test_claim_recovers_when_the_stream_key_has_been_deleted(queue):
    """The wedge, at its source.

    Delete `_with_group`'s `self._ensure_group()` call (or narrow its
    NOGROUP match) and this goes red with the raw
    `ResponseError: NOGROUP No such key 'piwall:jobs:match' or consumer
    group 'match-workers'` -- which is exactly what a real worker used to
    take, once per iteration, forever.
    """
    get_redis().delete(STREAM)

    # Jobs enqueued after the loss are the ones that must not be stranded:
    # XADD rebuilds the key with no group, so this succeeds either way.
    queue.enqueue(JOB)

    entry_id, job = queue.claim("worker-1", block_ms=500)
    assert job["match_id"] == "m_test_1", (
        "a job enqueued after the stream key was lost must still be claimable"
    )
    queue.ack(entry_id)
    assert queue.pending_count() == 0


def test_claim_recovers_when_only_the_group_has_been_destroyed(queue):
    """The other NOGROUP case: the stream survives, the group does not.

    Re-creating at id="0" re-delivers what is still in the stream, which is
    safe by determinism and by _persist_result's idempotency, and is the
    correct trade against creating at "$" and silently discarding every job
    enqueued during the outage.
    """
    queue.enqueue(JOB)
    get_redis().xgroup_destroy(STREAM, GROUP)

    entry_id, job = queue.claim("worker-1", block_ms=500)
    assert job["match_id"] == "m_test_1", (
        "an undelivered job must survive the group being destroyed under it"
    )
    queue.ack(entry_id)


def test_reclaim_and_pending_count_recover_too(queue):
    """All three group-scoped commands, not just the one that was noticed.

    `process_one` calls reclaim_stalled BEFORE claim, so a fix that only
    covered claim would leave the worker wedged on the very first line it
    reaches. pending_count is what every assertion in this suite reads the
    queue's state through.
    """
    get_redis().delete(STREAM)
    assert queue.reclaim_stalled("worker-1", min_idle_ms=0) == []
    assert queue.pending_count() == 0

    queue.enqueue(JOB)
    entry_id, _job = queue.claim("worker-1", block_ms=500)
    assert queue.pending_count() == 1
    assert queue.reclaim_stalled("worker-2", min_idle_ms=0)[0][0] == entry_id


def test_a_non_nogroup_response_error_is_not_swallowed(queue):
    """The NOGROUP-only match is load-bearing, exactly as BUSYGROUP's is.

    Widen `_with_group` to a bare `except ResponseError` and a genuinely
    broken stream stops raising: the queue would quietly re-create a group,
    retry, and surface a different error or none at all, instead of failing
    on the real problem.
    """
    client = get_redis()
    client.delete(STREAM)
    client.set(STREAM, "not a stream")
    try:
        with pytest.raises(ResponseError, match="WRONGTYPE"):
            # Built against the bad key directly: __init__ raises here, which
            # is the loud startup failure test_ensure_group_does_not_swallow_
            # a_non_busygroup_error already pins. This asserts the same
            # property for the retry path by driving it on an existing queue.
            queue._with_group(lambda: client.xlen(STREAM) and client.xreadgroup(
                GROUP, "worker-1", {STREAM: ">"}, count=1, block=100
            ))
    finally:
        client.delete(STREAM)


def test_enqueue_caps_the_stream_so_it_cannot_grow_without_bound(queue,
                                                                 monkeypatch):
    """XACK does not delete, so nothing else ever shrinks this stream.

    Delete the `maxlen=`/`approximate=` arguments from enqueue and this goes
    red at 500 entries instead of ~100: the stream keeps every job ever
    enqueued, which is unbounded Redis memory and one of the routes by which
    the key gets evicted and the NOGROUP wedge above fires in the first
    place.

    The cap is monkeypatched down rather than enqueuing ten thousand jobs.
    `approximate=True` means Redis trims whole radix nodes and so keeps MORE
    than the cap, never fewer -- measured here, a cap of 10 settles at
    `stream-node-max-entries` (100 by default) rather than at 10, and stays
    there no matter how many more arrive. That one-sidedness is the point:
    over-keeping is harmless, while trimming an entry that is still pending
    would lose that match. So this asserts trimming HAPPENS and never
    undershoots, and deliberately does not pin an exact length Redis is
    entitled to choose.
    """
    import backend.jobs.queue as queue_module

    monkeypatch.setattr(queue_module, "STREAM_MAXLEN", 10)
    for n in range(500):
        queue.enqueue({**JOB, "match_id": f"m_cap_{n}"})

    depth = queue.depth()
    assert depth < 250, (
        f"500 jobs enqueued under a cap of 10 left {depth} in the stream; "
        f"the stream is not being trimmed at all"
    )
    assert depth >= 10, (
        f"the trim undershot the cap ({depth} < 10) -- an entry still "
        f"pending would be a lost match"
    )
