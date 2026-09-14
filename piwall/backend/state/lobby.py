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
# ARGV[2] = "field" to set a top-level field, "player" to replace one whole
#           player dict, or "player_field" to set one field within one
#           player's dict without touching its other fields
# ARGV[3] = the field name (mode "field") or player id (modes "player" and
#           "player_field")
# ARGV[4] = the new value, JSON-encoded
# ARGV[5] = the field name within the player dict (mode "player_field" only)
#
# Returns false (-> None in Python) if the lobby does not exist (any mode),
# or if mode is "player_field" and no player with that id exists yet; else 1.
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
elseif ARGV[2] == 'player_field' then
    if lobby.players == nil or lobby.players[ARGV[3]] == nil then
        return false
    end
    lobby.players[ARGV[3]][ARGV[5]] = cjson.decode(ARGV[4])
else
    lobby[ARGV[3]] = cjson.decode(ARGV[4])
end
redis.call('SET', KEYS[1], cjson.encode(lobby), 'EX', ARGV[1])
return 1
"""

# KEYS[1] = the lobby key
# ARGV[1] = TTL seconds
# ARGV[2] = max_players
# ARGV[3] = player id
# ARGV[4] = player data, JSON-encoded. If data.car_id is missing/empty and
#           this is a new player, the script assigns one -- see below.
#
# A read-count-then-decide-then-write across an HTTP handler -- what
# join_race used to do with .get() plus a Python-side len() check -- lets
# two replicas both see "seven players, room for one more" and both admit
# an eighth, landing on nine. Folding the capacity check into the same Lua
# step as the write closes that: only one of two concurrent joins for the
# last slot can be the one whose EVAL actually sees room.
#
# The same reasoning applies to car_id: it is an engine identity, not a
# display label (main.py's own docstring on CAR_ID_PATTERN says so, and
# engine/race.py keys self.strategies and self.belief_models by it), so
# two concurrent new joins computing a default from a pre-write snapshot
# could both land on "P01" and hand one player's bot both cars. A default
# is therefore assigned HERE, inside the same atomic step that already
# knows the true pre-write count, so two concurrent new joins can never
# observe the same count and can never be assigned the same default.
#
# A re-join (the player id is already present) never counts against the
# cap and always succeeds -- it can only ever shrink or hold steady the
# player count, never grow it past max_players.
#
# Returns false if the lobby does not exist, else a JSON object
# {"full": true} if this is a new player and the lobby is already at
# max_players, or {"full": false, "is_new": bool, "count": <player count
# after this write>, "car_id": <the car_id actually stored>} otherwise.
_JOIN_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if raw == false then
    return false
end
local lobby = cjson.decode(raw)
if lobby.players == nil then
    lobby.players = {}
end
local player_id = ARGV[3]
local is_new = lobby.players[player_id] == nil
local count = 0
for _ in pairs(lobby.players) do
    count = count + 1
end
if is_new and count >= tonumber(ARGV[2]) then
    return cjson.encode({full = true})
end
local player_data = cjson.decode(ARGV[4])
if player_data.car_id == nil or player_data.car_id == cjson.null or player_data.car_id == '' then
    player_data.car_id = string.format("P%02d", count + 1)
end
lobby.players[player_id] = player_data
redis.call('SET', KEYS[1], cjson.encode(lobby), 'EX', ARGV[1])
local new_count = count
if is_new then
    new_count = count + 1
end
return cjson.encode({
    full = false, is_new = is_new, count = new_count, car_id = player_data.car_id
})
"""


class LobbyFullError(Exception):
    """join() raised this: a new player arrived after the lobby already
    reached max_players. Never raised for a re-join."""


class LobbyStore:
    """Reads and writes lobby documents. Safe to instantiate per request."""

    def __init__(self, redis_client=None):
        self._redis = redis_client or get_redis()
        self._mutate = self._redis.register_script(_MUTATE_SCRIPT)
        self._join_script = self._redis.register_script(_JOIN_SCRIPT)

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

    def set_player_field(self, race_id: str, player_id: str, field: str, value) -> None:
        """Set one field of one player's dict without touching the rest.

        add_player replaces a player's whole dict, which is correct for a
        join (there is no prior state to preserve) and wrong for a partial
        update: a caller that read the dict, changed one field in Python
        and wrote the whole thing back with add_player would silently
        clobber any other field a concurrent request changed in between.
        This does the read-modify-write inside the same Lua script as
        every other mutation here, so there is no such window.
        """
        ok = self._mutate(
            keys=[self._key(race_id)],
            args=[TTL_SECONDS, "player_field", player_id, json.dumps(value), field],
        )
        if not ok:
            raise KeyError(f"no lobby {race_id!r} or no player {player_id!r} in it")

    def join(self, race_id: str, player_id: str, data: dict, max_players: int = 8):
        """Atomically add a new player if there is room, or overwrite an
        existing player's row (a re-join, which never counts against the
        cap).

        If data["car_id"] is missing or empty and this is a new player,
        the script assigns one from the same atomic step that determined
        the pre-write player count -- never from a count read separately
        beforehand, which two concurrent new joins could both see as the
        same value and so both compute the same default. car_id is an
        engine identity (main.py keys belief dicts by it; engine/race.py
        keys self.strategies and self.belief_models by it), not cosmetic
        display text, so two players landing on the same one is not a
        display glitch -- it hands one player's bot both cars.

        Returns (is_new, player_count, car_id) — player_count is the
        lobby's total after this write and car_id is whatever was
        actually stored (the caller's own, if given), both computed
        inside the same atomic step rather than read separately
        afterwards (which would itself race a concurrent join). Raises
        KeyError if the lobby does not exist, LobbyFullError if this is a
        new player and the lobby is already at max_players.
        """
        raw = self._join_script(
            keys=[self._key(race_id)],
            args=[TTL_SECONDS, max_players, player_id, json.dumps(data)],
        )
        # A Lua `false` return arrives via RESP as nil, which redis-py
        # decodes as None, not Python False -- checking `is False` here
        # would never match and every "no lobby" case would instead crash
        # inside json.loads(None) one line down.
        if raw is None:
            raise KeyError(f"no lobby {race_id!r}")
        result = json.loads(raw)
        if result["full"]:
            raise LobbyFullError(f"lobby {race_id!r} is already full")
        return result["is_new"], result["count"], result["car_id"]

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
