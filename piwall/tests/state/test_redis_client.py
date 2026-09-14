"""One place makes a Redis connection, and one probe says whether it works.

The reachability probe exists because every later test in this phase gates on
it. Gating on `REDIS_URL` being set would repeat a mistake this repo has
already made: under compose the variable is always present and points at a
service that may not be running, which turned five skips into five errors and
three minutes of connection timeouts.
"""

import pytest

from backend.state.redis_client import (
    REDIS_URL,
    close_redis,
    get_redis,
    redis_is_reachable,
)

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(),
    reason="needs a reachable Redis; the rest of the suite is hermetic",
)


def test_redis_url_has_a_local_default():
    assert REDIS_URL.startswith("redis://")


def test_get_redis_returns_a_working_client():
    client = get_redis()
    assert client.ping() is True


def test_get_redis_reuses_one_connection_pool():
    """A new pool per call would exhaust connections under load."""
    assert get_redis() is get_redis()


def test_values_round_trip_as_strings_not_bytes():
    """decode_responses must be on, or every caller has to .decode()."""
    client = get_redis()
    client.set("piwall:test:roundtrip", "hello")
    try:
        assert client.get("piwall:test:roundtrip") == "hello"
    finally:
        client.delete("piwall:test:roundtrip")


def test_close_redis_allows_a_fresh_client_afterwards():
    first = get_redis()
    close_redis()
    second = get_redis()
    assert second is not first
    assert second.ping() is True


def test_reachability_is_false_for_a_dead_address(monkeypatch):
    """The probe must test reachability, not configuration."""
    import backend.state.redis_client as mod

    monkeypatch.setattr(mod, "REDIS_URL", "redis://127.0.0.1:59998/0")
    mod.close_redis()
    try:
        assert mod.redis_is_reachable(timeout=0.2) is False
    finally:
        mod.close_redis()
