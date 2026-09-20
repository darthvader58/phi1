"""A match id on API log lines, not only on the worker's.

backend/observability/logging.py exists for one question -- "what happened
to match X" -- across three processes: an API replica creates the match, a
worker runs it, and (possibly) a different API replica streams the result.
The worker bound an id; main bound none, so no API line ever carried a
match_id and the cross-process join the module was built for could not
actually be performed.
"""

import asyncio
import json
import logging
import time

import pytest

from backend.observability.logging import JsonFormatter
from backend.state.lobby import LobbyStore
from backend.state.redis_client import redis_is_reachable

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)


class _Capture(logging.Handler):
    """Formats through the real JsonFormatter, so what is asserted is the
    line an operator would actually grep -- not the ContextVar behind it."""

    def __init__(self):
        super().__init__()
        self.setFormatter(JsonFormatter("api"))
        self.lines = []

    def emit(self, record):
        self.lines.append(json.loads(self.format(record)))


async def _no_sleep(_seconds):
    return None


def _match_ids(lines):
    return {line.get("match_id") for line in lines}


def test_run_race_puts_the_match_id_on_its_log_lines(monkeypatch):
    """Red line: the `with match_context(race_id):` wrapper in
    main._run_race. Remove it and every line comes out with no match_id,
    which is the state this finding describes.
    """
    import backend.main as main
    from backend.db import crud

    monkeypatch.setattr(main.asyncio, "sleep", _no_sleep)

    race_id = f"m_corr_{int(time.time() * 1000)}"
    store = LobbyStore()
    store.delete(race_id)
    store.create(race_id, track="bahrain", race_type="quick")
    store.add_player(race_id, "p1", {
        "username": "alex", "car_id": "USR-01",
        "code": "def my_strategy(state, my_car):\n    return {'pit': False, "
                "'compound': 'MEDIUM'}\n",
        "starting_compound": "MEDIUM",
    })

    # Fail the race so _run_race's own logger.exception fires -- that is a
    # real API log line about a specific match, and exactly the kind
    # someone greps for.
    def boom(_db, _manifest):
        raise ValueError("simulated")

    monkeypatch.setattr(crud, "save_manifest", boom)

    handler = _Capture()
    logger = logging.getLogger("piwall")
    logger.addHandler(handler)
    try:
        asyncio.run(main._run_race(race_id))
    finally:
        logger.removeHandler(handler)
        store.delete(race_id)
        db = main.SessionLocal()
        try:
            db.db.races.delete_many({"id": race_id})
        finally:
            db.close()

    assert handler.lines, "the failure path logged nothing at all"
    assert _match_ids(handler.lines) == {race_id}, (
        f"every API log line from a match must carry its id, got "
        f"{_match_ids(handler.lines)}"
    )


def test_the_id_does_not_leak_past_the_match(monkeypatch):
    """Why match_context and not bind_match. _run_race can leave by an
    exception, and a bare clear_match() after the body would be skipped --
    leaving one match's id stamped on whatever ran next in this context.

    Red line: `match_context`'s use in main._run_race. Swap it for a
    `bind_match(race_id)` call with no reset and this goes red.
    """
    import backend.main as main
    from backend.db import crud

    monkeypatch.setattr(main.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(crud, "save_manifest",
                        lambda *_a: (_ for _ in ()).throw(ValueError("x")))

    race_id = f"m_corr_leak_{int(time.time() * 1000)}"
    store = LobbyStore()
    store.delete(race_id)
    store.create(race_id, track="bahrain", race_type="quick")

    handler = _Capture()
    logger = logging.getLogger("piwall")
    logger.addHandler(handler)
    try:
        async def scenario():
            await main._run_race(race_id)
            logging.getLogger("piwall").info("unrelated work")

        asyncio.run(scenario())
    finally:
        logger.removeHandler(handler)
        store.delete(race_id)
        db = main.SessionLocal()
        try:
            db.db.races.delete_many({"id": race_id})
        finally:
            db.close()

    assert handler.lines[-1]["message"] == "unrelated work"
    assert "match_id" not in handler.lines[-1], (
        "a match id outliving its match stamps the wrong id on the next "
        "unit of work in this context"
    )


def test_streaming_a_finished_replay_is_also_correlated(monkeypatch):
    """The third process in the story: the replica that streams the result
    is often not the one that created the match.

    Red line: the `with match_context(race_id):` wrapper in
    main._stream_stored_replay.
    """
    import backend.main as main

    race_id = f"m_corr_stream_{int(time.time() * 1000)}"
    seen = []

    async def recording_broadcast(rid, message):
        from backend.observability.logging import _match_id
        seen.append(_match_id.get())

    monkeypatch.setattr(main, "_broadcast", recording_broadcast)
    asyncio.run(main._stream_stored_replay(race_id))
    assert seen == [race_id]
