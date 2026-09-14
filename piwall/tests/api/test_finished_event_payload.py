"""The "finished" websocket event has to carry what the client reads.

Round 1 of the decouple shrank _stream_stored_replay's payload to
{race_id, replay_sha256}, on the reasoning that a spectator who wants the
standings reads GET /api/race/{race_id} afterwards. No client does that.

frontend/src/lib/websocket.ts's "finished" case is:

    setStatus("finished");
    if (msg.result) setResult(msg.result);

and the shrunken payload is a truthy object, so `result` becomes it. The
race page (frontend/src/app/race/[id]/page.tsx) then renders

    {status === "finished" && result && ( ... [...result.standings] ... )}

which throws TypeError on a result with no standings, inside render.
There is no error.tsx or global-error.tsx anywhere under
frontend/src/app/, so Next.js's default boundary replaces the whole race
page. Not "a finished race never renders as finished" -- a finished race
takes the page down, for every spectator, every time.

This file pins the contract from the backend side: exactly the fields
that frontend code actually reads, at the exact types it reads them at.
"""

import uuid

import pytest

from backend.db import crud
from backend.db.models import create_db_engine, init_db, mongo_url
from backend.observability.health import _mongo_is_reachable

pytestmark = pytest.mark.skipif(
    not _mongo_is_reachable(), reason="needs a reachable database"
)


class _Namespace:
    def __init__(self, d):
        self.__dict__.update(d)


@pytest.fixture
def db():
    session = init_db(create_db_engine(mongo_url()))()
    yield session
    session.close()


def _finish_a_race(db, race_id):
    """Persist a finished race exactly the way the worker does."""
    standings = [
        {"player_id": f"p1_{race_id}", "car_id": "P01", "position": 1,
         "retired": False, "total_time": 100.0, "pit_laps": [12, 34],
         "compounds_used": ["MEDIUM", "HARD", "SOFT"]},
        {"player_id": f"p2_{race_id}", "car_id": "P02", "position": 2,
         "retired": False, "total_time": 112.5, "pit_laps": [20],
         "compounds_used": ["SOFT", "MEDIUM"]},
        {"player_id": f"p3_{race_id}", "car_id": "P03", "position": 3,
         "retired": True, "total_time": None, "pit_laps": [],
         "compounds_used": ["MEDIUM"]},
    ]
    crud.save_race_results(db, race_id, [_Namespace(s) for s in standings])
    crud.save_race_data(db, race_id, [{"lap": n} for n in range(1, 58)], [])


def _capture_finished_event(race_id):
    """Run _stream_stored_replay with one fake socket attached."""
    import asyncio

    import backend.main as main

    sent = []

    class FakeSocket:
        async def send_json(self, message):
            sent.append(message)

    main.SOCKETS[race_id] = {FakeSocket()}
    try:
        asyncio.run(main._stream_stored_replay(race_id))
    finally:
        main.SOCKETS.pop(race_id, None)
    assert len(sent) == 1
    return sent[0]


def test_the_finished_event_carries_the_standings_the_race_page_renders(db):
    """Red line: the `"standings": standings` entry in
    _stream_stored_replay's broadcast payload (backend/main.py). Without
    it `[...result.standings]` spreads undefined and the page throws.
    """
    race_id = f"r_finished_evt_{uuid.uuid4().hex[:8]}"
    race = crud.create_race(db, "bahrain", "quick", owner_id="owner")
    db.db.races.update_one({"id": race.id}, {"$set": {"id": race_id}})
    try:
        _finish_a_race(db, race_id)

        message = _capture_finished_event(race_id)

        assert message["type"] == "finished"
        result = message["result"]

        # page.tsx:364 -- [...result.standings].sort(...)
        assert isinstance(result["standings"], list)
        assert [c["car_id"] for c in result["standings"]] == ["P01", "P02", "P03"]

        # page.tsx:404 -- <TyreStrategyChart totalLaps={result.total_laps} />,
        # and parseStints uses it as the last stint boundary, so 0 would
        # collapse every bar.
        assert result["total_laps"] == 57

        for car in result["standings"]:
            # Every field the finished panel and TyreStrategyChart touch.
            assert isinstance(car["position"], int)
            assert isinstance(car["retired"], bool)
            assert isinstance(car["pit_count"], int)
            assert isinstance(car["pit_laps"], list)
            assert isinstance(car["compounds_used"], list)
            # car.gap_to_leader.toFixed(3) -- a null here is a TypeError
            # in exactly the same way a missing standings list is.
            assert isinstance(car["gap_to_leader"], float)
    finally:
        db.db.race_results.delete_many({"race_id": race_id})
        db.db.races.delete_many({"id": {"$in": [race_id, race.id]}})


def test_gap_to_leader_and_pit_count_are_derived_from_the_stored_rows(db):
    """race_results stores neither, and the page renders both. Deriving
    them here is what lets the event be built from the durable rows
    rather than from a result object the API never sees.

    Red line: the `"gap_to_leader": (round(row.total_time - leader_time,
    3) ...)` entry in _stream_stored_replay.
    """
    race_id = f"r_finished_gap_{uuid.uuid4().hex[:8]}"
    race = crud.create_race(db, "bahrain", "quick", owner_id="owner")
    db.db.races.update_one({"id": race.id}, {"$set": {"id": race_id}})
    try:
        _finish_a_race(db, race_id)

        standings = _capture_finished_event(race_id)["result"]["standings"]
        by_car = {c["car_id"]: c for c in standings}

        assert by_car["P01"]["gap_to_leader"] == 0.0
        assert by_car["P02"]["gap_to_leader"] == 12.5
        assert by_car["P01"]["pit_count"] == 2
        assert by_car["P02"]["pit_count"] == 1

        # A retired car has no total_time at all; the page shows "DNF"
        # and never reads the gap, but it must still be a number rather
        # than null in case anything else does.
        assert by_car["P03"]["retired"] is True
        assert by_car["P03"]["gap_to_leader"] == 0.0
    finally:
        db.db.race_results.delete_many({"race_id": race_id})
        db.db.races.delete_many({"id": {"$in": [race_id, race.id]}})


def test_a_race_with_nothing_persisted_yet_sends_an_empty_standings_list(db):
    """The event is published by the worker after it persists, so this
    should not happen -- but "no rows" must degrade to an empty result
    panel, never to the undefined that takes the page down."""
    race_id = f"r_finished_empty_{uuid.uuid4().hex[:8]}"

    result = _capture_finished_event(race_id)["result"]

    assert result["standings"] == []
    assert result["total_laps"] == 0
