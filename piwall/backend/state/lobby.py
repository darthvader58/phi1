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
of loose fields. Redis is single-threaded and HGETALL is atomic, so that is
not about partial reads — it is because one key keeps the TTL and `delete`
semantics simple: a hash of loose fields would mean separate expirations and
a multi-field delete to get the same guarantee. The document is always read
whole, and every mutating write goes through a Lua script (below) rather than
a client-side get-modify-set, so the read-modify-write itself is atomic too:
two replicas racing to mutate the same lobby cannot lose one of their writes.
"""

import json
from typing import Optional

from .redis_client import get_redis

KEY_PREFIX = "piwall:lobby:"
OPEN_STATUSES = ("lobby", "countdown", "running")

# Lobbies expire so an abandoned one does not occupy Redis forever. Six hours
# is far longer than any match and short enough that a leak is self-healing.
TTL_SECONDS = 6 * 60 * 60

# A client-side GET, modify in Python, SET is correct with one process and
# wrong with two: replica A's GET can be followed by replica B's complete
# GET-modify-SET, after which replica A's SET writes back a version that
# never saw B's change, overwriting it — a lost update, and nothing raises.
# This script runs the whole get-decode-modify-encode-set cycle as one
# atomic step inside Redis (Redis is single-threaded and EVAL blocks the
# keyspace only for the script's own duration), so there is no window for
# another replica's write to land in between.
#
# KEYS[1] = the lobby key
# ARGV[1] = TTL seconds, re-applied on every write so a mutation refreshes it
# ARGV[2] = "field" to set a top-level field, or "player" to set one player
# ARGV[3] = the field name (mode "field") or player id (mode "player")
# ARGV[4] = the new value, JSON-encoded
#
# Returns false (-> None in Python) if the lobby does not exist, else 1.
_MUTATE_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if raw == false then
    return false
end
local lobby = cjson.decode(raw)
if ARGV[2] == 'player' then
    if lobby.players == nil then
        lobby.players = {}
    end
    lobby.players[ARGV[3]] = cjson.decode(ARGV[4])
else
    lobby[ARGV[3]] = cjson.decode(ARGV[4])
end
redis.call('SET', KEYS[1], cjson.encode(lobby), 'EX', ARGV[1])
return 1
"""


class LobbyStore:
    """Reads and writes lobby documents. Safe to instantiate per request."""

    def __init__(self, redis_client=None):
        self._redis = redis_client or get_redis()
        self._mutate = self._redis.register_script(_MUTATE_SCRIPT)

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
        return self._decode(raw) if raw else None

    def _decode(self, raw: str) -> dict:
        lobby = json.loads(raw)
        # Redis has no float type; a whole-number speed (5.0) comes back
        # from Lua/JSON as the int 5, and 11.0 / 5 behaves very differently
        # from 11.0 / 5.0 the moment someone divides by it. Restoring the
        # type here, once, means every caller gets a real float regardless
        # of how the value happened to be encoded on the way in.
        if "speed" in lobby:
            lobby["speed"] = float(lobby["speed"])
        return lobby

    def _write(self, lobby: dict) -> None:
        self._redis.set(
            self._key(lobby["race_id"]), json.dumps(lobby), ex=TTL_SECONDS
        )

    def _atomic_set(self, race_id: str, mode: str, name: str, value) -> None:
        ok = self._mutate(
            keys=[self._key(race_id)],
            args=[TTL_SECONDS, mode, name, json.dumps(value)],
        )
        if not ok:
            raise KeyError(f"no lobby {race_id!r}")

    def set_status(self, race_id: str, status: str) -> None:
        self._atomic_set(race_id, "field", "status", status)

    def set_speed(self, race_id: str, speed: float) -> None:
        self._atomic_set(race_id, "field", "speed", float(speed))

    def add_player(self, race_id: str, player_id: str, data: dict) -> None:
        self._atomic_set(race_id, "player", player_id, data)

    def players(self, race_id: str) -> dict:
        lobby = self.get(race_id)
        if lobby is None:
            raise KeyError(f"no lobby {race_id!r}")
        return lobby["players"]

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
            lobby = self._decode(raw)
            if lobby.get("status") in OPEN_STATUSES:
                out.append(lobby)
        return sorted(out, key=lambda l: l["race_id"])

    def delete(self, race_id: str) -> None:
        self._redis.delete(self._key(race_id))
