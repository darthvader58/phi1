"""No two cars in one race may share a car_id — every source of one.

This invariant has now been broken three times, each by a source the
previous fix did not know about (see backend/state/car_ids.py, which
states the rule once):

    round 3 -- two concurrent brand-new joins computing the same default
    round 4 -- a re-join recomputing a default and freeing its old label
    round 5 -- a player explicitly claiming a HOUSE BOT's identity

The first two were fixed at their source and are pinned in
tests/state/test_lobby.py. This file is about the part that was missing
all three times: a single place that sees every source at once, so the
fourth cannot be added without passing through it.

`_build_job_and_manifest` is that place. Every car_id converges there --
lobby players, then house bots -- and it is the last point before the
manifest, and therefore the replay hash, seals whatever the grid turned
out to be.

Nothing here touches MongoDB. `_build_job_and_manifest` reads a lobby
dict and returns a job and a manifest; the one test that needs a lobby in
Redis builds and deletes its own key, and the one that needs an
authenticated player fakes `authenticate` rather than writing a player row
to the shared database.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from backend.engine.bots import BUILTIN_BOTS
from backend.main import _build_job_and_manifest
from backend.state.car_ids import (
    RESERVED_CAR_IDS,
    DuplicateCarIdError,
    assert_unique_car_ids,
)
from backend.state.lobby import CarIdTakenError, LobbyStore
from backend.state.redis_client import redis_is_reachable
from backend.worker import _spec_from_job


def _lobby(players):
    return {
        "race_id": "r_car_id_invariant",
        "track": "bahrain",
        "race_type": "quick",
        "status": "lobby",
        "speed": 1.0,
        "seed": 4242,
        "players": players,
    }


def test_the_reserved_set_is_exactly_what_house_bots_contribute():
    """RESERVED_CAR_IDS is what LobbyStore.join() is told to keep clear
    of, so it has to stay equal to the ids house bots actually bring. A
    hand-maintained list that drifted from BUILTIN_BOTS would be worse
    than none: it would read as protection while leaving the newest bot's
    identity claimable.

    Red line: `RESERVED_CAR_IDS = frozenset(BUILTIN_BOTS)` in
    backend/state/car_ids.py. Pin it to a literal set and this goes red
    the moment the two disagree.
    """
    assert RESERVED_CAR_IDS == frozenset(BUILTIN_BOTS)

    # And that those really are the ids the grid-assembly step emits, so
    # the equality above is about the right thing.
    _job, _manifest = _build_job_and_manifest("r_reserved", _lobby({}))
    spec = _spec_from_job(_job)
    assert {car["car_id"] for car in spec["cars"]} == set(BUILTIN_BOTS)


def test_build_job_and_manifest_refuses_a_grid_with_a_duplicate_car_id():
    """The chokepoint. A player holding a house bot's identity reaches
    engine.add_car twice under one name -- engine/race.py:152-153 does
    `self.strategies[car_id] = strategy` and
    `self.belief_models[car_id] = BeliefModel(...)`, so the second call
    wins and one bot drives both cars.

    The lobby is built as a plain dict here, deliberately bypassing
    LobbyStore.join(): the point of a chokepoint is that it catches a
    duplicate whatever produced it, including a source that does not
    exist yet.

    Red line: the `assert_unique_car_ids(...)` call in
    `_build_job_and_manifest` (backend/main.py).
    """
    lobby = _lobby({"p1": {"username": "eve", "car_id": "VEL-01", "code": ""}})

    with pytest.raises(DuplicateCarIdError, match="VEL-01"):
        _build_job_and_manifest("r_dup_housebot", lobby)


def test_build_job_and_manifest_refuses_two_players_with_no_car_id_at_all():
    """The same chokepoint, reached by a different shape: a lobby row
    with no car_id key reaches the worker as
    `participant.get("car_id") or house_bot` -> None, and two of them
    collide on None. No production path writes such a row, which is
    exactly why a check that only knew about the reported shapes would
    miss it.

    Red line: the same `assert_unique_car_ids(...)` call. It is one
    guard, and these two tests are two of its cases rather than two
    separately-removable lines.
    """
    lobby = _lobby({
        "p1": {"username": "alex", "code": ""},
        "p2": {"username": "bo", "code": ""},
    })

    with pytest.raises(DuplicateCarIdError):
        _build_job_and_manifest("r_dup_none", lobby)


def test_an_ordinary_grid_still_assembles():
    """The negative control, so the two tests above cannot pass merely
    because the chokepoint rejects everything. Not red against any
    production line by design -- that is what makes it a control."""
    lobby = _lobby({
        "p1": {"username": "alex", "car_id": "P01", "code": ""},
        "p2": {"username": "bo", "car_id": "P02", "code": ""},
    })

    job, manifest = _build_job_and_manifest("r_ok", lobby)

    car_ids = [car["car_id"] for car in _spec_from_job(job)["cars"]]
    assert car_ids[:2] == ["P01", "P02"]
    assert len(set(car_ids)) == len(car_ids) == 2 + len(BUILTIN_BOTS)
    assert len(manifest.participants) == len(car_ids)


def test_assert_unique_car_ids_names_the_colliding_id():
    """The helper itself: it reports the first repeat in the caller's own
    order rather than diffing sets, so an operator reading the log is
    told which identity actually collided.

    Red line: `raise DuplicateCarIdError(...)` inside
    `assert_unique_car_ids` (backend/state/car_ids.py).
    """
    assert_unique_car_ids([])
    assert_unique_car_ids(["P01", "P02", "VEL-01"])

    with pytest.raises(DuplicateCarIdError, match="P02"):
        assert_unique_car_ids(["P01", "P02", "VEL-01", "P02", "P01"])


@pytest.mark.skipif(not redis_is_reachable(), reason="needs a reachable Redis")
def test_a_lobby_built_through_join_can_never_assemble_a_duplicate():
    """Prevention and detection together, end to end, reproducing the
    exact sequence NEW-9 reported: a player asks for a house bot's id,
    and the spec that reaches the engine is checked afterwards.

    Without the reserved-id guard the join is accepted and the spec comes
    out as ['VEL-01', 'VEL-01', 'NXS-07', ...]; with it the join is
    refused and the player falls back to a default that cannot collide.

    Red line: `for _, reserved in ipairs(cjson.decode(ARGV[5])) do` in
    _JOIN_SCRIPT (backend/state/lobby.py) -- the loop that seeds `taken`
    with RESERVED_CAR_IDS.
    """
    race_id = f"r_join_to_spec_{uuid.uuid4().hex[:8]}"
    store = LobbyStore()
    store.create(race_id, track="bahrain", race_type="quick")
    try:
        with pytest.raises(CarIdTakenError):
            store.join(race_id, "p_thief", {"username": "eve", "car_id": "VEL-01"})

        store.join(race_id, "p_thief", {"username": "eve"})
        store.join(race_id, "p_honest", {"username": "alex"})

        job, _manifest = _build_job_and_manifest(race_id, store.get(race_id))
        car_ids = [car["car_id"] for car in _spec_from_job(job)["cars"]]

        assert len(set(car_ids)) == len(car_ids), (
            f"two cars reached the engine under one identity: {car_ids}"
        )
        assert set(car_ids) >= set(BUILTIN_BOTS)
    finally:
        store.delete(race_id)


@pytest.mark.skipif(not redis_is_reachable(), reason="needs a reachable Redis")
def test_the_api_refuses_a_join_that_claims_a_house_bot_identity(monkeypatch):
    """The HTTP surface of the same refusal: a 400 the caller can act on,
    not a 500. `authenticate` is faked rather than backed by a real
    player row, so this test writes nothing to any database.

    Red line: `except CarIdTakenError:` in `join_race`
    (backend/main.py). Without it CarIdTakenError escapes the handler as
    an unhandled 500.
    """
    import backend.main as main

    race_id = f"r_api_housebot_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(
        main, "authenticate",
        lambda api_key: {"id": "p_api", "username": "eve", "elo": 1200.0,
                         "role": "player"},
    )
    main.LOBBIES.create(race_id, track="bahrain", race_type="quick")
    try:
        client = TestClient(main.app)
        response = client.post(
            f"/api/race/{race_id}/join",
            json={"car_id": "VEL-01", "starting_compound": "MEDIUM"},
            headers={"x-api-key": "irrelevant"},
        )

        assert response.status_code == 400, response.text
        assert "VEL-01" in response.json()["detail"]
        assert main.LOBBIES.players(race_id) == {}

        # ...and the same player joining without one is still fine.
        ok = client.post(
            f"/api/race/{race_id}/join",
            json={"starting_compound": "MEDIUM"},
            headers={"x-api-key": "irrelevant"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["car_id"] == "P01"
    finally:
        main.LOBBIES.delete(race_id)
