"""Lobby state in Redis, so every API replica sees the same lobby.

This replaces `active_lobbies`, a module-global dict. With two replicas that
dict meant a lobby existed on whichever process happened to serve the create
call and nowhere else, so a join routed to the other replica returned "Race
not found".

What deliberately does NOT live here: the set of WebSocket connections. A
socket is a live object owned by one process and cannot be shared through
Redis. Each replica keeps its own set and learns when to use it from the
pub/sub events in backend/jobs/events.py.

Fields are stored as a JSON document under one key rather than as a Redis hash
of loose fields, because a lobby is read as a whole and a hash would invite
partial reads that see a half-updated lobby.
"""

import json
from typing import Optional

from .redis_client import get_redis

KEY_PREFIX = "piwall:lobby:"
OPEN_STATUSES = ("lobby", "countdown", "running")

# Lobbies expire so an abandoned one does not occupy Redis forever. Six hours
# is far longer than any match and short enough that a leak is self-healing.
TTL_SECONDS = 6 * 60 * 60


class LobbyStore:
    """Reads and writes lobby documents. Safe to instantiate per request."""

    def __init__(self, redis_client=None):
        self._redis = redis_client or get_redis()

    def _key(self, race_id: str) -> str:
        return f"{KEY_PREFIX}{race_id}"

    def create(self, race_id: str, track: str, race_type: str = "quick") -> dict:
        lobby = {
            "race_id": race_id,
            "track": track,
            "race_type": race_type,
            "status": "lobby",
            "speed": 1.0,
            "players": {},
        }
        self._write(lobby)
        return lobby

    def get(self, race_id: str) -> Optional[dict]:
        raw = self._redis.get(self._key(race_id))
        return json.loads(raw) if raw else None

    def _write(self, lobby: dict) -> None:
        self._redis.set(
            self._key(lobby["race_id"]), json.dumps(lobby), ex=TTL_SECONDS
        )

    def _require(self, race_id: str) -> dict:
        lobby = self.get(race_id)
        if lobby is None:
            raise KeyError(f"no lobby {race_id!r}")
        return lobby

    def set_status(self, race_id: str, status: str) -> None:
        lobby = self._require(race_id)
        lobby["status"] = status
        self._write(lobby)

    def set_speed(self, race_id: str, speed: float) -> None:
        lobby = self._require(race_id)
        lobby["speed"] = float(speed)
        self._write(lobby)

    def add_player(self, race_id: str, player_id: str, data: dict) -> None:
        lobby = self._require(race_id)
        lobby["players"][player_id] = data
        self._write(lobby)

    def players(self, race_id: str) -> dict:
        return self._require(race_id)["players"]

    def list_open(self) -> list:
        """Every lobby not yet finished or aborted.

        SCAN rather than KEYS: KEYS blocks Redis for the whole keyspace, which
        is fine with three lobbies and an outage with thirty thousand.
        """
        out = []
        for key in self._redis.scan_iter(match=f"{KEY_PREFIX}*", count=100):
            raw = self._redis.get(key)
            if not raw:
                continue
            lobby = json.loads(raw)
            if lobby.get("status") in OPEN_STATUSES:
                out.append(lobby)
        return sorted(out, key=lambda l: l["race_id"])

    def delete(self, race_id: str) -> None:
        self._redis.delete(self._key(race_id))
