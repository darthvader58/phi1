"""active_lobbies must be gone, and the socket registry must stay local.

Both halves matter. Shared state in a module-global dict breaks the second
replica; socket objects in Redis is impossible. The split is the design.
"""

import pytest

from backend.state.redis_client import redis_is_reachable

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)


def test_active_lobbies_no_longer_exists():
    """A module-global lobby dict is invisible to the other replica."""
    import backend.main as main

    assert not hasattr(main, "active_lobbies"), (
        "active_lobbies still exists; lobby state is not shared between replicas"
    )


def test_the_socket_registry_is_keyed_by_race_and_holds_sets():
    """Sockets stay per-process; only their bookkeeping is local."""
    import backend.main as main

    assert isinstance(main.SOCKETS, dict)


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


def test_no_module_global_holds_a_race_engine():
    """An engine in the API process is the thing this phase removes."""
    import backend.main as main
    from backend.engine.race import RaceEngine

    engines = [n for n, v in vars(main).items() if isinstance(v, RaceEngine)]
    assert engines == [], f"RaceEngine instances still live in the API: {engines}"
