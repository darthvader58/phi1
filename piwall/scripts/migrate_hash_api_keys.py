"""One-shot migration: replace plaintext api_key with api_key_hash.

Existing keys keep working — the same raw key hashes to the stored digest.
Run once, from piwall/, with MONGODB_URI set:
    PYTHONPATH=. python scripts/migrate_hash_api_keys.py

Self-sufficient: does not depend on init_db() having already dropped the
stale plaintext unique index, so it defensively drops it itself before
migrating. Re-runnable: the selection filter ({"api_key": {"$exists": True}})
only matches unmigrated documents, so a second run is a clean no-op. Each
document is migrated independently — one failure does not strand the rest.
"""

from pymongo.errors import OperationFailure

from backend.db.crud import hash_api_key
from backend.db.models import create_db_engine

if __name__ == "__main__":
    db = create_db_engine()

    # A unique index on the plaintext key predates this migration. Once a
    # document's api_key is $unset, it collides with every other migrated
    # document on null unless this stale index is gone first. Don't assume
    # init_db() has run — drop it here too, idempotently.
    try:
        db.players.drop_index("api_key_1")
    except OperationFailure:
        pass

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
