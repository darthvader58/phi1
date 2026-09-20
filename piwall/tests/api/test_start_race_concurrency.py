"""Only one /start may win, and shutdown must not drop the one that did.

These are the two halves of the same window. Between /start and enqueue a
match exists only as an in-memory asyncio task on one API replica: nothing
is in the queue and nothing durable says the match is coming. Two callers
entering that window at once used to both start it (F2), and a replica
stopping inside it used to lose it outright (F3).
"""

import asyncio
import time

import pytest

from backend.state.lobby import OPEN_STATUSES, LobbyStore
from backend.state.redis_client import redis_is_reachable

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)


@pytest.fixture
def lobby_id():
    store = LobbyStore()
    race_id = f"m_cas_{int(time.time() * 1000)}"
    store.delete(race_id)
    store.create(race_id, track="bahrain", race_type="quick")
    yield race_id
    store.delete(race_id)


# ── the compare-and-set itself ────────────────────────────────────────────

def test_only_one_caller_wins_the_transition(lobby_id):
    """The whole point. Two replicas both read status "lobby"; only one may
    be told it made the move.

    Red line: the `field_if` branch's `if not allowed then return false end`
    in _MUTATE_SCRIPT (backend/state/lobby.py). Delete it and both callers
    are told yes, which is exactly the read-check-write this replaces.
    """
    a, b = LobbyStore(), LobbyStore()
    results = [a.set_status_if(lobby_id, "countdown", ("lobby",)),
               b.set_status_if(lobby_id, "countdown", ("lobby",))]
    assert results.count(True) == 1, (
        f"exactly one caller may win the lobby->countdown transition, "
        f"got {results}"
    )
    assert a.get(lobby_id)["status"] == "countdown"


def test_the_transition_actually_happens_for_the_winner(lobby_id):
    """Guards the opposite failure: a compare-and-set that always refuses
    would pass the test above vacuously."""
    store = LobbyStore()
    assert store.set_status_if(lobby_id, "countdown", ("lobby",)) is True
    assert store.get(lobby_id)["status"] == "countdown"


def test_a_terminal_status_is_never_overwritten(lobby_id):
    """The abort path's use of it: a race the worker has already finished
    must not be marked aborted by a late failure on the API side.

    Red line: the `expected` argument threaded into set_status_if's
    `field_if` args. Pass an empty allow-list check (or call plain
    set_status) and "finished" is overwritten.
    """
    store = LobbyStore()
    store.set_status(lobby_id, "finished")
    assert store.set_status_if(lobby_id, "aborted", OPEN_STATUSES) is False
    assert store.get(lobby_id)["status"] == "finished"


def test_a_missing_lobby_answers_no_rather_than_raising():
    """"The lobby is gone" and "someone else got there first" are the same
    answer to "may I proceed", and a KeyError at this call site would turn
    an expired lobby into a 500."""
    store = LobbyStore()
    assert store.set_status_if("m_does_not_exist_at_all", "countdown",
                               ("lobby",)) is False


# ── F2 at the endpoint ────────────────────────────────────────────────────

def test_a_second_start_is_refused_rather_than_spawning_a_second_race(
    monkeypatch, lobby_id, throwaway_session
):
    """F2 end to end.

    Both callers pass start_race's snapshot check -- they read the same
    lobby -- and used to both spawn _run_race. Two _run_races build two
    jobs for one race with two DIFFERENT random seeds; the loser's
    save_manifest raises and its except block marked the winner's live race
    aborted, permanently if it landed after the worker's finish.

    Red line: `if not LOBBIES.set_status_if(race_id, "countdown",
    ("lobby",)): raise HTTPException(400, ...)` in main.start_race. Put
    `LOBBIES.set_status(race_id, "countdown")` back and `spawned` reaches 2.
    """
    import backend.main as main
    from backend.db import crud
    from fastapi import HTTPException

    monkeypatch.setattr(main, "SessionLocal", lambda: throwaway_session)
    race = crud.create_race(throwaway_session, "bahrain", "quick",
                            owner_id="p_owner")

    store = LobbyStore()
    store.delete(race.id)
    store.create(race.id, track="bahrain", race_type="quick")

    spawned = []
    monkeypatch.setattr(main, "_spawn_background",
                        lambda coro: spawned.append(coro) or coro.close())
    monkeypatch.setattr(main, "authenticate",
                        lambda key: {"id": "p_owner", "username": "owner"})

    # THE WINDOW, made explicit. start_race's own `lobby["status"] !=
    # "lobby"` check reads a snapshot, and the whole defect is that two
    # replicas take that snapshot before either has written -- so both see
    # "lobby". Calling the endpoint twice in sequence does not reproduce
    # that: the second call re-reads and is refused by the snapshot check,
    # which passes even with the compare-and-set removed. Pinning the read
    # to the pre-write snapshot is what puts both callers inside the window
    # at once, leaving the compare-and-set as the only thing that can tell
    # them apart.
    real_get = main.LOBBIES.get
    snapshot = real_get(race.id)
    assert snapshot["status"] == "lobby"
    monkeypatch.setattr(main.LOBBIES, "get",
                        lambda rid: dict(snapshot) if rid == race.id
                        else real_get(rid))

    async def both():
        refusals = 0
        for _ in range(2):
            try:
                await main.start_race(race.id, x_api_key="k")
            except HTTPException as exc:
                assert exc.status_code == 400
                refusals += 1
        return refusals

    try:
        refusals = asyncio.run(both())
        assert len(spawned) == 1, (
            f"exactly one _run_race may be spawned for one race, "
            f"got {len(spawned)}"
        )
        assert refusals == 1
    finally:
        store.delete(race.id)
        db = main.SessionLocal()
        try:
            db.db.races.delete_many({"id": race.id})
        finally:
            db.close()


# ── F3: shutdown must not abandon a countdown ─────────────────────────────

def test_shutdown_waits_for_work_already_in_hand():
    """F3.

    `lifespan` cancelled the event relay and drained sockets but never
    looked at `_background_tasks`, so a replica stopping during the 5s
    countdown took the match with it: no job ever enqueued, and a lobby
    left at "countdown" that nothing reaps until its 6h TTL. A rolling
    restart is exactly what triggers it, and rolling restarts are why two
    replicas exist.

    Red line: the `await asyncio.wait(tasks, timeout=timeout)` in
    main._finish_background_tasks. Remove the await (return immediately)
    and `finished` stays empty.
    """
    import backend.main as main

    finished = []

    async def slow_work():
        await asyncio.sleep(0.2)
        finished.append("enqueued")

    async def scenario():
        main._spawn_background(slow_work())
        # Not awaited directly -- this is the shutdown call, exactly as
        # lifespan makes it.
        return await main._finish_background_tasks(timeout=5.0)

    drained = asyncio.run(scenario())
    assert finished == ["enqueued"], (
        "shutdown returned before work in hand had finished -- that work is "
        "a match that was promised to a player and never enqueued"
    )
    assert drained == 1


def test_shutdown_is_bounded_and_says_what_it_gave_up_on(caplog):
    """The timeout has to exist too: shutdown that waits forever is a
    different outage. A task still running at the deadline is cancelled and
    named, so the lost match is at least recoverable by hand.

    Red line: the `for task in pending: ... task.cancel()` loop in
    main._finish_background_tasks.
    """
    import backend.main as main

    async def never_finishes():
        await asyncio.sleep(30)

    async def scenario():
        task = main._spawn_background(never_finishes())
        drained = await main._finish_background_tasks(timeout=0.05)
        # Read INSIDE the running loop. asyncio.run cancels whatever is
        # still pending as it closes the loop, so a check made after it
        # returns reports "cancelled" whether or not this function did the
        # cancelling -- which is the assertion passing for the wrong reason.
        return drained, task.cancelling() > 0 or task.cancelled()

    with caplog.at_level("ERROR", logger="piwall"):
        drained, was_cancelled = asyncio.run(scenario())

    assert drained == 0
    assert was_cancelled, (
        "a task past the deadline must be cancelled by shutdown itself, not "
        "left running into interpreter teardown"
    )
    assert any("did not finish before shutdown" in r.message
               for r in caplog.records)


def test_a_failing_background_task_does_not_break_shutdown():
    """Best effort, like drain_sockets: a task raising must not be the
    reason ASGI shutdown itself fails.

    Red line: the `exc = task.exception(); if exc is not None: logger.error`
    handling in main._finish_background_tasks -- replace it with a bare
    `task.result()` and the exception propagates out of shutdown.
    """
    import backend.main as main

    async def explodes():
        raise RuntimeError("boom")

    async def scenario():
        main._spawn_background(explodes())
        return await main._finish_background_tasks(timeout=5.0)

    assert asyncio.run(scenario()) == 1


def test_a_late_failure_does_not_abort_a_race_the_worker_already_finished(
    monkeypatch, throwaway_session
):
    """The other half of F2, and the one that was permanent.

    _run_race's except block ran its abort writes unconditionally. The
    worker's own update_race_status("finished") heals that only if the
    worker wins the race to write; if the abort lands afterwards -- one
    slow Mongo round trip is enough -- a correctly finished race is marked
    aborted forever, with its results still sitting in race_results.

    Red line: `LOBBIES.set_status_if(race_id, "aborted",
    OPEN_LOBBY_STATUSES)` in main._run_race's except block. Put
    `LOBBIES.set_status(race_id, "aborted")` back and the finished lobby is
    overwritten.
    """
    import backend.main as main
    from backend.db import crud

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(main.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(main, "SessionLocal", lambda: throwaway_session)

    race_id = f"m_late_abort_{int(time.time() * 1000)}"
    store = LobbyStore()
    store.delete(race_id)
    store.create(race_id, track="bahrain", race_type="quick")
    store.add_player(race_id, "p1", {
        "username": "alex", "car_id": "USR-01",
        "code": "def my_strategy(state, my_car):\n    return {'pit': False, "
                "'compound': 'MEDIUM'}\n",
        "starting_compound": "MEDIUM",
    })

    db = main.SessionLocal()
    try:
        db.db.races.insert_one({"id": race_id, "track": "bahrain",
                                "race_type": "quick", "status": "lobby",
                                "finished_at": None, "started_at": None})
    finally:
        db.close()

    # The worker finishes the match while this coroutine is mid-flight...
    def finish_then_explode(_db, _manifest):
        store.set_status(race_id, "finished")
        inner = main.SessionLocal()
        try:
            crud.update_race_status(inner, race_id, "finished")
        finally:
            inner.close()
        raise ValueError("simulated manifest conflict")

    monkeypatch.setattr(crud, "save_manifest", finish_then_explode)

    try:
        asyncio.run(main._run_race(race_id))

        assert store.get(race_id)["status"] == "finished", (
            "a race the worker finished must not be marked aborted by a "
            "late failure on the API side"
        )
        db = main.SessionLocal()
        try:
            assert crud.get_race(db, race_id).status == "finished"
        finally:
            db.close()
    finally:
        store.delete(race_id)
        db = main.SessionLocal()
        try:
            db.db.races.delete_many({"id": race_id})
        finally:
            db.close()
