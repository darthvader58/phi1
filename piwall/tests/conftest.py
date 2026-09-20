import uuid

import pytest


@pytest.fixture
def throwaway_session():
    """A MongoSession on a database this test owns, dropped afterwards.

    For any test that drives production code which reaches the database
    through main.SessionLocal(). Those tests used to run against whatever
    mongo_url() names -- in a developer's shell that is the shared `phi1`
    -- and left rows behind in it: races, manifests, and (once the race
    path started storing them) bot sources and per-match replay inputs.
    Pointing SessionLocal at this instead means the suite cannot write to
    the shared database at all, rather than writing to it and tidying up,
    which only works when every test remembers to.

    Use with `monkeypatch.setattr(main, "SessionLocal", lambda: session)`.
    """
    from pymongo import MongoClient

    from backend.db.models import MongoSession, init_db, mongo_url

    name = f"piwall_test_session_{uuid.uuid4().hex[:8]}"
    client = MongoClient(mongo_url())
    database = client[name]
    init_db(database)
    session = MongoSession(database)
    yield session
    session.close()
    client.drop_database(name)
    client.close()


import pytest


@pytest.fixture
def sample_car():
    return {
        "car_id": "c1", "player_id": "p1", "position": 3,
        "gap_to_leader": 4.2, "compound": "MEDIUM", "tyre_age": 22,
        "fuel_kg": 40.0, "pit_count": 0, "pit_laps": [],
        "last_lap_time": 94.1, "total_time": 900.0, "retired": False,
        "drs_available": False, "compounds_used": ["MEDIUM"],
        "beliefs": {"c2": {"undercut_viable": True, "undercut_gain": 3.1}},
    }


@pytest.fixture
def sample_state(sample_car):
    return {
        "lap": 25, "total_laps": 57, "track": "bahrain", "weather": "dry",
        "safety_car": False, "safety_car_laps_left": 0,
        "track_temp": 32.0, "cars": [sample_car],
    }
