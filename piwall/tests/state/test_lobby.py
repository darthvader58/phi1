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


def test_join_admits_up_to_max_players(replica_a):
    """Review round 2, F18: join_race's capacity check used to be a
    Python-side len() read, separate from the write -- two replicas could
    both see "seven players, room for one more" and both admit an eighth.
    """
    replica_a.create(RACE, track="bahrain")
    seen_car_ids = set()
    for i in range(3):
        is_new, count, car_id = replica_a.join(
            RACE, f"p{i}", {"username": f"u{i}"}, max_players=3
        )
        assert is_new is True
        assert count == i + 1
        seen_car_ids.add(car_id)
    assert set(replica_a.players(RACE)) == {"p0", "p1", "p2"}
    assert len(seen_car_ids) == 3, "three new joins must not share a default car_id"


def test_join_refuses_a_new_player_once_full():
    from backend.state.lobby import LobbyFullError

    store = LobbyStore()
    store.delete(RACE)
    store.create(RACE, track="bahrain")
    try:
        for i in range(3):
            store.join(RACE, f"p{i}", {"username": f"u{i}"}, max_players=3)
        with pytest.raises(LobbyFullError):
            store.join(RACE, "p_ninth", {"username": "nine"}, max_players=3)
        assert set(store.players(RACE)) == {"p0", "p1", "p2"}
    finally:
        store.delete(RACE)


def test_join_never_counts_a_rejoin_against_the_cap(replica_a):
    """A re-join must succeed and must not change the player count, even
    when the lobby is already at max_players -- the F18 regression this
    guards against the other direction of."""
    replica_a.create(RACE, track="bahrain")
    for i in range(3):
        replica_a.join(RACE, f"p{i}", {"username": f"u{i}"}, max_players=3)

    is_new, count, _car_id = replica_a.join(
        RACE, "p1", {"username": "u1-rejoined"}, max_players=3
    )
    assert is_new is False
    assert count == 3
    assert replica_a.players(RACE)["p1"]["username"] == "u1-rejoined"


def test_join_on_a_missing_lobby_raises(replica_a):
    with pytest.raises(KeyError):
        replica_a.join("r_nope", "p1", {"username": "x"})


def test_join_survives_two_replicas_racing_for_the_last_slot(replica_a, replica_b):
    """The actual atomicity claim: only one of two concurrent joins for the
    lobby's last slot can succeed, never both.

    Mirrors test_add_player_survives_interleaved_writes's technique --
    replica_a's own .get() (the read half of the old read-count-then-write
    pattern) is where a non-atomic implementation would pause -- but here
    the assertion is about the CAPACITY outcome, not just that both writes
    landed: a lobby capped at 1 must end with exactly one player, not two.
    """
    replica_a.create(RACE, track="bahrain", race_type="quick")

    a_read = threading.Event()
    b_done = threading.Event()
    real_get = replica_a._redis.get

    def delayed_get(*args, **kwargs):
        result = real_get(*args, **kwargs)
        a_read.set()
        b_done.wait(timeout=2)
        return result

    replica_a._redis.get = delayed_get
    outcomes = {}

    def try_join(store, key, player_id):
        try:
            outcomes[key] = store.join(
                RACE, player_id, {"username": player_id}, max_players=1
            )
        except Exception as exc:
            outcomes[key] = exc

    try:
        thread = threading.Thread(target=try_join, args=(replica_a, "a", "p_a"))
        thread.start()
        a_read.wait(timeout=0.5)
        try_join(replica_b, "b", "p_b")
        b_done.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
    finally:
        replica_a._redis.get = real_get

    assert len(replica_a.players(RACE)) == 1, (
        "a lobby capped at 1 player must never end with 2, no matter how "
        "the two joins interleaved"
    )


def test_set_player_field_updates_one_field_without_touching_others(replica_a, replica_b):
    replica_a.create(RACE, track="bahrain")
    replica_a.add_player(RACE, "p1", {"username": "alex", "car_id": "VEL-01", "code": "old"})

    replica_a.set_player_field(RACE, "p1", "code", "new code")

    player = replica_b.get(RACE)["players"]["p1"]
    assert player["code"] == "new code"
    assert player["username"] == "alex"
    assert player["car_id"] == "VEL-01"


def test_set_player_field_on_a_missing_player_raises(replica_a):
    replica_a.create(RACE, track="bahrain")
    with pytest.raises(KeyError):
        replica_a.set_player_field(RACE, "p_missing", "code", "x")


def test_set_player_field_on_a_missing_lobby_raises(replica_a):
    with pytest.raises(KeyError):
        replica_a.set_player_field("r_nope", "p1", "code", "x")


def test_join_never_hands_two_concurrent_new_players_the_same_default_car_id(
    replica_a,
):
    """N2 (fix round 3): car_id is an engine identity, not a display
    label -- main.py keys belief dicts by it, and engine/race.py keys
    self.strategies and self.belief_models by it. Two concurrent
    brand-new joins with no explicit car_id used to both default to
    "P01" from a snapshot read before the write, handing one player's
    bot both cars.

    Round 3's version of this test hooked replica_a._redis.get to stall
    one join mid-read, which is how the SEQUENTIAL lobby mutators are
    tested elsewhere in this file -- but join() goes through an EVALSHA
    and never calls _redis.get at all, so the hook fired zero times and
    the two joins ran strictly in sequence (re-review of round 3,
    NEW-5). This interleaves for real instead: eight threads, each with
    its own LobbyStore on its own connection, all released from one
    threading.Barrier into the same empty lobby.

    Red line: the default assignment inside _JOIN_SCRIPT --
    `player_data.car_id = string.format('P%02d', n)`. Replace it with a
    constant, or move the choice back out of the script into Python
    where each thread picks from its own pre-read snapshot, and the
    eight ids stop being eight.
    """
    replica_a.create(RACE, track="bahrain", race_type="quick")

    thread_count = 8
    barrier = threading.Barrier(thread_count)
    car_ids = {}
    clients = []

    def joiner(index):
        client = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
        clients.append(client)
        store = LobbyStore(redis_client=client)
        # register_script does not touch the network, so every thread is
        # already loaded and waiting when the barrier releases.
        barrier.wait(timeout=5)
        car_ids[index] = store.join(
            RACE, f"p_{index}", {"username": f"u{index}"}, max_players=thread_count
        )[2]

    threads = [
        threading.Thread(target=joiner, args=(i,)) for i in range(thread_count)
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
    finally:
        for client in clients:
            client.close()

    assert len(car_ids) == thread_count, "every concurrent join must have returned"
    assert len(set(car_ids.values())) == thread_count, (
        f"eight concurrent new joins shared a default car_id: "
        f"{sorted(car_ids.values())} -- one player's bot now drives two cars"
    )
    stored = {p["car_id"] for p in replica_a.players(RACE).values()}
    assert stored == set(car_ids.values()), (
        "the car_ids join() reported must be the ones actually stored"
    )


def test_a_rejoin_without_a_car_id_keeps_the_one_the_player_already_has(
    replica_a, replica_b
):
    """A re-join must not silently rename the player. Round 3 recomputed
    the default on every join, so pressing the frontend's Join button a
    second time (the natural way to change starting compound -- and it
    never sends a car_id, frontend/src/lib/api.ts) moved that player to
    a different engine identity.

    The player here holds a car_id of their own choosing, which is what
    makes this test about the re-join rule specifically: "lowest label
    nobody else holds" would answer "P01" and rename them, so only the
    re-join branch can keep VEL-01.

    Red line: `player_data.car_id = existing.car_id` in _JOIN_SCRIPT.
    """
    replica_a.create(RACE, track="bahrain")
    _, _, first = replica_a.join(RACE, "p_a", {"username": "alex", "car_id": "VEL-01"})

    # Same player, no car_id -- exactly what the frontend sends.
    is_new, _count, rejoined = replica_b.join(
        RACE, "p_a", {"username": "alex", "starting_compound": "SOFT"}
    )

    assert is_new is False
    assert rejoined == first == "VEL-01", (
        f"a re-join reassigned {first!r} -> {rejoined!r}; car_id is an "
        f"engine identity, not a label the server may re-pick at will"
    )
    assert replica_b.players(RACE)["p_a"]["car_id"] == "VEL-01"


def test_a_new_player_after_a_rejoin_does_not_collide_with_it(replica_a):
    """The harm NEW-1 actually reproduced, end to end and with no
    concurrency at all: A joins, B joins, A re-joins, C joins -- and C
    was handed the same label the re-join had just moved A onto. Both
    reached _build_job_and_manifest, and engine/race.py keys
    self.strategies and self.belief_models by car_id, so one player's
    bot drove both cars, sealed into a manifest and a replay hash.

    Red line: round 3's `player_data.car_id = string.format("P%02d",
    count + 1)` -- the unconditional default this round replaced. Round
    4 replaced that one line with two independent guards (keep a
    re-join's existing id; derive a new player's from the lowest unheld
    label), and for THIS four-call sequence either guard alone is enough
    to prevent the collision, so each has its own single-line test above
    and below. This one pins the reported behaviour end to end.
    """
    replica_a.create(RACE, track="bahrain")
    replica_a.join(RACE, "p_a", {"username": "alex"})
    replica_a.join(RACE, "p_b", {"username": "bo"})
    replica_a.join(RACE, "p_a", {"username": "alex"})  # re-join, no car_id
    replica_a.join(RACE, "p_c", {"username": "cass"})

    car_ids = [p["car_id"] for p in replica_a.players(RACE).values()]
    assert sorted(car_ids) == ["P01", "P02", "P03"], (
        f"lobby ended with {sorted(car_ids)} -- two players sharing a car_id "
        f"means engine/race.py keys one strategy and one BeliefModel for both"
    )


def test_a_default_car_id_fills_the_lowest_label_nobody_holds(replica_a):
    """A default derived from the player count stops being unique the
    moment any label is out of sequence -- an explicit "P01" from one
    player is enough. The lowest unheld label is chosen instead.

    Red line: the `while taken[string.format('P%02d', n)] do` loop in
    _JOIN_SCRIPT. Replace it with `count + 1` and the second join here
    collides with the first.
    """
    replica_a.create(RACE, track="bahrain")
    replica_a.join(RACE, "p_a", {"username": "alex", "car_id": "P02"})

    _, _, car_id = replica_a.join(RACE, "p_b", {"username": "bo"})

    assert car_id == "P01", f"expected the lowest free label, got {car_id!r}"
    _, _, third = replica_a.join(RACE, "p_c", {"username": "cass"})
    assert third == "P03"


def test_join_refuses_a_car_id_another_player_in_the_lobby_holds(
    replica_a, replica_b
):
    """The same collision by its other route: an explicitly-requested
    car_id somebody else already holds. Checking for it in the handler
    would reopen the read-then-write window the script exists to close,
    so the refusal lives in the script.

    Red line: the `elseif taken[player_data.car_id] then` branch in
    _JOIN_SCRIPT.
    """
    from backend.state.lobby import CarIdTakenError

    replica_a.create(RACE, track="bahrain")
    replica_a.join(RACE, "p_a", {"username": "alex", "car_id": "VEL-01"})

    with pytest.raises(CarIdTakenError):
        replica_b.join(RACE, "p_b", {"username": "bo", "car_id": "VEL-01"})

    assert set(replica_a.players(RACE)) == {"p_a"}, (
        "the refused join must not have been written at all"
    )


def test_a_player_may_resend_the_car_id_it_already_holds(replica_a):
    """The refusal above must not lock a player out of their own
    re-join: a client that echoes back the car_id it was given (or asks
    for the one it already has) is not a collision."""
    replica_a.create(RACE, track="bahrain")
    replica_a.join(RACE, "p_a", {"username": "alex", "car_id": "VEL-01"})

    is_new, count, car_id = replica_a.join(
        RACE, "p_a", {"username": "alex", "car_id": "VEL-01"}
    )

    assert (is_new, count, car_id) == (False, 1, "VEL-01")
