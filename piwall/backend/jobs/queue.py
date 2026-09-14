"""The match job queue, on a Redis Stream with a consumer group.

Why not a list. `LPOP` removes the job at the instant it is handed to a
worker, so a worker killed immediately afterwards takes the match with it and
nothing records that a match existed. The phase gate for this work is "a
worker restart mid-match loses no match", which a list cannot satisfy.

A consumer group keeps every delivered entry in a per-consumer pending list
until `XACK`. A worker that dies leaves its entry there, and `XAUTOCLAIM`
hands it to a live worker after an idle threshold.

Delivery is therefore at-least-once: a match can run twice. That is safe here
because of Phase 1, not by luck — the same manifest produces a byte-identical
replay, so a second execution writes the same bytes under the same match id.
Determinism is what lets this queue be simple.
"""

import json
from typing import Optional, Tuple

from redis.exceptions import ResponseError

from ..state.redis_client import get_redis

STREAM = "piwall:jobs:match"
GROUP = "match-workers"


class MatchJobQueue:
    def __init__(self, redis_client=None):
        self._redis = redis_client or get_redis()
        self._ensure_group()

    def _ensure_group(self) -> None:
        """Create the group, tolerating the race between two workers starting.

        xgroup_create raises BUSYGROUP if the group already exists; that is
        the normal case for every worker after the first, not an error.
        mkstream=True so the group can be created before any job exists;
        otherwise the first worker to start crashes on a missing stream.
        """
        try:
            self._redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def enqueue(self, job: dict) -> str:
        """Add a job. Values are JSON-encoded because streams store strings."""
        return self._redis.xadd(STREAM, {"payload": json.dumps(job)})

    def claim(self, consumer: str, block_ms: int = 2000) -> Optional[Tuple[str, dict]]:
        """Take the next undelivered job, blocking briefly.

        ">" means "entries never delivered to this group". Blocking rather than
        polling keeps an idle worker from spinning on Redis.
        """
        response = self._redis.xreadgroup(
            GROUP, consumer, {STREAM: ">"}, count=1, block=block_ms
        )
        if not response:
            return None
        _stream, entries = response[0]
        if not entries:
            return None
        entry_id, fields = entries[0]
        return entry_id, json.loads(fields["payload"])

    def ack(self, entry_id: str) -> None:
        """Mark a job done. Until this runs, the job is recoverable."""
        self._redis.xack(STREAM, GROUP, entry_id)

    def reclaim_stalled(self, consumer: str, min_idle_ms: int = 30000):
        """Take over jobs whose consumer has been silent too long.

        The idle threshold is the whole safety margin: too low and a live
        worker's job gets stolen mid-match, too high and a crashed worker's
        match waits. 30s default is well above a match's runtime.

        count=1, not a larger batch: XAUTOCLAIM reassigns every entry it
        returns to `consumer` and resets each one's idle timer, whether or
        not the caller goes on to process it. A caller that only acts on the
        first entry of a bigger batch (this queue's only caller, worker.py's
        process_one, processes one match per call) would silently strand the
        rest -- reassigned to a consumer that never touches them, with a
        freshly reset idle timer, unreachable by reclaim_stalled again until
        another full min_idle_ms passes. One entry per call means nothing is
        ever claimed without also being handed back to a caller that acts on
        it immediately.
        """
        _next, entries, _deleted = self._redis.xautoclaim(
            STREAM, GROUP, consumer, min_idle_time=min_idle_ms, count=1
        )
        return [(eid, json.loads(f["payload"])) for eid, f in entries]

    def pending_count(self) -> int:
        """Jobs delivered but not yet acked — i.e. in flight or abandoned."""
        return int(self._redis.xpending(STREAM, GROUP)["pending"])

    def depth(self) -> int:
        return int(self._redis.xlen(STREAM))
