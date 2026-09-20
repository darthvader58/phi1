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
from .determinism.manifest import Participant, build_manifest, manifest_sha256
from .determinism.replay import REPLAY_FORMAT_VERSION, ReplayHashConflict
from .engine.bots import BUILTIN_BOTS
from .sandbox.isolation import ChildFailed, LimitExceeded
from .sandbox.match_job import run_match_isolated
from .jobs import heartbeat
from .jobs.events import MatchEvents
from .jobs.queue import MAX_DELIVERIES, RECLAIM_MIN_IDLE_MS, MatchJobQueue
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

# What a spectator and the player are told when a match could not be run for
# a reason that is ours rather than theirs. Composed here, from our own
# words: the underlying text may have come out of an untrusted child process
# and may carry filesystem paths (see sandbox/isolation.py's ChildFailed,
# which draws the same line for the same reason). The real detail goes to
# the log.
UNRUNNABLE_REASON = (
    "This match could not be run and has been abandoned. No result was "
    "recorded and no ratings changed."
)


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
            # `or`, not a dict default: a participant row can carry the
            # key with value None, in which case a default argument never
            # fires and None reaches engine.add_car as the compound.
            "starting_compound": (
                participant.get("starting_compound") or default_compound
            ),
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


class ManifestMismatch(RuntimeError):
    """This worker's manifest is not the one the match was created with.

    An ordinary Exception, not a SandboxSignal: it is raised in the worker
    about a mismatch between two processes, never through bot code, so it has
    no business bypassing anyone's `except Exception:` (see
    determinism/signals.py for what does).
    """


def _persist_abort(db, match_id: str, reason: str) -> None:
    """Record that a match will never run, and why.

    A match the engine refuses to run is an outcome, not a gap. Left
    unrecorded, the race document sits at "running" and the lobby at
    "running" until its TTL, so the player whose bot was rejected is shown a
    race that is perpetually about to finish rather than the reason it did
    not. That is also what makes the job safe to ack: the queue may only stop
    tracking a job once its outcome is durable, and "aborted, because X" is
    as durable an outcome as a result.

    The race document is written FIRST and the Redis lobby SECOND, the same
    order and for the same reason as the finished path below.

    Deliberately does NOT touch ratings or results. Nothing was simulated, so
    there is no standing to record and no Elo to move; a redelivery that
    reaches here twice writes the same two fields over themselves.
    """
    crud.abort_race(db, match_id, reason)
    # As in _persist_result: a KeyError means the lobby aged past its TTL,
    # and the race document above is the durable record either way.
    try:
        LOBBIES.set_status(match_id, "aborted")
    except KeyError:
        pass


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
    - save_race_results upserts one row per car, keyed by (race_id,
      player_id), rather than only ever inserting, so re-running it after
      a partial or full previous run lands on the same final rows instead
      of a duplicate set. (Round 3, N6: an earlier delete-then-insert
      version of this made it idempotent too, but briefly left the
      collection with zero rows for an already-finished race that
      GET /api/race/{id} and /api/season both read live -- see crud.py's
      docstring on save_race_results.) Its own DuplicateKeyError -- two
      workers' upserts both deciding "no match, insert" for the same key
      -- is retried inside save_race_results rather than escaping into
      here (round 4, closing NEW-6), because the retry turns it into the
      plain update it would have been had the two not overlapped.
    - save_race_data is already idempotent ($set).
    - Each player's Elo transition is its own unit: the elo_history row is
      written BEFORE the rating moves, using the unique index on
      (player_id, race_id) as the idempotency token -- which is also what
      lets the rating move by an atomic $inc rather than a $set, so two
      DIFFERENT matches finishing for one player at once cannot lose an
      update (see crud.apply_player_elo). A DuplicateKeyError
      means an earlier delivery already recorded this player's transition
      for this race -- a raise here would strand the whole job unacked
      forever (the exact bug Task 7's ordering was chasing), so it is
      caught rather than left to escape.

    That per-player ordering also fixes what round 1 got backwards:
    computing the new rating and writing it BEFORE the history row meant
    the index could only report a double-apply after the damage was
    already done, not prevent it.

    Round 2 caught the DuplicateKeyError and skipped that player entirely.
    That moved the vulnerable window rather than closing it: a crash
    between the elo_history insert and the rating write left
    the row written and the rating unmoved, and skipping on redelivery
    left it unmoved forever -- elo_history then permanently disagreeing
    with players.elo for that player. The row is the record of intent,
    not of completion, so a DuplicateKeyError now reconciles the rating
    against the existing row's elo_after instead of skipping (see the Elo
    loop below). That is idempotent either way: if the prior delivery had
    in fact finished cleanly, reconciling writes the same value already
    there; if it crashed mid-write, reconciling is what finally applies
    the move the row already recorded.
    """
    match_id = result["match_id"]

    # A match that never produced a result takes the other path entirely --
    # there is no replay hash to store, no standings and no Elo. One
    # callable rather than two so every caller of process_one gets the abort
    # path automatically; an `abort=` parameter with a default would be a
    # parameter a test (and then a deployment) could forget to pass, and the
    # symptom of forgetting would be a silently unrecorded abort.
    if result.get("outcome") == "aborted":
        _persist_abort(db, match_id, result["reason"])
        return

    # The worker REBUILDS the manifest from the job rather than receiving
    # it, and five of its fields (engine_version, ruleset_version,
    # calibration_id, python_version, dep_lock_sha256) come from this
    # process's ambient environment rather than from the job. Under one
    # image they always agree with the API's. Under a rolling deploy they do
    # not, and the replay hash written below would then be a hash of bytes
    # embedding a manifest that is NOT the one save_manifest persisted for
    # this match -- the same shape as the code_sha256 bug, one level up, and
    # nothing would notice. Comparing the digests is what makes it
    # impossible to not notice.
    stored_digest = crud.get_manifest_digest(db, match_id)
    computed_digest = manifest_sha256(result["manifest"])
    if stored_digest is not None and stored_digest != computed_digest:
        raise ManifestMismatch(
            f"match {match_id!r}: this worker reconstructed manifest "
            f"{computed_digest} but the stored manifest is {stored_digest}. "
            f"Refusing to record a replay hash against a manifest that is "
            f"not the one this match was created with."
        )

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

    # Every reason to refuse this job is established BEFORE any status is
    # marked finished. k_factor's source used to be the in-memory lobby
    # (lobby.race_type), which no longer exists by the time a match
    # finishes. The job payload does not carry race_type either -- adding
    # it there would be a second copy that could drift from the one
    # create_race() already wrote durably before this job ever existed, so
    # the race document is read instead, as the single authority for it. A
    # missing race document is refused rather than guessed: silently
    # defaulting to k=32 would apply the wrong K-factor to what may have
    # been a season race and write that durably, with nothing to say it
    # was ever in doubt.
    #
    # This read sits here, above the writes, rather than beside the Elo
    # loop that consumes it (round 3, N7's residual). It depends on
    # nothing any write below produces, and while it sat lower a job with
    # no race document marked the Redis lobby "finished" and only THEN
    # raised -- so /api/races reported the race finished while
    # GET /api/race/{id} 404'd on the DB read, and the job retried
    # forever in that state. Refusing first leaves the lobby untouched,
    # which is the honest report of a job that did not complete.
    race = crud.get_race(db, match_id)
    if race is None:
        raise RuntimeError(
            f"no race document for {match_id!r} -- refusing to guess a "
            f"k_factor rather than silently apply the wrong one"
        )
    race_type = getattr(race, "race_type", "quick")
    k_factor = 48.0 if race_type == "season" else 32.0

    # The lobby's terminal status is durable, cross-replica state, and it
    # must not depend on whether any API replica happens to have a
    # spectator socket open for this race -- that was F2: SOCKETS gating a
    # write nothing else guards. The worker is the one process that always
    # runs exactly once per finished match, so it is where this belongs.
    #
    # The race document is updated FIRST, the Redis lobby SECOND (N7,
    # fix round 3 -- round 2 had this backwards). A crash between the two
    # is otherwise self-healing on redelivery either way, but this
    # ordering means a reader who sees the lobby as "finished" (main.py's
    # get_race falls through to Mongo for a terminal lobby) is more likely
    # to find the race document already there too, not the other way
    # round.
    crud.update_race_status(db, match_id, "finished")
    # A KeyError means the lobby already aged past its 6h TTL (or, in a
    # test, was never created at all) -- the database row above is the
    # durable record either way, so there is nothing left to update.
    try:
        LOBBIES.set_status(match_id, "finished")
    except KeyError:
        pass

    # crud reads standings and events by attribute; the isolated child
    # hands them back as plain dicts, so adapt at this boundary.
    crud.save_race_results(
        db, match_id, [to_namespace(c) for c in result["standings"]]
    )
    crud.save_race_data(
        db, match_id, result["lap_data"],
        [to_namespace(e) for e in result["events"]],
    )

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
            first_application = True
        except DuplicateKeyError:
            # Another delivery already wrote this player's transition for
            # this race -- and may have crashed between that insert and
            # the rating write below, leaving the row written and the
            # rating unmoved. Skipping here (what round 2 did) left that
            # player stuck at their pre-race rating forever, permanently
            # contradicting their own elo_history row. The row is the
            # record of intent, not of completion: reconcile the rating
            # against the values it already committed to instead of
            # skipping.
            recorded = crud.get_elo_history_entry(db, pid, match_id)
            old_elo, new_elo = recorded.elo_before, recorded.elo_after
            first_application = False
        # Not a $set. Two DIFFERENT matches finishing for the same player
        # at once both read the same pre-race rating, and a $set from each
        # discards the other's change -- see crud.apply_player_elo, which
        # is why this carries first_application rather than just a value.
        crud.apply_player_elo(db, pid, old_elo, new_elo, first_application)


def _abandon(queue: MatchJobQueue, events: MatchEvents,
             persist: Callable[[dict], None], entry_id: str, job: dict,
             match_id: str, reason: str, log_note: str) -> str:
    """Record a match as never-run, tell its spectators, and retire the job.

    Same ordering rule as the happy path, and for the same reason: persist,
    publish, THEN take the entry out of the queue. If this process dies
    before the dead-letter write, the entry is still pending and a later
    delivery repeats all three steps -- every one of which is idempotent.
    The other order would let a crash between the ack and the write lose
    both the match and the explanation.
    """
    log.error("abandoning match: %s", log_note)
    persist({"outcome": "aborted", "match_id": match_id, "reason": reason})
    events.publish({"type": "match_aborted", "match_id": match_id,
                    "reason": reason})
    queue.dead_letter(entry_id, job, reason=log_note)
    return match_id


def process_one(
    queue: MatchJobQueue,
    events: MatchEvents,
    persist: Callable[[dict], None],
    consumer: str = WORKER_NAME,
    block_ms: int = 2000,
    min_idle_ms: int = RECLAIM_MIN_IDLE_MS,
) -> Optional[str]:
    """Run at most one match. Returns its match id, or None if idle.

    Stalled jobs are checked first: a match abandoned by a dead worker has
    already made a client wait, so it goes ahead of new work.

    Every way a match can fail to produce a result ends with the entry
    leaving the queue, because the entry is always re-served ahead of new
    work: `reclaim_stalled` returns the idle-most entry, and an entry that
    fails on every delivery is always the idle-most one by the time the loop
    comes round. So an entry that is never acked is not merely retried --
    it occupies the head of the queue forever and no other match on the
    deployment runs. One player submitting a bot that burns its CPU budget
    was enough to do that, with /ready green throughout.

    The three cases, classified rather than swept into one `except`:

    * LimitExceeded -- the bot breached its own CPU, wall-clock or memory
      budget. That is a *recorded outcome about that bot*, in the sense
      determinism/signals.py gives the term: a verdict, not a worker
      failure, and a reproducible one. Re-running it can only reach the same
      verdict, so it is retired on the first delivery. The player is told,
      in words composed here, that their bot exceeded its limits.
    * ChildFailed -- the child died for a reason that is OURS (an engine
      error, an OOM-killed process, a pickling failure). Its str() is
      deliberately generic because it reaches a spectator socket; the real
      text is on .detail and goes only to the log. It may be transient, so
      it is retried up to MAX_DELIVERIES and only then retired.
    * Anything else -- a Mongo blip in persist, a Redis blip in publish.
      Also retried to the cap, then retired. `test_the_result_is_persisted_
      before_the_ack` pins that the first such failure still leaves the job
      pending: the cap changes what happens on the third, not the first.

    Redis counts deliveries, not this process: a worker that dies holding a
    poison job restarts constantly, which is exactly when a counter living
    in worker memory would reset to zero on every pass.
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
        try:
            # run_match_isolated, NOT replay_from_manifest. The latter raises
            # NotImplementedError for any participant without a house_bot,
            # because replaying a player bot needs its source and Phase 1
            # deliberately left that to this phase. run_match_isolated takes
            # the source in the spec, which is exactly what a real match has.
            result = run_match_isolated(_spec_from_job(job))
            replay = _replay_bytes_from_result(result, manifest)
            digest = _replay_sha256_of(replay)

            # run, persist, publish, THEN ack -- in that order. Acking first
            # would let a crash between ack and save lose the match with no
            # record; acking last means the worst case is a harmless re-run
            # under the same match id (see module docstring).
            #
            # replay_bytes is deliberately not in this payload: _persist_result
            # reads only what is listed here, and passing the whole canonical
            # replay (potentially megabytes) read as though the body were being
            # stored, which it is not -- object storage for replay bodies is
            # Phase 4. The manifest IS passed, because _persist_result compares
            # its digest against the stored row.
            persist({"match_id": match_id, "replay_sha256": digest,
                     "manifest": manifest,
                     "standings": result["standings"],
                     "lap_data": result["lap_data"],
                     "events": result["events"]})
        except LimitExceeded as exc:
            return _abandon(
                queue, events, persist, entry_id, job, match_id,
                reason=str(exc), log_note=f"resource limit breached: {exc}",
            )
        except ReplayHashConflict as exc:
            # The most important event this system can observe: two
            # executions of one manifest produced different bytes. The first
            # hash and this one are both preserved on the manifest document
            # by save_replay_hash -- it refuses to overwrite rather than
            # $set over the difference. The match itself already completed
            # and persisted on the earlier delivery, so the race is NOT
            # aborted here; the job is simply retired, because no further
            # redelivery can resolve a disagreement that is deterministic in
            # this build.
            log.error("determinism break: %s", exc)
            queue.dead_letter(entry_id, job, reason=str(exc))
            return match_id
        except ChildFailed as exc:
            return _retry_or_abandon(
                queue, events, persist, entry_id, job, match_id,
                # exc.detail, never str(exc) or the child's text in the
                # reason: the detail can carry filesystem paths out of an
                # untrusted process, so it goes to the log and nowhere else.
                log_note=f"match child failed: {exc.detail}",
            )
        except Exception as exc:
            return _retry_or_abandon(
                queue, events, persist, entry_id, job, match_id,
                log_note=f"{type(exc).__name__}: {exc}",
            )
        events.publish({"type": "match_finished", "match_id": match_id,
                        "replay_sha256": digest})
        queue.ack(entry_id)
        log.info("match complete")
        return match_id


def _retry_or_abandon(queue: MatchJobQueue, events: MatchEvents,
                      persist: Callable[[dict], None], entry_id: str,
                      job: dict, match_id: str, log_note: str) -> str:
    """Leave the job pending for another attempt, until the cap is reached.

    Re-raising is the right answer for a failure that might be transient --
    the entry stays pending and reclaim_stalled serves it again. It is the
    wrong answer forever, which is what it used to be: re-raising
    unconditionally is what let one job occupy the head of the queue for the
    life of the deployment.

    The bare `raise` re-raises the exception process_one is currently
    handling. Python's "exception being handled" state belongs to the thread,
    not to the frame, so it is still set inside this call -- which is what
    keeps the original traceback intact instead of rebuilding it from a
    passed-in object.
    """
    deliveries = queue.delivery_count(entry_id)
    if deliveries < MAX_DELIVERIES:
        log.warning("match failed on delivery %d of %d: %s",
                    deliveries, MAX_DELIVERIES, log_note)
        raise
    return _abandon(queue, events, persist, entry_id, job, match_id,
                    reason=UNRUNNABLE_REASON,
                    log_note=f"{log_note} (after {deliveries} deliveries)")


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
            # Before the work, not after: a pass that fails still proves
            # this worker is looping, and that is exactly the state the
            # heartbeat exists to distinguish from a worker that has stopped
            # looping altogether. Written every pass rather than on a timer
            # so there is no second clock to get wrong -- the key's own TTL
            # is the clock.
            heartbeat.beat(consumer)
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
    try:
        heartbeat.stop(consumer)
    except Exception:
        # A dropped heartbeat key is cosmetic -- it expires on its own --
        # and must never be the reason a clean shutdown reports a failure.
        log.warning("could not clear the worker heartbeat", exc_info=True)
    log.info("worker stopped")


if __name__ == "__main__":
    run_forever()
