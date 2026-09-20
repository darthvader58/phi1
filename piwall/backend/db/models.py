"""MongoDB helpers for PIT WALL persistent data."""

import logging
import os
from types import SimpleNamespace
from urllib.parse import urlparse

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure


def _resolve_database_name(url: str) -> str:
    explicit_name = os.environ.get("MONGODB_DB")
    if explicit_name:
        return explicit_name

    parsed = urlparse(url)
    path_name = parsed.path.lstrip("/")
    return path_name or "phi1"


class MongoSession:
    """Tiny session-like wrapper so the rest of the app can keep its structure."""

    def __init__(self, db):
        self.db = db

    def close(self):
        return None


def mongo_url() -> str:
    """The Mongo connection string. The ONE definition of it.

    main imports the health router and the worker imports neither, so both
    need this without reaching back into the API module -- and it lives here
    because this module already owns database configuration.

    Everything that resolves a connection string goes through this,
    including create_db_engine's own no-arg fallback below. That fallback
    used to read MONGODB_URI but not DATABASE_URL, which made it a third
    precedence rule alongside this one and main's copy: a deployment setting
    only DATABASE_URL got the localhost default from some call sites and the
    real server from others, with nothing to say so.
    """
    return (
        os.environ.get("MONGODB_URI")
        or os.environ.get("DATABASE_URL")
        or "mongodb://127.0.0.1:27017/phi1"
    )


def create_db_engine(url: str | None = None):
    resolved = url or mongo_url()
    client = MongoClient(resolved)
    db = client[_resolve_database_name(resolved)]
    return db


# MongoDB reports "an index with this name exists but with different options"
# as IndexOptionsConflict (85); older servers used IndexKeySpecsConflict (86)
# for the same situation when the key pattern also differed.
_INDEX_CONFLICT_CODES = frozenset({85, 86})


def _is_conflicting_index_options(exc: OperationFailure) -> bool:
    """True when create_index failed purely because the index already exists
    with different options -- the one case drop-and-recreate can fix."""
    if exc.code in _INDEX_CONFLICT_CODES:
        return True
    # Very old servers report the conflict without a usable code.
    return exc.code is None and "already exists with different options" in str(exc)


def init_db(db):
    db.players.create_index([("id", ASCENDING)], unique=True)
    db.players.create_index([("username", ASCENDING)], unique=True)

    # A pre-hash deployment leaves a unique index on the plaintext key. Once the
    # migration unsets that field every migrated player collides on null, so the
    # stale index must go. Idempotent: absent on a fresh database.
    try:
        db.players.drop_index("api_key_1")
    except OperationFailure:
        pass

    # Partial, so players not yet migrated (no api_key_hash yet) are not indexed
    # at all instead of all colliding on null and crashing startup.
    try:
        db.players.create_index(
            [("api_key_hash", ASCENDING)],
            unique=True,
            partialFilterExpression={"api_key_hash": {"$exists": True}},
        )
    except OperationFailure as exc:
        # Only one failure is recoverable here: an index named api_key_hash_1
        # already exists with different options (e.g. a non-partial unique
        # index from an earlier deploy). Anything else -- a transient auth
        # failure, a malformed filter -- must surface as itself, because
        # dropping and recreating would fail again and bury the real cause
        # under a more confusing error.
        if not _is_conflicting_index_options(exc):
            raise
        db.players.drop_index("api_key_hash_1")
        db.players.create_index(
            [("api_key_hash", ASCENDING)],
            unique=True,
            partialFilterExpression={"api_key_hash": {"$exists": True}},
        )

    db.players.create_index([("elo", DESCENDING)])

    db.seasons.create_index([("id", ASCENDING)], unique=True)
    db.seasons.create_index([("active", ASCENDING)])

    db.races.create_index([("id", ASCENDING)], unique=True)
    db.races.create_index([("status", ASCENDING)])
    db.races.create_index([("season_id", ASCENDING), ("created_at", ASCENDING)])

    db.race_results.create_index([("id", ASCENDING)], unique=True)
    db.race_results.create_index([("race_id", ASCENDING), ("position", ASCENDING)])
    db.race_results.create_index([("player_id", ASCENDING), ("race_id", ASCENDING)])

    # crud.save_race_results upserts one row per car keyed by
    # (race_id, player_id) rather than a delete-then-insert specifically so
    # this index can make a concurrent double-persist (two workers racing
    # on the same match after a stalled reclaim) impossible rather than
    # merely unlikely -- without it, two upserts that both find no existing
    # row for the same key can each decide to insert, doubling a race's
    # championship points.
    _create_unique_index_or_log(
        db.race_results, [("race_id", ASCENDING), ("player_id", ASCENDING)],
        "race_results",
    )

    db.bot_submissions.create_index([("id", ASCENDING)], unique=True)
    db.bot_submissions.create_index([("player_id", ASCENDING), ("submitted_at", DESCENDING)])

    db.elo_history.create_index([("id", ASCENDING)], unique=True)
    db.elo_history.create_index([("player_id", ASCENDING), ("created_at", ASCENDING)])

    # The worker's job queue is at-least-once: a redelivered match must not
    # apply its Elo update twice. backend/worker.py's _persist_result writes
    # each player's history row BEFORE moving their rating specifically so
    # this index can refuse a duplicate before the damage is done, rather
    # than merely reporting it afterwards.
    _create_unique_index_or_log(
        db.elo_history, [("player_id", ASCENDING), ("race_id", ASCENDING)],
        "elo_history",
    )

    db.manifests.create_index([("match_id", ASCENDING)], unique=True)

    # Player source, content-addressed. A manifest names a participant's
    # code only by its code_sha256, so this is the store that makes that
    # name resolvable -- without it the manifest references source that
    # exists nowhere and the replay hash it seals cannot be re-derived even
    # in principle. Unique because the key IS the content: two rows for one
    # digest would mean one of them is not what it claims to be.
    db.bot_sources.create_index([("code_sha256", ASCENDING)], unique=True)

    # The per-participant inputs a match had that the manifest schema does
    # not yet carry -- today, starting_compound. See crud.save_replay_inputs.
    db.replay_inputs.create_index([("match_id", ASCENDING)], unique=True)

    return lambda: MongoSession(db)


def _create_unique_index_or_log(collection, keys, collection_name: str) -> None:
    """create_index(unique=True), tolerating pre-existing duplicates.

    create_index(unique=True) raises DuplicateKeyError if the collection
    already holds a document pair that violates the new index -- exactly
    the kind of row the index exists to prevent could have already
    written on a live deployment, before the index existed. init_db()
    runs from both the API's lifespan and the worker's run_forever, so an
    uncaught raise here would refuse to let either process start at all
    over a data problem a boot cannot fix. Logging and continuing without
    the index is safer than bricking the boot; whatever code-level
    ordering the index backs up is still in effect either way, just
    without this extra check until the collection is deduped and the
    process restarted.
    """
    try:
        collection.create_index(keys, unique=True)
    except DuplicateKeyError as exc:
        logging.getLogger("piwall").error(
            "%s already has a duplicate %s; the protective unique index "
            "was NOT created. Dedupe the collection and restart to "
            "re-enable it. %s", collection_name, keys, exc,
        )


def to_namespace(document):
    if document is None:
        return None
    payload = {key: value for key, value in document.items() if key != "_id"}
    return SimpleNamespace(**payload)
