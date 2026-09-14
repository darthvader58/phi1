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

Everything here runs on a disposable database -- including
_stream_stored_replay's own reads, which go through main.SessionLocal --
so no test in this file touches the shared piwall/phi1 data.
"""

import uuid

import pytest
from pymongo import MongoClient

from backend.db import crud
from backend.db.models import init_db
from backend.observability.health import _mongo_is_reachable

pytestmark = pytest.mark.skipif(
    not _mongo_is_reachable(), reason="needs a reachable database"
)


class _Namespace:
    def __init__(self, d):
        self.__dict__.update(d)


@pytest.fixture
def db(monkeypatch):
    """A disposable database, never the shared piwall/phi1 one.

    _stream_stored_replay does its own SessionLocal() lookup rather than
    taking a session argument, so main.SessionLocal is pointed here too.
    Without that the function under test would read the shared database
    while the fixtures wrote to this one -- the tests would still pass,
    by reading nothing, which is worse than failing.
    """
    import backend.main as main

    name = f"piwall_test_finished_event_{uuid.uuid4().hex[:8]}"
    client = MongoClient("mongodb://127.0.0.1:27017/")
    session = init_db(client[name])()
    monkeypatch.setattr(main, "SessionLocal", lambda: session)
    yield session
    client.drop_database(name)
    client.close()


PENALTY_EVENT = {"lap": 57, "event_type": "penalty", "car_id": "P02",
                 "detail": "P02 +30s penalty: did not use 2 different compounds"}


def _finish_a_race(db, events=(PENALTY_EVENT,), laps=57):
    """Persist a finished race exactly the way the worker does.

    Returns its race_id.
    """
    race = crud.create_race(db, "bahrain", "quick", owner_id="owner")
    race_id = race.id
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
    crud.save_race_data(
        db, race_id,
        [{"lap": n} for n in range(1, laps + 1)],
        [_Namespace(e) for e in events],
    )
    return race_id


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
    race_id = _finish_a_race(db)

    message = _capture_finished_event(race_id)

    assert message["type"] == "finished"
    result = message["result"]

    # page.tsx:364 -- [...result.standings].sort(...)
    assert isinstance(result["standings"], list)
    assert [c["car_id"] for c in result["standings"]] == ["P01", "P02", "P03"]

    # page.tsx -- <TyreStrategyChart totalLaps={result.total_laps} />,
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


def test_gap_to_leader_and_pit_count_are_derived_from_the_stored_rows(db):
    """race_results stores neither, and the page renders both. Deriving
    them here is what lets the event be built from the durable rows
    rather than from a result object the API never sees.

    Red line: the `"gap_to_leader": (round(row.total_time - leader_time,
    3) ...)` entry in _stream_stored_replay.
    """
    race_id = _finish_a_race(db)

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


def test_the_finishing_compound_is_derived_from_the_stint_history(db):
    """NEW-11 (round 5): the live-timing panels read `compound`, and it
    is the one such field a result row can honestly supply --
    compounds_used is an ordered stint history, so its last entry is the
    tyre the car finished on. tyre_age, drs_available and beliefs cannot
    be supplied and are deliberately absent rather than zero-filled;
    frontend types.ts's DisplayCar is what says so.

    Red line: the `"compound": (row.compounds_used or [None])[-1]` entry
    in _stream_stored_replay.
    """
    race_id = _finish_a_race(db)

    by_car = {c["car_id"]: c
              for c in _capture_finished_event(race_id)["result"]["standings"]}

    assert by_car["P01"]["compound"] == "SOFT", "last stint of MEDIUM/HARD/SOFT"
    assert by_car["P02"]["compound"] == "MEDIUM"
    assert by_car["P03"]["compound"] == "MEDIUM"

    # And it must never be a field the payload simply lacks, or
    # getCompoundColor would silently colour every car the same.
    assert all("compound" in c for c in
               _capture_finished_event(race_id)["result"]["standings"])


def test_the_finished_event_carries_the_persisted_race_events(db):
    """NEW-12 (round 5): save_race_data persists events_json durably and
    the payload dropped it, so EventLog was permanently empty on a
    finished decoupled race -- including, ironically, the +30s penalty
    event that explains the standings sitting next to it. The worker
    never sends per-lap messages, so this payload is the only thing that
    can fill it.

    The rows are read one line above the broadcast either way; this sends
    them. Shape must be frontend types.ts's RaceEvent exactly:
    {lap, type, car_id, detail}.

    Red line: the `"events": events` entry in _stream_stored_replay.
    """
    race_id = _finish_a_race(db)

    events = _capture_finished_event(race_id)["result"]["events"]

    assert len(events) == 1
    assert set(events[0]) == {"lap", "type", "car_id", "detail"}
    assert events[0]["type"] == "penalty"
    assert events[0]["car_id"] == "P02"
    assert "+30s penalty" in events[0]["detail"]


def test_total_laps_falls_back_to_the_track_distance_not_zero(db):
    """NEW-13 (round 5): TyreStrategyChart scales every stint bar by
    totalLaps as ((endLap - startLap) / totalLaps), so a 0 alongside
    non-empty standings renders width:Infinity%. Result rows without lap
    data is not a state _persist_result produces -- save_race_results and
    save_race_data are consecutive -- but the zero was a deliberate
    fallback whose one consumer divides by it.

    Red line: the `else getattr(TRACKS.get(track), "total_laps", 0)`
    branch of the "total_laps" entry in _stream_stored_replay.
    """
    from backend.data.tracks import TRACKS

    race_id = _finish_a_race(db, laps=0)  # results persisted, lap_data empty

    result = _capture_finished_event(race_id)["result"]

    assert result["standings"], "the standings that would be divided by it"
    assert result["total_laps"] == TRACKS["bahrain"].total_laps
    assert result["total_laps"] > 0


def test_a_race_with_nothing_persisted_yet_sends_an_empty_standings_list(db):
    """The event is published by the worker after it persists, so this
    should not happen -- but "no rows" must degrade to an empty result
    panel, never to the undefined that takes the page down."""
    race_id = f"r_finished_empty_{uuid.uuid4().hex[:8]}"

    result = _capture_finished_event(race_id)["result"]

    assert result["standings"] == []
    assert result["events"] == []
    assert result["total_laps"] == 0
