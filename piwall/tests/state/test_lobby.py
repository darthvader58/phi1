"""Lobby state has to be visible to a second API replica.

active_lobbies was a module-global dict, so a lobby created on replica A did
not exist on replica B and a player routed there got "Race not found" — half
the time, depending on the load balancer.

Two separate LobbyStore instances stand in for two replicas throughout: if a
test only ever uses one instance it proves nothing about sharing.
"""

import pytest

from backend.state.lobby import LobbyStore
from backend.state.redis_client import redis_is_reachable

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
    """A second instance, standing in for the other API replica."""
    return LobbyStore()


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
