"""Credential migration for the API-key hashing change.

Two modes, run in this order against a pre-existing database.

1. Migrate (default) -- replace each plaintext `api_key` with `api_key_hash`.
   Existing keys keep working: the same raw key hashes to the stored digest.

       PYTHONPATH=. MONGODB_URI=... python scripts/migrate_hash_api_keys.py

   This is a required pre-start step. `init_db` drops the stale `api_key_1`
   index at startup and `get_player_by_api_key` queries only the hash, so
   every unmigrated player gets 401 until this has run.

2. Rotate (`--rotate`) -- issue a brand-new key to every player.

       PYTHONPATH=. MONGODB_URI=... python scripts/migrate_hash_api_keys.py --rotate

   Hashing at rest does not un-leak a key that was already plaintext. Every
   key that existed before the hashing change has been sitting in Mongo and
   in each player's browser localStorage, so it must be treated as disclosed
   and replaced. Rotation invalidates the old key immediately.

   Rotation also rewrites `playerProfiles.backendApiKey` for any profile that
   maps to the rotated player, because that is where the web app reads the
   key it presents on the player's behalf. A player with no such profile --
   an API-only account, or one whose profile lives in a different database --
   is reported separately: those keys must be redistributed by hand, and this
   script deliberately never prints one.

Both modes are re-runnable. Migration selects only unmigrated documents
({"api_key": {"$exists": True}}), so a second run is a clean no-op. Each
document is handled independently: one failure does not strand the rest.

Migration is self-sufficient -- it does not depend on init_db() having already
dropped the stale plaintext unique index, and defensively drops it itself.
"""

import argparse
import secrets

from pymongo.errors import OperationFailure

from backend.db.crud import hash_api_key
from backend.db.models import create_db_engine


def drop_stale_plaintext_index(db) -> None:
    """A unique index on the plaintext key predates this migration.

    Once a document's api_key is $unset it collides with every other migrated
    document on null unless this stale index is gone first. Don't assume
    init_db() has run -- drop it here too, idempotently.
    """
    try:
        db.players.drop_index("api_key_1")
    except OperationFailure:
        pass


def migrate(db) -> int:
    migrated = 0
    failed = 0
    for doc in db.players.find({"api_key": {"$exists": True}}):
        try:
            db.players.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {"api_key_hash": hash_api_key(doc["api_key"])},
                    "$unset": {"api_key": ""},
                },
            )
            migrated += 1
        except Exception as exc:  # noqa: BLE001 - one bad doc must not stop the rest
            failed += 1
            print(f"failed to migrate player _id={doc.get('_id')!r}: {exc}")

    print(f"migrated {migrated} player(s), {failed} failure(s)")
    return failed


def rotate(db) -> int:
    rotated = 0
    relinked = 0
    orphaned = 0
    failed = 0
    for doc in db.players.find({}, {"id": 1, "username": 1}):
        raw_key = f"pw_{secrets.token_hex(24)}"
        try:
            db.players.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {"api_key_hash": hash_api_key(raw_key)},
                    "$unset": {"api_key": ""},
                },
            )
            rotated += 1
        except Exception as exc:  # noqa: BLE001 - one bad doc must not stop the rest
            failed += 1
            print(f"failed to rotate player _id={doc.get('_id')!r}: {exc}")
            continue

        # Hand the new key to the web app, which presents it for the player.
        # Match on the backend player id first; username is the fallback for
        # profiles written before backendPlayerId was stored.
        result = db.playerProfiles.update_one(
            {"$or": [{"backendPlayerId": doc.get("id")},
                     {"backendUsername": doc.get("username")}]},
            {"$set": {"backendApiKey": raw_key}},
        )
        if result.matched_count:
            relinked += 1
        else:
            orphaned += 1

    print(f"rotated {rotated} player(s), {failed} failure(s)")
    print(f"re-linked {relinked} web profile(s); {orphaned} player(s) have no profile "
          f"in this database and must be re-issued a key by hand")
    return failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="issue a fresh key to every player, invalidating keys that leaked "
             "as plaintext before hashing. Run after the default migration.",
    )
    args = parser.parse_args()

    db = create_db_engine()
    drop_stale_plaintext_index(db)

    failures = rotate(db) if args.rotate else migrate(db)
    raise SystemExit(1 if failures else 0)
