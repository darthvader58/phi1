"""CRUD operations for PIT WALL database backed by MongoDB."""

import datetime
import hashlib
import secrets
import uuid
from typing import List, Optional

from .models import to_namespace


def _now():
    return datetime.datetime.utcnow()


def _id():
    return str(uuid.uuid4())


def _player_doc(player):
    return {
        "id": player.id,
        "username": player.username,
        "api_key": player.api_key,
        "elo": player.elo,
        "team_name": player.team_name,
        "created_at": player.created_at,
    }


def create_player(db, username: str, team_name: str = "Independent"):
    player = to_namespace({
        "id": _id(),
        "username": username,
        "api_key": f"pw_{secrets.token_hex(24)}",
        "elo": 1200.0,
        "team_name": team_name,
        "created_at": _now(),
    })
    db.db.players.insert_one(_player_doc(player))
    return player


def get_player_by_api_key(db, api_key: str):
    return to_namespace(db.db.players.find_one({"api_key": api_key}))


def get_player_by_username(db, username: str):
    return to_namespace(db.db.players.find_one({"username": username}))


def get_player_by_id(db, player_id: str):
    return to_namespace(db.db.players.find_one({"id": player_id}))


def update_player_elo(db, player_id: str, new_elo: float):
    db.db.players.update_one({"id": player_id}, {"$set": {"elo": new_elo}})


def get_leaderboard(db, limit: int = 50):
    return [to_namespace(doc) for doc in db.db.players.find({}).sort("elo", -1).limit(limit)]


def create_race(db, track: str, race_type: str = "quick", season_id: Optional[str] = None, weather_seed: Optional[int] = None):
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


def update_race_status(db, race_id: str, status: str):
    updates = {"status": status}
    if status == "running":
        updates["started_at"] = _now()
    elif status == "finished":
        updates["finished_at"] = _now()
    db.db.races.update_one({"id": race_id}, {"$set": updates})


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
    rows = []
    for car in standings:
        row = {
            "id": _id(),
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
        db.db.race_results.insert_one(row)
        rows.append(to_namespace(row))
    return rows


def save_bot_submission(db, player_id: str, code: str, race_id: Optional[str] = None):
    sub = {
        "id": _id(),
        "player_id": player_id,
        "race_id": race_id,
        "code": code,
        "code_hash": hashlib.sha256(code.encode()).hexdigest()[:16],
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
