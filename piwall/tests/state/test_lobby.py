"""Lobby state has to be visible to a second API replica.

active_lobbies was a module-global dict, so a lobby created on replica A did
not exist on replica B and a player routed there got "Race not found" — half
the time, depending on the load balancer.

Two separate LobbyStore instances stand in for two replicas throughout: if a
test only ever uses one instance it proves nothing about sharing.
"""

import threading

import pytest
import redis as redis_lib

from backend.state.lobby import LobbyStore
from backend.state.redis_client import REDIS_URL, redis_is_reachable

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)

RACE = "r_test_lobby"


@pytest.fixture
def replica_a():
    store = LobbyStore()
    store.delete(RACE)
    yield store
    store.delete(RACE)


@pytest.fixture
def replica_b():
    """A second instance, on its own connection, standing in for the other
    API replica.

    Handing this LobbyStore() no client means it falls back to
    get_redis()'s process-wide singleton, so without this a naive
    ``replica_b = LobbyStore()`` would make ``replica_a._redis is
    replica_b._redis`` true: both fixtures would be the same Python object
    talking over the same socket in the same process. That proves two
    instances share state, not that two processes do — it would pass just
    as happily against a LobbyStore backed by a class-level dict, which is
    exactly the design this store replaces. A second, independent
    connection is what actually proves state crosses Redis rather than the
    Python heap.
    """
    client = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
    store = LobbyStore(redis_client=client)
    yield store
    client.close()


def test_a_lobby_created_on_one_replica_is_visible_on_the_other(replica_a, replica_b):
    replica_a.create(RACE, track="bahrain", race_type="quick")
    assert replica_b.get(RACE) is not None


def test_an_absent_lobby_reads_as_none(replica_a):
    assert replica_a.get("r_does_not_exist") is None


def test_created_fields_round_trip(replica_a):
    replica_a.create(RACE, track="monaco", race_type="ranked")
    lobby = replica_a.get(RACE)
    assert lobby["race_id"] == RACE
    assert lobby["track"] == "monaco"
    assert lobby["race_type"] == "ranked"
    assert lobby["status"] == "lobby"


def test_status_changes_are_seen_by_the_other_replica(replica_a, replica_b):
    replica_a.create(RACE, track="bahrain")
    replica_a.set_status(RACE, "running")
    assert replica_b.get(RACE)["status"] == "running"


def test_players_added_on_one_replica_are_seen_on_the_other(replica_a, replica_b):
    replica_a.create(RACE, track="bahrain")
    replica_a.add_player(RACE, "p1", {"username": "alex", "car_id": "VEL-01"})
    assert replica_b.players(RACE)["p1"]["username"] == "alex"


def test_players_is_empty_for_a_new_lobby(replica_a):
    replica_a.create(RACE, track="bahrain")
    assert replica_a.players(RACE) == {}


def test_speed_round_trips_as_a_float(replica_a, replica_b):
    """Redis stores strings; a speed read back as '5.0' breaks arithmetic."""
    replica_a.create(RACE, track="bahrain")
    replica_a.set_speed(RACE, 5.0)
    assert replica_b.get(RACE)["speed"] == 5.0
    assert isinstance(replica_b.get(RACE)["speed"], float)


def test_list_open_includes_a_waiting_lobby(replica_a):
    replica_a.create(RACE, track="bahrain")
    assert any(l["race_id"] == RACE for l in replica_a.list_open())


def test_list_open_excludes_a_finished_lobby(replica_a):
    replica_a.create(RACE, track="bahrain")
    replica_a.set_status(RACE, "finished")
    assert not any(l["race_id"] == RACE for l in replica_a.list_open())


def test_delete_removes_the_lobby_for_both_replicas(replica_a, replica_b):
    replica_a.create(RACE, track="bahrain")
    replica_a.delete(RACE)
    assert replica_b.get(RACE) is None


def test_add_player_to_a_missing_lobby_raises(replica_a):
    with pytest.raises(KeyError):
        replica_a.add_player("r_nope", "p1", {"username": "x"})


def test_add_player_survives_interleaved_writes(replica_a, replica_b):
    """Two replicas add different players to the same lobby at once.

    A get-modify-set mutator that is not atomic loses whichever write is
    based on the staler read: replica A reads the lobby, is delayed, and
    only then writes back a version that never saw replica B's player —
    silently, since nothing raises. This forces that exact interleaving —
    replica B's add_player runs to completion strictly between replica A's
    read and replica A's write — instead of hoping a race shows up under
    load, which it would only rarely and nondeterministically.

    The hook point is replica_a's own `.get()` call, which is where a
    get-modify-set mutator's read happens. Against an atomic mutator that
    call is never made mid-mutation — there is nothing to pause inside a
    single atomic step — so the wait below simply times out quickly and
    both writes land safely regardless of scheduling. That is the property
    this test exists to pin: the fix does not just make the race less
    likely, it removes the window the race needed.
    """
    replica_a.create(RACE, track="bahrain")

    a_read = threading.Event()
    b_done = threading.Event()
    real_get = replica_a._redis.get

    def delayed_get(*args, **kwargs):
        result = real_get(*args, **kwargs)
        a_read.set()
        b_done.wait(timeout=2)
        return result

    replica_a._redis.get = delayed_get
    try:
        thread = threading.Thread(
            target=replica_a.add_player,
            args=(RACE, "p1", {"username": "alex"}),
        )
        thread.start()
        # Non-atomic code reaches its read almost immediately; atomic code
        # may finish before this ever fires. Either way we move on.
        a_read.wait(timeout=0.5)
        replica_b.add_player(RACE, "p2", {"username": "bo"})
        b_done.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
    finally:
        replica_a._redis.get = real_get

    players = replica_a.players(RACE)
    assert set(players) == {"p1", "p2"}
