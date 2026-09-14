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
