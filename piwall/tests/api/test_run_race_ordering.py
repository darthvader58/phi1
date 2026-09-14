"""_run_race must save the manifest before enqueuing the job, never after.

backend/worker.py's persist step only ever UPDATEs the manifest row for a
match id -- save_replay_hash never upserts, and it now raises rather than
ack a match with nothing durable written (see backend/worker.py's
_persist_result). If the job reached a worker before a manifest existed
for it, every persist attempt would match zero documents, the job would
fail, get reclaimed, and fail again forever: a poison pill.

Review round 2, F8, called this invariant out by name as covered only by
throwaway scripts, not a real test. This patches crud.save_manifest and
the job queue to record call order, so it is enforced without needing a
real worker to actually drain the queue.
"""

import time

import pytest

from backend.state.redis_client import redis_is_reachable

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)

PLAYER_CODE = (
    "def my_strategy(state, my_car):\n"
    "    return {'pit': False, 'compound': 'MEDIUM'}\n"
)


async def _fast_sleep(_seconds):
    return None


class _RecordingJobs:
    def __init__(self, order):
        self._order = order

    def enqueue(self, job):
        self._order.append(("enqueue", job))
        return "fake-entry-id"


def test_run_race_saves_the_manifest_before_enqueuing_the_job(monkeypatch):
    import backend.main as main
    from backend.db import crud

    # The countdown's five real 1-second sleeps would make this test slow
    # for no reason -- what is under test is call order, not timing.
    monkeypatch.setattr(main.asyncio, "sleep", _fast_sleep)

    race_id = f"m_manifest_before_enqueue_{int(time.time() * 1000)}"
    main.LOBBIES.delete(race_id)
    main.LOBBIES.create(race_id, track="bahrain", race_type="quick")
    main.LOBBIES.add_player(race_id, "p1", {
        "username": "alex", "car_id": "USR-01", "code": PLAYER_CODE,
        "starting_compound": "MEDIUM",
    })

    order = []
    real_save_manifest = crud.save_manifest

    def recording_save_manifest(db, manifest):
        order.append(("save_manifest", manifest.match_id))
        return real_save_manifest(db, manifest)

    monkeypatch.setattr(crud, "save_manifest", recording_save_manifest)
    monkeypatch.setattr(main, "_get_jobs", lambda: _RecordingJobs(order))

    import asyncio
    try:
        asyncio.run(main._run_race(race_id))
    finally:
        main.LOBBIES.delete(race_id)
        db = main.SessionLocal()
        try:
            db.db.races.delete_many({"id": race_id})
            db.db.manifests.delete_many({"match_id": race_id})
        finally:
            db.close()

    steps = [step for step, _ in order]
    assert steps == ["save_manifest", "enqueue"], (
        f"save_manifest must run before enqueue; got call order {steps}"
    )


def test_run_race_marks_the_race_aborted_on_a_manifest_conflict(monkeypatch):
    """F13: _run_race had no error handling and its task handle was
    dropped. If crud.save_manifest raised (e.g. a ValueError from a
    manifest conflict -- two replicas both passing start_race's
    non-atomic status check and racing to start the same lobby with
    different seeds), the race was left at status "running" with no job
    ever enqueued, forever, with the traceback surfacing only at task GC.
    """
    import backend.main as main
    from backend.db import crud

    monkeypatch.setattr(main.asyncio, "sleep", _fast_sleep)

    race_id = f"m_run_race_abort_{int(time.time() * 1000)}"
    main.LOBBIES.delete(race_id)
    main.LOBBIES.create(race_id, track="bahrain", race_type="quick")
    main.LOBBIES.add_player(race_id, "p1", {
        "username": "alex", "car_id": "USR-01", "code": PLAYER_CODE,
        "starting_compound": "MEDIUM",
    })

    def exploding_save_manifest(db, manifest):
        raise ValueError("simulated manifest conflict")

    enqueued = []
    monkeypatch.setattr(crud, "save_manifest", exploding_save_manifest)
    monkeypatch.setattr(main, "_get_jobs", lambda: type(
        "Rec", (), {"enqueue": staticmethod(lambda job: enqueued.append(job))}
    )())

    import asyncio
    try:
        # Must not raise out of _run_race -- that would be exactly the
        # "surfaces only at task GC" failure mode this fix closes.
        asyncio.run(main._run_race(race_id))

        assert enqueued == [], "no job should ever have been enqueued"
        lobby = main.LOBBIES.get(race_id)
        assert lobby["status"] == "aborted", (
            f"race must be marked aborted, not left at {lobby['status']!r} "
            f"forever"
        )

        db = main.SessionLocal()
        try:
            race_doc = crud.get_race(db, race_id)
        finally:
            db.close()
        assert race_doc is None or getattr(race_doc, "status", None) != "running", (
            "the DB-side race status must not be left at running either"
        )
    finally:
        main.LOBBIES.delete(race_id)
        db = main.SessionLocal()
        try:
            db.db.races.delete_many({"id": race_id})
            db.db.manifests.delete_many({"match_id": race_id})
        finally:
            db.close()
