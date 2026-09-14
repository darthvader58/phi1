"""The worker must ack only after the result is durable.

Acking first is the tempting order — the job is "done", after all — but a
crash between ack and save loses the match with no record. Acking last means
the worst case is running the match twice, which Phase 1's determinism makes
harmless: the same manifest yields byte-identical output.
"""

import uuid

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


@pytest.fixture(autouse=True, scope="module")
def _stream_is_gone_when_this_file_is_done():
    """Module-level safety net on top of every per-test teardown below.

    Mirrors tests/jobs/test_queue.py's fixture of the same name: the `wiring`
    fixture already deletes STREAM after each test, but this guarantees one
    more delete after the *last* test in this file too, so this file can
    never be the reason the shared stream key survives past it.
    """
    yield
    get_redis().delete(STREAM)


@pytest.fixture
def wiring():
    client = get_redis()
    client.delete(STREAM)
    queue = MatchJobQueue()
    # An isolated channel per test, exactly like test_events.py's `events`
    # fixture -- CHANNEL is one fixed name every worker and API replica in
    # production must share, but that same fixed name means two copies of
    # this file (or this file and test_events.py) running at once against
    # this shared Redis would cross-deliver: one test's "here is the event I
    # expect" could see a different concurrent run's publish, or vice versa.
    # process_one takes `events` as an argument specifically so a test can do
    # this without touching worker.py's own default-channel MatchEvents() in
    # run_forever, which production still needs to keep matching everywhere.
    events = MatchEvents(channel=f"piwall:events:match:test-{uuid.uuid4()}")
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


def test_the_sandboxed_replay_bytes_match_an_independently_built_replay():
    """_replay_bytes_from_result must agree, byte for byte, with
    determinism.replay.replay_bytes -- an independently implemented
    encoding of the same replay contract.

    test_running_the_same_job_twice_produces_the_same_replay_hash cannot
    catch a format bug: it only ever compares two hashes produced by the
    SAME code path, so it is self-satisfying against a change to the
    payload's shape (a renamed field, a dropped format_version, a dropped
    manifest -- all invisible to it). This test builds the replay two
    different, independent ways and checks they land on the same bytes:

    - via_worker: run_match_isolated (the real sandboxed path, crossing a
      pickle boundary) followed by _replay_bytes_from_result -- exactly what
      process_one does.
    - via_engine: build_engine + engine.run() (the same construction
      replay_from_manifest and scripts/verify_determinism.py exercise)
      followed by replay_bytes directly.

    via_engine deliberately reads grid position (participant.slot + 1) and
    seed from the manifest, and car identity/compound from JOB's raw
    participants -- never from _spec_from_job's output. Comparing against a
    value _spec_from_job itself produced would let a bug in its own
    position or seed arithmetic (e.g. hardcoding start_position or seed)
    cancel out against itself, since both sides would then run the same
    wrong race and still agree.
    """
    from backend.determinism.replay import replay_bytes
    from backend.engine.bots import BUILTIN_BOTS
    from backend.engine.build import build_engine
    from backend.sandbox.match_job import _make_user_strategy, run_match_isolated
    from backend.worker import _manifest_from_job, _spec_from_job, _replay_bytes_from_result

    manifest = _manifest_from_job(JOB)
    spec = _spec_from_job(JOB)

    via_worker = _replay_bytes_from_result(run_match_isolated(spec), manifest)

    participants_by_slot = {int(p["slot"]): p for p in JOB["participants"]}
    engine = build_engine(manifest.track, manifest.seed)
    for participant in sorted(manifest.participants, key=lambda p: p.slot):
        raw = participants_by_slot[participant.slot]
        house_bot = raw.get("house_bot")
        if raw.get("code"):
            strategy = _make_user_strategy(raw["code"], manifest.seed, participant.slot)
            starting_compound = raw.get("starting_compound", "MEDIUM")
        else:
            bot = BUILTIN_BOTS[house_bot]
            strategy = bot["strategy"]
            starting_compound = bot["starting_compound"]
        engine.add_car(
            car_id=raw.get("car_id") or house_bot,
            player_id=raw.get("player_id") or house_bot,
            strategy=strategy,
            starting_position=participant.slot + 1,
            starting_compound=starting_compound,
        )
    via_engine = replay_bytes(engine.run(), manifest)

    assert via_worker == via_engine
