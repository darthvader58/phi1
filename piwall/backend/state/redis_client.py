"""The one place a Redis connection is made.

Redis holds what must outlive a single API process: lobby state, the match job
queue, and match lifecycle events. Two API replicas share all of it, so any
state that stays in a module-global dict is state the second replica cannot
see.
"""

import os
from typing import Optional

import redis

REDIS_URL = os.environ.get("REDIS_URL") or "redis://127.0.0.1:6379/0"

_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    """Return the process-wide client, creating it on first use.

    One client, therefore one connection pool. A client per call would open a
    fresh pool each time and exhaust Redis's connection limit under load.

    decode_responses=True so callers get `str` rather than `bytes`; without it
    every read site has to remember to decode, and the one that forgets
    compares a bytes key against a str and silently never matches.
    """
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            health_check_interval=30,
        )
    return _client


def close_redis() -> None:
    """Drop the client so the next get_redis() builds a fresh one."""
    global _client
    if _client is not None:
        try:
            _client.close()
        finally:
            _client = None


def redis_is_reachable(timeout: float = 0.5) -> bool:
    """Whether Redis actually answers, not whether REDIS_URL is set.

    Tests gate on this. The short timeout matters because it runs at
    collection time, so a slow probe is paid by every session whether or not
    Redis exists.
    """
    try:
        probe = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
        )
        try:
            return probe.ping() is True
        finally:
            probe.close()
    except Exception:
        return False
