"""Match lifecycle notifications, over Redis pub/sub.

A worker finishes a match. The client watching it holds a WebSocket on some
API replica — possibly not the one that enqueued the job, and never the
worker itself. Pub/sub fits because the message must reach EVERY replica:
each one has its own socket set and only it can write to those sockets.

This is deliberately not a queue. A queue would deliver the notification to
one subscriber, and the wrong replica would receive it.

Pub/sub is fire-and-forget: a replica that is down misses the event. That is
acceptable because the replay is already persisted before publishing, so a
client can always fetch the finished match over HTTP. The event is an
optimisation for live viewers, not the source of truth. Do not layer
delivery guarantees on top of this — if a caller needs "every replica
eventually learns", that belongs in the persisted replay path, not here.
"""

import json
from typing import Optional

from ..state.redis_client import get_redis

CHANNEL = "piwall:events:match"

# How long subscribe() waits for Redis to confirm the SUBSCRIBE before
# returning. This is a local round-trip, normally sub-millisecond; the
# generous ceiling is only there so a genuinely stalled connection raises
# by timing out downstream rather than hanging forever.
_SUBSCRIBE_CONFIRM_TIMEOUT = 5.0


class MatchEvents:
    def __init__(self, redis_client=None):
        self._redis = redis_client or get_redis()

    def publish(self, event: dict) -> int:
        """Publish one event. Returns the number of subscribers that got it."""
        return int(self._redis.publish(CHANNEL, json.dumps(event)))

    def subscribe(self):
        """Subscribe to the channel. Caller owns closing the returned pubsub.

        Blocks until Redis has confirmed the subscription before returning.
        `subscribe()`'s own SUBSCRIBE command is written to the pubsub
        connection's socket asynchronously — it does not wait for a reply —
        so a `publish()` issued right after this call, on a different
        connection, can otherwise reach the server before the subscription
        does and the event is simply never delivered. Reading the
        subscribe-confirmation message here forces this call to wait for
        that round trip, so by the time it returns, the subscription is live
        on the server. `ignore_subscribe_messages=True` is what keeps that
        confirmation from ever surfacing to callers of `listen()`.
        """
        pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(CHANNEL)
        pubsub.get_message(timeout=_SUBSCRIBE_CONFIRM_TIMEOUT)
        return pubsub

    def listen(self, pubsub, timeout: float = 1.0) -> Optional[dict]:
        """Next event, or None if none arrives within the timeout."""
        message = pubsub.get_message(timeout=timeout)
        if not message or message.get("type") != "message":
            return None
        return json.loads(message["data"])
