"""No per-process module global may hold per-match mutable state, and the
one process-local registry that legitimately exists (SOCKETS) must never be
mistaken for a place to look up shared truth.

Both halves matter. Shared state in a module-global dict breaks the second
replica; socket objects in Redis is impossible. The split is the design.

Review round 2 (task-8-review.md, F9/F10/F11) found the first version of
this file checked names rather than properties: a rename of
active_lobbies to anything else would have passed, `isinstance(dict)` says
nothing about keying or content, and there was never a module-global
RaceEngine even before this phase, so that check could not fail. Every
test below is written to have an actual line that, if deleted or reverted,
turns it red.
"""

import pytest

from backend.state.redis_client import redis_is_reachable

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)


def test_no_hidden_module_global_holds_per_match_state():
    """The load-bearing property active_lobbies violated, checked by value
    rather than by name.

    A rename of active_lobbies to _lobbies (or anything else) while keeping
    the in-process dict would pass a check for the literal name
    "active_lobbies" and still be exactly the bug this phase removes: a
    lobby that exists on whichever replica happened to create it and
    nowhere else. This instead looks for ANY module-level dict other than
    SOCKETS itself (identity-compared, not name-compared, so renaming
    SOCKETS would not accidentally exempt a second dict either) -- shared
    per-match state belongs in Redis via LOBBIES, never in a process-local
    dict.
    """
    import backend.main as main

    # TRACKS and BUILTIN_BOTS are read-only reference tables imported from
    # elsewhere (backend/data/tracks.py, backend/engine/bots.py) and never
    # mutated by anything in this module -- static configuration, not
    # per-match state, and unlike active_lobbies neither is ever written
    # to after import. Dunders are Python's own module machinery, not
    # application state. Anything else that is a dict, under any name, and
    # is not SOCKETS itself, is exactly the shape of bug this checks for.
    allowed_names = {"TRACKS", "BUILTIN_BOTS"}
    offending = [
        name for name, value in vars(main).items()
        if isinstance(value, dict)
        and value is not main.SOCKETS
        and name not in allowed_names
        and not (name.startswith("__") and name.endswith("__"))
    ]
    assert offending == [], (
        f"unexpected module-level dict(s) found: {offending} -- shared "
        f"per-match state belongs in Redis (LOBBIES), never a process-local "
        f"dict"
    )


def test_the_socket_registry_is_keyed_by_race_and_holds_sets():
    """SOCKETS actually holds a set of sockets under its race id key, and
    _discard_socket removes the entry once it is empty rather than leaking
    it forever (F2's second half: `race_id in SOCKETS` staying true after
    the last spectator left was what made the membership check wrong in
    both directions).
    """
    import backend.main as main

    assert isinstance(main.SOCKETS, dict)

    race_id = "r_test_socket_registry_shape"
    assert race_id not in main.SOCKETS
    fake_socket = object()

    main.SOCKETS.setdefault(race_id, set()).add(fake_socket)
    assert isinstance(main.SOCKETS[race_id], set)
    assert fake_socket in main.SOCKETS[race_id]

    main._discard_socket(race_id, fake_socket)
    assert race_id not in main.SOCKETS, (
        "an empty socket set must be removed, not left behind as a leak"
    )


def test_main_uses_a_lobby_store():
    import backend.main as main
    from backend.state.lobby import LobbyStore

    assert isinstance(main.LOBBIES, LobbyStore)


def test_main_holds_a_job_queue_and_an_event_bus():
    import backend.main as main
    from backend.jobs.events import MatchEvents
    from backend.jobs.queue import MatchJobQueue

    assert isinstance(main.JOBS, MatchJobQueue)
    assert isinstance(main.EVENTS, MatchEvents)
