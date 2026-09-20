"""A worker that has stopped making progress must stop looking alive.

Twice now matches have quietly stopped finishing while every probe stayed
green: a consumer group that vanished under a long-lived worker (3636b01),
and a poison job re-served at the head of the queue forever. In both the
process was running, Redis and Mongo answered, and /ready said "ready". The
missing fact was never "is a dependency up" -- it was "is any worker still
going round its loop".
"""

import time

import pytest

from backend.jobs import heartbeat
from backend.sandbox.isolation import DEFAULT_WALL_SECONDS
from backend.state.redis_client import get_redis, redis_is_reachable

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)

NAME = "test-worker-heartbeat"


@pytest.fixture(autouse=True)
def _clean():
    get_redis().delete(f"{heartbeat.KEY_PREFIX}{NAME}")
    yield
    get_redis().delete(f"{heartbeat.KEY_PREFIX}{NAME}")


def test_a_worker_that_has_never_beaten_is_not_listed():
    """The vacuous-pass guard: every assertion below is about a name
    appearing in this list, so the list must be able to not contain it."""
    assert NAME not in heartbeat.live_workers()


def test_a_beat_makes_the_worker_visible():
    """Red line: the `client.set(...)` in heartbeat.beat."""
    heartbeat.beat(NAME)
    assert NAME in heartbeat.live_workers()


def test_a_heartbeat_expires_on_its_own():
    """The reason this is a TTL key and not a counter.

    A worker that stops looping writes nothing more; the key has to go away
    by itself or its last value reads as "alive" forever. Asserted by
    actually letting one expire, with a 1s TTL, rather than by reading the
    TTL back -- what failed before was a signal that never went stale, which
    is behaviour, not configuration.

    Red line: the `ex=ttl_seconds` argument in heartbeat.beat. Drop it and
    the key persists and this never goes false.
    """
    heartbeat.beat(NAME, ttl_seconds=1)
    assert NAME in heartbeat.live_workers()
    deadline = time.time() + 5
    while NAME in heartbeat.live_workers() and time.time() < deadline:
        time.sleep(0.1)
    assert NAME not in heartbeat.live_workers(), (
        "a heartbeat that outlives its worker is worse than none"
    )


def test_the_ttl_outlasts_a_single_match():
    """A live worker inside run_match_isolated touches Redis not at all for
    up to DEFAULT_WALL_SECONDS. A TTL shorter than that reports the busiest
    worker on the fleet as dead, which would make the signal useless the
    moment anyone relied on it.

    Red line: `TTL_SECONDS = DEFAULT_WALL_SECONDS * 2` in
    backend/jobs/heartbeat.py. Write 30 there and this goes red.
    """
    assert heartbeat.TTL_SECONDS > DEFAULT_WALL_SECONDS


def test_a_clean_stop_drops_the_key_rather_than_waiting_out_the_ttl():
    """A rolling deploy is the one time the count most needs to be true, and
    it is exactly when a worker exits deliberately.

    Red line: the `client.delete(...)` in heartbeat.stop.
    """
    heartbeat.beat(NAME)
    heartbeat.stop(NAME)
    assert NAME not in heartbeat.live_workers()


def test_ready_reports_the_live_worker_count():
    """The signal has to reach somewhere a human or an alert looks.

    Red line: the `"workers": workers` entry in observability.health.ready's
    200 body. Delete it and the key is absent.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.observability.health as mod

    app = FastAPI()
    app.include_router(mod.health_router)
    heartbeat.beat(NAME)
    body = TestClient(app).get("/ready").json()
    assert body["workers"] >= 1


def test_ready_does_not_fail_when_no_worker_is_alive(monkeypatch):
    """Deliberate, and the opposite of what the name suggests.

    An API replica with no worker behind it still serves every read
    endpoint. 503-ing every replica because the worker fleet is down takes
    the whole site out over an outage that only stops new matches finishing.
    The number is there to alert on, not to withdraw traffic.

    Red line: the `if all(checks.values())` condition in
    observability.health.ready. Add `and workers` to it and this goes red.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.observability.health as mod

    monkeypatch.setattr(mod, "redis_is_reachable", lambda **kw: True)
    monkeypatch.setattr(mod, "_mongo_is_reachable", lambda **kw: True)
    monkeypatch.setattr(mod, "_live_worker_count", lambda: 0)

    app = FastAPI()
    app.include_router(mod.health_router)
    response = TestClient(app).get("/ready")
    assert response.status_code == 200
    assert response.json()["workers"] == 0


def test_the_worker_count_survives_redis_being_unreachable(monkeypatch):
    """A readiness probe that raises tells an orchestrator nothing about
    readiness, and Redis being down is already reported by its own check.

    Red line: the `except Exception: return 0` in
    observability.health._live_worker_count.
    """
    import backend.observability.health as mod

    def boom():
        raise ConnectionError("redis is gone")

    monkeypatch.setattr(heartbeat, "live_workers", boom)
    assert mod._live_worker_count() == 0


def test_the_worker_loop_beats_on_every_pass(monkeypatch):
    """The key is only a signal if the loop that proves liveness writes it.

    Everything around the loop is stubbed except the beat itself: the
    database factory, the logging install and process_one. What is real is
    run_forever's own control flow and the Redis write, which is the thing
    under test.

    The beat is asserted from INSIDE the stubbed process_one, not after
    run_forever returns, because run_forever deletes the key on a clean
    exit -- so an after-the-fact check would pass for a run_forever that
    beat once at startup and never again, which is the counter-shaped bug
    the TTL exists to rule out.

    Red line: the `heartbeat.beat(consumer)` call in worker.run_forever.
    """
    import backend.db.models as models
    import backend.worker as worker

    seen = []

    def fake_process_one(*_args, **_kwargs):
        seen.append(heartbeat.live_workers())
        worker._shutting_down = True

    monkeypatch.setattr(worker, "_shutting_down", False)
    monkeypatch.setattr(worker, "process_one", fake_process_one)
    monkeypatch.setattr(worker, "configure_logging", lambda service: None)
    monkeypatch.setattr(models, "create_db_engine", lambda url=None: object())
    monkeypatch.setattr(models, "init_db", lambda engine: (lambda: None))

    worker.run_forever(consumer=NAME)

    assert seen, "run_forever never reached process_one"
    assert NAME in seen[0], "the loop ran a pass without recording a heartbeat"
