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
import hashlib
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

from .db.models import MongoSession, create_db_engine, init_db
from .db import crud
from .data.tracks import TRACKS
from .determinism.manifest import Participant, build_manifest
from .engine.physics import TyreModel, TrackPhysics
from .engine.bots import BUILTIN_BOTS
from .engine.build import build_track_physics
from .jobs.events import MatchEvents
from .jobs.queue import MatchJobQueue
from .sandbox.runner import NAMESPACE_RESERVED_KEYS, STRATEGY_TEMPLATE
from .state.car_ids import assert_unique_car_ids
from .state.lobby import CarIdTakenError, LobbyFullError, LobbyStore
from backend.sandbox.validation import validate_submission
from backend.sandbox.isolation import ChildFailed, LimitExceeded
from backend.sandbox.match_job import run_match_isolated


# ─── State management ──────────────────────────────────────────────────

# Lobby state lives in Redis so both API replicas see the same lobby. A
# lobby is a plain dict (fields: race_id, track, race_type, status, speed,
# players) — this replaces active_lobbies, a module-global dict invisible
# to any replica that did not happen to create or mutate a given lobby.
#
# LOBBIES and EVENTS are safe to build here: neither constructor makes a
# Redis round trip (LobbyStore.register_script only computes a local SHA1;
# MatchEvents just stores a channel name). JOBS is different --
# MatchJobQueue.__init__ calls xgroup_create, a real round trip -- so it is
# built lazily instead, by _get_jobs() below. Building it eagerly here made
# `import backend.main` require a live Redis, which meant every test file
# that imports this module (most of them do not even touch JOBS) errored
# at collection with Redis down instead of the Redis-dependent ones
# skipping -- the suite ran zero tests instead of skipping only what
# needed Redis, which is exactly the failure the global constraints name.
LOBBIES = LobbyStore()
EVENTS = MatchEvents()

# This replica's own WebSocket connections, keyed by race id. Deliberately
# NOT in Redis: a socket is a live object owned by one process. Each replica
# serves the clients attached to it and learns when to do so from EVENTS.
# A race id's entry is removed once its last socket disconnects (see
# websocket_race and _broadcast) rather than left behind as an empty set,
# so membership here (`race_id in SOCKETS`) means "this replica currently
# has at least one spectator" and nothing durable is ever decided by it —
# only whether to bother calling send_json.
SOCKETS: Dict[str, Set[WebSocket]] = {}

# Fire-and-forget background tasks (currently only _run_race) need a
# strong reference held somewhere other than the event loop's own internal
# bookkeeping, or a task with nothing else referencing it can be garbage
# collected before it finishes -- a well-known asyncio footgun, independent
# of _run_race's own try/except making sure no exception escapes it
# uncaught. Tasks remove themselves once done via add_done_callback.
_background_tasks: Set[asyncio.Task] = set()

logger = logging.getLogger("piwall")


def _spawn_background(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


_jobs: Optional[MatchJobQueue] = None


def _get_jobs() -> MatchJobQueue:
    """Build the job queue on first real use rather than at import time.

    See the comment above LOBBIES/EVENTS for why: this is the one
    constructor here that touches Redis.
    """
    global _jobs
    if _jobs is None:
        _jobs = MatchJobQueue()
    return _jobs


def __getattr__(name: str):
    """PEP 562: make `main.JOBS` resolve lazily too, for external access.

    `main.JOBS` is part of this module's tested surface
    (tests/api/test_lobby_integration.py asserts
    isinstance(main.JOBS, MatchJobQueue)). Internal code calls _get_jobs()
    directly -- a module's own top-level code does not go through
    __getattr__ for its own global names -- but this keeps `main.JOBS`
    itself working for callers outside the module without eagerly
    constructing it just because someone imported this file.
    """
    if name == "JOBS":
        return _get_jobs()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    # Relays finished-match events to whichever sockets this replica holds.
    # Runs on every replica; cancelled on shutdown so it never outlives the
    # app it belongs to.
    event_task = asyncio.create_task(_relay_match_events())
    yield
    event_task.cancel()
    try:
        await event_task
    except asyncio.CancelledError:
        pass
    except Exception:
        # N4's residual (round 3), closed. Cancelling the relay runs its
        # own `finally`, which closes the pub/sub connection -- against a
        # Redis that has already gone away, that raises, and the raise
        # comes out of `await event_task` as itself rather than as
        # CancelledError. Uncaught it propagates out of ASGI shutdown,
        # turning a clean stop into a failed one over a connection that
        # was being discarded anyway. There is nothing left to salvage at
        # this point in shutdown, so it is logged and swallowed; anything
        # this task needed to do durably was done before it was
        # cancelled.
        logger.exception("event relay did not shut down cleanly")
    try:
        drained = await drain_sockets()
        logger.info("closed %d websocket(s) on shutdown", drained)
    except Exception:
        # A failure here must not be the reason ASGI shutdown itself fails
        # or hangs -- draining is a best-effort courtesy to spectators, not
        # a step anything durable depends on (see drain_sockets's own
        # docstring). Whatever this was, there is nothing left to salvage
        # for it at this point in shutdown.
        logger.exception("socket drain did not complete cleanly")
    print("PIT WALL shutting down...")


app = FastAPI(title="PIT WALL", version="0.1.0", lifespan=lifespan)

from .observability.health import health_router
from .observability.logging import configure_logging

configure_logging("api")
app.include_router(health_router)


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
        LOBBIES.create(race.id, track=req.track, race_type=req.race_type)
        LOBBIES.set_speed(race.id, req.speed)
        return {"race_id": race.id, "track": req.track, "status": "lobby",
                "race_type": req.race_type, "season_id": season_id}
    finally:
        db.close()


@app.post("/api/race/{race_id}/join")
def join_race(race_id: str, req: JoinRaceRequest, x_api_key: str = Header()):
    player = authenticate(x_api_key)
    lobby = LOBBIES.get(race_id)
    if not lobby:
        raise HTTPException(404, "Race not found")
    if lobby["status"] != "lobby":
        raise HTTPException(400, "Race already started")

    # car_id is an engine identity, not a display label (see
    # CAR_ID_PATTERN's own docstring below, and engine/race.py's
    # self.strategies / self.belief_models, both keyed by it) -- so
    # nothing about it is decided here. Any check or default computed in
    # this handler would sit between LOBBIES.get() above and the write
    # below, which is exactly the window two replicas can both pass. It
    # is all done inside LOBBIES.join()'s single atomic script instead.
    # The rules, in full, are on LobbyStore.join; in short: a re-join
    # that sends no car_id (which is every join the shipped frontend
    # makes -- frontend/src/lib/api.ts posts only starting_compound)
    # keeps the one it holds, unless that id has become impossible to
    # keep, in which case it is assigned a free one; a re-join that
    # sends a different unclaimed car_id is honoured; a new player with
    # no car_id gets the lowest label no other car in the race holds;
    # and any explicit label another car already holds -- another lobby
    # player OR one of the house bots reserved in state/car_ids.py -- is
    # refused.
    try:
        _is_new, position, car_id = LOBBIES.join(race_id, player["id"], {
            "username": player["username"],
            "car_id": req.car_id,
            "code": STRATEGY_TEMPLATE,
            "starting_compound": req.starting_compound,
        })
    except LobbyFullError:
        raise HTTPException(400, "Race is full (8 players max)")
    except CarIdTakenError as exc:
        # str(exc), not req.car_id: LobbyStore.join raises naming the id
        # it actually refused, and reformatting from the request field
        # reported "car_id None is already taken" for any refusal where
        # the caller sent none -- naming a value they never supplied.
        raise HTTPException(
            400,
            f"{exc}. Re-join with a different car_id, or omit it to be "
            f"assigned a free one.",
        )
    return {"car_id": car_id, "position": position}


@app.post("/api/race/{race_id}/submit-bot")
def submit_bot(race_id: str, req: SubmitBotRequest, x_api_key: str = Header()):
    player = authenticate(x_api_key)
    lobby = LOBBIES.get(race_id)
    if not lobby:
        raise HTTPException(404, "Race not found")
    if player["id"] not in lobby["players"]:
        raise HTTPException(400, "Not in this race")

    # Validate code
    error = validate_submission(req.code)
    if error:
        raise HTTPException(400, error)

    # set_player_field, not add_player: add_player replaces the whole
    # player dict, and this handler only has a snapshot of it (read above,
    # for the membership check) -- writing that whole snapshot back would
    # silently clobber any other field (car_id, starting_compound) a
    # concurrent request changed in between the read and this write.
    LOBBIES.set_player_field(race_id, player["id"], "code", req.code)
    car_id = lobby["players"][player["id"]]["car_id"]

    # Save to DB
    db = SessionLocal()
    try:
        crud.save_bot_submission(db, player["id"], req.code, race_id)
    finally:
        db.close()

    return {"status": "submitted", "car_id": car_id}


@app.post("/api/race/{race_id}/start")
async def start_race(race_id: str, x_api_key: str = Header()):
    player = authenticate(x_api_key)
    lobby = LOBBIES.get(race_id)
    if not lobby:
        raise HTTPException(404, "Race not found")
    if lobby["status"] != "lobby":
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

    LOBBIES.set_status(race_id, "countdown")

    db = SessionLocal()
    try:
        crud.update_race_status(db, race_id, "countdown")
    finally:
        db.close()

    # Start race in background
    _spawn_background(_run_race(race_id))
    return {"status": "countdown", "message": "Race starting in 5 seconds..."}


# A lobby's status only ever advances to "finished"/"aborted" durably, in
# the worker (see backend/worker.py's _persist_result) -- never in an API
# handler -- so once a lobby reports one of these it is terminal and the
# real data (results, lap data, Elo already applied) lives in Mongo, not
# in the Redis snapshot. Serving the (permanently None) Redis "result" for
# a terminal lobby is what F1 was: a finished race stuck reporting
# "running" with a null result for the rest of its 6h TTL.
_TERMINAL_LOBBY_STATUSES = ("finished", "aborted")


@app.get("/api/race/{race_id}")
def get_race(race_id: str):
    lobby = LOBBIES.get(race_id)
    if lobby and lobby["status"] not in _TERMINAL_LOBBY_STATUSES:
        return {
            "race_id": race_id,
            "track": lobby["track"],
            "status": lobby["status"],
            "players": {pid: {"username": p["username"], "car_id": p["car_id"]}
                        for pid, p in lobby["players"].items()},
            # Always None: there is no per-lap live state to report. The
            # worker runs a match to completion in one shot (N8, fix round
            # 3) rather than the API replaying lap_data incrementally the
            # way the pre-decouple in-process simulation did, so nothing
            # in this architecture ever produces an intermediate snapshot
            # to serve here. Reading a lobby field that no writer has
            # populated since round 1 (or serving one that looks live but
            # never was) would be worse than stating the gap: closing it
            # for real needs either the worker to publish incremental
            # progress over pub/sub or the replay-body storage Phase 4
            # is expected to add, neither of which exists yet.
            "current_state": None,
            "result": None,
        }

    # A finished/aborted race, or one whose Redis lobby has already aged
    # past its TTL, is read from Mongo -- the worker's persist step is what
    # writes results, lap data and events there once the match completes.
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
    return [
        {
            "race_id": lobby["race_id"],
            "track": lobby["track"],
            "status": lobby["status"],
            "player_count": len(lobby["players"]),
            "race_type": lobby["race_type"],
        }
        for lobby in LOBBIES.list_open()
    ]


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
    for lobby in LOBBIES.list_open():
        if lobby["status"] != "lobby":
            continue
        if len(lobby["players"]) >= 8:
            continue

        # Calculate average ELO of players in lobby
        db = SessionLocal()
        try:
            elos = []
            # sorted(), not a bare dict iteration: lobby["players"] came
            # through Redis as a Lua table re-encoded by cjson, so its key
            # order is a hash order rather than the stable insertion order
            # a Python dict would have had, and float addition is not
            # associative -- summing it in whatever order Redis happened
            # to produce would make this average order-dependent.
            for pid in sorted(lobby["players"]):
                p = crud.get_player_by_id(db, pid)
                if p:
                    elos.append(p.elo)
            avg_elo = sum(elos) / len(elos) if elos else 1200.0
            elo_diff = abs(player["elo"] - avg_elo)
            suggestions.append({
                "race_id": lobby["race_id"],
                "track": lobby["track"],
                "race_type": lobby["race_type"],
                "player_count": len(lobby["players"]),
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
    lobby = LOBBIES.get(race_id)
    if not lobby:
        await websocket.send_json({"error": "Race not found"})
        await websocket.close()
        return

    SOCKETS.setdefault(race_id, set()).add(websocket)
    try:
        # No initial "state" message: there is no per-lap live state to
        # send. See get_race's identical note (N8, fix round 3) -- the
        # worker runs a match to completion in one shot, so a socket that
        # connects mid-race has nothing to catch up on until the
        # "finished" event arrives; it is not silently dropping one.

        # Keep connection alive, listen for speed control messages
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)
                if msg.get("type") == "speed":
                    LOBBIES.set_speed(race_id, float(msg.get("speed", 1.0)))
            except asyncio.TimeoutError:
                # Send ping to keep alive
                await websocket.send_json({"type": "ping"})
            except WebSocketDisconnect:
                break
    finally:
        _discard_socket(race_id, websocket)


# Per-socket ceiling on how long drain_sockets waits for one close() to
# finish. WebSocket.close() awaits a send; a client with a wedged or full
# transport buffer would otherwise stall this coroutine indefinitely, and
# with it -- since lifespan awaits this before ASGI shutdown completes --
# the whole shutdown. Well under a client's own idle-ping timeout (30s in
# websocket_race above), so this is what actually bounds shutdown, not
# whatever the far end feels like doing.
_DRAIN_CLOSE_TIMEOUT_SECONDS = 5.0


async def drain_sockets() -> int:
    """Close every socket this replica holds. Returns how many closed cleanly.

    Called on shutdown so clients get a clean close frame and reconnect to a
    live replica, instead of waiting on a TCP timeout against a process that
    is already gone. Pure fan-out, on purpose: it must never write anything
    durable, and one socket's close() raising or hanging must never strand
    the rest -- SOCKETS is per-process spectator bookkeeping (see its own
    comment above), and nothing here decides whether a match finished.

    Sockets are closed concurrently, each bounded by _DRAIN_CLOSE_TIMEOUT_SECONDS,
    rather than one at a time with no limit: a sequential, unbounded loop
    means the single slowest (or wedged) client sets how long every other
    client -- and ASGI shutdown itself -- waits.
    """
    every_socket = [
        ws
        for race_id in list(SOCKETS.keys())
        for ws in list(SOCKETS.pop(race_id, set()))
    ]

    async def _close_one(ws) -> bool:
        try:
            await asyncio.wait_for(ws.close(), timeout=_DRAIN_CLOSE_TIMEOUT_SECONDS)
            return True
        except Exception:
            return False

    results = await asyncio.gather(*(_close_one(ws) for ws in every_socket))
    return sum(results)


def _discard_socket(race_id: str, websocket: WebSocket) -> None:
    """Drop one socket, and the race's whole entry once it holds none.

    Without the second half, `race_id in SOCKETS` stays true forever after
    the last spectator for that race disconnects -- an unbounded leak, and
    (before this fix round) the exact membership test _relay_match_events
    used to decide whether a match's durable "finished" write happened at
    all. It no longer gates anything durable (see _persist_result in
    backend/worker.py), but the leak is worth closing regardless.
    """
    sockets = SOCKETS.get(race_id)
    if sockets is None:
        return
    sockets.discard(websocket)
    if not sockets:
        SOCKETS.pop(race_id, None)


# ─── Race execution ─────────────────────────────────────────────────

async def _broadcast(race_id: str, message: dict) -> None:
    """Send to this replica's sockets for a race, dropping dead ones."""
    sockets = SOCKETS.get(race_id)
    if not sockets:
        return
    dead = set()
    for ws in list(sockets):
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)
    for ws in dead:
        _discard_socket(race_id, ws)


def _build_job_and_manifest(race_id: str, lobby: dict):
    """Assemble a worker job and the manifest that describes it.

    Slots are dense and contiguous starting at 0: the worker derives each
    bot's RNG stream from its index in the spec while grid position comes
    from the slot, and sparse slots would make those two disagree. Sorted
    by player id first so the grid is deterministic across replicas -- a
    dict's insertion order is not something two processes are guaranteed
    to agree on.
    """
    participants = []
    manifest_participants = []
    slot = 0
    for pid, pdata in sorted(lobby["players"].items()):
        code = pdata.get("code") or ""
        code_sha256 = "sha256:" + hashlib.sha256(code.encode()).hexdigest()
        participants.append({
            "slot": slot,
            "player_id": pid,
            "car_id": pdata.get("car_id"),
            "code": code,
            # Carried through so the worker's own manifest
            # (_manifest_from_job) hashes to the same value as this one --
            # without it, _manifest_from_job reads code_sha256 as None and
            # embeds a manifest in the replay that does not match the row
            # crud.save_manifest wrote for this same match_id below.
            "code_sha256": code_sha256,
            "starting_compound": pdata.get("starting_compound"),
        })
        manifest_participants.append(Participant(
            slot=slot,
            player_id=pid,
            bot_version_id=None,
            code_sha256=code_sha256,
            house_bot=None,
        ))
        slot += 1
    for bot_id in BUILTIN_BOTS:
        if slot >= 10:
            break
        participants.append({"slot": slot, "player_id": bot_id, "house_bot": bot_id})
        manifest_participants.append(Participant(
            slot=slot, player_id=bot_id, bot_version_id=None,
            code_sha256=None, house_bot=bot_id,
        ))
        slot += 1

    # The LOBBY chokepoint for the car_id uniqueness invariant
    # (state/car_ids.py). Both lobby-path sources converge here -- players
    # above, house bots just now -- and this is the last point before the
    # JOB is built, the job being what carries car_id to the worker. The
    # MANIFEST does not: determinism/manifest.Participant has no car_id
    # field, so the manifest hash is independent of every choice made here.
    # The replay hash does depend on it (replay_bytes serialises
    # final_standings through car_state_to_dict), which is the artefact a
    # duplicate would actually corrupt.
    #
    # This is not the only check. It is the EARLY one: failing here means
    # failing in this process, before save_manifest and before enqueue, so
    # a bad grid degrades to _run_race's abort path with no manifest row
    # and no stranded job. The check that no grid can route around lives in
    # RaceEngine.add_car, because three other sites in this codebase build
    # a grid without coming through here -- see state/car_ids.py's map.
    #
    # `car_id or house_bot` is exactly how worker._spec_from_job derives the
    # id it hands engine.add_car, so this checks the values the engine will
    # actually see -- including a player row with no car_id at all, which
    # would reach the engine as None.
    assert_unique_car_ids(
        p.get("car_id") or p.get("house_bot") for p in participants
    )

    seed = int(lobby.get("seed") or random.randint(0, 99999))
    manifest = build_manifest(race_id, seed, lobby["track"], manifest_participants)
    job = {
        "match_id": race_id,
        "track": lobby["track"],
        "seed": seed,
        "participants": participants,
    }
    return job, manifest


async def _run_race(race_id: str):
    """Countdown, hand the match to a worker, then stream the stored replay.

    The API no longer simulates. It never did so incrementally — the
    previous code computed the whole race in one shot and then re-played
    lap_data for display pacing — so this changes which process holds the
    race while it runs, not what a spectator eventually sees finish.

    Everything from the countdown through the enqueue is wrapped in one
    try/except. Without it, a `save_manifest` ValueError (two replicas both
    pass start_race's non-atomic "is this lobby still open" check and race
    to start the same lobby with different random seeds) or a queue error
    on enqueue left the race at status "running" with no job ever enqueued
    -- forever, since this coroutine is launched fire-and-forget and
    nothing else was watching for its exception.
    """
    lobby = LOBBIES.get(race_id)
    if lobby is None:
        return

    try:
        for seconds in (5, 4, 3, 2, 1):
            await _broadcast(race_id, {"type": "countdown", "seconds": seconds})
            await asyncio.sleep(1.0)
        await _broadcast(race_id, {"type": "lights_out"})

        LOBBIES.set_status(race_id, "running")
        db = SessionLocal()
        try:
            crud.update_race_status(db, race_id, "running")
        finally:
            db.close()

        job, manifest = _build_job_and_manifest(race_id, lobby)

        # save_manifest before enqueue, not after: the worker's persist step
        # only ever UPDATEs the manifest row for a match id (it never
        # upserts), and it now raises rather than ack a match with nothing
        # durable written. Enqueueing first would let the worker claim the
        # job before a manifest exists for it, so every persist attempt
        # would match zero documents and the job would fail, get reclaimed,
        # and fail forever.
        db = SessionLocal()
        try:
            crud.save_manifest(db, manifest)
        finally:
            db.close()

        _get_jobs().enqueue(job)
    except Exception:
        logger.exception("race %s failed to start; marking it aborted", race_id)
        try:
            LOBBIES.set_status(race_id, "aborted")
        except KeyError:
            pass
        db = SessionLocal()
        try:
            crud.update_race_status(db, race_id, "aborted")
        finally:
            db.close()
        # Generic text, deliberately: spectators on this socket are
        # unauthenticated, and the real exception (a ValueError from a
        # manifest conflict, a Redis error from enqueue) is not theirs to
        # see. logger.exception above is where the real detail goes.
        await _broadcast(race_id, {"type": "aborted", "reason": "internal error"})


_RELAY_MIN_BACKOFF_SECONDS = 1.0
_RELAY_MAX_BACKOFF_SECONDS = 30.0


async def _relay_match_events() -> None:
    """Stream finished matches to whichever sockets this replica holds.

    Runs on every replica, fed by one shared pub/sub channel; a replica
    with no sockets for a match correctly does nothing here, which is why
    fanning the event out to every replica is correct rather than
    wasteful. The durable half of "this match is finished" -- the race
    document's status and the Redis lobby's status -- is written by the
    worker itself before it ever publishes (see backend/worker.py's
    _persist_result), unconditionally, whether or not any replica has a
    spectator. SOCKETS decides fan-out here and nothing durable.

    Two failure modes, both handled with capped exponential backoff
    sharing one counter that resets on any success:

    - EVENTS.subscribe() itself can fail -- Redis down at the moment this
      task starts, e.g. at app startup. Round 2 left this call outside any
      try/except, so that failure killed the task for the rest of the
      process's life, surfacing only when `lifespan` awaited it at
      shutdown. This retries the subscribe itself instead.
    - Each iteration inside the subscribed loop (the listen call, and
      relaying one event) is wrapped in its own try/except: one Redis
      blip on EVENTS.listen or one Mongo blip while looking up a replay
      hash must not permanently kill relay either. redis-py's PubSub
      re-subscribes on its own after a transient connection drop, so the
      same pubsub object is reused across these -- the backoff here is
      purely to stop a *sustained* outage from spinning this loop as fast
      as the failure can be raised and logged.
    """
    backoff = _RELAY_MIN_BACKOFF_SECONDS
    while True:
        try:
            pubsub = EVENTS.subscribe()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "event relay: could not subscribe, retrying in %.1fs", backoff
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RELAY_MAX_BACKOFF_SECONDS)
            continue

        loop = asyncio.get_running_loop()
        # A dedicated single-thread executor for this coroutine's own
        # blocking listen() calls, not asyncio's shared default executor:
        # on cancellation (app shutdown), the `await` here raises
        # immediately, but a concurrent.futures.Future that has already
        # started running cannot itself be cancelled or interrupted -- the
        # underlying thread keeps calling pubsub.get_message() until its
        # own 1.0s timeout elapses. This executor's own shutdown(wait=True)
        # in the finally below blocks until that call has actually
        # returned, so pubsub.close() can never run concurrently with it
        # -- redis-py's PubSub is not safe against a concurrent close.
        # The shutdown call is itself dispatched to the default executor
        # (`run_in_executor(None, ...)`), not awaited directly, so that
        # blocking wait does not stall this event loop for other work
        # while it happens.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            while True:
                try:
                    event = await loop.run_in_executor(
                        executor, EVENTS.listen, pubsub, 1.0
                    )
                    backoff = _RELAY_MIN_BACKOFF_SECONDS
                    if not event or event.get("type") != "match_finished":
                        continue
                    race_id = event["match_id"]
                    if race_id not in SOCKETS:
                        continue
                    await _stream_stored_replay(race_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "event relay: dropping this event, backing off %.1fs",
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _RELAY_MAX_BACKOFF_SECONDS)
        finally:
            await loop.run_in_executor(None, executor.shutdown, True)
            pubsub.close()


async def _stream_stored_replay(race_id: str) -> None:
    """Tell this replica's sockets the match is done. Fan-out only.

    The durable "this match is finished" write -- the race document's
    status and the Redis lobby's status -- already happened in the worker
    before it published the event that triggered this call (see
    backend/worker.py's _persist_result), regardless of whether any
    replica has a spectator socket for it. This function's only job is
    telling THIS replica's own sockets, if it has any for this race.

    The worker persists the manifest and the replay's hash but not yet the
    replay body itself -- object storage for replay bodies is deferred
    beyond this phase (see the phase plan's "Deferred beyond this phase"
    section) -- so there is no lap-by-lap body to re-stream here yet. This
    sends one terminal "finished" event rather than the paced per-lap
    messages the old in-process simulation produced.

    What it does carry is the final standings, because the client on the
    other end of this socket needs them to render the result at all.
    Round 1 of the decouple shrank this payload to {race_id,
    replay_sha256} on the reasoning that "a spectator who wants them
    reads GET /api/race/{race_id} after this event arrives" -- but no
    client does that. frontend/src/lib/websocket.ts's "finished" case
    does `if (msg.result) setResult(msg.result)`, and the shrunken
    payload is truthy, so the race page then evaluates
    `[...result.standings]` on a result that has no standings. That
    throws inside render, and with no error boundary under
    frontend/src/app/ it takes the whole race page down: every finished
    race, for every spectator. Round 3's re-review carried it forward as
    NEW-8, informational.

    The rows come from race_results (the post-race authority: it is
    written after the engine applies its end-of-race compound-rule time
    penalties, so positions and total_time here are final) rather than
    from the last lap_data snapshot (taken before them). gap_to_leader,
    pit_count and the finishing compound are not stored -- they are
    derived from total_time, pit_laps and compounds_used, which are. A
    race with no document or no rows yet sends an empty standings list,
    which renders as an empty result panel rather than crashing the page.

    Round 5 added the race's events for the same reason the standings
    were added in round 4: crud.save_race_data persists them durably, the
    worker sends no per-lap messages that could carry them, and nothing
    re-fetches -- so EventLog sat permanently empty on a finished race
    (NEW-12). What is still NOT here is per-lap state: tyre age, fuel,
    DRS and beliefs exist only while a race runs, and bringing them back
    means replay bodies, which are Phase 4. frontend types.ts's
    DisplayCar marks exactly those fields optional so a component has to
    say what it shows in their place rather than rendering a zero.
    """
    db = SessionLocal()
    try:
        replay_sha256 = crud.get_replay_hash(db, race_id)
        race = crud.get_race(db, race_id)
        results = crud.get_race_results(db, race_id) if race else []
        lap_data = getattr(race, "lap_data_json", None) or [] if race else []
        events = getattr(race, "events_json", None) or [] if race else []
        track = getattr(race, "track", None) if race else None
    finally:
        db.close()

    # get_race_results sorts by position, so the leader is first; a race
    # whose winner retired (everyone retired) has no meaningful baseline
    # and every gap is reported as 0.0.
    leader_time = None
    for row in results:
        if not row.retired and row.total_time is not None:
            leader_time = row.total_time
            break

    standings = [
        {
            "car_id": row.car_id,
            "position": row.position,
            "retired": row.retired,
            "points": row.points,
            "total_time": row.total_time,
            "gap_to_leader": (
                round(row.total_time - leader_time, 3)
                if not row.retired
                and row.total_time is not None
                and leader_time is not None
                else 0.0
            ),
            "pit_laps": row.pit_laps,
            "pit_count": len(row.pit_laps or []),
            "compounds_used": row.compounds_used,
            # The tyre the car finished on. Not stored as its own column,
            # but compounds_used is an ordered stint history, so the last
            # entry is exactly it -- the one live-timing field the result
            # row can honestly supply (round 5, NEW-11). tyre_age,
            # drs_available and beliefs genuinely cannot be: they are
            # per-lap state that only a replay body brings back, which is
            # Phase 4 work.
            "compound": (row.compounds_used or [None])[-1],
        }
        for row in results
    ]

    await _broadcast(race_id, {
        "type": "finished",
        "result": {
            "race_id": race_id,
            "replay_sha256": replay_sha256,
            "track": track,
            # The number of laps actually run. TyreStrategyChart scales
            # every stint bar by this, as ((endLap - startLap) / totalLaps),
            # so a 0 alongside non-empty standings renders width:Infinity%
            # (round 5, NEW-13). Falling back to the track's scheduled
            # distance keeps it a real number whenever lap_data is missing
            # but result rows are not; only a race with no document at all
            # reaches 0, and that case sends no standings to divide.
            "total_laps": (
                lap_data[-1].get("lap", len(lap_data)) if lap_data
                else getattr(TRACKS.get(track), "total_laps", 0)
            ),
            "standings": standings,
            # Persisted by crud.save_race_data and then dropped from this
            # payload until round 5 (NEW-12), which left EventLog
            # permanently empty on a finished decoupled race -- including
            # the +30s compound-rule penalty event that explains the
            # standings sitting next to it. The rows are already read
            # above; nothing new is fetched to send them. Shape is
            # {lap, type, car_id, detail}, which is frontend
            # types.ts's RaceEvent exactly.
            "events": events,
        },
    })

