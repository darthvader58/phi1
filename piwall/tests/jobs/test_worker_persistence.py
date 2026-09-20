"""The worker must persist race results, lap data and Elo -- not just a
replay hash -- and it must do so at most once per match even though the
job queue delivers at-least-once.

Task 7's `persist` only ever saved the replay hash; nothing under backend/
called save_race_results, save_race_data or compute_elo_updates after that,
so a finished match left no results and no rating change. This file proves
the restored path works, and specifically proves the part that is not a
copy-paste of the pre-decouple code: a redelivered job must not double-apply
an Elo update.

Fix round 2 (task-8-review.md F5/F6/F8) found two problems with the first
version of this fix: the idempotency guard keyed on crud.get_race_results,
the FIRST thing the block wrote, so a worker that died between that write
and the Elo loop left the guard reading "already done" and lost the Elo
update and lap data forever; and nothing in this file distinguished a
protection that actually works from one that merely looks like it -- the
unique index could be deleted with the suite staying green, and the
"redelivery" test only ever re-enqueued a fresh job rather than reproducing
the actual failure (a crash partway through one persist, then a real
redelivery of the same entry). test_partial_persist_then_redelivery... and
test_the_unique_index_is_what_prevents_duplicate_history_rows below close
both gaps directly.
"""

import hashlib
import uuid

import pytest

from backend.db import crud
from backend.db.models import create_db_engine, init_db, mongo_url
from backend.determinism.manifest import Participant, build_manifest
from backend.jobs.events import MatchEvents
from backend.jobs.queue import STREAM, MatchJobQueue
from backend.observability.health import _mongo_is_reachable
from backend.season.elo import compute_elo_updates
from backend.state.lobby import LobbyStore
from backend.state.redis_client import get_redis, redis_is_reachable
from backend.worker import _persist_result, process_one

pytestmark = pytest.mark.skipif(
    not (redis_is_reachable() and _mongo_is_reachable()),
    reason="needs a reachable Redis and a reachable database",
)

PLAYER_CODE = (
    "def my_strategy(state, my_car):\n"
    "    return {'pit': my_car.tyre_age > 20, 'compound': 'MEDIUM'}\n"
)


def _cleanup(db, race_ids=(), player_ids=()):
    race_ids, player_ids = list(race_ids), list(player_ids)
    if race_ids:
        db.db.races.delete_many({"id": {"$in": race_ids}})
        db.db.race_results.delete_many({"race_id": {"$in": race_ids}})
        db.db.elo_history.delete_many({"race_id": {"$in": race_ids}})
        db.db.manifests.delete_many({"match_id": {"$in": race_ids}})
    if player_ids:
        db.db.players.delete_many({"id": {"$in": player_ids}})


def _job_and_manifest(match_id: str, player_id: str):
    """A player-plus-house-bot job, mirroring tests/jobs/test_worker.py's
    JOB fixture: a player bot in the mix on purpose, so a worker that could
    only replay house bots would still fail this.
    """
    code_sha256 = "sha256:" + hashlib.sha256(PLAYER_CODE.encode()).hexdigest()
    job = {
        "match_id": match_id, "track": "bahrain", "seed": 1000,
        "participants": [
            {"slot": 0, "player_id": player_id, "car_id": "USR-01",
             "code": PLAYER_CODE, "code_sha256": code_sha256},
            {"slot": 1, "house_bot": "NXS-07"},
        ],
    }
    # The job and the manifest must describe the SAME match, exactly as
    # main._build_job_and_manifest builds them -- same slots, same
    # player_ids, same code_sha256. They did not before: the manifest
    # declared a placeholder code_sha256 the job never carried, so the
    # manifest saved here was not the one the worker rebuilds from this
    # job. _persist_result now compares the two digests and refuses a
    # mismatch, which is what caught it.
    manifest = build_manifest(
        match_id=match_id, seed=1000, track="bahrain",
        participants=[
            Participant(slot=0, player_id=player_id, bot_version_id=None,
                       code_sha256=code_sha256, house_bot=None),
            Participant(slot=1, player_id=None, bot_version_id=None,
                       code_sha256=None, house_bot="NXS-07"),
        ],
    )
    return job, manifest


@pytest.fixture
def db():
    engine = create_db_engine(mongo_url())
    factory = init_db(engine)
    session = factory()
    yield session
    session.close()


@pytest.fixture
def throwaway_db():
    """A disposable database, never the shared piwall/phi1 one.

    For tests that need to drop and recreate a protective index at
    runtime (the only way to prove one is load-bearing against a
    persistent database -- see test_the_unique_index_is_what_prevents_duplicate_history_rows
    below): doing that against the shared database means an interrupted
    run leaves production without the index, silently, for however long
    it takes someone to notice. init_db() is run here too, so the
    fixture starts in the same state SessionLocal()'s real factory would.
    """
    from pymongo import MongoClient

    from backend.db.models import MongoSession, mongo_url

    name = f"piwall_test_worker_persistence_{uuid.uuid4().hex[:8]}"
    # mongo_url(), not a hardcoded 127.0.0.1: the skip gate on this module
    # probes mongo_url(), so hardcoding a host here means the gate says
    # "run" against a server this client cannot reach -- an error rather
    # than a skip, which is exactly what happens inside a container.
    client = MongoClient(mongo_url())
    database = client[name]
    init_db(database)
    session = MongoSession(database)
    yield session
    session.close()
    client.drop_database(name)
    client.close()


@pytest.fixture
def wiring():
    client = get_redis()
    client.delete(STREAM)
    queue = MatchJobQueue()
    # Isolated channel per test, same reasoning as test_worker.py's `wiring`:
    # CHANNEL is one fixed production name, and two tests sharing it could
    # cross-deliver each other's completion events.
    events = MatchEvents(channel=f"piwall:events:match:test-{uuid.uuid4()}")
    yield queue, events
    client.delete(STREAM)


def test_a_finished_match_leaves_results_lap_data_and_an_elo_change(db, wiring):
    """The regression itself. Delete the save_race_results call in
    _persist_result and this must go red: race_results stays empty even
    though the race reports "finished".
    """
    queue, events = wiring
    player = crud.create_player(db, f"wp_reg_{uuid.uuid4().hex[:8]}", "Test Team")
    race = crud.create_race(db, "bahrain", "quick", owner_id=player.id)
    match_id = race.id
    job, manifest = _job_and_manifest(match_id, player.id)
    crud.save_manifest(db, manifest)

    elo_before = crud.get_player_by_id(db, player.id).elo

    try:
        queue.enqueue(job)
        returned_match_id = process_one(
            queue, events, persist=lambda r: _persist_result(db, r), consumer="wtest"
        )
        assert returned_match_id == match_id

        results = crud.get_race_results(db, match_id)
        assert {r.car_id for r in results} == {"USR-01", "NXS-07"}, (
            "every car in the race must have a result row"
        )

        race_doc = crud.get_race(db, match_id)
        assert race_doc.status == "finished"
        assert race_doc.lap_data_json, "lap_data_json must not be empty"

        elo_after = crud.get_player_by_id(db, player.id).elo
        assert elo_after != elo_before, "Elo must change after a race with a result"
    finally:
        _cleanup(db, race_ids=[match_id], player_ids=[player.id])


def test_redelivery_does_not_double_apply_elo(db, wiring):
    """The queue is at-least-once: a worker can die after running a match
    but before acking, and reclaim_stalled hands the job to another worker,
    which re-runs the deterministic match and persists an identical result
    a second time. That second persist must be a no-op past the replay
    hash, not a second set of result rows and a second Elo delta on top of
    a rating that has already moved.

    This is the real second-delivery path -- the same job run through
    process_one twice -- not a hand-built duplicate row.
    """
    queue, events = wiring
    player = crud.create_player(db, f"wp_redeliver_{uuid.uuid4().hex[:8]}", "Test Team")
    race = crud.create_race(db, "bahrain", "quick", owner_id=player.id)
    match_id = race.id
    job, manifest = _job_and_manifest(match_id, player.id)
    crud.save_manifest(db, manifest)

    try:
        queue.enqueue(job)
        process_one(queue, events, persist=lambda r: _persist_result(db, r),
                   consumer="wtest")
        elo_after_first = crud.get_player_by_id(db, player.id).elo
        results_after_first = crud.get_race_results(db, match_id)
        history_after_first = crud.get_elo_history(db, player.id)

        queue.enqueue(job)
        second_match_id = process_one(
            queue, events, persist=lambda r: _persist_result(db, r),
            consumer="wtest",
        )
        # B2: a DuplicateKeyError from the unique index must be caught and
        # skipped inside _persist_result, not escape persist() -- an
        # uncaught raise here would mean process_one never acks, and the
        # entry would be reclaimed and fail identically forever.
        assert second_match_id == match_id
        assert queue.pending_count() == 0, (
            "the second delivery must ack cleanly, not strand the job"
        )
        elo_after_second = crud.get_player_by_id(db, player.id).elo
        results_after_second = crud.get_race_results(db, match_id)
        history_after_second = crud.get_elo_history(db, player.id)

        assert len(results_after_second) == len(results_after_first), (
            "a redelivered job must not insert a second set of result rows"
        )
        assert len(history_after_second) == len(history_after_first), (
            "a redelivered job must not insert a second Elo history row"
        )
        assert elo_after_second == elo_after_first, (
            "a redelivered job must not apply the Elo delta twice"
        )
    finally:
        _cleanup(db, race_ids=[match_id], player_ids=[player.id])


def test_k_factor_follows_the_race_documents_race_type(db):
    """Identical standings, different race_type, must produce different Elo
    deltas -- k=48 for season, k=32 for quick. Asserted on the delta itself
    (not on which branch ran), so a rewrite that reads race_type some other
    way still passes as long as it reads the right value.

    _persist_result is called directly here rather than through the queue:
    this is purely about where k_factor comes from, and going through the
    queue and a real sandboxed match would only add noise to that question.
    """
    season_player = crud.create_player(db, f"wp_season_{uuid.uuid4().hex[:8]}", "T")
    quick_player = crud.create_player(db, f"wp_quick_{uuid.uuid4().hex[:8]}", "T")
    opponent_1 = crud.create_player(db, f"wp_opp1_{uuid.uuid4().hex[:8]}", "T")
    opponent_2 = crud.create_player(db, f"wp_opp2_{uuid.uuid4().hex[:8]}", "T")

    season_race = crud.create_race(db, "bahrain", "season")
    quick_race = crud.create_race(db, "bahrain", "quick")

    def standings_for(winner_id, loser_id):
        return [
            {"player_id": winner_id, "car_id": "P01", "position": 1,
             "retired": False, "total_time": 5000.0, "pit_laps": [10],
             "compounds_used": ["MEDIUM"]},
            {"player_id": loser_id, "car_id": "P02", "position": 2,
             "retired": False, "total_time": 5010.0, "pit_laps": [10],
             "compounds_used": ["MEDIUM"]},
        ]

    def build_result(match_id, standings):
        manifest = build_manifest(
            match_id=match_id, seed=1, track="bahrain",
            participants=[
                Participant(slot=0, player_id=standings[0]["player_id"],
                           bot_version_id=None, code_sha256="sha256:" + "a" * 64,
                           house_bot=None),
                Participant(slot=1, player_id=standings[1]["player_id"],
                           bot_version_id=None, code_sha256="sha256:" + "b" * 64,
                           house_bot=None),
            ],
        )
        crud.save_manifest(db, manifest)
        return {
            "match_id": match_id,
            "replay_sha256": "sha256:" + "c" * 64,
            "manifest": manifest,
            "standings": standings,
            "lap_data": [{"lap": 1}],
            "events": [],
        }

    try:
        _persist_result(db, build_result(
            season_race.id, standings_for(season_player.id, opponent_1.id)
        ))
        _persist_result(db, build_result(
            quick_race.id, standings_for(quick_player.id, opponent_2.id)
        ))

        season_delta = crud.get_player_by_id(db, season_player.id).elo - 1200.0
        quick_delta = crud.get_player_by_id(db, quick_player.id).elo - 1200.0

        assert season_delta != quick_delta
        assert abs(season_delta) > abs(quick_delta), (
            "a season race (k=48) must move Elo more than a quick race "
            "(k=32) for identical standings"
        )
    finally:
        _cleanup(
            db,
            race_ids=[season_race.id, quick_race.id],
            player_ids=[season_player.id, quick_player.id,
                       opponent_1.id, opponent_2.id],
        )


def test_partial_persist_then_redelivery_does_not_lose_elo_or_lap_data(db, wiring):
    """The actual F5 regression, reproduced as the real failure shape:
    a worker dies partway through persisting -- after save_race_results
    but before save_race_data and the Elo loop -- and the SAME queue entry
    is genuinely redelivered afterwards (reclaim_stalled, not a fresh
    enqueue). Round 1's guard keyed on "does race_results already exist",
    which this partial state satisfies, so the redelivered call read the
    guard as "already done" and never wrote lap data or Elo at all --
    lost, not merely delayed.
    """
    queue, events = wiring
    player = crud.create_player(db, f"wp_partial_{uuid.uuid4().hex[:8]}", "Test Team")
    race = crud.create_race(db, "bahrain", "quick", owner_id=player.id)
    match_id = race.id
    job, manifest = _job_and_manifest(match_id, player.id)
    crud.save_manifest(db, manifest)

    elo_before = crud.get_player_by_id(db, player.id).elo

    real_save_race_data = crud.save_race_data

    def exploding_save_race_data(*args, **kwargs):
        raise RuntimeError("simulated worker death right after save_race_results")

    crud.save_race_data = exploding_save_race_data
    try:
        queue.enqueue(job)
        with pytest.raises(RuntimeError):
            process_one(queue, events, persist=lambda r: _persist_result(db, r),
                       consumer="wtest-1")
    finally:
        crud.save_race_data = real_save_race_data

    # Confirm the partial state this test is named for actually happened:
    # results written, nothing else yet, and the job still pending (never
    # acked, because process_one's persist raised).
    assert crud.get_race_results(db, match_id), (
        "results must exist at the simulated crash point"
    )
    assert crud.get_player_by_id(db, player.id).elo == elo_before, (
        "Elo must not have moved yet at the simulated crash point"
    )
    assert queue.pending_count() == 1

    try:
        # Redelivery: process_one's own reclaim_stalled picks the still-
        # pending entry back up, exactly like
        # test_a_stalled_job_is_reclaimed_and_completed in test_worker.py --
        # min_idle_ms=0 so this does not wait out the real 30-second
        # default idle threshold.
        redelivered_match_id = process_one(
            queue, events, persist=lambda r: _persist_result(db, r),
            consumer="wtest-2", min_idle_ms=0,
        )
        assert redelivered_match_id == match_id
        assert queue.pending_count() == 0

        results = crud.get_race_results(db, match_id)
        assert {r.car_id for r in results} == {"USR-01", "NXS-07"}, (
            "results must still be exactly one set after the redelivered persist"
        )
        race_doc = crud.get_race(db, match_id)
        assert race_doc.lap_data_json, (
            "lap_data_json must not be permanently lost after a partial-crash "
            "redelivery"
        )
        elo_after = crud.get_player_by_id(db, player.id).elo
        assert elo_after != elo_before, (
            "Elo must not be permanently lost after a partial-crash redelivery"
        )
    finally:
        _cleanup(db, race_ids=[match_id], player_ids=[player.id])


def test_the_unique_index_is_what_prevents_duplicate_history_rows(throwaway_db):
    """Braces, verified as actual braces rather than assumed.

    Editing backend/db/models.py to remove the create_index call has no
    effect on an index that already exists in a persistent database --
    create_index is never retroactively undone by deleting the code that
    once called it -- so a test that only edits source and reruns the
    suite proves nothing here. This drops the index at runtime, confirms a
    double _persist_result call for the same result actually succeeds in
    inserting a second, duplicate history row without it (closing review
    finding F8's "delete the index and the suite stays green" gap
    directly), then restores the index and confirms the identical
    double-apply is refused with it back.

    Review round 3, N3: the first version of this test called drop_index
    directly against the shared database (the `db` fixture's
    mongo_url()), so an interrupted run could leave the real database
    without the index the rest of this file's tests -- and production --
    depend on, silently. This runs against a disposable database instead
    (see the `throwaway_db` fixture), the same pattern
    tests/db/test_init_db_migration.py already uses, so dropping and
    recreating an index here can never touch anything but this test's own
    throwaway data.
    """
    player = crud.create_player(throwaway_db, f"wp_idxcheck_{uuid.uuid4().hex[:8]}", "T")
    race = crud.create_race(throwaway_db, "bahrain", "quick", owner_id=player.id)
    match_id = race.id
    job, manifest = _job_and_manifest(match_id, player.id)
    crud.save_manifest(throwaway_db, manifest)

    result = {
        "match_id": match_id,
        "replay_sha256": "sha256:" + "e" * 64,
        "manifest": manifest,
        "standings": [
            {"player_id": player.id, "car_id": "USR-01", "position": 1,
             "retired": False, "total_time": 100.0, "pit_laps": [],
             "compounds_used": ["MEDIUM"]},
            {"player_id": "NXS-07", "car_id": "NXS-07", "position": 2,
             "retired": False, "total_time": 110.0, "pit_laps": [],
             "compounds_used": ["MEDIUM"]},
        ],
        "lap_data": [{"lap": 1}],
        "events": [],
    }

    # throwaway_db already ran init_db(), so the index exists; drop it to
    # reach the "no protection" state this test is about.
    throwaway_db.db.elo_history.drop_index("player_id_1_race_id_1")

    _persist_result(throwaway_db, result)
    _persist_result(throwaway_db, result)
    assert len(crud.get_elo_history(throwaway_db, player.id)) == 2, (
        "without the index, a second persist of the same result must "
        "actually succeed in inserting a duplicate row -- this is the "
        "exact vulnerability the index exists to close, reproduced "
        "directly rather than assumed"
    )

    throwaway_db.db.elo_history.delete_many({"race_id": match_id})
    throwaway_db.db.race_results.delete_many({"race_id": match_id})
    throwaway_db.db.elo_history.create_index(
        [("player_id", 1), ("race_id", 1)], unique=True
    )

    # With the index restored, the identical double-apply must now be
    # refused: exactly one history row, not two.
    _persist_result(throwaway_db, result)
    _persist_result(throwaway_db, result)
    assert len(crud.get_elo_history(throwaway_db, player.id)) == 1, (
        "with the index restored, a second persist of the same result "
        "must be refused rather than inserted as a duplicate"
    )


def test_a_missing_race_document_refuses_the_job_rather_than_guessing_k(db):
    """F16: a missing race document (partial state some other bug left
    behind, or a manifest saved for a race that was never actually
    created) must not silently apply k=32 to what may have been a season
    race and write that durably. Refusing loudly -- the job stays pending
    and gets retried -- is the same "fail loud, not drift" shape as the
    Elo unique index.
    """
    match_id = f"m_no_race_doc_{uuid.uuid4().hex[:8]}"
    manifest = build_manifest(
        match_id=match_id, seed=1, track="bahrain",
        participants=[Participant(0, None, None, None, "VEL-01")],
    )
    crud.save_manifest(db, manifest)
    # Deliberately no crud.create_race(db, ...) call -- no race document
    # exists for this match_id.

    result = {
        "match_id": match_id,
        "replay_sha256": "sha256:" + "f" * 64,
        "manifest": manifest,
        "standings": [
            {"player_id": "VEL-01", "car_id": "VEL-01", "position": 1,
             "retired": False, "total_time": 100.0, "pit_laps": [],
             "compounds_used": ["MEDIUM"]},
        ],
        "lap_data": [{"lap": 1}],
        "events": [],
    }

    try:
        with pytest.raises(RuntimeError, match="no race document"):
            _persist_result(db, result)
    finally:
        db.db.manifests.delete_many({"match_id": match_id})
        db.db.race_results.delete_many({"race_id": match_id})


def test_a_refused_job_does_not_leave_the_lobby_reporting_finished(db):
    """The N7 residual, closed (re-review of round 3, NEW-4).

    A job whose race document is missing raises the F16 RuntimeError and
    is retried forever. While the race-document lookup sat below the
    status writes, that job first marked the Redis lobby "finished" --
    so /api/races reported the race finished while GET /api/race/{id}
    404'd on the DB read, permanently. Round 3's report called closing
    this a redesign; it is a hoist, and this is the behaviour it buys.

    Red line: the `race = crud.get_race(db, match_id)` / `if race is
    None: raise RuntimeError(...)` block's POSITION in
    backend/worker.py's _persist_result -- above crud.update_race_status
    rather than below it. Move it back down and the lobby below reads
    "finished".
    """
    match_id = f"m_poison_lobby_{uuid.uuid4().hex[:8]}"
    manifest = build_manifest(
        match_id=match_id, seed=1, track="bahrain",
        participants=[Participant(0, None, None, None, "VEL-01")],
    )
    crud.save_manifest(db, manifest)
    # Deliberately no crud.create_race(db, ...): this is the poison pill.

    lobbies = LobbyStore()
    lobbies.create(match_id, track="bahrain", race_type="quick")
    lobbies.set_status(match_id, "running")

    result = {
        "match_id": match_id,
        "replay_sha256": "sha256:" + "7" * 64,
        "manifest": manifest,
        "standings": [
            {"player_id": "VEL-01", "car_id": "VEL-01", "position": 1,
             "retired": False, "total_time": 100.0, "pit_laps": [],
             "compounds_used": ["MEDIUM"]},
        ],
        "lap_data": [{"lap": 1}],
        "events": [],
    }

    try:
        with pytest.raises(RuntimeError, match="no race document"):
            _persist_result(db, result)

        assert lobbies.get(match_id)["status"] == "running", (
            "a job that refused itself must not have told every /api/races "
            "reader the race finished -- GET /api/race/{id} 404s for it"
        )
    finally:
        lobbies.delete(match_id)
        db.db.manifests.delete_many({"match_id": match_id})
        db.db.race_results.delete_many({"race_id": match_id})


def test_a_crash_between_history_and_rating_still_converges_on_redelivery(db):
    """N1 (fix round 3): making the elo_history row the idempotency token
    moved the vulnerable window rather than closing it.

    A crash between crud.save_elo_history (the history row lands) and
    crud.apply_player_elo (the rating itself never moves) for one player
    left that player's rating stuck at its pre-race value forever on
    round 2's code: the redelivery's save_elo_history call raised
    DuplicateKeyError, which round 2 caught and skipped, so the rating
    write for that player was never even attempted again.
    elo_history then permanently contradicted players.elo for that player.

    Reproduced with three real players, k=48 (season), matching the
    reviewer's numbers: a clean run gives [1176.0, 1224.0, 1200.0]; this
    crashes between the two writes for the FIRST player processed and
    asserts the redelivered result still converges to the same three
    numbers, not [1200.0, 1224.0, 1200.0].
    """
    p1 = crud.create_player(db, f"wp_n1_a_{uuid.uuid4().hex[:8]}", "T")
    p2 = crud.create_player(db, f"wp_n1_b_{uuid.uuid4().hex[:8]}", "T")
    p3 = crud.create_player(db, f"wp_n1_c_{uuid.uuid4().hex[:8]}", "T")
    race = crud.create_race(db, "bahrain", "season", owner_id=p1.id)
    match_id = race.id

    manifest = build_manifest(
        match_id=match_id, seed=1, track="bahrain",
        participants=[
            Participant(slot=0, player_id=p1.id, bot_version_id=None,
                       code_sha256="sha256:" + "a" * 64, house_bot=None),
            Participant(slot=1, player_id=p2.id, bot_version_id=None,
                       code_sha256="sha256:" + "b" * 64, house_bot=None),
            Participant(slot=2, player_id=p3.id, bot_version_id=None,
                       code_sha256="sha256:" + "c" * 64, house_bot=None),
        ],
    )
    crud.save_manifest(db, manifest)

    result = {
        "match_id": match_id,
        "replay_sha256": "sha256:" + "9" * 64,
        "manifest": manifest,
        "standings": [
            {"player_id": p1.id, "car_id": "P01", "position": 1,
             "retired": False, "total_time": 100.0, "pit_laps": [],
             "compounds_used": ["MEDIUM"]},
            {"player_id": p2.id, "car_id": "P02", "position": 3,
             "retired": False, "total_time": 120.0, "pit_laps": [],
             "compounds_used": ["MEDIUM"]},
            {"player_id": p3.id, "car_id": "P03", "position": 2,
             "retired": False, "total_time": 110.0, "pit_laps": [],
             "compounds_used": ["MEDIUM"]},
        ],
        "lap_data": [{"lap": 1}],
        "events": [],
    }

    try:
        # Clean-run baseline: what the three ratings converge to with no
        # interruption at all, computed independently of _persist_result
        # via the same Elo function it calls.
        standings_tuples = [(p1.id, 1, False), (p2.id, 3, False), (p3.id, 2, False)]
        clean_ratings = compute_elo_updates(
            standings_tuples, {p1.id: 1200.0, p2.id: 1200.0, p3.id: 1200.0}, 48.0
        )

        # Simulate a crash strictly between save_elo_history and the
        # rating write for the first player _persist_result reaches
        # (dict insertion order == standings order, since compute_elo_updates
        # returns dict(ratings) mutated in place). The rating write is
        # crud.apply_player_elo, not crud.update_player_elo: the match path
        # moved to an atomic $inc / compare-and-set so two different matches
        # finishing for one player at once cannot lose an update.
        real_apply_player_elo = crud.apply_player_elo
        calls = []

        def exploding_apply_player_elo(*args, **kwargs):
            if not calls:
                calls.append(1)
                raise RuntimeError("simulated worker death after save_elo_history")
            return real_apply_player_elo(*args, **kwargs)

        crud.apply_player_elo = exploding_apply_player_elo
        try:
            with pytest.raises(RuntimeError):
                _persist_result(db, result)
        finally:
            crud.apply_player_elo = real_apply_player_elo

        # Confirm the exact partial state this test is named for: one
        # history row exists, no rating has moved yet.
        assert len(crud.get_elo_history(db, p1.id)) + \
               len(crud.get_elo_history(db, p2.id)) + \
               len(crud.get_elo_history(db, p3.id)) == 1
        assert crud.get_player_by_id(db, p1.id).elo == 1200.0
        assert crud.get_player_by_id(db, p2.id).elo == 1200.0
        assert crud.get_player_by_id(db, p3.id).elo == 1200.0

        # Redelivery: the same result, persisted again.
        _persist_result(db, result)

        final = {
            p1.id: crud.get_player_by_id(db, p1.id).elo,
            p2.id: crud.get_player_by_id(db, p2.id).elo,
            p3.id: crud.get_player_by_id(db, p3.id).elo,
        }
        assert final == pytest.approx(clean_ratings), (
            f"redelivery must converge to the clean run's ratings "
            f"{clean_ratings}, got {final}"
        )
        # And exactly one history row per player -- reconciling on
        # DuplicateKeyError must not insert a second row.
        for p in (p1, p2, p3):
            assert len(crud.get_elo_history(db, p.id)) == 1
    finally:
        _cleanup(db, race_ids=[match_id], player_ids=[p1.id, p2.id, p3.id])


def test_the_race_document_is_marked_finished_before_the_redis_lobby(db):
    """N7 (fix round 3, informational): the race document is the record
    a client's GET /api/race/{id} falls back to once the Redis lobby
    reports a terminal status. Updating the DB row first means a crash
    between the two writes leaves a lobby a reader would treat as
    non-terminal (still "running") over a race document that has
    already finished -- readable, not a 404 -- rather than the other way
    round.
    """
    player = crud.create_player(db, f"wp_order_{uuid.uuid4().hex[:8]}", "T")
    race = crud.create_race(db, "bahrain", "quick", owner_id=player.id)
    match_id = race.id
    job, manifest = _job_and_manifest(match_id, player.id)
    crud.save_manifest(db, manifest)

    order = []
    real_update_race_status = crud.update_race_status
    real_set_status = LobbyStore.set_status

    def recording_update_race_status(*args, **kwargs):
        order.append("update_race_status")
        return real_update_race_status(*args, **kwargs)

    def recording_set_status(self, *args, **kwargs):
        order.append("lobby_set_status")
        return real_set_status(self, *args, **kwargs)

    crud.update_race_status = recording_update_race_status
    LobbyStore.set_status = recording_set_status
    try:
        _persist_result(db, {
            "match_id": match_id,
            "replay_sha256": "sha256:" + "1" * 64,
            "manifest": manifest,
            "standings": [
                {"player_id": player.id, "car_id": "USR-01", "position": 1,
                 "retired": False, "total_time": 100.0, "pit_laps": [],
                 "compounds_used": ["MEDIUM"]},
                {"player_id": "NXS-07", "car_id": "NXS-07", "position": 2,
                 "retired": False, "total_time": 110.0, "pit_laps": [],
                 "compounds_used": ["MEDIUM"]},
            ],
            "lap_data": [{"lap": 1}],
            "events": [],
        })
    finally:
        crud.update_race_status = real_update_race_status
        LobbyStore.set_status = real_set_status
        _cleanup(db, race_ids=[match_id], player_ids=[player.id])

    assert order == ["update_race_status", "lobby_set_status"], (
        f"the race document must be marked finished before the Redis "
        f"lobby is; got {order}"
    )
