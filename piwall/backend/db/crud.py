"""CRUD operations for PIT WALL database."""

import hashlib
import secrets
import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from .models import Player, Season, Race, RaceResultRow, BotSubmission, EloHistory


# ─── Players ──────────────────────────────────────────────────────────

def create_player(db: Session, username: str, team_name: str = "Independent") -> Player:
    api_key = f"pw_{secrets.token_hex(24)}"
    player = Player(username=username, api_key=api_key, team_name=team_name)
    db.add(player)
    db.commit()
    db.refresh(player)
    return player


def get_player_by_api_key(db: Session, api_key: str) -> Optional[Player]:
    return db.query(Player).filter(Player.api_key == api_key).first()


def get_player_by_username(db: Session, username: str) -> Optional[Player]:
    return db.query(Player).filter(Player.username == username).first()


def get_leaderboard(db: Session, limit: int = 50) -> List[Player]:
    return db.query(Player).order_by(Player.elo.desc()).limit(limit).all()


# ─── Races ────────────────────────────────────────────────────────────

def create_race(
    db: Session,
    track: str,
    race_type: str = "quick",
    season_id: Optional[str] = None,
    weather_seed: Optional[int] = None,
) -> Race:
    race = Race(
        track=track,
        race_type=race_type,
        season_id=season_id,
        weather_seed=weather_seed,
    )
    db.add(race)
    db.commit()
    db.refresh(race)
    return race


def get_race(db: Session, race_id: str) -> Optional[Race]:
    return db.query(Race).filter(Race.id == race_id).first()


def get_active_races(db: Session) -> List[Race]:
    return db.query(Race).filter(
        Race.status.in_(["lobby", "countdown", "running"])
    ).order_by(Race.created_at.desc()).all()


def update_race_status(db: Session, race_id: str, status: str):
    race = get_race(db, race_id)
    if race:
        race.status = status
        if status == "running":
            race.started_at = datetime.datetime.utcnow()
        elif status == "finished":
            race.finished_at = datetime.datetime.utcnow()
        db.commit()


def save_race_data(db: Session, race_id: str, lap_data: list, events: list):
    race = get_race(db, race_id)
    if race:
        race.lap_data_json = lap_data
        race.events_json = [
            {"lap": e.lap, "type": e.event_type, "car_id": e.car_id, "detail": e.detail}
            for e in events
        ]
        db.commit()


# ─── Race Results ─────────────────────────────────────────────────────

POINTS_TABLE = {1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1}


def save_race_results(db: Session, race_id: str, standings: list) -> List[RaceResultRow]:
    results = []
    for car in standings:
        points = POINTS_TABLE.get(car.position, 0) if not car.retired else 0
        row = RaceResultRow(
            race_id=race_id,
            player_id=car.player_id,
            car_id=car.car_id,
            position=car.position,
            points=points,
            total_time=car.total_time if not car.retired else None,
            pit_laps=car.pit_laps,
            compounds_used=car.compounds_used,
            retired=car.retired,
        )
        db.add(row)
        results.append(row)
    db.commit()
    return results


# ─── Bot Submissions ─────────────────────────────────────────────────

def save_bot_submission(
    db: Session, player_id: str, code: str, race_id: Optional[str] = None,
) -> BotSubmission:
    code_hash = hashlib.sha256(code.encode()).hexdigest()[:16]
    sub = BotSubmission(
        player_id=player_id,
        race_id=race_id,
        code=code,
        code_hash=code_hash,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def get_player_submissions(db: Session, player_id: str, limit: int = 20) -> List[BotSubmission]:
    return (
        db.query(BotSubmission)
        .filter(BotSubmission.player_id == player_id)
        .order_by(BotSubmission.submitted_at.desc())
        .limit(limit)
        .all()
    )


# ─── Seasons ──────────────────────────────────────────────────────────

def create_season(db: Session, name: str, tracks: List[str]) -> Season:
    season = Season(name=name, track_rotation=tracks)
    db.add(season)
    db.commit()
    db.refresh(season)
    return season


def get_active_season(db: Session) -> Optional[Season]:
    return db.query(Season).filter(Season.active == True).first()


def get_season_standings(db: Session, season_id: str) -> List[dict]:
    """Get championship standings for a season."""
    races = db.query(Race).filter(Race.season_id == season_id, Race.status == "finished").all()
    race_ids = [r.id for r in races]
    if not race_ids:
        return []

    results = db.query(RaceResultRow).filter(RaceResultRow.race_id.in_(race_ids)).all()

    # Aggregate points per player
    standings = {}
    for r in results:
        if r.player_id not in standings:
            player = db.query(Player).filter(Player.id == r.player_id).first()
            standings[r.player_id] = {
                "player_id": r.player_id,
                "username": player.username if player else r.car_id,
                "team": player.team_name if player else "Unknown",
                "total_points": 0,
                "races": 0,
                "wins": 0,
            }
        standings[r.player_id]["total_points"] += r.points
        standings[r.player_id]["races"] += 1
        if r.position == 1 and not r.retired:
            standings[r.player_id]["wins"] += 1

    return sorted(standings.values(), key=lambda x: -x["total_points"])
