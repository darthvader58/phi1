"""One-shot migration: replace plaintext api_key with api_key_hash.

Existing keys keep working — the same raw key hashes to the stored digest.
Run once, from piwall/, with MONGODB_URI set:
    PYTHONPATH=. python scripts/migrate_hash_api_keys.py
"""

from backend.db.crud import hash_api_key
from backend.db.models import create_db_engine

if __name__ == "__main__":
    db = create_db_engine()
    migrated = 0
    for doc in db.players.find({"api_key": {"$exists": True}}):
        db.players.update_one(
            {"_id": doc["_id"]},
            {
                "$set": {"api_key_hash": hash_api_key(doc["api_key"])},
                "$unset": {"api_key": ""},
            },
        )
        migrated += 1
    print(f"migrated {migrated} player(s)")
