"""A stored replay hash must have inputs someone can actually re-derive.

This branch is the first to persist a manifest and a replay hash for every
real race. Both were unverifiable by construction:

  * `replay_from_manifest`'s NotImplementedError said the source is "stored
    against code_sha256". It was not. `save_bot_submission` wrote a
    TRUNCATED, unprefixed 16-character digest, and only for players who had
    called /submit-bot -- a player who joined and raced with the default
    STRATEGY_TEMPLATE left no row at all.
  * `starting_compound` is chosen by the player and changes the race, and
    `Participant` has no field for it.

The first is closed here. The second is a manifest schema change: adding
the field moves all six committed golden replay hashes, verified, because
the manifest is embedded in the hashed replay payload. What this file pins
is that the input is at least KEPT, so a later schema version can be
honest about matches that have already run, and that the error message no
longer claims a guarantee that does not exist.
"""

import uuid

import pytest

from backend.db import crud
from backend.db.models import MongoSession, init_db, mongo_url
from backend.determinism.manifest import Participant, build_manifest
from backend.determinism.replay import replay_from_manifest


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


mongo = pytest.mark.skipif(
    not _mongo_is_reachable(), reason="needs a reachable MongoDB"
)


@pytest.fixture
def db():
    from pymongo import MongoClient

    name = f"piwall_test_replay_inputs_{uuid.uuid4().hex[:8]}"
    client = MongoClient(mongo_url())
    database = client[name]
    init_db(database)
    session = MongoSession(database)
    yield session
    session.close()
    client.drop_database(name)
    client.close()


CODE = "def my_strategy(state, my_car):\n    return {'pit': False, 'compound': 'SOFT'}\n"


# ── the digest is one string, spelled one way ─────────────────────────────

def test_the_stored_address_is_the_one_a_manifest_uses():
    """The bug in one line: a manifest's code_sha256 is prefixed and
    full-length; the only stored digest was unprefixed and 16 characters,
    so the reference could never resolve.

    Red line: `return "sha256:" + hashlib.sha256(code.encode()).hexdigest()`
    in crud.code_sha256. Truncate it (`[:16]`) or drop the prefix and this
    goes red.
    """
    digest = crud.code_sha256(CODE)
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64

    manifest = build_manifest(
        match_id="m", seed=1, track="bahrain",
        participants=[Participant(0, "p1", None, digest, None)],
    )
    assert manifest.participants[0].code_sha256 == digest


@mongo
def test_source_is_recoverable_from_a_manifests_code_sha256(db):
    """The claim the error message used to make, now true.

    Red line: the `db.db.bot_sources.update_one(... upsert=True)` in
    crud.save_bot_source.
    """
    digest = crud.save_bot_source(db, CODE)
    assert crud.get_bot_source(db, digest) == CODE
    assert crud.get_bot_source(db, "sha256:" + "0" * 64) is None


@mongo
def test_a_stored_source_is_never_rewritten(db):
    """Content-addressed storage where the content can change is not
    content-addressed. Two players submitting identical code is the common
    case (the default template), so this must be a no-op, not a conflict.

    Red line: `$setOnInsert` in crud.save_bot_source. Make it `$set` and a
    second write with a forged row would replace the source a committed
    replay hash was computed over.
    """
    digest = crud.code_sha256(CODE)
    # A row already standing at this address, with content that is NOT what
    # the caller is about to store. Contrived on purpose: it is the only way
    # to observe whether the second write modifies the first, because
    # honest callers always pass matching content. If save_bot_source
    # touches an existing row at all, it touches this one.
    db.db.bot_sources.insert_one(
        {"code_sha256": digest, "code": "PRE-EXISTING", "first_seen_at": None}
    )

    assert crud.save_bot_source(db, CODE) == digest
    assert crud.get_bot_source(db, digest) == "PRE-EXISTING", (
        "a second write to an existing content address must not modify it "
        "-- a committed replay hash was computed over whatever is there"
    )
    assert db.db.bot_sources.count_documents({"code_sha256": digest}) == 1

    # And the ordinary path still stores, so the guard above is not simply
    # refusing to write anything.
    other = "def my_strategy(state, my_car):\n    return {'pit': True, 'compound': 'HARD'}\n"
    assert crud.get_bot_source(db, crud.save_bot_source(db, other)) == other


@mongo
def test_a_player_who_never_submitted_still_has_their_source_stored(db):
    """The gap that made this universal rather than occasional: the default
    STRATEGY_TEMPLATE races for anyone who joins without submitting, and
    save_bot_submission was the only writer.

    Red line: the `crud.save_bot_source(db, participant["code"])` loop in
    main._run_race. It runs over the JOB's participants, which includes
    every racer, not only those who called /submit-bot.
    """
    from backend.sandbox.runner import STRATEGY_TEMPLATE

    # No save_bot_submission call anywhere -- exactly the player this
    # missed.
    digest = crud.save_bot_source(db, STRATEGY_TEMPLATE)
    assert crud.get_bot_source(db, digest) == STRATEGY_TEMPLATE


# ── the input the manifest cannot name ────────────────────────────────────

@mongo
def test_the_starting_compound_is_kept_even_though_the_manifest_omits_it(db):
    """Not a fix for the schema gap -- a refusal to lose the data while it
    stands. Without this the compound is gone the moment the Redis lobby
    expires, and no later manifest version can be honest about a match that
    has already run.

    Red line: `crud.save_replay_inputs(db, race_id, job["participants"])`
    in main._run_race.
    """
    match_id = f"m_{uuid.uuid4().hex[:8]}"
    crud.save_replay_inputs(db, match_id, [
        {"slot": 1, "code_sha256": crud.code_sha256(CODE),
         "starting_compound": "SOFT"},
        {"slot": 0, "house_bot": "VEL-01"},
    ])
    rows = crud.get_replay_inputs(db, match_id)
    assert [r["slot"] for r in rows] == [0, 1], "stored sorted by slot"
    assert rows[1]["starting_compound"] == "SOFT"


@mongo
def test_recorded_inputs_are_immutable(db):
    """Same reason save_manifest is: this is the definition of what a match
    was, and rewriting one invalidates a recorded replay hash with no trace.

    Red line: the `if existing.get("participants") != rows: raise
    ValueError` branch in crud.save_replay_inputs.
    """
    match_id = f"m_{uuid.uuid4().hex[:8]}"
    rows = [{"slot": 0, "code_sha256": None, "starting_compound": "MEDIUM"}]
    crud.save_replay_inputs(db, match_id, rows)
    crud.save_replay_inputs(db, match_id, rows)  # identical: fine
    with pytest.raises(ValueError):
        crud.save_replay_inputs(db, match_id, [
            {"slot": 0, "code_sha256": None, "starting_compound": "SOFT"},
        ])
    assert crud.get_replay_inputs(db, match_id)[0]["starting_compound"] == "MEDIUM"


# ── the message itself ────────────────────────────────────────────────────

def test_the_refusal_no_longer_claims_a_guarantee_it_cannot_keep():
    """A message that says the source is stored, when it is not, is worse
    than no message: it sends whoever hits it looking for data that does
    not exist. It must now name the real blocker, which is the compound.

    Red line: the NotImplementedError body in
    determinism.replay.replay_from_manifest.
    """
    manifest = build_manifest(
        match_id="m_player", seed=1, track="bahrain",
        participants=[Participant(0, "p1", None, crud.code_sha256(CODE), None)],
    )
    with pytest.raises(NotImplementedError) as caught:
        replay_from_manifest(manifest)
    message = str(caught.value)
    assert "STARTING COMPOUND" in message, (
        "the message must name the field that actually blocks replay"
    )
    assert "get_bot_source" in message, (
        "and say where the source really is, since it now really is stored"
    )


def test_a_house_bot_manifest_still_replays():
    """The guard must refuse only what it cannot do. Six golden manifests
    depend on this path staying open."""
    manifest = build_manifest(
        match_id="m_house", seed=1000, track="bahrain",
        participants=[Participant(0, None, None, None, "VEL-01"),
                      Participant(1, None, None, None, "NXS-07")],
    )
    assert replay_from_manifest(manifest)


@mongo
@pytest.mark.skipif(
    not __import__("backend.state.redis_client", fromlist=["x"]).redis_is_reachable(),
    reason="needs a reachable Redis",
)
def test_starting_a_race_stores_both_before_the_job_is_enqueued(db, monkeypatch):
    """The two red lines above named main._run_race; this is the test that
    actually drives it, so those claims are checked rather than asserted.

    Ordering matters as much as presence: the manifest REFERENCES the
    source, and the worker seals a replay hash against the manifest. If the
    job reached a worker before the source was stored, a crash in between
    would leave exactly the unverifiable record this finding is about.

    Red lines: the `crud.save_bot_source(...)` loop and the
    `crud.save_replay_inputs(...)` call in main._run_race. Delete either
    and the corresponding assertion below goes red; move either after
    `_get_jobs().enqueue(job)` and the order assertion goes red.
    """
    import asyncio
    import time

    import backend.main as main
    from backend.state.lobby import LobbyStore

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(main.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(main, "SessionLocal", lambda: db)

    order = []
    real_save_source = crud.save_bot_source
    real_save_inputs = crud.save_replay_inputs

    monkeypatch.setattr(crud, "save_bot_source", lambda d, c: (
        order.append("source"), real_save_source(d, c))[1])
    monkeypatch.setattr(crud, "save_replay_inputs", lambda d, m, p: (
        order.append("inputs"), real_save_inputs(d, m, p))[1])
    monkeypatch.setattr(main, "_get_jobs", lambda: type(
        "Rec", (), {"enqueue": staticmethod(
            lambda job: order.append("enqueue"))})())

    race_id = f"m_store_{int(time.time() * 1000)}"
    store = LobbyStore()
    store.delete(race_id)
    store.create(race_id, track="bahrain", race_type="quick")
    store.add_player(race_id, "p1", {
        "username": "alex", "car_id": "USR-01", "code": CODE,
        "starting_compound": "SOFT",
    })
    db.db.races.insert_one({"id": race_id, "track": "bahrain",
                            "race_type": "quick", "status": "lobby",
                            "started_at": None, "finished_at": None})

    try:
        asyncio.run(main._run_race(race_id))
    finally:
        store.delete(race_id)

    assert order.index("source") < order.index("enqueue"), (
        f"source must be stored before the job is enqueued; got {order}"
    )
    assert order.index("inputs") < order.index("enqueue"), (
        f"inputs must be stored before the job is enqueued; got {order}"
    )

    digest = crud.code_sha256(CODE)
    assert crud.get_bot_source(db, digest) == CODE, (
        "the source a manifest references must exist by the time a worker "
        "can claim the job"
    )
    rows = crud.get_replay_inputs(db, race_id)
    player_row = next(r for r in rows if r["code_sha256"] == digest)
    assert player_row["starting_compound"] == "SOFT"
