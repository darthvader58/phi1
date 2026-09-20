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
import time
from typing import Optional, Tuple

from redis.exceptions import ResponseError

from ..sandbox.isolation import DEFAULT_WALL_SECONDS
from ..state.redis_client import get_redis

STREAM = "piwall:jobs:match"
GROUP = "match-workers"

# Where a job that cannot be run goes to stop being the queue's problem.
#
# A separate stream rather than a flag on the entry: an entry only leaves the
# pending list by being acked, and an acked entry is indistinguishable from a
# completed one. Copying it here first means "this match never ran, and why"
# survives the ack that stops it being re-served forever.
DEAD_LETTER_STREAM = "piwall:jobs:match:dead"
DEAD_LETTER_MAXLEN = 1_000

# How many times one entry may be delivered before the queue gives up on it.
#
# The cap is the whole point: without one, a job that fails the same way on
# every delivery is re-served ahead of all new work forever, because
# reclaim_stalled always returns the idle-most entry and a permanently
# failing entry is always the idle-most one. Three is enough to ride out a
# transient (an OOM-killed child, a Mongo blip) and small enough that a
# genuinely poisoned job leaves the head of the queue in minutes, not never.
MAX_DELIVERIES = 3

# The idle threshold at which a job is considered abandoned and handed to
# another worker.
#
# It is derived from the match wall-clock budget rather than written as a
# number, because the two are not independent: a worker legitimately holds an
# entry, without touching Redis, for as long as run_match_isolated is allowed
# to take. The previous 30s default sat BELOW DEFAULT_WALL_SECONDS = 60, so
# the docstring's "well above a match's runtime" safety margin did not exist
# -- a slow match was reclaimed underneath a worker that was still running it,
# which doubles the compute on exactly the matches that are already expensive
# and is the mechanism that turns one failing job into queue starvation.
# Deriving it here means raising the wall budget cannot silently re-open that
# gap.
RECLAIM_MIN_IDLE_MS = DEFAULT_WALL_SECONDS * 1000 * 2

# XACK does not remove an entry -- an acked job stays in the stream forever --
# so without a cap this grows by one entry per match for the life of the
# deployment. That is unbounded Redis memory, and it is also one of the ways
# the NOGROUP condition below gets triggered: a stream large enough to hit
# `maxmemory` can have its key evicted, taking the consumer group with it.
#
# The cap has a floor that is not about memory at all. MAXLEN trims by age,
# not by acked-ness, so trimming an entry that is still PENDING would lose
# that match outright -- the precise thing this queue exists to make
# impossible. The cap must therefore sit far above the worst backlog an
# outage could build, not merely above the steady state. Ten thousand unrun
# matches is orders of magnitude beyond anything that could accumulate before
# someone noticed, and `approximate=True` lets Redis keep MORE than this (it
# trims on radix-node boundaries) and never fewer, so the error is always in
# the safe direction.
#
# It also bounds the re-delivery described on _with_group below.
STREAM_MAXLEN = 10_000


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

    def _with_group(self, operation):
        """Run a group-scoped command, re-creating the group if it vanished.

        Every group-scoped command -- XREADGROUP, XAUTOCLAIM, XPENDING --
        fails with NOGROUP once the stream key or the group itself is gone,
        and `_ensure_group` used to run in `__init__` and nowhere else.
        `run_forever` builds one queue at startup and keeps it for the life of
        the process, so a stream that disappeared afterwards wedged that
        worker permanently: every iteration raised, the loop logged it as a
        job failure and backed off to a 30s ceiling, and the process never
        exited -- so `restart: unless-stopped` never fired and no probe
        noticed. `/ready` reports healthy throughout, because Redis and Mongo
        genuinely are.

        The other half is what made it silent: XADD does NOT fail. `enqueue`
        recreates the stream key without a group and keeps returning entry
        ids, so the API goes on accepting races and stacking up jobs no
        worker can read. Matches simply stop finishing.

        Recreating the group here is safe in both cases that produce NOGROUP,
        though for different reasons:

          * stream key lost (a Redis restart with no persistence, an
            eviction, a DEL) -- every acked entry went with it, so
            `_ensure_group`'s id="0" picks up exactly the jobs that
            accumulated during the outage and nothing else.
          * group destroyed, stream intact (an operator XGROUP DESTROY) --
            id="0" re-delivers everything still in the stream, including
            already-finished matches. That is safe rather than merely
            tolerable: Phase 1 makes the same manifest produce a
            byte-identical replay, and `_persist_result` is idempotent per
            match and per player, so a re-run writes the same bytes under
            the same id. STREAM_MAXLEN is what bounds how much of it there
            is to re-run.

        Creating the group at "$" instead would avoid the re-delivery and
        would also silently discard every job enqueued during the outage,
        which is the failure this whole module exists to prevent. id="0" is
        the correct trade.

        Only these three commands need the guard: XACK and XLEN return 0 on a
        missing key or group rather than raising, so `ack` and `depth` cannot
        hit it (verified against Redis, not assumed).

        Retried exactly once. A second NOGROUP means the group could not be
        created, which is a real Redis problem and belongs in the caller's
        exception handler rather than in a loop here.
        """
        try:
            return operation()
        except ResponseError as exc:
            if "NOGROUP" not in str(exc):
                raise
            self._ensure_group()
            return operation()

    def enqueue(self, job: dict) -> str:
        """Add a job. Values are JSON-encoded because streams store strings.

        Capped at STREAM_MAXLEN, approximately -- see that constant for why
        the cap exists and why it must stay far above any real backlog.
        """
        return self._redis.xadd(
            STREAM, {"payload": json.dumps(job)},
            maxlen=STREAM_MAXLEN, approximate=True,
        )

    def claim(self, consumer: str, block_ms: int = 2000) -> Optional[Tuple[str, dict]]:
        """Take the next undelivered job, blocking briefly.

        ">" means "entries never delivered to this group". Blocking rather than
        polling keeps an idle worker from spinning on Redis.
        """
        response = self._with_group(lambda: self._redis.xreadgroup(
            GROUP, consumer, {STREAM: ">"}, count=1, block=block_ms
        ))
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

    def reclaim_stalled(self, consumer: str, min_idle_ms: int = RECLAIM_MIN_IDLE_MS):
        """Take over jobs whose consumer has been silent too long.

        The idle threshold is the whole safety margin: too low and a live
        worker's job gets stolen mid-match, too high and a crashed worker's
        match waits. The default is derived from the match wall-clock budget
        -- see RECLAIM_MIN_IDLE_MS, and do not replace it with a literal.

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
        _next, entries, _deleted = self._with_group(lambda: self._redis.xautoclaim(
            STREAM, GROUP, consumer, min_idle_time=min_idle_ms, count=1
        ))
        return [(eid, json.loads(f["payload"])) for eid, f in entries]

    def delivery_count(self, entry_id: str) -> int:
        """How many times this entry has been handed to a consumer.

        Redis counts this for us: every XREADGROUP and every XAUTOCLAIM of an
        entry increments its `times-delivered`. Counting in the worker instead
        would reset on every worker restart -- and a worker that dies holding
        a poison job restarts constantly, which is precisely when the count
        has to keep going up.

        Returns 0 for an entry that is no longer pending (already acked, or
        never existed), so a caller cannot read "not pending" as "delivered
        many times" or vice versa.
        """
        entries = self._with_group(lambda: self._redis.xpending_range(
            STREAM, GROUP, min=entry_id, max=entry_id, count=1
        ))
        if not entries:
            return 0
        return int(entries[0]["times_delivered"])

    def dead_letter(self, entry_id: str, job: dict, reason: str) -> str:
        """Move a job that cannot be run out of the queue, with its reason.

        Copy FIRST, then ack. The other order loses the job entirely if this
        process dies in between -- and a job nobody can run is still the only
        record that a match was promised to a player.

        The reason is composed by the caller from our own constants, never
        from child-process output: everything crossing that boundary passed
        through untrusted code (see sandbox/isolation.py's ChildFailed).
        """
        entry = self._redis.xadd(
            DEAD_LETTER_STREAM,
            {
                "payload": json.dumps(job),
                "reason": reason,
                "entry_id": entry_id,
                "failed_at": str(time.time()),
            },
            maxlen=DEAD_LETTER_MAXLEN, approximate=True,
        )
        self.ack(entry_id)
        return entry

    def dead_letter_depth(self) -> int:
        return int(self._redis.xlen(DEAD_LETTER_STREAM))

    def dead_letters(self, count: int = 100) -> list:
        """The dead-lettered jobs, oldest first. For operators and tests."""
        return [
            {"entry_id": eid, "reason": fields.get("reason"),
             "job": json.loads(fields["payload"])}
            for eid, fields in self._redis.xrange(DEAD_LETTER_STREAM, count=count)
        ]

    def pending_count(self) -> int:
        """Jobs delivered but not yet acked — i.e. in flight or abandoned."""
        return int(self._with_group(
            lambda: self._redis.xpending(STREAM, GROUP)
        )["pending"])

    def depth(self) -> int:
        return int(self._redis.xlen(STREAM))
