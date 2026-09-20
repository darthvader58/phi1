"""One bad event must not permanently kill event relay for the process.

Review round 2, F7: _relay_match_events had no per-iteration try/except.
Any exception -- a Redis blip on EVENTS.listen, a Mongo blip while looking
up a replay hash, a KeyError from an expired lobby -- escaped the `while
True` entirely, ran the `finally: pubsub.close()`, and ended the task for
the rest of the process's life. Every later match on that replica would
then silently never notify a connected spectator, with no log and no
restart short of recycling the whole process.
"""

import asyncio
import uuid

import pytest

from backend.jobs.events import MatchEvents
from backend.state.redis_client import redis_is_reachable

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)


def test_relay_survives_one_bad_event_and_still_processes_the_next(monkeypatch):
    import backend.main as main

    test_events = MatchEvents(channel=f"piwall:events:match:test-{uuid.uuid4()}")
    monkeypatch.setattr(main, "EVENTS", test_events)

    calls = []

    async def flaky_stream(race_id):
        calls.append(race_id)
        if race_id == "r_bad":
            raise RuntimeError("simulated Redis/Mongo blip")

    monkeypatch.setattr(main, "_stream_stored_replay", flaky_stream)
    main.SOCKETS["r_bad"] = set()
    main.SOCKETS["r_good"] = set()

    async def scenario():
        task = asyncio.create_task(main._relay_match_events())
        try:
            await asyncio.sleep(0.2)  # let the subscribe confirmation land
            test_events.publish({"type": "match_finished", "match_id": "r_bad"})
            await asyncio.sleep(1.5)
            test_events.publish({"type": "match_finished", "match_id": "r_good"})
            await asyncio.sleep(1.5)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    try:
        asyncio.run(scenario())
    finally:
        main.SOCKETS.pop("r_bad", None)
        main.SOCKETS.pop("r_good", None)

    assert calls == ["r_bad", "r_good"], (
        f"the loop must survive the first event's exception and still "
        f"process the second; got {calls}"
    )


def test_relay_retries_subscribe_after_a_failure_at_startup(monkeypatch):
    """N4 (fix round 3): EVENTS.subscribe() itself used to be outside any
    try/except -- Redis being down at the moment this task starts (e.g.
    at app startup) killed the whole task permanently, surfacing only
    when `lifespan` awaited it at shutdown. This must retry instead.
    """
    import backend.main as main

    monkeypatch.setattr(main, "_RELAY_MIN_BACKOFF_SECONDS", 0.05)
    monkeypatch.setattr(main, "_RELAY_MAX_BACKOFF_SECONDS", 0.05)

    real_events = MatchEvents(channel=f"piwall:events:match:test-{uuid.uuid4()}")

    class FlakyEvents:
        def __init__(self):
            self.attempts = 0

        def subscribe(self):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("simulated Redis outage at startup")
            return real_events.subscribe()

        def listen(self, pubsub, timeout):
            return real_events.listen(pubsub, timeout)

    flaky = FlakyEvents()
    monkeypatch.setattr(main, "EVENTS", flaky)

    calls = []

    async def recording_stream(race_id):
        calls.append(race_id)

    monkeypatch.setattr(main, "_stream_stored_replay", recording_stream)
    main.SOCKETS["r_after_retry"] = set()

    async def scenario():
        task = asyncio.create_task(main._relay_match_events())
        try:
            await asyncio.sleep(0.3)  # let the first subscribe fail and retry
            real_events.publish({"type": "match_finished", "match_id": "r_after_retry"})
            await asyncio.sleep(1.2)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    try:
        asyncio.run(scenario())
    finally:
        main.SOCKETS.pop("r_after_retry", None)

    assert flaky.attempts >= 2, "must have retried subscribe after the first failure"
    assert calls == ["r_after_retry"], (
        f"the relay must recover and process events after a subscribe "
        f"retry; got {calls}"
    )


def test_shutdown_survives_the_relay_raising_on_its_way_out():
    """N4's residual (round 3), closed. `lifespan` cancels the relay and
    awaits it; cancelling runs the relay's own `finally`, which closes
    the pub/sub connection. Against a Redis that has already gone away
    that raises, and the raise arrives at `await event_task` as itself,
    not as CancelledError -- so catching only CancelledError let it
    propagate out of ASGI shutdown and turn a clean stop into a failed
    one.

    The relay is replaced wholesale here rather than having Redis torn
    out from under a real one: what is being pinned is lifespan's
    handling of a task that raises while being cancelled, and a stand-in
    reproduces exactly that with no dependence on which call inside the
    real relay's finally happens to fail.

    Red line: `except Exception:` in backend/main.py's lifespan.
    """
    import backend.main as main
    from fastapi import FastAPI

    started = asyncio.Event()

    async def exploding_relay():
        try:
            started.set()
            await asyncio.sleep(3600)
        finally:
            # pubsub.close() against a dead connection.
            raise RuntimeError("simulated pubsub.close() against a dead Redis")

    async def scenario():
        app = FastAPI()
        async with main.lifespan(app):
            await asyncio.wait_for(started.wait(), timeout=2)
        # Reaching here at all is the assertion: the relay's exception
        # must not have escaped the shutdown half of the context manager.

    real_relay = main._relay_match_events
    main._relay_match_events = exploding_relay
    try:
        asyncio.run(scenario())
    finally:
        main._relay_match_events = real_relay
