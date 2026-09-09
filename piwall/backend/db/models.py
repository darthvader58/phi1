"""MongoDB helpers for PIT WALL persistent data."""

import os
from types import SimpleNamespace
from urllib.parse import urlparse

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import OperationFailure


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


def create_db_engine(url: str | None = None):
    mongo_url = url or os.environ.get("MONGODB_URI") or "mongodb://127.0.0.1:27017/phi1"
    client = MongoClient(mongo_url)
    db = client[_resolve_database_name(mongo_url)]
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

    db.bot_submissions.create_index([("id", ASCENDING)], unique=True)
    db.bot_submissions.create_index([("player_id", ASCENDING), ("submitted_at", DESCENDING)])

    db.elo_history.create_index([("id", ASCENDING)], unique=True)
    db.elo_history.create_index([("player_id", ASCENDING), ("created_at", ASCENDING)])

    db.manifests.create_index([("match_id", ASCENDING)], unique=True)

    return lambda: MongoSession(db)


def to_namespace(document):
    if document is None:
        return None
    payload = {key: value for key, value in document.items() if key != "_id"}
    return SimpleNamespace(**payload)
