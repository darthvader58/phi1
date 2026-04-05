"""MongoDB helpers for PIT WALL persistent data."""

import os
from types import SimpleNamespace
from urllib.parse import urlparse

from pymongo import ASCENDING, DESCENDING, MongoClient


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


def init_db(db):
    db.players.create_index([("id", ASCENDING)], unique=True)
    db.players.create_index([("username", ASCENDING)], unique=True)
    db.players.create_index([("api_key", ASCENDING)], unique=True)
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

    return lambda: MongoSession(db)


def to_namespace(document):
    if document is None:
        return None
    payload = {key: value for key, value in document.items() if key != "_id"}
    return SimpleNamespace(**payload)
