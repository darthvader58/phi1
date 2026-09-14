"""The process that runs matches.

Moving execution here is what lets the API scale: an API replica no longer
holds a match in memory for the length of a race, so a second replica is just
another stateless reader.

Ordering rule, and the reason for it: run, persist, publish, THEN ack. Acking
earlier would make a crash lose the match silently. Acking last means the
worst case is a match running twice — harmless, because Phase 1 made a
manifest produce a byte-identical replay, so the second run writes the same
bytes under the same id.
"""

import hashlib
import os
import signal
import socket
import time
from dataclasses import asdict
from typing import Callable, Optional

from pymongo.errors import DuplicateKeyError

from .db import crud
from .db.models import to_namespace
from .determinism.canonical import canonical_json
from .determinism.manifest import Participant, build_manifest
from .determinism.replay import REPLAY_FORMAT_VERSION
from .engine.bots import BUILTIN_BOTS
from .sandbox.match_job import run_match_isolated
from .jobs.events import MatchEvents
from .jobs.queue import MatchJobQueue
from .observability.logging import configure_logging, get_logger, match_context
from .season.elo import compute_elo_updates
from .state.lobby import LobbyStore

log = get_logger("piwall.worker")

# LobbyStore's constructor makes no Redis round trip (register_script only
# computes a local SHA1; the script itself loads lazily on first EVAL), so
# building this once at import time carries none of the risk that keeping
# backend.main's MatchJobQueue eager did (see backend/main.py's _get_jobs).
LOBBIES = LobbyStore()

WORKER_NAME = f"{socket.gethostname()}-{os.getpid()}"

_shutting_down = False

# Cap for run_forever's exponential backoff after a failed process_one --
# e.g. Redis unreachable. Without a cap, a long-broken dependency would push
# the delay towards minutes; 30s is well inside "an operator will notice and
# is watching logs anyway" territory.
_MAX_BACKOFF_SECONDS = 30.0
_BACKOFF_STEP_SECONDS = 0.5


def _request_shutdown(_signum, _frame) -> None:
    """Finish the job in hand, then stop. Never abandon a claimed match."""
    global _shutting_down
    _shutting_down = True


def _sleep_unless_shutting_down(seconds: float) -> None:
    """Sleep in short steps, stopping early if a shutdown signal lands.

    A single time.sleep(seconds) would make SIGTERM/SIGINT during backoff
    wait out the whole delay before the main loop checks _shutting_down
    again; stepping keeps shutdown responsive even while backing off.
    """
    remaining = seconds
    while remaining > 0 and not _shutting_down:
        time.sleep(min(_BACKOFF_STEP_SECONDS, remaining))
        remaining -= _BACKOFF_STEP_SECONDS


def _manifest_from_job(job: dict):
    participants = [
        Participant(
            slot=int(p["slot"]),
            player_id=p.get("player_id"),
            bot_version_id=p.get("bot_version_id"),
            code_sha256=p.get("code_sha256"),
            house_bot=p.get("house_bot"),
        )
        for p in job["participants"]
    ]
    return build_manifest(
        match_id=job["match_id"],
        seed=int(job["seed"]),
        track=job["track"],
        participants=participants,
    )


def _spec_from_job(job: dict) -> dict:
    """Build the runnable spec run_match_isolated expects.

    track_physics is built HERE, in the worker, rather than shipped in the
    job: build_track_physics reads the frozen calibration artifact from
    disk, and a TrackPhysics object in a Redis job payload would have to be
    serialized for no benefit. The worker image carries the same committed
    calibration as the API, so both produce identical physics.
    """
    from .engine.build import build_track_physics

    spec = {
        "track": job["track"],
        "track_physics": build_track_physics(job["track"]),
        "seed": int(job["seed"]),
        "cars": [],
    }
    for participant in sorted(job["participants"], key=lambda p: int(p["slot"])):
        position = int(participant["slot"]) + 1
        house_bot = participant.get("house_bot")
        # A house bot's starting compound is fixed per bot (three of the five
        # builtins start on SOFT, not MEDIUM) -- both replay_from_manifest and
        # main.py read it from BUILTIN_BOTS rather than defaulting it, and this
        # spec must agree or it simulates a different race on the same seed.
        # "MEDIUM" is only a real default for a player car, which has no such
        # fixed compound of its own.
        default_compound = (
            BUILTIN_BOTS[house_bot]["starting_compound"] if house_bot else "MEDIUM"
        )
        car = {
            "car_id": participant.get("car_id") or house_bot,
            "player_id": participant.get("player_id") or house_bot,
            "start_position": position,
            "starting_compound": participant.get("starting_compound", default_compound),
        }
        if participant.get("code"):
            car["code"] = participant["code"]
        elif house_bot:
            car["bot_id"] = house_bot
        spec["cars"].append(car)
    return spec


def _replay_bytes_from_result(result: dict, manifest) -> bytes:
    """Canonical replay bytes for a match_job.run_match_isolated() result.

    determinism/replay.py's replay_bytes() takes a live RaceResult and calls
    car_state_to_dict()/asdict() on its contents itself — the shape
    replay_from_manifest needs, since it re-runs the race in this same
    process. run_match_isolated's result already crossed a pickle boundary
    out of a sandboxed child process, so run_match() on the other side of
    that boundary already applied car_state_to_dict()/asdict() before
    returning (see sandbox/match_job.py) — calling either again here would
    raise, since a plain dict is neither a CarState nor a dataclass. This
    mirrors replay_bytes's payload shape directly from the dict instead of
    calling it, using the exact same format version and canonical encoding
    so the two stay one contract.
    """
    payload = {
        "format_version": REPLAY_FORMAT_VERSION,
        "manifest": asdict(manifest),
        "track": result["track"],
        "total_laps": result["total_laps"],
        "final_standings": result["standings"],
        "events": result["events"],
        "lap_data": result["lap_data"],
        "weather_history": result["weather_history"],
    }
    return canonical_json(payload)


def _replay_sha256_of(replay: bytes) -> str:
    return "sha256:" + hashlib.sha256(replay).hexdigest()


def _persist_result(db, result: dict) -> None:
    """Write everything a finished match must leave behind, exactly once
    per player, no matter how many times this exact result is redelivered.

    Spec 4.1 puts result persistence here -- `result -> Mongo (atomic Elo)
    -> pub/sub notify` -- and it belongs in the worker rather than the API
    because the worker is the only place that ever holds the full result;
    the API enqueues a job and later learns only a race id and a replay
    hash (see backend/main.py's `_stream_stored_replay`).

    Round 1 of this fix keyed a single "already done?" guard on
    crud.get_race_results, the FIRST thing the block wrote. A worker that
    died between that write and the Elo loop left results rows behind, so
    every later delivery read the guard as "already done" and skipped the
    Elo update and lap data forever -- lost, not merely delayed. There is
    no single marker written last that a guard could key on instead
    without the same problem recurring one line earlier the next time this
    function grows a new write. So this makes every individual write
    idempotent on its own terms instead of gating the whole block behind
    one flag:

    - save_replay_hash and update_race_status are already idempotent ($set
      against a fixed key -- a repeat writes the same value over itself).
    - save_race_results now replaces this race's rows wholesale (delete
      then insert) rather than only ever inserting, so re-running it after
      a partial or full previous run lands on the same final rows instead
      of a duplicate set.
    - save_race_data is already idempotent ($set).
    - Each player's Elo transition is its own unit: the elo_history row is
      written BEFORE the rating moves, using the unique index on
      (player_id, race_id) as the idempotency token. A DuplicateKeyError
      means an earlier delivery already recorded this player's transition
      for this race, so this delivery skips moving their rating and
      continues with the rest -- a raise here would strand the whole job
      unacked forever (the exact bug Task 7's ordering was chasing), and a
      partial redelivery must not let one already-applied player block
      every other player's update.

    That per-player ordering also fixes what round 1 got backwards:
    computing the new rating and writing it BEFORE the history row meant
    the index could only report a double-apply after the damage was
    already done, not prevent it.
    """
    match_id = result["match_id"]

    update = crud.save_replay_hash(db, match_id, result["replay_sha256"])
    if update.matched_count == 0:
        # save_replay_hash is update-only and never upserts, so a
        # match_id with no manifest document already means this write
        # touched nothing. "Ack only after the result is durable" is
        # enforced here, not just in ordering: silently continuing would
        # let process_one persist(), publish() and ack() a job whose
        # result was never actually written down.
        raise RuntimeError(
            f"save_replay_hash matched no manifest document for "
            f"match {match_id!r} -- refusing to treat this result as "
            f"persisted"
        )

    # The lobby's terminal status is durable, cross-replica state, and it
    # must not depend on whether any API replica happens to have a
    # spectator socket open for this race -- that was F2: SOCKETS gating a
    # write nothing else guards. The worker is the one process that always
    # runs exactly once per finished match, so it is where this belongs.
    # A KeyError means the lobby already aged past its 6h TTL (or, in a
    # test, was never created at all) -- the database row above is the
    # durable record either way, so there is nothing left to update.
    try:
        LOBBIES.set_status(match_id, "finished")
    except KeyError:
        pass

    crud.update_race_status(db, match_id, "finished")
    # crud reads standings and events by attribute; the isolated child
    # hands them back as plain dicts, so adapt at this boundary.
    crud.save_race_results(
        db, match_id, [to_namespace(c) for c in result["standings"]]
    )
    crud.save_race_data(
        db, match_id, result["lap_data"],
        [to_namespace(e) for e in result["events"]],
    )

    # k_factor's source used to be the in-memory lobby (lobby.race_type),
    # which no longer exists by the time a match finishes. The job payload
    # does not carry race_type either -- adding it there would be a second
    # copy that could drift from the one create_race() already wrote
    # durably before this job ever existed, so the race document is read
    # instead, as the single authority for it. A missing race document is
    # refused rather than guessed: silently defaulting to k=32 would apply
    # the wrong K-factor to what may have been a season race and write
    # that durably, with nothing to say it was ever in doubt.
    race = crud.get_race(db, match_id)
    if race is None:
        raise RuntimeError(
            f"no race document for {match_id!r} -- refusing to guess a "
            f"k_factor rather than silently apply the wrong one"
        )
    race_type = getattr(race, "race_type", "quick")
    k_factor = 48.0 if race_type == "season" else 32.0

    standings_tuples = [
        (c["player_id"], c["position"], c["retired"])
        for c in result["standings"]
    ]
    current_ratings = {}
    for pid, _, _ in standings_tuples:
        # If this player's transition for this exact race was already
        # recorded (a prior delivery got this far before dying on a later
        # player), elo_before there is their true pre-race rating.
        # crud.get_player_by_id's CURRENT rating is not a safe stand-in
        # once that row exists -- it already reflects this same race's
        # own effect, and using it as another player's opponent baseline
        # would compute that player's delta against a result that has
        # already happened rather than the state before the race.
        already = crud.get_elo_history_entry(db, pid, match_id)
        if already is not None:
            current_ratings[pid] = already.elo_before
        else:
            player = crud.get_player_by_id(db, pid)
            current_ratings[pid] = player.elo if player else 1200.0

    new_ratings = compute_elo_updates(standings_tuples, current_ratings, k_factor)

    for pid, new_elo in new_ratings.items():
        player = crud.get_player_by_id(db, pid)
        if player is None:
            continue
        old_elo = current_ratings[pid]
        try:
            # History first: this insert is the idempotency token, not a
            # record written after the fact. Only once it succeeds -- i.e.
            # only once we know this player's transition for this race has
            # never been recorded before -- does the rating actually move.
            crud.save_elo_history(db, pid, match_id, old_elo, new_elo)
        except DuplicateKeyError:
            # Another delivery already recorded this player's transition
            # for this race. Moving their rating again on top of one that
            # already moved is exactly the drift this index exists to
            # stop -- skip this player and let the rest of the loop finish
            # normally rather than raising and stranding the whole job.
            continue
        crud.update_player_elo(db, pid, new_elo)


def process_one(
    queue: MatchJobQueue,
    events: MatchEvents,
    persist: Callable[[dict], None],
    consumer: str = WORKER_NAME,
    block_ms: int = 2000,
    min_idle_ms: int = 30000,
) -> Optional[str]:
    """Run at most one match. Returns its match id, or None if idle.

    Stalled jobs are checked first: a match abandoned by a dead worker has
    already made a client wait, so it goes ahead of new work.
    """
    stalled = queue.reclaim_stalled(consumer, min_idle_ms=min_idle_ms)
    if stalled:
        entry_id, job = stalled[0]
    else:
        claimed = queue.claim(consumer, block_ms=block_ms)
        if claimed is None:
            return None
        entry_id, job = claimed

    match_id = job["match_id"]
    with match_context(match_id):
        log.info("running match")
        manifest = _manifest_from_job(job)
        # run_match_isolated, NOT replay_from_manifest. The latter raises
        # NotImplementedError for any participant without a house_bot,
        # because replaying a player bot needs its source and Phase 1
        # deliberately left that to this phase. run_match_isolated takes the
        # source in the spec, which is exactly what a real match has.
        result = run_match_isolated(_spec_from_job(job))
        replay = _replay_bytes_from_result(result, manifest)
        digest = _replay_sha256_of(replay)

        # run, persist, publish, THEN ack -- in that order. Acking first
        # would let a crash between ack and save lose the match with no
        # record; acking last means the worst case is a harmless re-run
        # under the same match id (see module docstring).
        persist({"match_id": match_id, "replay_sha256": digest,
                 "replay_bytes": replay, "manifest": manifest,
                 "standings": result["standings"], "lap_data": result["lap_data"],
                 "events": result["events"]})
        events.publish({"type": "match_finished", "match_id": match_id,
                        "replay_sha256": digest})
        queue.ack(entry_id)
        log.info("match complete")
        return match_id


def run_forever(consumer: str = WORKER_NAME) -> None:
    configure_logging("worker")
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    from .db.models import create_db_engine, init_db, mongo_url

    # mongo_url() rather than importing main: the worker serves no HTTP and
    # has no business importing FastAPI, slowapi and the whole API surface.
    session_factory = init_db(create_db_engine(mongo_url()))

    def persist(result: dict) -> None:
        db = session_factory()
        try:
            _persist_result(db, result)
        finally:
            db.close()

    # Built once, at startup: MatchJobQueue's constructor calls
    # xgroup_create every time, and building one per job would pay a
    # redundant BUSYGROUP round trip on every single claim.
    queue, events = MatchJobQueue(), MatchEvents()
    log.info("worker ready")
    consecutive_failures = 0
    while not _shutting_down:
        try:
            process_one(queue, events, persist, consumer=consumer)
            consecutive_failures = 0
        except Exception:
            # An unacked job stays pending and is reclaimed; a crashed
            # worker loop would stop draining the queue entirely.
            log.exception("job failed, leaving it pending for reclaim")
            consecutive_failures += 1
            # Backoff, not a bare retry: with Redis unreachable, a bare
            # `while not _shutting_down: try/except` loop spins as fast as
            # the exception can be raised and caught -- millions of
            # iterations a second, logging a traceback on every one. The
            # sleep is broken into short steps and re-checks _shutting_down
            # between them so a signal during backoff still stops promptly
            # instead of waiting out the full delay.
            _sleep_unless_shutting_down(min(2 ** consecutive_failures, _MAX_BACKOFF_SECONDS))
    log.info("worker stopped")


if __name__ == "__main__":
    run_forever()
