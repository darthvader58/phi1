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
