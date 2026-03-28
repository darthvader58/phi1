"""PIT WALL — FastAPI backend with WebSocket race broadcasting.

Endpoints:
- POST /api/register — create player account
- POST /api/race/create — create a race lobby
- POST /api/race/{id}/join — join a race
- POST /api/race/{id}/start — start countdown then race
- POST /api/race/{id}/submit-bot — submit strategy code
- GET  /api/race/{id} — get race state/results
- GET  /api/races — list active races
- GET  /api/leaderboard — ELO leaderboard
- GET  /api/player/{username} — player profile
- GET  /api/season — current season standings
- GET  /api/track/{name} — track info and calibration
- WS   /ws/race/{id} — live race WebSocket stream
"""

import asyncio
import json
import os
import random
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Dict, List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .db.models import create_db_engine, init_db
from .db import crud
from .data.tracks import TRACKS
from .data.calibration import calibrate_track
from .engine.physics import TyreModel, TrackPhysics
from .engine.race import RaceEngine, RaceState, CarState, Decision, RaceEvent
from .engine.bots import BUILTIN_BOTS
from .engine.cli_runner import build_track_physics
from .sandbox.runner import execute_strategy, compile_strategy, STRATEGY_TEMPLATE
from .season.elo import compute_elo_updates


# ─── State management (in-memory, Redis replacement for MVP) ─────────

class RaceLobby:
    """In-memory race lobby state."""
    def __init__(self, race_id: str, track: str, race_type: str = "quick"):
        self.race_id = race_id
        self.track = track
        self.race_type = race_type
        self.players: Dict[str, dict] = {}  # player_id -> {username, car_id, code, ...}
        self.status = "lobby"  # lobby/countdown/running/finished
        self.engine: Optional[RaceEngine] = None
        self.result = None
        self.websockets: Set[WebSocket] = set()
        self.speed = 1.0  # 1x / 5x / 20x
        self.current_state: Optional[dict] = None


# Global state
active_lobbies: Dict[str, RaceLobby] = {}


# ─── Database setup ──────────────────────────────────────────────────

DB_URL = os.environ.get("DATABASE_URL", "sqlite:///piwall.db")
db_engine = create_db_engine(DB_URL)
SessionLocal = init_db(db_engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─── App ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: pre-calibrate tracks
    print("PIT WALL starting up...")
    yield
    print("PIT WALL shutting down...")


app = FastAPI(title="PIT WALL", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Auth helper ─────────────────────────────────────────────────────

def authenticate(api_key: str) -> dict:
    db = SessionLocal()
    try:
        player = crud.get_player_by_api_key(db, api_key)
        if not player:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return {"id": player.id, "username": player.username, "elo": player.elo}
    finally:
        db.close()


# ─── Request/Response models ─────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    team_name: str = "Independent"

class CreateRaceRequest(BaseModel):
    track: str
    race_type: str = "quick"
    speed: float = 5.0

class JoinRaceRequest(BaseModel):
    car_id: Optional[str] = None
    starting_compound: str = "MEDIUM"

class SubmitBotRequest(BaseModel):
    code: str

class TestBotRequest(BaseModel):
    code: str
    track: str = "bahrain"
    laps: int = 20


# ─── Endpoints ───────────────────────────────────────────────────────

@app.post("/api/register")
def register(req: RegisterRequest):
    db = SessionLocal()
    try:
        existing = crud.get_player_by_username(db, req.username)
        if existing:
            raise HTTPException(400, "Username already taken")
        player = crud.create_player(db, req.username, req.team_name)
        return {
            "id": player.id,
            "username": player.username,
            "api_key": player.api_key,
            "elo": player.elo,
        }
    finally:
        db.close()


@app.post("/api/race/create")
def create_race(req: CreateRaceRequest, x_api_key: str = Header()):
    player = authenticate(x_api_key)
    if req.track not in TRACKS:
        raise HTTPException(400, f"Unknown track: {req.track}")

    db = SessionLocal()
    try:
        race = crud.create_race(db, req.track, req.race_type)
        lobby = RaceLobby(race.id, req.track, req.race_type)
        lobby.speed = req.speed
        active_lobbies[race.id] = lobby
        return {"race_id": race.id, "track": req.track, "status": "lobby"}
    finally:
        db.close()


@app.post("/api/race/{race_id}/join")
def join_race(race_id: str, req: JoinRaceRequest, x_api_key: str = Header()):
    player = authenticate(x_api_key)
    lobby = active_lobbies.get(race_id)
    if not lobby:
        raise HTTPException(404, "Race not found")
    if lobby.status != "lobby":
        raise HTTPException(400, "Race already started")
    if len(lobby.players) >= 8:
        raise HTTPException(400, "Race is full (8 players max)")

    car_id = req.car_id or f"P{len(lobby.players) + 1:02d}"
    lobby.players[player["id"]] = {
        "username": player["username"],
        "car_id": car_id,
        "code": STRATEGY_TEMPLATE,
        "starting_compound": req.starting_compound,
    }
    return {"car_id": car_id, "position": len(lobby.players)}


@app.post("/api/race/{race_id}/submit-bot")
def submit_bot(race_id: str, req: SubmitBotRequest, x_api_key: str = Header()):
    player = authenticate(x_api_key)
    lobby = active_lobbies.get(race_id)
    if not lobby:
        raise HTTPException(404, "Race not found")
    if player["id"] not in lobby.players:
        raise HTTPException(400, "Not in this race")

    # Validate code
    error = compile_strategy(req.code)
    if error:
        raise HTTPException(400, error)

    lobby.players[player["id"]]["code"] = req.code

    # Save to DB
    db = SessionLocal()
    try:
        crud.save_bot_submission(db, player["id"], req.code, race_id)
    finally:
        db.close()

    return {"status": "submitted", "car_id": lobby.players[player["id"]]["car_id"]}


@app.post("/api/race/{race_id}/start")
async def start_race(race_id: str, x_api_key: str = Header()):
    player = authenticate(x_api_key)
    lobby = active_lobbies.get(race_id)
    if not lobby:
        raise HTTPException(404, "Race not found")
    if lobby.status != "lobby":
        raise HTTPException(400, "Race already started")

    lobby.status = "countdown"

    db = SessionLocal()
    try:
        crud.update_race_status(db, race_id, "countdown")
    finally:
        db.close()

    # Start race in background
    asyncio.create_task(_run_race(race_id))
    return {"status": "countdown", "message": "Race starting in 5 seconds..."}


@app.get("/api/race/{race_id}")
def get_race(race_id: str):
    lobby = active_lobbies.get(race_id)
    if lobby:
        return {
            "race_id": race_id,
            "track": lobby.track,
            "status": lobby.status,
            "players": {pid: {"username": p["username"], "car_id": p["car_id"]}
                        for pid, p in lobby.players.items()},
            "current_state": lobby.current_state,
            "result": _serialize_result(lobby.result) if lobby.result else None,
        }

    # Check DB for finished races
    db = SessionLocal()
    try:
        race = crud.get_race(db, race_id)
        if not race:
            raise HTTPException(404, "Race not found")
        results = db.query(crud.RaceResultRow).filter(
            crud.RaceResultRow.race_id == race_id
        ).order_by(crud.RaceResultRow.position).all()
        return {
            "race_id": race_id,
            "track": race.track,
            "status": race.status,
            "results": [
                {
                    "car_id": r.car_id,
                    "position": r.position,
                    "points": r.points,
                    "total_time": r.total_time,
                    "pit_laps": r.pit_laps,
                    "compounds_used": r.compounds_used,
                    "retired": r.retired,
                }
                for r in results
            ],
            "lap_data": race.lap_data_json,
            "events": race.events_json,
        }
    finally:
        db.close()


@app.get("/api/races")
def list_races():
    result = []
    for rid, lobby in active_lobbies.items():
        result.append({
            "race_id": rid,
            "track": lobby.track,
            "status": lobby.status,
            "player_count": len(lobby.players),
            "race_type": lobby.race_type,
        })
    return result


@app.get("/api/leaderboard")
def leaderboard():
    db = SessionLocal()
    try:
        players = crud.get_leaderboard(db)
        return [
            {"username": p.username, "elo": round(p.elo, 1), "team": p.team_name}
            for p in players
        ]
    finally:
        db.close()


@app.get("/api/player/{username}")
def get_player(username: str):
    db = SessionLocal()
    try:
        player = crud.get_player_by_username(db, username)
        if not player:
            raise HTTPException(404, "Player not found")
        submissions = crud.get_player_submissions(db, player.id, limit=10)
        return {
            "username": player.username,
            "elo": round(player.elo, 1),
            "team": player.team_name,
            "created_at": player.created_at.isoformat() if player.created_at else None,
            "bot_history": [
                {"code_hash": s.code_hash, "submitted_at": s.submitted_at.isoformat()}
                for s in submissions
            ],
        }
    finally:
        db.close()


@app.get("/api/track/{name}")
def get_track_info(name: str):
    if name not in TRACKS:
        raise HTTPException(404, "Track not found")
    cfg = TRACKS[name]
    return {
        "name": cfg.name,
        "display_name": cfg.display_name,
        "country": cfg.country,
        "total_laps": cfg.total_laps,
        "pit_loss_seconds": cfg.pit_loss_seconds,
        "drs_zones": cfg.drs_zones,
        "overtake_difficulty": cfg.overtake_difficulty,
        "safety_car_prob_dry": cfg.safety_car_prob_dry,
        "safety_car_prob_wet": cfg.safety_car_prob_wet,
        "typical_stint": cfg.typical_stint,
    }


@app.get("/api/tracks")
def list_tracks():
    return [
        {
            "name": cfg.name,
            "display_name": cfg.display_name,
            "country": cfg.country,
            "total_laps": cfg.total_laps,
        }
        for cfg in TRACKS.values()
    ]


@app.post("/api/test-bot")
def test_bot(req: TestBotRequest, x_api_key: str = Header()):
    """Run a quick offline simulation with the user's bot vs built-in bots."""
    player = authenticate(x_api_key)

    error = compile_strategy(req.code)
    if error:
        raise HTTPException(400, error)

    track = build_track_physics(req.track)
    track_cfg = TRACKS[req.track]

    # Override total laps for quick test
    track.total_laps = min(req.laps, track_cfg.total_laps)

    engine = RaceEngine(
        track=track,
        weather_transitions=track_cfg.weather_transitions,
        seed=random.randint(0, 99999),
        sc_prob_dry=track_cfg.safety_car_prob_dry,
        sc_prob_wet=track_cfg.safety_car_prob_wet,
    )

    # Add user's bot
    def user_strategy(state: RaceState, my_car: CarState) -> Decision:
        state_dict = _race_state_to_dict(state)
        car_dict = _car_state_to_dict(my_car)
        result = execute_strategy(req.code, state_dict, car_dict)
        if "error" in result:
            return Decision(pit=False, compound=my_car.compound)
        return Decision(pit=result["pit"], compound=result["compound"])

    engine.add_car("USER", player["id"], user_strategy, 1, "MEDIUM")

    # Add built-in bots
    for pos, (bot_id, bot_info) in enumerate(BUILTIN_BOTS.items(), 2):
        engine.add_car(bot_id, bot_id, bot_info["strategy"], pos,
                       bot_info["starting_compound"])

    result = engine.run()

    return {
        "standings": [
            {
                "car_id": c.car_id,
                "position": c.position,
                "gap_to_leader": round(c.gap_to_leader, 3),
                "pit_count": c.pit_count,
                "pit_laps": c.pit_laps,
                "retired": c.retired,
            }
            for c in result.final_standings
        ],
        "events": [
            {"lap": e.lap, "type": e.event_type, "detail": e.detail}
            for e in result.events[:50]
        ],
    }


@app.get("/api/strategy/template")
def get_strategy_template():
    return {"template": STRATEGY_TEMPLATE}


# ─── WebSocket ───────────────────────────────────────────────────────

@app.websocket("/ws/race/{race_id}")
async def websocket_race(websocket: WebSocket, race_id: str):
    await websocket.accept()
    lobby = active_lobbies.get(race_id)
    if not lobby:
        await websocket.send_json({"error": "Race not found"})
        await websocket.close()
        return

    lobby.websockets.add(websocket)
    try:
        # Send current state if race is in progress
        if lobby.current_state:
            await websocket.send_json({"type": "state", "data": lobby.current_state})

        # Keep connection alive, listen for speed control messages
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)
                if msg.get("type") == "speed":
                    lobby.speed = float(msg.get("speed", 1.0))
            except asyncio.TimeoutError:
                # Send ping to keep alive
                await websocket.send_json({"type": "ping"})
            except WebSocketDisconnect:
                break
    finally:
        lobby.websockets.discard(websocket)


# ─── Race execution ─────────────────────────────────────────────────

async def _broadcast(lobby: RaceLobby, message: dict):
    """Send message to all connected WebSocket clients."""
    dead = set()
    for ws in lobby.websockets:
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)
    lobby.websockets -= dead


async def _run_race(race_id: str):
    """Background task that runs the race simulation and broadcasts state."""
    lobby = active_lobbies.get(race_id)
    if not lobby:
        return

    # Countdown
    await _broadcast(lobby, {"type": "countdown", "seconds": 5})
    await asyncio.sleep(3)
    await _broadcast(lobby, {"type": "countdown", "seconds": 2})
    await asyncio.sleep(2)

    lobby.status = "running"
    db = SessionLocal()
    try:
        crud.update_race_status(db, race_id, "running")
    finally:
        db.close()

    # Build track
    track = build_track_physics(lobby.track)
    track_cfg = TRACKS[lobby.track]

    engine = RaceEngine(
        track=track,
        weather_transitions=track_cfg.weather_transitions,
        seed=random.randint(0, 99999),
        sc_prob_dry=track_cfg.safety_car_prob_dry,
        sc_prob_wet=track_cfg.safety_car_prob_wet,
    )

    # Add human players with sandboxed strategies
    pos = 1
    for pid, pdata in lobby.players.items():
        code = pdata["code"]

        def make_strategy(player_code):
            def strategy(state, my_car):
                sd = _race_state_to_dict(state)
                cd = _car_state_to_dict(my_car)
                result = execute_strategy(player_code, sd, cd)
                if "error" in result:
                    return Decision(pit=False, compound=my_car.compound)
                return Decision(pit=result["pit"], compound=result["compound"])
            return strategy

        engine.add_car(
            pdata["car_id"], pid, make_strategy(code), pos,
            pdata.get("starting_compound", "MEDIUM"),
        )
        pos += 1

    # Fill remaining slots with built-in bots
    for bot_id, bot_info in BUILTIN_BOTS.items():
        if pos > 10:
            break
        engine.add_car(bot_id, bot_id, bot_info["strategy"], pos,
                       bot_info["starting_compound"])
        pos += 1

    # Run lap by lap with broadcasts
    total_laps = track.total_laps
    for lap in range(1, total_laps + 1):
        # Simulate one lap (call internal step logic)
        # We need to run the race lap-by-lap — refactor to use step mode
        pass  # Engine runs all at once, we broadcast from lap_data after

    # Run the full race
    result = engine.run()
    lobby.result = result

    # Broadcast each lap with delay based on speed
    for lap_snapshot in result.lap_data:
        lap_num = lap_snapshot["lap"]
        events_this_lap = [
            {"lap": e.lap, "type": e.event_type, "car_id": e.car_id, "detail": e.detail}
            for e in result.events if e.lap == lap_num
        ]

        state_msg = {
            "type": "lap",
            "lap": lap_num,
            "total_laps": total_laps,
            "data": lap_snapshot,
            "events": events_this_lap,
        }
        lobby.current_state = state_msg
        await _broadcast(lobby, state_msg)

        # Delay between laps based on speed setting
        delay = max(0.05, 1.0 / lobby.speed)
        await asyncio.sleep(delay)

    # Race finished
    lobby.status = "finished"

    # Save results to DB
    db = SessionLocal()
    try:
        crud.update_race_status(db, race_id, "finished")
        crud.save_race_results(db, race_id, result.final_standings)
        crud.save_race_data(db, race_id, result.lap_data, result.events)

        # Update ELO
        standings_tuples = [
            (c.player_id, c.position, c.retired) for c in result.final_standings
        ]
        current_ratings = {}
        for pid, _, _ in standings_tuples:
            player = db.query(crud.Player).filter(crud.Player.id == pid).first()
            if player:
                current_ratings[pid] = player.elo
            else:
                current_ratings[pid] = 1200.0

        k_factor = 48.0 if lobby.race_type == "season" else 32.0
        new_ratings = compute_elo_updates(standings_tuples, current_ratings, k_factor)

        for pid, new_elo in new_ratings.items():
            player = db.query(crud.Player).filter(crud.Player.id == pid).first()
            if player:
                old_elo = player.elo
                player.elo = new_elo
                elo_hist = crud.EloHistory(
                    player_id=pid,
                    race_id=race_id,
                    elo_before=old_elo,
                    elo_after=new_elo,
                    delta=new_elo - old_elo,
                )
                db.add(elo_hist)
        db.commit()
    finally:
        db.close()

    # Broadcast final results
    await _broadcast(lobby, {
        "type": "finished",
        "result": _serialize_result(result),
    })


# ─── Serialization helpers ───────────────────────────────────────────

def _race_state_to_dict(state: RaceState) -> dict:
    return {
        "lap": state.lap,
        "total_laps": state.total_laps,
        "track": state.track,
        "weather": state.weather,
        "safety_car": state.safety_car,
        "safety_car_laps_left": state.safety_car_laps_left,
        "track_temp": state.track_temp,
        "cars": [_car_state_to_dict(c) for c in state.cars],
    }


def _car_state_to_dict(car: CarState) -> dict:
    return {
        "car_id": car.car_id,
        "player_id": car.player_id,
        "position": car.position,
        "gap_to_leader": car.gap_to_leader,
        "compound": car.compound,
        "tyre_age": car.tyre_age,
        "fuel_kg": car.fuel_kg,
        "pit_count": car.pit_count,
        "pit_laps": car.pit_laps,
        "last_lap_time": car.last_lap_time,
        "total_time": car.total_time,
        "retired": car.retired,
        "drs_available": car.drs_available,
        "compounds_used": car.compounds_used,
        "beliefs": car.beliefs,
    }


def _serialize_result(result) -> dict:
    if result is None:
        return None
    return {
        "track": result.track,
        "total_laps": result.total_laps,
        "standings": [
            {
                "car_id": c.car_id,
                "player_id": c.player_id,
                "position": c.position,
                "gap_to_leader": round(c.gap_to_leader, 3),
                "compound": c.compound,
                "tyre_age": c.tyre_age,
                "pit_count": c.pit_count,
                "pit_laps": c.pit_laps,
                "compounds_used": c.compounds_used,
                "total_time": round(c.total_time, 3),
                "retired": c.retired,
            }
            for c in result.final_standings
        ],
        "events": [
            {"lap": e.lap, "type": e.event_type, "car_id": e.car_id, "detail": e.detail}
            for e in result.events
        ],
        "weather_history": result.weather_history,
    }
