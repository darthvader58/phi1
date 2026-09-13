"""The worker must ack only after the result is durable.

Acking first is the tempting order — the job is "done", after all — but a
crash between ack and save loses the match with no record. Acking last means
the worst case is running the match twice, which Phase 1's determinism makes
harmless: the same manifest yields byte-identical output.
"""

import pytest

from backend.jobs.queue import STREAM, MatchJobQueue
from backend.jobs.events import MatchEvents
from backend.state.redis_client import get_redis, redis_is_reachable
from backend.worker import process_one

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)

PLAYER_CODE = (
    "def my_strategy(state, my_car):\n"
    "    return {'pit': my_car.tyre_age > 20, 'compound': 'MEDIUM'}\n"
)

# A player bot in slot 0 on purpose. A job of house bots only would pass even
# if the worker used replay_from_manifest, which cannot run player code at all
# — so an all-house-bot fixture would hide the defect this test exists to catch.
JOB = {"match_id": "m_worker_1", "track": "bahrain", "seed": 1000,
       "participants": [
           {"slot": 0, "player_id": "p1", "car_id": "USR-01",
            "code": PLAYER_CODE},
           {"slot": 1, "house_bot": "NXS-07"}]}


@pytest.fixture
def wiring():
    client = get_redis()
    client.delete(STREAM)
    queue = MatchJobQueue()
    events = MatchEvents()
    yield queue, events
    client.delete(STREAM)


def test_a_job_is_processed_and_acked(wiring):
    queue, events = wiring
    queue.enqueue(JOB)
    saved = []
    match_id = process_one(queue, events, persist=saved.append, consumer="w1")
    assert match_id == "m_worker_1"
    assert queue.pending_count() == 0


def test_the_result_is_persisted_before_the_ack(wiring):
    """Ordering is the point. Persist raising must leave the job recoverable."""
    queue, events = wiring
    queue.enqueue(JOB)

    def exploding_persist(_result):
        raise RuntimeError("database down")

    with pytest.raises(RuntimeError):
        process_one(queue, events, persist=exploding_persist, consumer="w1")
    assert queue.pending_count() == 1, "a failed save must leave the job pending"


def test_persist_receives_the_match_id_and_a_replay_hash(wiring):
    queue, events = wiring
    queue.enqueue(JOB)
    saved = []
    process_one(queue, events, persist=saved.append, consumer="w1")
    assert saved[0]["match_id"] == "m_worker_1"
    assert saved[0]["replay_sha256"].startswith("sha256:")


def test_a_completion_event_is_published(wiring):
    queue, events = wiring
    pubsub = events.subscribe()
    try:
        queue.enqueue(JOB)
        process_one(queue, events, persist=lambda r: None, consumer="w1")
        received = events.listen(pubsub, timeout=3.0)
        assert received["type"] == "match_finished"
        assert received["match_id"] == "m_worker_1"
    finally:
        pubsub.close()


def test_process_one_returns_none_on_an_empty_queue(wiring):
    queue, events = wiring
    assert process_one(queue, events, persist=lambda r: None,
                       consumer="w1", block_ms=200) is None


def test_running_the_same_job_twice_produces_the_same_replay_hash(wiring):
    """At-least-once delivery is only safe if a repeat is identical."""
    queue, events = wiring
    saved = []
    queue.enqueue(JOB)
    process_one(queue, events, persist=saved.append, consumer="w1")
    queue.enqueue(JOB)
    process_one(queue, events, persist=saved.append, consumer="w1")
    assert saved[0]["replay_sha256"] == saved[1]["replay_sha256"]


def test_a_player_bots_code_is_actually_executed(wiring):
    """The worker must run submitted source, not just house bots.

    replay_from_manifest raises NotImplementedError for a participant with no
    house_bot, so a worker built on it would fail every real match. This test
    fails loudly in that case instead of passing on a house-bot-only fixture.
    """
    queue, events = wiring
    queue.enqueue(JOB)
    saved = []
    match_id = process_one(queue, events, persist=saved.append, consumer="w1")
    assert match_id == "m_worker_1", "a match containing player code did not run"


def test_a_stalled_job_is_reclaimed_and_completed(wiring):
    """The phase gate: worker-1 dies holding a job, worker-2 finishes it."""
    queue, events = wiring
    queue.enqueue(JOB)
    queue.claim("worker-1", block_ms=500)  # dies without acking
    saved = []
    match_id = process_one(queue, events, persist=saved.append,
                           consumer="worker-2", min_idle_ms=0)
    assert match_id == "m_worker_1"
    assert queue.pending_count() == 0
