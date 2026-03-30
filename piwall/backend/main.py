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
import concurrent.futures
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
    laps: int = 0  # 0 = use full race distance

class CreateSeasonRequest(BaseModel):
    name: str
    tracks: List[str] = ["bahrain", "monaco", "monza", "spa", "silverstone", "suzuka"]


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
        # Auto-assign season races to the active season
        season_id = None
        if req.race_type == "season":
            active_season = crud.get_active_season(db)
            if not active_season:
                raise HTTPException(400, "No active season. Create a season first.")
            season_id = active_season.id

        race = crud.create_race(db, req.track, req.race_type, season_id=season_id)
        lobby = RaceLobby(race.id, req.track, req.race_type)
        lobby.speed = req.speed
        active_lobbies[race.id] = lobby
        return {"race_id": race.id, "track": req.track, "status": "lobby",
                "race_type": req.race_type, "season_id": season_id}
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
        elo_history = crud.get_elo_history(db, player.id)
        race_results = crud.get_player_race_results(db, player.id, limit=20)

        # Compute stats
        total_races = len(race_results)
        wins = sum(1 for r in race_results if r["position"] == 1 and not r["retired"])
        podiums = sum(1 for r in race_results if r["position"] <= 3 and not r["retired"])
        dnfs = sum(1 for r in race_results if r["retired"])

        return {
            "username": player.username,
            "elo": round(player.elo, 1),
            "team": player.team_name,
            "created_at": player.created_at.isoformat() if player.created_at else None,
            "stats": {
                "total_races": total_races,
                "wins": wins,
                "podiums": podiums,
                "dnfs": dnfs,
                "win_rate": round(wins / total_races * 100, 1) if total_races > 0 else 0,
            },
            "elo_history": [
                {
                    "race_id": h.race_id,
                    "elo_before": round(h.elo_before, 1),
                    "elo_after": round(h.elo_after, 1),
                    "delta": round(h.delta, 1),
                }
                for h in elo_history
            ],
            "recent_races": race_results,
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

    # Override total laps for quick test (0 = full race distance)
    track.total_laps = track_cfg.total_laps if req.laps <= 0 else min(req.laps, track_cfg.total_laps)

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


# ─── Season endpoints ───────────────────────────────────────────────

@app.post("/api/season")
def create_season(req: CreateSeasonRequest, x_api_key: str = Header()):
    authenticate(x_api_key)
    for t in req.tracks:
        if t not in TRACKS:
            raise HTTPException(400, f"Unknown track: {t}")
    db = SessionLocal()
    try:
        # Deactivate any existing active season
        active = crud.get_active_season(db)
        if active:
            crud.end_season(db, active.id)
        season = crud.create_season(db, req.name, req.tracks)
        return {
            "id": season.id,
            "name": season.name,
            "tracks": season.track_rotation,
            "active": season.active,
        }
    finally:
        db.close()


@app.get("/api/seasons")
def list_seasons():
    db = SessionLocal()
    try:
        seasons = crud.get_all_seasons(db)
        return [
            {
                "id": s.id,
                "name": s.name,
                "tracks": s.track_rotation,
                "active": s.active,
                "start_date": s.start_date.isoformat() if s.start_date else None,
                "end_date": s.end_date.isoformat() if s.end_date else None,
                "race_count": len(s.races),
            }
            for s in seasons
        ]
    finally:
        db.close()


@app.get("/api/season/active")
def get_active_season():
    db = SessionLocal()
    try:
        season = crud.get_active_season(db)
        if not season:
            return {"active": False, "season": None}
        races = crud.get_season_races(db, season.id)
        standings = crud.get_season_standings(db, season.id)
        return {
            "active": True,
            "season": {
                "id": season.id,
                "name": season.name,
                "tracks": season.track_rotation,
                "start_date": season.start_date.isoformat() if season.start_date else None,
                "races": [
                    {
                        "id": r.id,
                        "track": r.track,
                        "status": r.status,
                        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                    }
                    for r in races
                ],
                "standings": standings,
                "next_track": _get_next_track(season.track_rotation, races),
                "completed_tracks": [r.track for r in races if r.status == "finished"],
            },
        }
    finally:
        db.close()


def _get_next_track(track_rotation: list, races: list) -> Optional[str]:
    """Determine the next track in the season rotation."""
    completed = [r.track for r in races if r.status == "finished"]
    for track in track_rotation:
        if track not in completed:
            return track
    return None


@app.get("/api/season/{season_id}/standings")
def get_season_standings(season_id: str):
    db = SessionLocal()
    try:
        season = crud.get_season(db, season_id)
        if not season:
            raise HTTPException(404, "Season not found")
        standings = crud.get_season_standings(db, season_id)
        races = crud.get_season_races(db, season_id)
        return {
            "season_id": season_id,
            "name": season.name,
            "standings": standings,
            "races_completed": len([r for r in races if r.status == "finished"]),
            "races_total": len(season.track_rotation or []),
        }
    finally:
        db.close()


@app.post("/api/season/{season_id}/end")
def end_season(season_id: str, x_api_key: str = Header()):
    authenticate(x_api_key)
    db = SessionLocal()
    try:
        season = crud.end_season(db, season_id)
        if not season:
            raise HTTPException(404, "Season not found")
        return {"status": "ended", "id": season.id, "name": season.name}
    finally:
        db.close()


# ─── Player profile + ELO history ───────────────────────────────────

@app.get("/api/player/{username}/elo-history")
def get_elo_history(username: str):
    db = SessionLocal()
    try:
        player = crud.get_player_by_username(db, username)
        if not player:
            raise HTTPException(404, "Player not found")
        history = crud.get_elo_history(db, player.id)
        return {
            "username": player.username,
            "current_elo": round(player.elo, 1),
            "history": [
                {
                    "race_id": h.race_id,
                    "elo_before": round(h.elo_before, 1),
                    "elo_after": round(h.elo_after, 1),
                    "delta": round(h.delta, 1),
                }
                for h in history
            ],
        }
    finally:
        db.close()


@app.get("/api/player/{username}/races")
def get_player_races(username: str):
    db = SessionLocal()
    try:
        player = crud.get_player_by_username(db, username)
        if not player:
            raise HTTPException(404, "Player not found")
        results = crud.get_player_race_results(db, player.id)
        return {
            "username": player.username,
            "races": results,
        }
    finally:
        db.close()


# ─── Matchmaking ────────────────────────────────────────────────────

@app.get("/api/matchmaking/suggest")
def suggest_match(x_api_key: str = Header()):
    """Suggest a race lobby with players closest in ELO to the requester."""
    player = authenticate(x_api_key)

    # Find active lobbies with players within ELO range
    suggestions = []
    for rid, lobby in active_lobbies.items():
        if lobby.status != "lobby":
            continue
        if len(lobby.players) >= 8:
            continue

        # Calculate average ELO of players in lobby
        db = SessionLocal()
        try:
            elos = []
            for pid in lobby.players:
                p = db.query(crud.Player).filter(crud.Player.id == pid).first()
                if p:
                    elos.append(p.elo)
            avg_elo = sum(elos) / len(elos) if elos else 1200.0
            elo_diff = abs(player["elo"] - avg_elo)
            suggestions.append({
                "race_id": rid,
                "track": lobby.track,
                "race_type": lobby.race_type,
                "player_count": len(lobby.players),
                "avg_elo": round(avg_elo, 0),
                "elo_diff": round(elo_diff, 0),
            })
        finally:
            db.close()

    # Sort by ELO proximity
    suggestions.sort(key=lambda x: x["elo_diff"])
    return {"suggestions": suggestions[:5]}


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

    # Countdown: 5, 4, 3, 2, 1 — one broadcast per second
    for seconds in [5, 4, 3, 2, 1]:
        await _broadcast(lobby, {"type": "countdown", "seconds": seconds})
        await asyncio.sleep(1.0)

    # Immediately broadcast lights out so the frontend transitions
    await _broadcast(lobby, {"type": "lights_out"})

    lobby.status = "running"
    db = SessionLocal()
    try:
        crud.update_race_status(db, race_id, "running")
    finally:
        db.close()

    # Build track and engine setup
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

    # Run the full race simulation in a thread pool so we don't block the event loop
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        result = await loop.run_in_executor(pool, engine.run)

    lobby.result = result
    total_laps = track.total_laps

    # Pre-index events by lap for fast lookup
    events_by_lap: Dict[int, list] = {}
    for e in result.events:
        events_by_lap.setdefault(e.lap, []).append(
            {"lap": e.lap, "type": e.event_type, "car_id": e.car_id, "detail": e.detail}
        )

    # Broadcast each lap with pacing for an enjoyable viewing experience
    # Speed: 1x = 3.75s/lap, 5x = 0.75s/lap, 20x = 0.1875s/lap
    for lap_snapshot in result.lap_data:
        lap_num = lap_snapshot["lap"]

        state_msg = {
            "type": "lap",
            "lap": lap_num,
            "total_laps": total_laps,
            "data": lap_snapshot,
            "events": events_by_lap.get(lap_num, []),
        }
        lobby.current_state = state_msg
        await _broadcast(lobby, state_msg)

        # Cinematic pacing: ~11s per lap at 1x, ~2.2s at 5x, ~0.55s at 20x
        delay = max(0.05, 11.0 / lobby.speed)
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
