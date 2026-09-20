"""A worker saying "I am still here", with an expiry.

The failure this exists for is not a worker crashing loudly. It is the
opposite: matches quietly stop finishing while every probe stays green. That
has now happened twice by different routes -- a vanished consumer group
(3636b01) and a poison job at the head of the queue -- and in both the worker
process was alive, Redis and Mongo were reachable, and `/ready` reported
healthy the whole time. Nothing anywhere said "no worker has made progress".

A TTL key is the smallest thing that cannot lie about that. The worker writes
it with an expiry on every pass of its loop; if the worker stops looping, the
key expires on its own and the absence is the signal. A counter the worker
increments could stay at its last value forever and look the same as a busy
worker; a key with a TTL cannot.

Read by backend/observability/health.py, which reports the count on /ready.
It deliberately does NOT make /ready fail: an API replica with no worker
behind it can still serve every read endpoint, and 503-ing every replica
because the worker fleet is down would turn a worker outage into a total
one. The number is there to be alerted on, not to take traffic away.
"""

from typing import List

from ..sandbox.isolation import DEFAULT_WALL_SECONDS
from ..state.redis_client import get_redis

KEY_PREFIX = "piwall:worker:alive:"

# The TTL must exceed the longest a live worker can legitimately go without
# writing one, or a busy worker reports as dead. That interval is one match:
# process_one blocks inside run_match_isolated for up to DEFAULT_WALL_SECONDS
# and touches nothing else while it does. Twice the wall budget leaves room
# for the persist and publish either side of it and still notices a dead
# worker within a couple of minutes. Derived rather than written as a number
# for the same reason RECLAIM_MIN_IDLE_MS is: raising the wall budget must
# not silently start reporting live workers as dead.
TTL_SECONDS = DEFAULT_WALL_SECONDS * 2


def beat(worker_name: str, redis_client=None, ttl_seconds: int = TTL_SECONDS) -> None:
    """Record that this worker is alive, for ttl_seconds."""
    client = redis_client or get_redis()
    client.set(f"{KEY_PREFIX}{worker_name}", "1", ex=ttl_seconds)


def stop(worker_name: str, redis_client=None) -> None:
    """Drop this worker's key on a clean shutdown.

    Waiting out the TTL instead would report a deliberately stopped worker as
    live for two more minutes, which is exactly the window a rolling deploy
    happens in -- the one time an operator most needs the count to be true.
    """
    client = redis_client or get_redis()
    client.delete(f"{KEY_PREFIX}{worker_name}")


def live_workers(redis_client=None) -> List[str]:
    """Names of workers whose heartbeat has not expired.

    SCAN rather than KEYS, for the reason LobbyStore.list_open gives: KEYS
    blocks Redis across the whole keyspace, and this runs on every /ready
    poll.
    """
    client = redis_client or get_redis()
    return sorted(
        key[len(KEY_PREFIX):]
        for key in client.scan_iter(match=f"{KEY_PREFIX}*", count=100)
    )
