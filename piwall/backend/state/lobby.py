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

from .car_ids import RESERVED_CAR_IDS
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
# ARGV[2] = "field" to set a top-level field, "field_if" to set one only
#           when its current value is in an allowed set, "player" to replace
#           one whole player dict, or "player_field" to set one field within
#           one player's dict without touching its other fields
# ARGV[3] = the field name (modes "field" and "field_if") or player id
#           (modes "player" and "player_field")
# ARGV[4] = the new value, JSON-encoded
# ARGV[5] = the field name within the player dict (mode "player_field"), or
#           a JSON array of acceptable current values (mode "field_if")
#
# Returns false (-> None in Python) if the lobby does not exist (any mode),
# if mode is "player_field" and no player with that id exists yet, or if
# mode is "field_if" and the field's current value is not in the allowed
# set; else 1.
#
# "field_if" is a compare-and-set, and it exists because a check in an HTTP
# handler is not one. start_race read the lobby, checked status == "lobby",
# and then wrote "countdown"; two concurrent /start calls both passed the
# check and both spawned a _run_race, which built two jobs with two
# different random seeds for one race. The loser's save_manifest raised and
# its except block marked the WINNER'S live race aborted -- permanently, if
# it landed after the worker's finish. Folding the check into the same
# atomic step as the write is the only place it cannot be raced, exactly as
# _JOIN_SCRIPT already does for capacity and car_id.
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
elseif ARGV[2] == 'field_if' then
    local allowed = false
    for _, value in ipairs(cjson.decode(ARGV[5])) do
        if lobby[ARGV[3]] == value then
            allowed = true
        end
    end
    if not allowed then
        return false
    end
    lobby[ARGV[3]] = cjson.decode(ARGV[4])
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
# ARGV[4] = player data, JSON-encoded. If data.car_id is missing/empty the
#           script supplies one -- see below.
# ARGV[5] = JSON array of car_ids reserved by cars that are not lobby
#           players (the house bots), which no player may be assigned or
#           claim
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
# could both land on "P01" and hand one player's bot both cars. Everything
# that decides a car_id therefore happens HERE, inside the one atomic step
# that can see the lobby's true pre-write contents:
#
#   * A RE-JOIN that supplies NO car_id keeps the one the player already
#     holds, unless keeping it is impossible -- see the auto-heal note
#     under join()'s docstring. Round 3 derived the default from the pre-write player count,
#     which for a re-join already counts the rejoining player -- so
#     re-joining moved that player to a fresh label and left the one they
#     vacated free for the next genuinely-new player to be handed as well.
#     Four sequential API calls (join A, join B, re-join A, join C) were
#     enough to put two 'P03's in one lobby, and the shipped frontend never
#     sends a car_id at all (frontend/src/lib/api.ts), so that was the
#     DEFAULT path. A re-join that supplies a DIFFERENT unclaimed car_id is
#     honoured and frees the old label -- deliberate, see join()'s
#     docstring.
#   * A NEW player with no car_id gets the lowest P%02d that no other car
#     in the race holds, rather than count + 1 -- a label derived from the
#     count is only unique while no label has ever been vacated, which is
#     precisely the assumption the re-join bug broke.
#   * An EXPLICIT car_id that another player in this lobby already holds is
#     refused. That is the same collision by a different route, and a
#     caller cannot check for it beforehand without reopening the very
#     read-then-write window this script exists to close.
#   * A car_id belonging to a HOUSE BOT is refused, and never assigned as a
#     default, the same way. House bots are not lobby players, so the
#     "taken" set built from the lobby alone never covered them -- and
#     every race gets them appended, so claiming one put two cars called
#     VEL-01 on the grid. They are passed in as ARGV[5] (see
#     state/car_ids.py, which states this invariant once for every source
#     of a car_id) rather than hardcoded here, because Lua cannot see
#     BUILTIN_BOTS and a set that drifts from it would be worse than none.
#
# A re-join (the player id is already present) never counts against the
# cap and always succeeds -- it can only ever shrink or hold steady the
# player count, never grow it past max_players.
#
# Returns false if the lobby does not exist, else a JSON object:
#   {"full": true}                    new player, lobby already at max
#   {"full": false, "taken": true, "car_id": <id>}
#                                     car_id held by another car in the race
#   {"full": false, "taken": false, "is_new": bool,
#    "count": <player count after this write>,
#    "car_id": <the car_id actually stored>}
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
local existing = lobby.players[player_id]
local is_new = existing == nil
local count = 0
for _ in pairs(lobby.players) do
    count = count + 1
end
if is_new and count >= tonumber(ARGV[2]) then
    return cjson.encode({full = true})
end
local player_data = cjson.decode(ARGV[4])

-- A car_id absent from the JSON arrives as nil; an explicit JSON null
-- arrives as cjson.null, which is a sentinel userdata and NOT nil.
local function blank(value)
    return value == nil or value == cjson.null or value == ''
end

-- Every car_id already spoken for by a car OTHER than the one being
-- written: first the identities house bots bring to every race, then the
-- other players in this lobby. The rejoining player's own current label
-- is deliberately excluded, so re-sending it (or keeping it, below) is
-- never mistaken for a clash.
local taken = {}
for _, reserved in ipairs(cjson.decode(ARGV[5])) do
    taken[reserved] = true
end
for pid, p in pairs(lobby.players) do
    if pid ~= player_id and type(p) == 'table' and not blank(p.car_id) then
        taken[p.car_id] = true
    end
end

if blank(player_data.car_id) and not is_new
        and not blank(existing.car_id) and not taken[existing.car_id] then
    player_data.car_id = existing.car_id
end
if blank(player_data.car_id) then
    local n = 1
    while taken[string.format('P%02d', n)] do
        n = n + 1
    end
    player_data.car_id = string.format('P%02d', n)
elseif taken[player_data.car_id] then
    return cjson.encode({
        full = false, taken = true, car_id = player_data.car_id
    })
end

lobby.players[player_id] = player_data
redis.call('SET', KEYS[1], cjson.encode(lobby), 'EX', ARGV[1])
local new_count = count
if is_new then
    new_count = count + 1
end
return cjson.encode({
    full = false, taken = false, is_new = is_new, count = new_count,
    car_id = player_data.car_id
})
"""


class LobbyFullError(Exception):
    """join() raised this: a new player arrived after the lobby already
    reached max_players. Never raised for a re-join."""


class CarIdTakenError(Exception):
    """join() raised this: the caller asked for a car_id that another
    player in this lobby already holds.

    car_id is an engine identity (engine/race.py keys self.strategies and
    self.belief_models by it), so honouring the request would hand one
    player's bot both cars. Never raised for a player re-sending the
    car_id they already hold."""


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
            "players": {},
        }
        self._write(lobby)
        return lobby

    def get(self, race_id: str) -> Optional[dict]:
        raw = self._redis.get(self._key(race_id))
        return self._decode(raw) if raw else None

    def _decode(self, raw: str) -> dict:
        return json.loads(raw)

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

    def set_status_if(self, race_id: str, status: str, expected) -> bool:
        """Move the status only if it currently holds one of `expected`.

        Returns True if this call made the transition, False if the lobby is
        gone or its status was something else. The caller that gets True is
        the only one that made it, which is what makes it safe to act on --
        two replicas racing the same transition cannot both be told yes.

        Never raises KeyError the way _atomic_set does: "the lobby moved on"
        and "there is no lobby" are the same answer to the only question
        this asks, which is "am I the one who may proceed".
        """
        ok = self._mutate(
            keys=[self._key(race_id)],
            args=[TTL_SECONDS, "field_if", "status", json.dumps(status),
                  json.dumps(list(expected))],
        )
        return bool(ok)

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

        car_id is an engine identity (main.py keys belief dicts by it;
        engine/race.py keys self.strategies and self.belief_models by
        it), not cosmetic display text, so two players in one lobby
        landing on the same one is not a display glitch -- it hands one
        player's bot both cars, and the manifest and replay hash seal
        that in. Every decision about it is therefore made inside the
        script, where the read and the write are a single atomic step:

        - A re-join (this player id is already present) that supplies NO
          car_id -- data["car_id"] missing, empty or JSON null, which is
          every join the shipped frontend makes -- KEEPS the car_id it
          already holds. Deriving a fresh default for a re-join is what
          round 3 did, and it both moved that player and freed their old
          label for the next new player to be handed too.
        - A re-join that supplies a DIFFERENT, unclaimed car_id is
          honoured, and the label it vacates becomes available again.
          This is deliberate, not an oversight (round 5, NEW-10): car_id
          is a documented field of the join request, and refusing it on
          a re-join would make the identical request succeed or fail
          depending on whether the caller had joined before, leaving a
          player no way to correct an id they regret. Nothing durable is
          keyed on it yet either -- join_race refuses any status but
          "lobby", and the identity is only sealed when
          _build_job_and_manifest builds the manifest at race start. The
          rename goes through the same collision check as any other
          explicit id, so uniqueness holds at every step.
        - A new player with no car_id gets the lowest "P%02d" that no
          other car in the race holds -- not one derived from the player
          count, which stops being unique the moment any label is
          vacated.
        - An explicit car_id another car in the race already holds is
          refused, whether that car is another lobby player or one of
          the house bots appended to every race (see state/car_ids.py).

        Returns (is_new, player_count, car_id) — player_count is the
        lobby's total after this write and car_id is whatever was
        actually stored (the caller's own, if given), both computed
        inside the same atomic step rather than read separately
        afterwards (which would itself race a concurrent join). Raises
        KeyError if the lobby does not exist, LobbyFullError if this is a
        new player and the lobby is already at max_players, and
        CarIdTakenError if the car_id being stored belongs to another car
        in this race.

        The keep rule has one exception, and it is the difference
        between a stuck lobby and a working one. A lobby written before
        house-bot ids were reserved can hold a player on "VEL-01". That
        id is now impossible to keep -- the race cannot start while it
        stands, because assert_unique_car_ids refuses the grid -- so a
        BLANK re-join by that player falls through to a fresh default
        instead of being refused. It heals the lobby rather than locking
        the player out of it.

        That is not round 4's rename bug returning. Round 4's bug renamed
        a player whose id was perfectly VALID, which freed a label and
        handed the collision to the next joiner. This fires only when the
        id cannot be kept at all, which `not taken[existing.car_id]`
        states directly: a valid id is never in `taken` (it excludes the
        rejoining player's own row), so the keep branch still fires for
        every ordinary re-join.

        An EXPLICIT request for a taken id is still refused, because
        there the caller named something and deserves to be told it is
        unavailable rather than quietly given something else.
        """
        raw = self._join_script(
            keys=[self._key(race_id)],
            args=[
                TTL_SECONDS, max_players, player_id, json.dumps(data),
                # Imported rather than passed in by the caller: every
                # caller of join() must get this guarantee, and a
                # parameter is something a future one can forget.
                json.dumps(sorted(RESERVED_CAR_IDS)),
            ],
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
        if result["taken"]:
            raise CarIdTakenError(
                f"car_id {result['car_id']!r} is already held by another "
                f"car in race {race_id!r}"
            )
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
