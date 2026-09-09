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
import hmac
import json
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Dict, List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pymongo.errors import DuplicateKeyError
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .db.models import MongoSession, create_db_engine, init_db, to_namespace
from .db import crud
from .data.tracks import TRACKS
from .engine.physics import TyreModel, TrackPhysics
from .engine.race import RaceEngine
from .engine.bots import BUILTIN_BOTS
from .engine.build import build_track_physics
from .sandbox.runner import NAMESPACE_RESERVED_KEYS, STRATEGY_TEMPLATE
from backend.sandbox.validation import validate_submission
from backend.sandbox.isolation import ChildFailed, LimitExceeded, MatchAborted
from backend.sandbox.match_job import run_match_isolated
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

logger = logging.getLogger("piwall")


# ─── Database setup ──────────────────────────────────────────────────

DB_URL = os.environ.get("MONGODB_URI") or os.environ.get("DATABASE_URL") or "mongodb://127.0.0.1:27017/phi1"


def _parse_cors_origins() -> List[str]:
    raw = os.environ.get("CORS_ORIGINS") or os.environ.get("FRONTEND_URL")
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


CORS_ORIGINS = _parse_cors_origins()

# Shared secret proving a caller is our own Next.js server rather than the
# open internet. /api/register issues credentials, so it cannot authenticate
# the way every other endpoint does; this is the identity it authenticates
# with instead. Unset means registration is closed (see provisioning_subject)
# — failing open here would silently restore the hole in exactly the
# deployment that forgot to configure it.
PROVISIONING_SECRET = os.environ.get("PROVISIONING_SECRET") or ""

# MongoClient() opens no socket until the first operation, so building the
# engine here costs nothing and needs no reachable server. init_db() does talk
# to the database (it creates and drops indexes), so it runs in lifespan and
# never at import time: importing this module must not require — or mutate — a
# live database, or the test suite cannot even be collected without one.
db_engine = create_db_engine(DB_URL)
_session_factory = None


def SessionLocal():
    """Return a database session for one unit of work.

    lifespan installs the real factory once init_db() has run. The fallback
    keeps every request path working if a caller reaches the database before
    startup finished (or outside the app entirely, as tests and scripts do);
    it differs only in that indexes have not been ensured.
    """
    if _session_factory is None:
        return MongoSession(db_engine)
    return _session_factory()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─── App ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _session_factory
    print("PIT WALL starting up...")
    if not PROVISIONING_SECRET:
        logger.warning(
            "PROVISIONING_SECRET is unset: /api/register will refuse every "
            "request with 404 and no new players can be provisioned. Set it "
            "on both the backend and the Next.js server to open registration."
        )
    _session_factory = init_db(db_engine)
    yield
    print("PIT WALL shutting down...")


app = FastAPI(title="PIT WALL", version="0.1.0", lifespan=lifespan)


def rate_limit_key(request: Request) -> str:
    """Bucket rate limits per player, not per socket address.

    Every game request now arrives from the Next.js server process, so keying
    on the remote address alone turned per-player limits into platform-wide
    ones: one player's burst throttled everybody. The API key identifies the
    caller across that hop. It is hashed so raw credentials never reach the
    limiter's storage keys or any log line that prints them.

    The key is not verified before it is bucketed, and deliberately so:
    every endpoint using this key_func calls authenticate() first, so a
    caller rotating the header to dodge its limit only spreads its own 401s
    across buckets. Verifying here instead would put a database round trip
    on the limiter path for no gain. Registration is the exception — it has
    no authenticate() behind it — and so it uses registration_rate_key.

    Requests with no key (the public reads) fall back to the socket address,
    which is the best identity available for them.
    """
    api_key = request.headers.get("x-api-key")
    if api_key:
        return f"player:{crud.hash_api_key(api_key)}"
    return get_remote_address(request)


def provisioning_subject(request: Request) -> Optional[str]:
    """The user id a trusted caller vouched for, or None if it is untrusted.

    A subject comes back only when the caller proved it holds the shared
    provisioning secret, so both gates that read it — the 404 in register()
    and the rate limit bucket — key on that proof rather than on anything
    the caller can pick for itself. compare_digest, not ==: a secret
    compared with a short-circuiting operator leaks its prefix to a caller
    who can time the response.
    """
    if not PROVISIONING_SECRET:
        return None
    presented = request.headers.get("x-provision-secret") or ""
    if not hmac.compare_digest(presented, PROVISIONING_SECRET):
        return None
    return (request.headers.get("x-provision-subject") or "").strip()[:128] or None


def registration_rate_key(request: Request) -> str:
    """Bucket registration on the vouched-for user, never on x-api-key.

    Registration is the one limited endpoint with no authenticate() behind
    it, and it issues credentials rather than presenting them. The shared
    key_func's x-api-key branch would therefore let any caller mint a fresh
    bucket per request by rotating a header nobody verifies, while its
    remote-address fallback collapses every real signup into one
    platform-wide bucket, because they all arrive from the Next.js process.
    The vouched-for subject is the only identity here that is neither
    forgeable nor shared.

    Untrusted callers share an address-keyed bucket. That traffic gets a 404
    regardless, so the bucket only bounds how much of it we process.
    """
    subject = provisioning_subject(request)
    if subject:
        return f"provision:{subject}"
    return f"anon:{get_remote_address(request)}"


limiter = Limiter(key_func=rate_limit_key)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
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
        return {
            "id": player.id,
            "username": player.username,
            "elo": player.elo,
            "role": getattr(player, "role", "player"),
        }
    finally:
        db.close()


def require_admin(player: dict) -> None:
    """Raise unless the authenticated player holds the admin role."""
    if player.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


# ─── Request/Response models ─────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    team_name: str = "Independent"

class CreateRaceRequest(BaseModel):
    track: str
    race_type: str = "quick"
    speed: float = 5.0

# A car_id is not just a label: belief dicts are keyed by it, and those keys
# become attributes of the Namespace object every bot in the race receives
# (see sandbox/runner.py). An unconstrained one let a player pick a name that
# shadowed `Namespace.get` -- breaking `my_car.beliefs.get(...)`, the line in
# the shipped STRATEGY_TEMPLATE, for every *opponent* -- or `__class__`,
# whose TypeError escapes execute_strategy and aborts the match. Letters,
# digits, hyphen and underscore only, which is what the house bot ids
# ("VEL-01") and the generated defaults ("P01") already use. The leading
# character may not be an underscore, which is what rules out "__class__".
CAR_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,15}$"


class JoinRaceRequest(BaseModel):
    car_id: Optional[str] = Field(default=None, pattern=CAR_ID_PATTERN)
    starting_compound: str = "MEDIUM"

    @field_validator("car_id")
    @classmethod
    def car_id_must_not_shadow_a_namespace_attribute(cls, value):
        """CAR_ID_PATTERN alone still admits "get", which is a plain word.

        Namespace drops such a key rather than letting it shadow the
        accessor, so this is not the load-bearing guard -- but a name that
        would be silently dropped there should be refused here, where the
        player can still be told about it.
        """
        if value is not None and value in NAMESPACE_RESERVED_KEYS:
            raise ValueError(
                f"car_id {value!r} is reserved: rival ids become attribute "
                f"names in every bot's view of the race, and this one would "
                f"shadow the accessor bots use to read beliefs"
            )
        return value


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
@limiter.limit("30/hour", key_func=registration_rate_key)
def register(request: Request, req: RegisterRequest):
    # 404 rather than 401/403: an unauthenticated scanner should not learn
    # that this endpoint exists, let alone why it refused.
    if not provisioning_subject(request):
        raise HTTPException(404, "Not Found")
    db = SessionLocal()
    try:
        existing = crud.get_player_by_username(db, req.username)
        if existing:
            raise HTTPException(400, "Username already taken")
        try:
            player = crud.create_player(db, req.username, req.team_name)
        except DuplicateKeyError:
            raise HTTPException(400, "Username already taken")
        return {
            "id": player.id,
            "username": player.username,
            "api_key": player.api_key,
            "elo": player.elo,
        }
    finally:
        db.close()


@app.post("/api/race/create")
@limiter.limit("30/minute")
def create_race(request: Request, req: CreateRaceRequest, x_api_key: str = Header()):
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

        race = crud.create_race(db, req.track, req.race_type, season_id=season_id,
                                owner_id=player["id"])
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
    error = validate_submission(req.code)
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

    db = SessionLocal()
    try:
        race = crud.get_race(db, race_id)
    finally:
        db.close()
    if race is None:
        raise HTTPException(404, "Race not found")
    if getattr(race, "owner_id", None) not in (None, player["id"]):
        raise HTTPException(403, "Only the race owner can start this race")

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
        results = crud.get_race_results(db, race_id)
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
@limiter.limit("10/minute")
def test_bot(request: Request, req: TestBotRequest, x_api_key: str = Header()):
    """Run a quick offline simulation with the user's bot vs built-in bots."""
    player = authenticate(x_api_key)

    error = validate_submission(req.code)
    if error:
        raise HTTPException(400, error)

    track = build_track_physics(req.track)
    track_cfg = TRACKS[req.track]

    # Override total laps for quick test (0 = full race distance). Applied in
    # the parent, so the prebuilt physics carried in the spec already knows it.
    track.total_laps = track_cfg.total_laps if req.laps <= 0 else min(req.laps, track_cfg.total_laps)

    spec = {
        "track": req.track,
        "track_physics": track,
        "seed": random.randint(0, 99999),
        "cars": [{
            "car_id": "USER", "player_id": player["id"], "code": req.code,
            "start_position": 1, "starting_compound": "MEDIUM",
        }],
    }
    for pos, (bot_id, bot_info) in enumerate(BUILTIN_BOTS.items(), 2):
        spec["cars"].append({
            "car_id": bot_id, "player_id": bot_id, "bot_id": bot_id,
            "start_position": pos,
            "starting_compound": bot_info["starting_compound"],
        })

    try:
        result = run_match_isolated(spec)
    except LimitExceeded as exc:
        raise HTTPException(400, f"Your bot was stopped: {exc}")
    except ChildFailed as exc:
        # Ours, not theirs. Attributing an engine bug to the player's code
        # sends them hunting a fault they did not write, and loses the signal.
        logger.error("test-bot match failed for player %s: %s", player["id"], exc.detail)
        raise HTTPException(500, str(exc))

    return {
        "standings": [
            {
                "car_id": c["car_id"],
                "position": c["position"],
                "gap_to_leader": round(c["gap_to_leader"], 3),
                "pit_count": c["pit_count"],
                "pit_laps": c["pit_laps"],
                "retired": c["retired"],
            }
            for c in result["standings"]
        ],
        "events": [
            {"lap": e["lap"], "type": e["event_type"], "detail": e["detail"]}
            for e in result["events"][:50]
        ],
    }


@app.get("/api/strategy/template")
def get_strategy_template():
    return {"template": STRATEGY_TEMPLATE}


# ─── Season endpoints ───────────────────────────────────────────────

@app.post("/api/season")
def create_season(req: CreateSeasonRequest, x_api_key: str = Header()):
    player = authenticate(x_api_key)
    require_admin(player)
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
    player = authenticate(x_api_key)
    require_admin(player)
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
                p = crud.get_player_by_id(db, pid)
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

    # Build the match spec. Track physics are built here, in the parent: the
    # isolated child forbids file writes and build_track_physics writes a
    # calibration cache.
    track = build_track_physics(lobby.track)
    seed = random.randint(0, 99999)

    spec = {
        "track": lobby.track,
        "track_physics": track,
        "seed": seed,
        "cars": [],
    }
    pos = 1
    for pid, pdata in lobby.players.items():
        spec["cars"].append({
            "car_id": pdata["car_id"], "player_id": pid,
            "code": pdata["code"], "start_position": pos,
            "starting_compound": pdata.get("starting_compound", "MEDIUM"),
        })
        pos += 1
    for bot_id, bot_info in BUILTIN_BOTS.items():
        if pos > 10:
            break
        spec["cars"].append({
            "car_id": bot_id, "player_id": bot_id, "bot_id": bot_id,
            "start_position": pos,
            "starting_compound": bot_info["starting_compound"],
        })
        pos += 1

    # Run the match in a resource-limited child process, off the event loop.
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, run_match_isolated, spec)
    except MatchAborted as exc:
        if isinstance(exc, ChildFailed):
            logger.error("race %s aborted by an internal failure: %s", race_id, exc.detail)
        lobby.status = "aborted"
        # str() only: spectators on this socket are unauthenticated, and
        # ChildFailed keeps its raw text off str() for exactly that reason.
        await _broadcast(lobby, {"type": "aborted", "reason": str(exc)})
        db = SessionLocal()
        try:
            crud.update_race_status(db, race_id, "aborted")
        finally:
            db.close()
        return

    lobby.result = result
    total_laps = track.total_laps

    # Pre-index events by lap for fast lookup
    events_by_lap: Dict[int, list] = {}
    for e in result["events"]:
        events_by_lap.setdefault(e["lap"], []).append(
            {"lap": e["lap"], "type": e["event_type"],
             "car_id": e["car_id"], "detail": e["detail"]}
        )

    # Broadcast each lap with pacing for an enjoyable viewing experience
    # Speed: 1x = 3.75s/lap, 5x = 0.75s/lap, 20x = 0.1875s/lap
    for lap_snapshot in result["lap_data"]:
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
        # crud reads standings and events by attribute; the isolated child
        # hands them back as plain dicts, so adapt at this boundary.
        crud.save_race_results(
            db, race_id, [to_namespace(c) for c in result["standings"]]
        )
        crud.save_race_data(
            db, race_id, result["lap_data"],
            [to_namespace(e) for e in result["events"]],
        )

        # Update ELO
        standings_tuples = [
            (c["player_id"], c["position"], c["retired"])
            for c in result["standings"]
        ]
        current_ratings = {}
        for pid, _, _ in standings_tuples:
            player = crud.get_player_by_id(db, pid)
            current_ratings[pid] = player.elo if player else 1200.0

        k_factor = 48.0 if lobby.race_type == "season" else 32.0
        new_ratings = compute_elo_updates(standings_tuples, current_ratings, k_factor)

        for pid, new_elo in new_ratings.items():
            player = crud.get_player_by_id(db, pid)
            if player:
                old_elo = player.elo
                crud.update_player_elo(db, pid, new_elo)
                crud.save_elo_history(db, pid, race_id, old_elo, new_elo)
    finally:
        db.close()

    # Broadcast final results
    await _broadcast(lobby, {
        "type": "finished",
        "result": _serialize_result(result),
    })


# ─── Serialization helpers ───────────────────────────────────────────

def _serialize_result(result) -> dict:
    if result is None:
        return None
    return {
        "track": result["track"],
        "total_laps": result["total_laps"],
        "standings": [
            {
                "car_id": c["car_id"],
                "player_id": c["player_id"],
                "position": c["position"],
                "gap_to_leader": round(c["gap_to_leader"], 3),
                "compound": c["compound"],
                "tyre_age": c["tyre_age"],
                "pit_count": c["pit_count"],
                "pit_laps": c["pit_laps"],
                "compounds_used": c["compounds_used"],
                "total_time": round(c["total_time"], 3),
                "retired": c["retired"],
            }
            for c in result["standings"]
        ],
        "events": [
            {"lap": e["lap"], "type": e["event_type"],
             "car_id": e["car_id"], "detail": e["detail"]}
            for e in result["events"]
        ],
        "weather_history": result["weather_history"],
    }
