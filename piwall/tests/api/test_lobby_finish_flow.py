"""F1 and F2, proven together through the real HTTP surface.

F1: a finished race reported status "running" and result null for six
hours, because GET /api/race/{id} preferred the (permanently stale) Redis
lobby snapshot over Mongo even once the race had actually finished.

F2: whether a match's durable state reached "finished" at all was gated by
SOCKETS -- a per-process, per-replica dict of live WebSocket connections.
A race nobody happened to be watching from this replica never finished, no
matter how long the worker had already been done with it. That is exactly
the class of per-process match state this whole task exists to remove.

This runs a real match through the real worker (backend.worker.process_one
and _persist_result), through a TestClient hitting the real endpoints, and
-- the point of the file -- never opens a WebSocket for the race at all.
If F2 were still present, nothing here would ever see "finished".
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from backend.jobs.events import MatchEvents
from backend.jobs.queue import STREAM, MatchJobQueue
from backend.observability.health import _mongo_is_reachable
from backend.state.redis_client import get_redis, redis_is_reachable
from backend.worker import process_one, _persist_result

pytestmark = pytest.mark.skipif(
    not (redis_is_reachable() and _mongo_is_reachable()),
    reason="needs a reachable Redis and a reachable database",
)


@pytest.fixture
def client(monkeypatch):
    import backend.main as main

    monkeypatch.setenv("PROVISIONING_SECRET", "test-secret-finish-flow")
    monkeypatch.setattr(main, "PROVISIONING_SECRET", "test-secret-finish-flow")
    with TestClient(main.app) as test_client:
        yield test_client, main


def _register(client, main):
    username = f"e2e_finish_{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/api/register",
        json={"username": username, "team_name": "T"},
        headers={
            "x-provision-secret": "test-secret-finish-flow",
            "x-provision-subject": "sub-finish-flow",
        },
    )
    assert r.status_code == 200, r.text
    return r.json(), username


def test_a_race_finishes_durably_even_with_no_socket_ever_connected(client):
    """The core property: no WebSocket is opened anywhere in this test.

    If SOCKETS still gated the durable "finished" write (F2), this race
    would stay stuck at "running" no matter what the worker did, because
    `race_id not in SOCKETS` would always be true here -- this replica
    never has a spectator for it.
    """
    test_client, main = client
    player, username = _register(test_client, main)
    api_key = player["api_key"]

    r = test_client.post(
        "/api/race/create",
        json={"track": "bahrain", "race_type": "quick", "speed": 5.0},
        headers={"x-api-key": api_key},
    )
    assert r.status_code == 200, r.text
    race_id = r.json()["race_id"]

    r = test_client.post(
        f"/api/race/{race_id}/join",
        json={"starting_compound": "MEDIUM"},
        headers={"x-api-key": api_key},
    )
    assert r.status_code == 200, r.text

    # Before the match runs: the lobby branch, live and not yet terminal.
    r = test_client.get(f"/api/race/{race_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "lobby"

    lobby = main.LOBBIES.get(race_id)
    job, manifest = main._build_job_and_manifest(race_id, lobby)

    db = main.SessionLocal()
    try:
        from backend.db import crud
        crud.save_manifest(db, manifest)
    finally:
        db.close()

    queue = MatchJobQueue()
    events = MatchEvents(channel=f"piwall:events:match:test-{uuid.uuid4()}")
    queue.enqueue(job)

    assert race_id not in main.SOCKETS, (
        "sanity check: this test must never have a socket for this race"
    )

    def real_persist(result):
        db = main.SessionLocal()
        try:
            _persist_result(db, result)
        finally:
            db.close()

    try:
        finished_match_id = process_one(
            queue, events, persist=real_persist, consumer="finish-flow-test"
        )
        assert finished_match_id == race_id

        assert race_id not in main.SOCKETS, (
            "still no socket for this race -- the worker's persist step "
            "must not have needed one"
        )

        # F1 + F2 together: the race must now report "finished" with a
        # real result, reachable purely through the HTTP surface, despite
        # no socket ever having existed for it on this (the only) replica.
        r = test_client.get(f"/api/race/{race_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "finished", (
            f"race never reached finished; got {body['status']!r} -- F2 "
            f"(SOCKETS gating a durable write) or F1 (get_race not reading "
            f"the DB for a terminal lobby) has regressed"
        )
        assert body["results"], "a finished race must report its results"

        lobby_after = main.LOBBIES.get(race_id)
        assert lobby_after["status"] == "finished", (
            "the Redis lobby itself must reach 'finished' with no socket "
            "ever connected -- this is F2's exact claim"
        )
    finally:
        main.LOBBIES.delete(race_id)
        db = main.SessionLocal()
        try:
            db.db.races.delete_one({"id": race_id})
            db.db.race_results.delete_many({"race_id": race_id})
            db.db.elo_history.delete_many({"race_id": race_id})
            db.db.manifests.delete_one({"match_id": race_id})
            db.db.players.delete_one({"username": username})
        finally:
            db.close()
        get_redis().delete(STREAM)
