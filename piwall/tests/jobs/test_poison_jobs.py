"""A match that cannot run must leave the queue, or nothing else ever runs.

The failure this file exists for is player-triggerable and total. A bot that
exceeds its CPU, wall-clock or memory budget makes `run_match_isolated` raise
`LimitExceeded`; that used to escape `process_one`, so the entry was never
acked and never dead-lettered. `process_one` checks `reclaim_stalled` first
and always takes `stalled[0]`, so the poisoned entry -- which by then is
always the idle-most one -- is re-served ahead of every new match, forever.
One player submitting an over-budget bot stopped every match on the
deployment, with /ready green throughout.

The classification is the fix, not a bare `except`. `backend/determinism/
signals.py` is the model: a signal declares what handlers must do with it
rather than leaving every handler to remember. Here:

  * `LimitExceeded` is a recorded outcome about that bot -- reproducible, so
    a retry can only reach the same verdict. Retired on the first delivery,
    with a reason the player can read.
  * `ChildFailed` is ours, and may be transient. Retried to a cap, then
    retired with generic text, because its detail can carry filesystem paths
    out of an untrusted process.
"""

import uuid

import pytest

from backend.jobs.events import MatchEvents
from backend.jobs.queue import (
    DEAD_LETTER_STREAM,
    MAX_DELIVERIES,
    RECLAIM_MIN_IDLE_MS,
    STREAM,
    MatchJobQueue,
)
from backend.sandbox.isolation import (
    DEFAULT_WALL_SECONDS,
    ChildFailed,
    LimitExceeded,
)
from backend.state.redis_client import get_redis, redis_is_reachable
import backend.worker as worker
from backend.worker import UNRUNNABLE_REASON, process_one

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)

HOUSE_JOB = {
    "match_id": "m_poison_good",
    "track": "bahrain",
    "seed": 1000,
    "participants": [{"slot": 0, "house_bot": "VEL-01"},
                     {"slot": 1, "house_bot": "NXS-07"}],
}
POISON_JOB = dict(HOUSE_JOB, match_id="m_poison_bad")


@pytest.fixture(autouse=True, scope="module")
def _streams_are_gone_when_this_file_is_done():
    """Mirrors the same fixture in test_queue.py / test_worker.py: one final
    delete after the last test in this file, on top of every per-test
    teardown, so this file can never be why a shared key survives it."""
    yield
    client = get_redis()
    client.delete(STREAM)
    client.delete(DEAD_LETTER_STREAM)


@pytest.fixture
def wiring():
    client = get_redis()
    client.delete(STREAM)
    client.delete(DEAD_LETTER_STREAM)
    queue = MatchJobQueue()
    # A per-test channel, exactly as test_worker.py does: CHANNEL is one
    # fixed name in production and two concurrent copies of this suite
    # against a shared Redis would otherwise cross-deliver.
    events = MatchEvents(channel=f"piwall:events:match:test-{uuid.uuid4()}")
    yield queue, events
    client.delete(STREAM)
    client.delete(DEAD_LETTER_STREAM)


def _raise_for(match_ids, exc_factory):
    """A run_match_isolated stand-in that fails only for named matches.

    Keyed on the car ids in the spec rather than on call count so a test can
    put a healthy job behind a poisoned one and have each get its own
    treatment no matter what order the queue serves them in.
    """
    real = worker.run_match_isolated

    def fake(spec):
        if any(car["car_id"] in match_ids for car in spec["cars"]):
            raise exc_factory()
        return real(spec)

    return fake


# The poison job and the good one differ only by match_id, so the stub keys
# on something the spec actually carries. Give the poison job a car id of
# its own.
POISON_JOB = {
    "match_id": "m_poison_bad",
    "track": "bahrain",
    "seed": 1000,
    "participants": [
        {"slot": 0, "player_id": "p_bad", "car_id": "BAD-01",
         "code": "def my_strategy(state, my_car):\n    return {'pit': False, "
                 "'compound': 'MEDIUM'}\n"},
        {"slot": 1, "house_bot": "NXS-07"},
    ],
}


def test_an_over_budget_bot_does_not_starve_every_other_match(wiring, monkeypatch):
    """The reproduction, end to end.

    A poisoned job and a healthy one, in that order. Before the fix the
    poisoned entry stayed pending and `reclaim_stalled` re-served it ahead of
    the healthy one on every pass -- five attempts in the review's run, with
    the good job never once served.

    Red line: the `except LimitExceeded` clause in worker.process_one. Delete
    it and LimitExceeded escapes again: the first call raises instead of
    returning, the poison entry stays pending, and `ran` never contains the
    healthy match.
    """
    queue, events = wiring
    monkeypatch.setattr(
        worker, "run_match_isolated",
        _raise_for({"BAD-01"}, lambda: LimitExceeded("cpu budget exhausted")),
    )
    queue.enqueue(POISON_JOB)
    queue.enqueue(HOUSE_JOB)

    ran = []
    # min_idle_ms=0 so reclaim_stalled is as eager as it can possibly be --
    # the exact condition that made the poisoned entry re-served first.
    for _ in range(3):
        ran.append(process_one(queue, events, persist=lambda r: None,
                               consumer="w-poison", min_idle_ms=0,
                               block_ms=200))

    assert "m_poison_good" in ran, (
        "the healthy job queued behind a poisoned one was never served"
    )
    assert queue.pending_count() == 0, "no entry may be left pending"


def test_a_limit_breach_is_recorded_as_an_aborted_match_the_player_can_read(
    wiring, monkeypatch
):
    """The outcome has to be written down, or the race sits at "running"
    until its TTL and the player is never told why their bot did not race.

    Red line: the `persist({"outcome": "aborted", ...})` call in
    worker._abandon. Delete it and `recorded` stays empty.
    """
    queue, events = wiring
    monkeypatch.setattr(
        worker, "run_match_isolated",
        _raise_for({"BAD-01"},
                   lambda: LimitExceeded("your bot exceeded the CPU limit")),
    )
    queue.enqueue(POISON_JOB)

    recorded = []
    process_one(queue, events, persist=recorded.append, consumer="w-poison",
                min_idle_ms=0, block_ms=200)

    assert len(recorded) == 1
    assert recorded[0]["outcome"] == "aborted"
    assert recorded[0]["match_id"] == "m_poison_bad"
    assert "CPU limit" in recorded[0]["reason"], (
        "a limit breach is the bot's own doing and must say so -- "
        "LimitExceeded's text is composed from our constants and is safe "
        "to show"
    )


def test_a_limit_breach_leaves_the_stream_for_the_dead_letter_stream(
    wiring, monkeypatch
):
    """Acking alone would make an unrunnable job indistinguishable from a
    completed one. The entry is copied out first, with its reason.

    Red line: the `queue.dead_letter(...)` call in worker._abandon. Delete it
    and the dead-letter stream stays empty (and the entry stays pending).
    """
    queue, events = wiring
    monkeypatch.setattr(
        worker, "run_match_isolated",
        _raise_for({"BAD-01"}, lambda: LimitExceeded("memory limit")),
    )
    queue.enqueue(POISON_JOB)
    process_one(queue, events, persist=lambda r: None, consumer="w-poison",
                min_idle_ms=0, block_ms=200)

    dead = queue.dead_letters()
    assert [d["job"]["match_id"] for d in dead] == ["m_poison_bad"]
    assert "memory limit" in dead[0]["reason"]
    assert queue.pending_count() == 0


def test_a_limit_breach_is_not_retried_at_all(wiring, monkeypatch):
    """It is reproducible: a second run reaches the same verdict. Retrying
    it to the cap would keep an unrunnable job at the head of the queue for
    two more reclaim cycles for no possible gain.

    Red line: the `except LimitExceeded` clause's placement ABOVE the generic
    `except Exception` in worker.process_one. Remove the clause and
    LimitExceeded falls into _retry_or_abandon, which re-raises on delivery
    one -- so this call raises instead of returning a match id.
    """
    queue, events = wiring
    monkeypatch.setattr(
        worker, "run_match_isolated",
        _raise_for({"BAD-01"}, lambda: LimitExceeded("wall clock")),
    )
    queue.enqueue(POISON_JOB)
    assert process_one(queue, events, persist=lambda r: None,
                       consumer="w-poison", min_idle_ms=0,
                       block_ms=200) == "m_poison_bad"
    assert queue.dead_letter_depth() == 1


def test_an_internal_child_failure_is_retried_and_only_then_abandoned(
    wiring, monkeypatch
):
    """ChildFailed is ours, not the player's, and may be transient -- an
    OOM-killed child, a pickling blip. It gets MAX_DELIVERIES attempts.

    Red line: the `if deliveries < MAX_DELIVERIES: raise` in
    worker._retry_or_abandon. Delete the condition (always raise) and the
    final call raises instead of dead-lettering; delete the raise (always
    abandon) and the first call already dead-letters, so `raised` is 0.
    """
    queue, events = wiring
    monkeypatch.setattr(
        worker, "run_match_isolated",
        _raise_for({"BAD-01"}, lambda: ChildFailed("/abs/path/engine.py line 4")),
    )
    queue.enqueue(POISON_JOB)

    recorded, raised = [], 0
    for _ in range(MAX_DELIVERIES):
        try:
            process_one(queue, events, persist=recorded.append,
                        consumer="w-poison", min_idle_ms=0, block_ms=200)
        except ChildFailed:
            raised += 1

    assert raised == MAX_DELIVERIES - 1, (
        "every delivery below the cap must leave the job pending for another "
        "attempt"
    )
    assert queue.dead_letter_depth() == 1
    assert queue.pending_count() == 0
    assert len(recorded) == 1 and recorded[0]["outcome"] == "aborted"


def test_an_internal_failure_never_shows_child_text_to_a_player(
    wiring, monkeypatch
):
    """ChildFailed.detail can carry absolute filesystem paths out of a
    process that ran untrusted code; the reason reaches a spectator socket
    and a 400 body.

    Red line: `reason=UNRUNNABLE_REASON` in worker._retry_or_abandon. Replace
    it with the log note (or with str(exc)) and the path leaks into the
    recorded reason.
    """
    queue, events = wiring
    secret = "/srv/piwall/secret/path.py"
    monkeypatch.setattr(
        worker, "run_match_isolated",
        _raise_for({"BAD-01"}, lambda: ChildFailed(secret)),
    )
    queue.enqueue(POISON_JOB)

    recorded = []
    for _ in range(MAX_DELIVERIES):
        try:
            process_one(queue, events, persist=recorded.append,
                        consumer="w-poison", min_idle_ms=0, block_ms=200)
        except ChildFailed:
            pass

    assert recorded[0]["reason"] == UNRUNNABLE_REASON
    assert secret not in recorded[0]["reason"]


def test_an_abandoned_match_tells_its_spectators(wiring, monkeypatch):
    """The API relay turns this event into the "aborted" frame the frontend
    already handles; without it a spectator watches a countdown that never
    resolves.

    Red line: the `events.publish({"type": "match_aborted", ...})` call in
    worker._abandon. Delete it and nothing arrives on the channel.
    """
    queue, events = wiring
    monkeypatch.setattr(
        worker, "run_match_isolated",
        _raise_for({"BAD-01"}, lambda: LimitExceeded("cpu")),
    )
    pubsub = events.subscribe()
    try:
        queue.enqueue(POISON_JOB)
        process_one(queue, events, persist=lambda r: None,
                    consumer="w-poison", min_idle_ms=0, block_ms=200)
        received = events.listen(pubsub, timeout=3.0)
    finally:
        pubsub.close()

    assert received["type"] == "match_aborted"
    assert received["match_id"] == "m_poison_bad"
    assert received["reason"]


def test_redis_counts_deliveries_not_the_worker(wiring):
    """The cap has to survive a worker restart, because a worker holding a
    poison job is exactly the one that keeps restarting. Redis's own
    times-delivered is the only counter that does.

    Red line: `delivery_count`'s `xpending_range` call in
    backend/jobs/queue.py. Return a constant instead and the count stops
    rising across claims.
    """
    queue, _events = wiring
    entry_id = queue.enqueue(HOUSE_JOB)
    assert queue.delivery_count(entry_id) == 0, "not yet delivered"

    queue.claim("w1", block_ms=200)
    assert queue.delivery_count(entry_id) == 1
    queue.reclaim_stalled("w2", min_idle_ms=0)
    assert queue.delivery_count(entry_id) == 2

    queue.ack(entry_id)
    assert queue.delivery_count(entry_id) == 0, (
        "an acked entry is no longer pending and must not read as "
        "heavily delivered"
    )


def test_the_reclaim_threshold_sits_above_the_match_wall_clock_budget():
    """F7, and the mechanism behind the starvation above.

    A worker legitimately holds an entry, touching Redis not at all, for as
    long as run_match_isolated is allowed to take. While min_idle_ms=30000
    sat below DEFAULT_WALL_SECONDS=60, a slow match was reclaimed underneath
    the worker still running it -- doubling compute on the most expensive
    matches and guaranteeing that a job which takes the full budget to fail
    is always the idle-most entry by the time the loop comes round.

    Red line: `RECLAIM_MIN_IDLE_MS = DEFAULT_WALL_SECONDS * 1000 * 2` in
    backend/jobs/queue.py. Put the old 30000 back and this goes red.
    """
    assert RECLAIM_MIN_IDLE_MS > DEFAULT_WALL_SECONDS * 1000, (
        "the reclaim threshold must exceed the longest a live worker can "
        "legitimately hold a job without touching Redis"
    )


def test_process_one_defaults_to_that_threshold():
    """The constant is only worth deriving if the caller actually uses it.

    Red line: `min_idle_ms: int = RECLAIM_MIN_IDLE_MS` in
    worker.process_one's signature. Hardcode 30000 there and this goes red
    even though the constant above is still correct.
    """
    import inspect

    default = inspect.signature(process_one).parameters["min_idle_ms"].default
    assert default == RECLAIM_MIN_IDLE_MS


def test_a_determinism_break_retires_the_job_without_aborting_the_race(
    wiring, monkeypatch
):
    """A second execution that disagreed with the first is not a transient
    failure and not the player's fault. The match already completed and
    persisted on the earlier delivery, so the race stays finished; the job
    simply leaves the queue, loudly, instead of being reclaimed forever.

    Red line: the `except ReplayHashConflict` clause in worker.process_one.
    Delete it and the conflict falls into _retry_or_abandon, which re-raises
    on delivery one -- so this call raises instead of returning.
    """
    from backend.determinism.replay import ReplayHashConflict

    queue, events = wiring
    monkeypatch.setattr(
        worker, "run_match_isolated",
        _raise_for(set(), lambda: None),  # the match itself runs fine
    )

    persisted = []

    def conflicting_persist(result):
        persisted.append(result)
        raise ReplayHashConflict("two executions produced different bytes")

    queue.enqueue(HOUSE_JOB)
    match_id = process_one(queue, events, persist=conflicting_persist,
                           consumer="w-poison", min_idle_ms=0, block_ms=200)

    assert match_id == "m_poison_good"
    assert queue.pending_count() == 0
    assert queue.dead_letter_depth() == 1
    assert [p.get("outcome") for p in persisted] == [None], (
        "a determinism break must not also mark the race aborted -- the "
        "earlier delivery's result is the durable one"
    )


# ─── Failures OUTSIDE the match, which escaped the same way ──────────────
#
# The classification above covers everything run_match_isolated can raise.
# It did not cover the statements sitting outside process_one's `try`: the
# match_id lookup, the manifest build, and the publish/ack pair. A job that
# failed in any of those escaped uncapped and re-wedged the queue exactly as
# an unhandled match failure used to -- same starvation, reached by a route
# the classification never saw.


def test_a_job_that_cannot_become_a_manifest_does_not_wedge_the_queue(wiring):
    """Red against moving `_manifest_from_job` inside the try.

    Duplicate slots are rejected by the manifest builder, deterministically.
    Built above the try, that raise left the entry pending forever and the
    healthy job behind it never ran.
    """
    queue, events = wiring
    malformed = dict(
        HOUSE_JOB,
        match_id="m_bad_manifest",
        participants=[{"slot": 0, "house_bot": "VEL-01"},
                      {"slot": 0, "house_bot": "NXS-07"}],
    )
    queue.enqueue(malformed)
    queue.enqueue(HOUSE_JOB)

    persisted = []
    for _ in range(MAX_DELIVERIES + 2):
        try:
            process_one(queue, events, persisted.append,
                        consumer="c_manifest", min_idle_ms=0)
        except Exception:
            # Escaping at all is the bug; keep draining so the assertions
            # below describe the queue rather than the first raise.
            pass

    assert queue.pending_count() == 0, (
        "the malformed job never left the queue, so it will be re-served "
        "ahead of every new match forever"
    )
    assert any(r["match_id"] == "m_poison_good" for r in persisted), (
        "the healthy job behind the malformed one never ran"
    )


def test_a_job_with_no_match_id_is_retired_on_the_first_delivery(wiring):
    """Red against the `job.get` guard.

    `job["match_id"]` sat above the try, so a job missing the key raised
    KeyError straight out of process_one -- uncapped, and before any handler
    could retire it.
    """
    queue, events = wiring
    queue.enqueue({"track": "bahrain", "seed": 1000,
                   "participants": [{"slot": 0, "house_bot": "VEL-01"}]})

    assert process_one(queue, events, lambda r: None,
                       consumer="c_no_id", min_idle_ms=0) is None
    assert queue.pending_count() == 0
    assert get_redis().xlen(DEAD_LETTER_STREAM) == 1, (
        "a job with no match id must be dead-lettered, not left pending"
    )


def test_a_publish_failure_does_not_escape_uncapped(wiring):
    """Red against moving publish/ack inside the try.

    A Redis blip in publish used to escape process_one after persist had
    already succeeded, leaving the entry pending with no cap on retries.

    Only the match_finished publish is broken, not every publish. Breaking
    all of them wedges _abandon's own publish too, and then staying pending
    is the *correct* answer rather than the bug -- the same trade the abort
    path already makes for Mongo: during a total outage, do not throw work
    away. The escape this pins is the bounded one.
    """
    queue, events = wiring
    queue.enqueue(HOUSE_JOB)

    real_publish = events.publish

    def boom(payload):
        if payload.get("type") == "match_finished":
            raise ConnectionError("redis went away mid-publish")
        return real_publish(payload)

    events.publish = boom
    for _ in range(MAX_DELIVERIES + 2):
        try:
            process_one(queue, events, lambda r: None,
                        consumer="c_publish", min_idle_ms=0)
        except ConnectionError:
            pass

    assert queue.pending_count() == 0, (
        "a failing publish left the entry pending forever"
    )
