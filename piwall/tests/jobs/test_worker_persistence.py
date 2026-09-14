"""The worker must persist race results, lap data and Elo -- not just a
replay hash -- and it must do so at most once per match even though the
job queue delivers at-least-once.

Task 7's `persist` only ever saved the replay hash; nothing under backend/
called save_race_results, save_race_data or compute_elo_updates after that,
so a finished match left no results and no rating change. This file proves
the restored path works, and specifically proves the part that is not a
copy-paste of the pre-decouple code: a redelivered job must not double-apply
an Elo update.
"""

import uuid

import pytest

from backend.db import crud
from backend.db.models import create_db_engine, init_db, mongo_url
from backend.determinism.manifest import Participant, build_manifest
from backend.jobs.events import MatchEvents
from backend.jobs.queue import STREAM, MatchJobQueue
from backend.observability.health import _mongo_is_reachable
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
    job = {
        "match_id": match_id, "track": "bahrain", "seed": 1000,
        "participants": [
            {"slot": 0, "player_id": player_id, "car_id": "USR-01",
             "code": PLAYER_CODE},
            {"slot": 1, "house_bot": "NXS-07"},
        ],
    }
    manifest = build_manifest(
        match_id=match_id, seed=1000, track="bahrain",
        participants=[
            Participant(slot=0, player_id=player_id, bot_version_id=None,
                       code_sha256="sha256:" + "a" * 64, house_bot=None),
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
        process_one(queue, events, persist=lambda r: _persist_result(db, r),
                   consumer="wtest")
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
