"""Writes that must survive concurrency and redelivery without lying.

Everything here is about a write that looked idempotent and was not, or
looked atomic and was not. Each is reachable in this phase's architecture
specifically because matches now complete asynchronously on a fleet rather
than serially inside one API process.

Every test runs against a throwaway database of its own and drops it
afterwards. Nothing here touches the shared one.
"""

import time
import uuid

import pytest

from backend.db import crud
from backend.db.models import MongoSession, init_db, mongo_url
from backend.determinism.manifest import Participant, build_manifest
from backend.determinism.replay import ReplayHashConflict


def _mongo_is_reachable() -> bool:
    from pymongo import MongoClient

    try:
        client = MongoClient(mongo_url(), serverSelectionTimeoutMS=500)
        try:
            client.admin.command("ping")
            return True
        finally:
            client.close()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _mongo_is_reachable(), reason="needs a reachable MongoDB"
)


def _redis_is_reachable() -> bool:
    """Only one test below needs Redis -- the abort path updates the Redis
    lobby as well as the race document, exactly as the finished path does.
    Imported lazily so this module's Mongo gate is not coupled to it."""
    from backend.state.redis_client import redis_is_reachable

    return redis_is_reachable()


@pytest.fixture
def db():
    """A database of this test's own, dropped afterwards.

    Never the shared one: these tests write ratings and race statuses, and
    an interrupted run must not leave anything behind in it.
    """
    from pymongo import MongoClient

    name = f"piwall_test_persistence_{uuid.uuid4().hex[:8]}"
    client = MongoClient(mongo_url())
    database = client[name]
    init_db(database)
    session = MongoSession(database)
    yield session
    session.close()
    client.drop_database(name)
    client.close()


def _manifest(match_id: str):
    return build_manifest(
        match_id=match_id, seed=1, track="bahrain",
        participants=[Participant(0, None, None, None, "VEL-01")],
    )


# ── F8: a redelivery must not move a recorded timestamp ────────────────────

def test_a_redelivered_finish_does_not_move_finished_at(db):
    """The queue is at-least-once, and an operator XGROUP DESTROY replays a
    whole stream's worth of already-finished matches. Either one used to
    walk `finished_at` forward on every pass -- the one write in
    _persist_result that was not value-idempotent.

    Red line: the `{"id": race_id, field: None}` filter in crud._stamp_once.
    Drop the `field: None` clause and the second call re-stamps.
    """
    race = crud.create_race(db, "bahrain", "quick")
    crud.update_race_status(db, race.id, "finished")
    first = crud.get_race(db, race.id).finished_at
    assert first is not None

    time.sleep(0.01)  # _now() has sub-second resolution; make a re-stamp visible
    crud.update_race_status(db, race.id, "finished")
    assert crud.get_race(db, race.id).finished_at == first


def test_a_redelivered_start_does_not_move_started_at(db):
    """started_at has the same shape and the same exposure.

    Red line: the `_STATUS_TIMESTAMP` entry for "running" going through
    crud._stamp_once rather than an unconditional $set.
    """
    race = crud.create_race(db, "bahrain", "quick")
    crud.update_race_status(db, race.id, "running")
    first = crud.get_race(db, race.id).started_at
    time.sleep(0.01)
    crud.update_race_status(db, race.id, "running")
    assert crud.get_race(db, race.id).started_at == first


def test_the_status_itself_still_moves(db):
    """The guard above must not have frozen the field it was protecting."""
    race = crud.create_race(db, "bahrain", "quick")
    crud.update_race_status(db, race.id, "running")
    crud.update_race_status(db, race.id, "finished")
    assert crud.get_race(db, race.id).status == "finished"


# ── abort_race ─────────────────────────────────────────────────────────────

def test_an_abort_records_a_reason_a_player_can_read(db):
    """Red line: the `"abort_reason": reason` field in crud.abort_race."""
    race = crud.create_race(db, "bahrain", "quick")
    crud.abort_race(db, race.id, "your bot exceeded the CPU limit")
    doc = crud.get_race(db, race.id)
    assert doc.status == "aborted"
    assert doc.abort_reason == "your bot exceeded the CPU limit"


def test_an_abort_cannot_overwrite_a_finished_race(db):
    """A finished race has results in race_results. Marking it aborted --
    what a redelivered abort, or the loser of a concurrent /start, used to
    do -- leaves those results unreachable behind an "aborted" status,
    permanently.

    Red line: the `"status": {"$ne": "finished"}` clause in crud.abort_race's
    filter. Remove it and the finished race is overwritten.
    """
    race = crud.create_race(db, "bahrain", "quick")
    crud.update_race_status(db, race.id, "finished")
    crud.abort_race(db, race.id, "too late")
    doc = crud.get_race(db, race.id)
    assert doc.status == "finished"
    assert getattr(doc, "abort_reason", None) is None


# ── F4: the rating write is atomic ─────────────────────────────────────────

def test_two_concurrent_matches_for_one_player_both_land(db):
    """The lost update, executed rather than reasoned about.

    Two DIFFERENT matches finish for the same player at once. Both read the
    same pre-race rating -- which is what actually happens, because each
    worker reads it before either has written -- and both apply their own
    delta. With a `$set` the second silently discards the first, while both
    elo_history rows persist, so the history and the rating disagree
    forever.

    Red line: the `{"$inc": {"elo": elo_after - elo_before}}` update in
    crud.apply_player_elo's first_application branch. Put
    `{"$set": {"elo": elo_after}}` back and this goes red by exactly the
    missing 10.0.
    """
    player = crud.create_player(db, f"p_{uuid.uuid4().hex[:8]}", "T")
    assert player.elo == 1200.0

    # Both workers read 1200.0 before either writes -- the interleaving is
    # the point, so it is written out rather than left to chance.
    crud.apply_player_elo(db, player.id, 1200.0, 1210.0, first_application=True)
    crud.apply_player_elo(db, player.id, 1200.0, 1190.0, first_application=True)

    assert crud.get_player_by_id(db, player.id).elo == pytest.approx(1200.0), (
        "both matches' deltas must land: +10 then -10 is 1200, not 1190"
    )


def test_a_reconciling_write_applies_an_outstanding_move(db):
    """The crash-recovery path: an elo_history row exists but the rating
    never moved. The redelivery has to finish the job.

    Red line: the `{"id": player_id, "elo": elo_before}` filter's `elo_before`
    value in crud.apply_player_elo. Change it to something the document
    never holds and the outstanding move is never applied.
    """
    player = crud.create_player(db, f"p_{uuid.uuid4().hex[:8]}", "T")
    applied = crud.apply_player_elo(
        db, player.id, 1200.0, 1225.0, first_application=False
    )
    assert applied
    assert crud.get_player_by_id(db, player.id).elo == pytest.approx(1225.0)


def test_a_reconciling_write_is_a_no_op_once_the_move_has_landed(db):
    """Redelivery of a match that already completed cleanly. The rating is
    already correct; writing again must change nothing -- and must not
    clobber a DIFFERENT match that has since moved it.

    Red line: the `"elo": elo_before` term in that same filter. Drop it (an
    unconditional $set, which is what this used to be) and the unrelated
    match's +40 below is erased.
    """
    player = crud.create_player(db, f"p_{uuid.uuid4().hex[:8]}", "T")
    crud.apply_player_elo(db, player.id, 1200.0, 1225.0, first_application=True)
    # Some other match finishes for this player in between.
    crud.apply_player_elo(db, player.id, 1225.0, 1265.0, first_application=True)

    # Now the first match is redelivered and reconciles.
    crud.apply_player_elo(db, player.id, 1200.0, 1225.0, first_application=False)
    assert crud.get_player_by_id(db, player.id).elo == pytest.approx(1265.0), (
        "reconciling an already-applied transition must not undo a later one"
    )


# ── F6: a differing replay hash is evidence, not noise ─────────────────────

def test_the_same_replay_hash_twice_is_accepted(db):
    """Redelivery of a deterministic match is the normal case and must stay
    silent."""
    match_id = f"m_{uuid.uuid4().hex[:8]}"
    crud.save_manifest(db, _manifest(match_id))
    digest = "sha256:" + "a" * 64
    assert crud.save_replay_hash(db, match_id, digest).matched_count == 1
    assert crud.save_replay_hash(db, match_id, digest).matched_count == 1
    assert crud.get_replay_hash(db, match_id) == digest


def test_a_differing_replay_hash_is_refused_rather_than_written_over(db):
    """The single most important event this system can observe.

    At-least-once delivery guarantees a second execution of every match
    eventually happens, so this is the one place two independent runs of one
    manifest are compared. A `$set` here meant a second run that produced
    DIFFERENT bytes -- a determinism break -- silently replaced the first
    hash with no error, no log and no trace: the mechanism best placed to
    catch the failure was erasing it.

    Red line: the `"$or": [{"replay_sha256": None}, {"replay_sha256":
    replay_sha256}]` clause in crud.save_replay_hash's filter. Remove it and
    the write succeeds, the first hash is gone, and nothing raises.
    """
    match_id = f"m_{uuid.uuid4().hex[:8]}"
    crud.save_manifest(db, _manifest(match_id))
    first = "sha256:" + "a" * 64
    second = "sha256:" + "b" * 64
    crud.save_replay_hash(db, match_id, first)

    with pytest.raises(ReplayHashConflict):
        crud.save_replay_hash(db, match_id, second)

    assert crud.get_replay_hash(db, match_id) == first, (
        "the first hash is the one recorded against this match and must "
        "not be replaced by a disagreeing one"
    )
    assert crud.get_replay_hash_conflicts(db, match_id) == [second], (
        "the disagreeing hash must be kept -- it is the evidence"
    )


def test_a_repeated_conflict_is_recorded_once(db):
    """Red line: `$addToSet` (not `$push`) in crud.save_replay_hash. With
    $push a job redelivered ten times writes ten identical rows."""
    match_id = f"m_{uuid.uuid4().hex[:8]}"
    crud.save_manifest(db, _manifest(match_id))
    crud.save_replay_hash(db, match_id, "sha256:" + "a" * 64)
    for _ in range(3):
        with pytest.raises(ReplayHashConflict):
            crud.save_replay_hash(db, match_id, "sha256:" + "b" * 64)
    assert len(crud.get_replay_hash_conflicts(db, match_id)) == 1


def test_no_manifest_row_still_reports_matched_nothing(db):
    """The worker refuses to ack a result whose write touched no document,
    and that check reads matched_count. A conflict path that raised here
    instead would turn "no manifest yet" into a determinism alarm.

    Red line: the `if conflicted is None: return result` branch in
    crud.save_replay_hash.
    """
    result = crud.save_replay_hash(db, f"m_{uuid.uuid4().hex[:8]}",
                                   "sha256:" + "a" * 64)
    assert result.matched_count == 0


# ── F14: the worker's manifest must be the stored one ──────────────────────

def test_the_stored_manifest_digest_is_readable_without_rebuilding_it(db):
    """The worker compares against what was WRITTEN, not against a rebuild
    of it in the worker's own process -- rebuilding would be comparing the
    worker with itself, which is exactly the comparison that cannot fail.

    Red line: the `{"manifest_sha256": 1}` projection read in
    crud.get_manifest_digest -- specifically that it reads the stored field
    rather than recomputing. Swap it for `manifest_sha256(get_manifest(...))`
    and the rolling-deploy skew this exists to catch becomes invisible.
    """
    from backend.determinism.manifest import manifest_sha256

    match_id = f"m_{uuid.uuid4().hex[:8]}"
    manifest = _manifest(match_id)
    crud.save_manifest(db, manifest)
    assert crud.get_manifest_digest(db, match_id) == manifest_sha256(manifest)
    assert crud.get_manifest_digest(db, "no such match") is None


def test_the_worker_refuses_a_result_whose_manifest_is_not_the_stored_one(db):
    """F14, end to end.

    The worker REBUILDS the manifest from the job instead of receiving it,
    and five of its fields come from the worker's own environment:
    engine_version, ruleset_version, calibration_id, python_version,
    dep_lock_sha256. Under one image they always match the API's. Under a
    rolling deploy -- API on v1, worker on v2 -- they diverge, and the
    replay hash would then be a hash of bytes embedding a manifest that is
    not the one save_manifest persisted. Nothing compared them.

    Red line: the `if stored_digest is not None and stored_digest !=
    computed_digest: raise ManifestMismatch(...)` block in
    worker._persist_result.
    """
    from backend.worker import ManifestMismatch, _persist_result

    match_id = f"m_{uuid.uuid4().hex[:8]}"
    crud.create_race(db, "bahrain", "quick")
    stored = _manifest(match_id)
    crud.save_manifest(db, stored)

    # What a worker on a different image would rebuild: same match, one
    # ambient field different.
    skewed = _manifest(match_id)
    skewed.engine_version = "9.9.9"

    with pytest.raises(ManifestMismatch):
        _persist_result(db, {
            "match_id": match_id,
            "replay_sha256": "sha256:" + "a" * 64,
            "manifest": skewed,
            "standings": [], "lap_data": [], "events": [],
        })

    assert crud.get_replay_hash(db, match_id) is None, (
        "a replay hash must not be recorded against a manifest this worker "
        "cannot have produced"
    )


@pytest.mark.skipif(not _redis_is_reachable(),
                    reason="the abort path also updates the Redis lobby")
def test_an_aborted_outcome_records_the_reason_and_writes_nothing_else(db):
    """The worker's abort path. Nothing was simulated, so there is no
    standing to record and no rating to move.

    Red line: the `if result.get("outcome") == "aborted": _persist_abort(...)
    ; return` branch at the top of worker._persist_result. Delete the
    `return` and the finished path runs on a result with no manifest and
    raises KeyError instead.
    """
    from backend.worker import _persist_result

    player = crud.create_player(db, f"p_{uuid.uuid4().hex[:8]}", "T")
    race = crud.create_race(db, "bahrain", "quick", owner_id=player.id)

    _persist_result(db, {"outcome": "aborted", "match_id": race.id,
                         "reason": "your bot exceeded the CPU limit"})

    doc = crud.get_race(db, race.id)
    assert doc.status == "aborted"
    assert doc.abort_reason == "your bot exceeded the CPU limit"
    assert crud.get_race_results(db, race.id) == []
    assert crud.get_player_by_id(db, player.id).elo == 1200.0
