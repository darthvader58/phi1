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
from dataclasses import asdict
from typing import Callable, Optional

from .determinism.canonical import canonical_json
from .determinism.manifest import Participant, build_manifest
from .determinism.replay import REPLAY_FORMAT_VERSION
from .sandbox.match_job import run_match_isolated
from .jobs.events import MatchEvents
from .jobs.queue import MatchJobQueue
from .observability.logging import configure_logging, get_logger, match_context

log = get_logger("piwall.worker")

WORKER_NAME = f"{socket.gethostname()}-{os.getpid()}"

_shutting_down = False


def _request_shutdown(_signum, _frame) -> None:
    """Finish the job in hand, then stop. Never abandon a claimed match."""
    global _shutting_down
    _shutting_down = True


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
        car = {
            "car_id": participant.get("car_id") or participant.get("house_bot"),
            "player_id": participant.get("player_id") or participant.get("house_bot"),
            "start_position": position,
            "starting_compound": participant.get("starting_compound", "MEDIUM"),
        }
        if participant.get("code"):
            car["code"] = participant["code"]
        elif participant.get("house_bot"):
            car["bot_id"] = participant["house_bot"]
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
                 "replay_bytes": replay, "manifest": manifest})
        events.publish({"type": "match_finished", "match_id": match_id,
                        "replay_sha256": digest})
        queue.ack(entry_id)
        log.info("match complete")
        return match_id


def run_forever(consumer: str = WORKER_NAME) -> None:
    configure_logging("worker")
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    from .db.crud import save_replay_hash
    from .db.models import create_db_engine, init_db, mongo_url

    # mongo_url() rather than importing main: the worker serves no HTTP and
    # has no business importing FastAPI, slowapi and the whole API surface.
    session_factory = init_db(create_db_engine(mongo_url()))

    def persist(result: dict) -> None:
        db = session_factory()
        try:
            save_replay_hash(db, result["match_id"], result["replay_sha256"])
        finally:
            db.close()

    # Built once, at startup: MatchJobQueue's constructor calls
    # xgroup_create every time, and building one per job would pay a
    # redundant BUSYGROUP round trip on every single claim.
    queue, events = MatchJobQueue(), MatchEvents()
    log.info("worker ready")
    while not _shutting_down:
        try:
            process_one(queue, events, persist, consumer=consumer)
        except Exception:
            # An unacked job stays pending and is reclaimed; a crashed
            # worker loop would stop draining the queue entirely.
            log.exception("job failed, leaving it pending for reclaim")
    log.info("worker stopped")


if __name__ == "__main__":
    run_forever()
