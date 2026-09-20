"""CRUD operations for PIT WALL database backed by MongoDB."""

import datetime
import hashlib
import secrets
import uuid
from typing import List, Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .models import to_namespace


def _now():
    return datetime.datetime.utcnow()


def _id():
    return str(uuid.uuid4())


def hash_api_key(raw: str) -> str:
    """Hash an API key for storage. Raw keys are never persisted."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _player_doc(player):
    return {
        "id": player.id,
        "username": player.username,
        "api_key_hash": player.api_key_hash,
        "elo": player.elo,
        "team_name": player.team_name,
        "created_at": player.created_at,
        "role": getattr(player, "role", "player"),
    }


def create_player(db, username: str, team_name: str = "Independent"):
    raw_key = f"pw_{secrets.token_hex(24)}"
    player = to_namespace({
        "id": _id(),
        "username": username,
        "api_key_hash": hash_api_key(raw_key),
        "elo": 1200.0,
        "team_name": team_name,
        "created_at": _now(),
        "role": "player",
    })
    db.db.players.insert_one(_player_doc(player))
    player.api_key = raw_key  # transient: returned once, never persisted
    return player


def get_player_by_api_key(db, api_key: str):
    return to_namespace(
        db.db.players.find_one({"api_key_hash": hash_api_key(api_key)})
    )


def get_player_by_username(db, username: str):
    return to_namespace(db.db.players.find_one({"username": username}))


def get_player_by_id(db, player_id: str):
    return to_namespace(db.db.players.find_one({"id": player_id}))


def update_player_elo(db, player_id: str, new_elo: float):
    """Set a player's rating outright. Prefer apply_player_elo for a match.

    Kept because it is the honest operation for anything that genuinely does
    know the value it wants (an admin correction, a fixture). It is a plain
    read-modify-write from the caller's point of view and carries the lost
    update that goes with one, which is why the match path does not use it.
    """
    db.db.players.update_one({"id": player_id}, {"$set": {"elo": new_elo}})


def apply_player_elo(db, player_id: str, elo_before: float, elo_after: float,
                     first_application: bool):
    """Move a player's rating by one match's delta, atomically.

    The old `$set` here was a lost update between two DIFFERENT matches
    finishing for the same player at once: both read the pre-race rating,
    both computed from it, and the second `$set` discarded the first match's
    change while both elo_history rows persisted -- so the history and the
    rating disagreed permanently. Redelivery of the same match was safe
    (determinism gives the same number); concurrency across matches was not,
    and the whole point of this phase is that matches now finish
    asynchronously on a fleet instead of serially inside one API process.

    Two paths, because the safe write differs by what the caller knows:

    * first_application -- the caller just inserted this player's
      elo_history row for this race, and the unique index on
      (player_id, race_id) means that insert happens at most once ever. So
      this branch runs at most once per transition and `$inc` is exactly
      right: it is atomic in the server, it composes with a concurrent
      match's `$inc`, and it cannot be applied twice.
    * otherwise -- the row already existed, so an earlier delivery may or
      may not have got as far as moving the rating. `$inc` here could double
      the move. A compare-and-set from the pre-race value applies it only if
      the rating is still exactly what it was before this race, which is the
      one state in which the move is definitely outstanding. If it is not,
      either this transition was already applied or another match has since
      moved the rating -- and in both cases doing nothing is right, where
      the old `$set` would have clobbered the other match.

    Returns True if this call moved the rating.
    """
    if first_application:
        result = db.db.players.update_one(
            {"id": player_id}, {"$inc": {"elo": elo_after - elo_before}}
        )
    else:
        result = db.db.players.update_one(
            {"id": player_id, "elo": elo_before}, {"$set": {"elo": elo_after}}
        )
    return bool(result.modified_count)


def get_leaderboard(db, limit: int = 50):
    return [to_namespace(doc) for doc in db.db.players.find({}).sort("elo", -1).limit(limit)]


def create_race(db, track: str, race_type: str = "quick", season_id: Optional[str] = None, weather_seed: Optional[int] = None, owner_id: Optional[str] = None):
    race = {
        "id": _id(),
        "season_id": season_id,
        "track": track,
        "race_type": race_type,
        "status": "lobby",
        "weather_seed": weather_seed,
        "created_at": _now(),
        "started_at": None,
        "finished_at": None,
        "lap_data_json": None,
        "events_json": None,
        "owner_id": owner_id,
    }
    db.db.races.insert_one(race)
    return to_namespace(race)


def get_race(db, race_id: str):
    return to_namespace(db.db.races.find_one({"id": race_id}))


def get_race_results(db, race_id: str):
    docs = db.db.race_results.find({"race_id": race_id}).sort("position", 1)
    return [to_namespace(doc) for doc in docs]


def get_active_races(db):
    docs = db.db.races.find({"status": {"$in": ["lobby", "countdown", "running"]}}).sort("created_at", -1)
    return [to_namespace(doc) for doc in docs]


# Which status stamps which timestamp. A table rather than a chain of ifs
# because abort_race below needs the same rule and must not re-derive it.
_STATUS_TIMESTAMP = {"running": "started_at", "finished": "finished_at"}


def _stamp_once(db, race_id: str, field: str) -> None:
    """Set a timestamp only if it is not already set.

    The job queue is at-least-once, so a fully persisted match can be
    delivered again; an unconditional $set then walks the recorded finish
    time forward on every redelivery, and an operator XGROUP DESTROY walks
    every finish time in the stream forward at once. The filter is what makes
    this write value-idempotent like every other write in _persist_result.

    `{field: None}` matches a document where the field is null AND one where
    it is absent -- both of which mean "not stamped yet".
    """
    db.db.races.update_one(
        {"id": race_id, field: None}, {"$set": {field: _now()}}
    )


def update_race_status(db, race_id: str, status: str):
    field = _STATUS_TIMESTAMP.get(status)
    if field:
        _stamp_once(db, race_id, field)
    db.db.races.update_one({"id": race_id}, {"$set": {"status": status}})


def abort_race(db, race_id: str, reason: str):
    """Mark a race aborted, recording why, without overwriting a result.

    Filtered on a non-terminal status rather than written unconditionally.
    Two callers can reach here about a race that is already finished -- a
    redelivered job whose earlier delivery completed, and (before the lobby
    status became a compare-and-set) the loser of a concurrent /start -- and
    an unconditional write would mark a race that has results in
    race_results as aborted, permanently, with the results still sitting
    there. A race that already finished is not abortable.
    """
    _stamp_once(db, race_id, "finished_at")
    return db.db.races.update_one(
        {"id": race_id, "status": {"$ne": "finished"}},
        {"$set": {"status": "aborted", "abort_reason": reason}},
    )


def save_race_data(db, race_id: str, lap_data: list, events: list):
    db.db.races.update_one(
        {"id": race_id},
        {
            "$set": {
                "lap_data_json": lap_data,
                "events_json": [
                    {"lap": e.lap, "type": e.event_type, "car_id": e.car_id, "detail": e.detail}
                    for e in events
                ],
            }
        },
    )


POINTS_TABLE = {1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1}


def save_race_results(db, race_id: str, standings: list):
    """Upsert this race's result rows, one at a time, keyed by
    (race_id, player_id) -- never a delete-then-insert.

    The worker's job queue is at-least-once, so this can run a second time
    for a race that already has rows -- either because the whole match was
    redelivered after fully succeeding once, or because a worker died
    partway through persisting and a later delivery is retrying the whole
    block from scratch (see backend/worker.py's _persist_result).

    A delete-then-insert (this function's first version) is idempotent in
    the end state but briefly leaves the collection with ZERO rows for a
    race that GET /api/race/{id} and /api/season both read live -- a
    reader landing in that window sees a finished race with no results, or
    a season table missing a round. Upserting each row individually by its
    natural key removes the window entirely: at every instant a car's row
    is either its old content or its new content, never absent. There is
    no "stale row to delete" case to handle either, because determinism
    guarantees a redelivered result names exactly the same set of cars
    every time.

    The (race_id, player_id) unique index (backend/db/models.py) is not
    just a safety net here the way the elo_history one is -- without it, a
    concurrent upsert for a document that does not exist yet is a genuine
    race in MongoDB itself (two upserts can both decide "no match, insert"
    and create two rows), which is exactly how two workers persisting the
    same match concurrently (reclaim_stalled firing while the first
    worker is still running) could double a race's championship points.

    That index is also why this retries once. An upsert that finds no
    matching document decides to insert, and if the other worker's insert
    for the same key lands in between, the index refuses this one with
    DuplicateKeyError -- documented pymongo behaviour, with the retry left
    to the caller. Letting it escape would mean escaping _persist_result,
    which strands the whole job unacked until reclaim_stalled picks it up:
    survivable, but it is the same "something escapes persist()" shape
    that has already cost this task two rounds, and the retry is one line.
    The retry cannot loop: the only way to get here is that the row now
    exists, so the second attempt matches it and takes the plain-update
    path, which no unique index can refuse.
    """
    rows = []
    for car in standings:
        row = {
            "race_id": race_id,
            "player_id": car.player_id,
            "car_id": car.car_id,
            "position": car.position,
            "points": POINTS_TABLE.get(car.position, 0) if not car.retired else 0,
            "total_time": car.total_time if not car.retired else None,
            "pit_laps": car.pit_laps,
            "compounds_used": car.compounds_used,
            "strategy_json": None,
            "retired": car.retired,
        }
        try:
            updated = _upsert_race_result(db, race_id, car.player_id, row)
        except DuplicateKeyError:
            updated = _upsert_race_result(db, race_id, car.player_id, row)
        rows.append(to_namespace(updated))
    return rows


def _upsert_race_result(db, race_id: str, player_id: str, row: dict):
    return db.db.race_results.find_one_and_update(
        {"race_id": race_id, "player_id": player_id},
        {"$set": row, "$setOnInsert": {"id": _id()}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )


def code_sha256(code: str) -> str:
    """The one spelling of a bot's content address.

    Prefixed and full-length, matching determinism/manifest.Participant's
    code_sha256 exactly. The manifest names source by this string, so every
    producer of it has to agree character for character or the name does not
    resolve -- which is how "Phase 2 stores it against code_sha256" came to
    be untrue while a 16-character unprefixed digest was being written.
    """
    return "sha256:" + hashlib.sha256(code.encode()).hexdigest()


def save_bot_source(db, code: str) -> str:
    """Store a bot's source under its content address. Returns the address.

    This is what makes a manifest's code_sha256 resolvable. Before it, the
    only copy of a player's source was a bot_submissions row keyed by a
    TRUNCATED, unprefixed digest -- and only for players who called
    /submit-bot at all, so anyone who joined and raced with the default
    STRATEGY_TEMPLATE had no row anywhere. Every real match therefore
    recorded a replay hash whose inputs could not be recovered.

    Content-addressed and immutable: the same source written twice is one
    row, and $setOnInsert means a second write cannot alter the first. An
    upsert rather than an insert-and-catch because two players submitting
    identical code -- which the default template makes the common case, not
    an edge one -- is normal, not a conflict.
    """
    digest = code_sha256(code)
    db.db.bot_sources.update_one(
        {"code_sha256": digest},
        {"$setOnInsert": {"code_sha256": digest, "code": code,
                          "first_seen_at": _now()}},
        upsert=True,
    )
    return digest


def get_bot_source(db, digest: str) -> Optional[str]:
    """The source behind a manifest's code_sha256, or None."""
    row = db.db.bot_sources.find_one({"code_sha256": digest})
    return row["code"] if row else None


def save_replay_inputs(db, match_id: str, participants: list):
    """Record the inputs a match had that the MANIFEST does not name.

    Today that is exactly one field: starting_compound. It is chosen by the
    player, it materially changes the race, it travels in the job -- and
    determinism/manifest.Participant has no field for it. So a manifest for
    a real match does not, on its own, determine the race it describes, and
    the replay hash stored against it cannot be re-derived from it.

    The honest fix is a manifest field, which changes the manifest's
    canonical bytes and therefore every committed golden replay hash -- a
    schema version bump, deliberately not folded into a fix round. Until
    then this keeps the data rather than losing it: without it the compound
    is gone the moment the Redis lobby expires, and no later schema change
    can recover a match that has already run.

    Immutable, like save_manifest and for the same reason: these are the
    definition of what a match was. Raises ValueError on any attempt to
    change one.

    `participants` is a list of {"slot", "code_sha256", "starting_compound"}.
    """
    rows = sorted(
        [
            {"slot": int(p["slot"]),
             "code_sha256": p.get("code_sha256"),
             "starting_compound": p.get("starting_compound"),
             "house_bot": p.get("house_bot")}
            for p in participants
        ],
        key=lambda row: row["slot"],
    )
    existing = db.db.replay_inputs.find_one({"match_id": match_id})
    if existing:
        if existing.get("participants") != rows:
            raise ValueError(
                f"replay inputs for {match_id} already exist with different "
                f"content"
            )
        return rows
    db.db.replay_inputs.insert_one(
        {"match_id": match_id, "participants": rows}
    )
    return rows


def get_replay_inputs(db, match_id: str):
    """The recorded per-slot inputs for a match, or None."""
    row = db.db.replay_inputs.find_one({"match_id": match_id})
    return row["participants"] if row else None


def save_bot_submission(db, player_id: str, code: str, race_id: Optional[str] = None):
    # The submission row is a player-facing history entry; the SOURCE is
    # stored separately, content-addressed, because that is what a manifest
    # references and a submission row is not guaranteed to exist for every
    # participant (a player who never calls /submit-bot races with the
    # default template).
    save_bot_source(db, code)
    sub = {
        "id": _id(),
        "player_id": player_id,
        "race_id": race_id,
        "code": code,
        # Kept: the frontend renders it (types.ts's bot_history) and it is
        # the id a player has seen for their own submissions.
        "code_hash": hashlib.sha256(code.encode()).hexdigest()[:16],
        # The address a manifest actually uses, so a submission row can be
        # joined to the manifest that raced it.
        "code_sha256": code_sha256(code),
        "submitted_at": _now(),
    }
    db.db.bot_submissions.insert_one(sub)
    return to_namespace(sub)


def get_player_submissions(db, player_id: str, limit: int = 20):
    docs = db.db.bot_submissions.find({"player_id": player_id}).sort("submitted_at", -1).limit(limit)
    return [to_namespace(doc) for doc in docs]


def create_season(db, name: str, tracks: List[str]):
    season = {
        "id": _id(),
        "name": name,
        "start_date": _now(),
        "end_date": None,
        "track_rotation": tracks,
        "active": True,
    }
    db.db.seasons.insert_one(season)
    season_obj = to_namespace(season)
    season_obj.races = []
    return season_obj


def get_active_season(db):
    season = to_namespace(db.db.seasons.find_one({"active": True}, sort=[("start_date", -1)]))
    if season:
        season.races = get_season_races(db, season.id)
    return season


def get_all_seasons(db):
    seasons = [to_namespace(doc) for doc in db.db.seasons.find({}).sort("start_date", -1)]
    for season in seasons:
        season.races = get_season_races(db, season.id)
    return seasons


def get_season(db, season_id: str):
    season = to_namespace(db.db.seasons.find_one({"id": season_id}))
    if season:
        season.races = get_season_races(db, season.id)
    return season


def end_season(db, season_id: str):
    db.db.seasons.update_one(
        {"id": season_id},
        {"$set": {"active": False, "end_date": _now()}},
    )
    return get_season(db, season_id)


def get_season_races(db, season_id: str):
    docs = db.db.races.find({"season_id": season_id}).sort("created_at", 1)
    return [to_namespace(doc) for doc in docs]


def get_season_standings(db, season_id: str):
    races = get_season_races(db, season_id)
    finished_race_ids = [race.id for race in races if race.status == "finished"]
    if not finished_race_ids:
        return []

    results = list(db.db.race_results.find({"race_id": {"$in": finished_race_ids}}))
    standings = {}

    for result in results:
        player_id = result["player_id"]
        if player_id not in standings:
            player = get_player_by_id(db, player_id)
            standings[player_id] = {
                "player_id": player_id,
                "username": player.username if player else result["car_id"],
                "team": player.team_name if player else "Unknown",
                "elo": player.elo if player else 1200.0,
                "total_points": 0,
                "races": 0,
                "wins": 0,
                "podiums": 0,
                "best_finish": 99,
                "per_race": [],
            }

        row = standings[player_id]
        row["total_points"] += result["points"]
        row["races"] += 1
        if result["position"] == 1 and not result["retired"]:
            row["wins"] += 1
        if result["position"] <= 3 and not result["retired"]:
            row["podiums"] += 1
        row["best_finish"] = min(row["best_finish"], result["position"])
        row["per_race"].append({
            "race_id": result["race_id"],
            "position": result["position"],
            "points": result["points"],
            "retired": result["retired"],
        })

    return sorted(standings.values(), key=lambda item: -item["total_points"])


def save_elo_history(db, player_id: str, race_id: str, elo_before: float, elo_after: float):
    record = {
        "id": _id(),
        "player_id": player_id,
        "race_id": race_id,
        "elo_before": elo_before,
        "elo_after": elo_after,
        "delta": elo_after - elo_before,
        "created_at": _now(),
    }
    db.db.elo_history.insert_one(record)
    return to_namespace(record)


def get_elo_history(db, player_id: str, limit: int = 100):
    docs = db.db.elo_history.find({"player_id": player_id}).sort("created_at", 1).limit(limit)
    return [to_namespace(doc) for doc in docs]


def get_elo_history_entry(db, player_id: str, race_id: str):
    """The one elo_history row for exactly this player and this race, if any.

    Used to recover a player's true pre-race rating on a retried persist:
    once this row exists, get_player_by_id's CURRENT rating already
    reflects this race's own effect and is no longer a safe "before" value
    to compute anyone else's delta against.
    """
    return to_namespace(
        db.db.elo_history.find_one({"player_id": player_id, "race_id": race_id})
    )


def save_manifest(db, manifest) -> str:
    """Write a manifest once. Raises ValueError on any attempt to change it.

    A manifest is the definition of what a match was; rewriting one would
    invalidate every replay recorded against it without leaving a trace.
    Re-saving identical content is harmless -- the comparison is on the
    canonical digest, never on Python object identity or dict ordering.
    """
    from dataclasses import asdict
    from ..determinism.manifest import manifest_sha256

    existing = db.db.manifests.find_one({"match_id": manifest.match_id})
    digest = manifest_sha256(manifest)
    if existing:
        if existing.get("manifest_sha256") != digest:
            raise ValueError(
                f"manifest {manifest.match_id} already exists with different content"
            )
        return digest
    db.db.manifests.insert_one({**asdict(manifest), "manifest_sha256": digest})
    return digest


def get_manifest(db, match_id: str):
    from ..determinism.manifest import MatchManifest

    raw = db.db.manifests.find_one({"match_id": match_id})
    if not raw:
        return None
    raw.pop("_id", None)
    raw.pop("manifest_sha256", None)
    raw.pop("replay_sha256", None)
    return MatchManifest.from_dict(raw)


def save_replay_hash(db, match_id: str, replay_sha256: str):
    """Record a replay's hash against its manifest. Never overwrite a
    DIFFERENT one.

    This is the one place in the system where two independent executions of
    the same match can be compared, and at-least-once delivery guarantees a
    second execution eventually happens. A `$set` here meant that if the
    second run produced different bytes -- a determinism break, the single
    failure this whole product rests on not happening -- the second hash
    silently replaced the first, with no error, no log and no trace. The
    mechanism best placed to catch it was erasing it.

    So: the write is conditional on the stored hash being absent or already
    equal, in ONE atomic update rather than a read-then-write (two workers
    persisting the same match concurrently is a designed-for state here, not
    an exotic one). If that matches nothing, the row either does not exist
    or holds a different hash; the differing hash is appended to
    `replay_sha256_conflicts` -- preserved alongside the original, not over
    it -- and ReplayHashConflict is raised so a caller cannot mistake this
    for a successful write. save_manifest three functions above already
    refuses a changed manifest this way; the replay hash is the same kind of
    claim about the same match.

    Returns the raw UpdateResult (matched_count == 0 means no manifest
    document exists for match_id yet, since this is update-only and never
    upserts) so a caller that must not treat this as durable unless a row
    was actually touched -- the worker, ack-ing only once persistence is
    real -- can check that itself instead of trusting a silent no-op.
    """
    from ..determinism.replay import ReplayHashConflict

    result = db.db.manifests.update_one(
        {
            "match_id": match_id,
            "$or": [{"replay_sha256": None}, {"replay_sha256": replay_sha256}],
        },
        {"$set": {"replay_sha256": replay_sha256}},
    )
    if result.matched_count:
        return result

    # Nothing matched: either there is no manifest row (the caller's own
    # matched_count == 0 check handles that, and $addToSet below touches
    # nothing) or the row holds a hash that is not this one.
    conflicted = db.db.manifests.find_one_and_update(
        {"match_id": match_id},
        {"$addToSet": {"replay_sha256_conflicts": replay_sha256}},
    )
    if conflicted is None:
        return result
    raise ReplayHashConflict(
        f"match {match_id!r} already recorded replay hash "
        f"{conflicted.get('replay_sha256')} and this execution produced "
        f"{replay_sha256}. The same manifest produced different bytes, so "
        f"the determinism contract is broken for this build. Both hashes "
        f"are kept on the manifest document."
    )


def get_replay_hash_conflicts(db, match_id: str) -> list:
    """Every replay hash for this match that disagreed with the first.

    Empty for every healthy match. Non-empty is the loudest fact this
    database can hold about the engine.
    """
    raw = db.db.manifests.find_one({"match_id": match_id})
    return list(raw.get("replay_sha256_conflicts") or []) if raw else []


def get_manifest_digest(db, match_id: str):
    """The stored manifest's own digest, or None if there is no manifest.

    Read rather than recomputed from get_manifest(): the point of the check
    this feeds (worker._persist_result) is to catch a manifest that would
    rebuild differently in this process, and rebuilding it here to compare
    would be comparing this process against itself.
    """
    raw = db.db.manifests.find_one({"match_id": match_id}, {"manifest_sha256": 1})
    return raw.get("manifest_sha256") if raw else None


def get_replay_hash(db, match_id: str):
    raw = db.db.manifests.find_one({"match_id": match_id})
    return raw.get("replay_sha256") if raw else None


def get_player_race_results(db, player_id: str, limit: int = 50):
    results = list(db.db.race_results.find({"player_id": player_id}).sort("id", -1).limit(limit))
    race_lookup = {
        race["id"]: race
        for race in db.db.races.find({"id": {"$in": [row["race_id"] for row in results]}})
    }
    payload = []
    for result in results:
        race = race_lookup.get(result["race_id"], {})
        payload.append({
            "race_id": result["race_id"],
            "track": race.get("track"),
            "race_type": race.get("race_type"),
            "position": result["position"],
            "points": result["points"],
            "pit_count": len(result.get("pit_laps") or []),
            "compounds_used": result.get("compounds_used") or [],
            "retired": result["retired"],
            "finished_at": race.get("finished_at").isoformat() if race.get("finished_at") else None,
        })
    return payload
