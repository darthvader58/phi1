"""Liveness and readiness, deliberately separate.

/health must touch nothing. A liveness probe that queries a database turns a
database incident into a rolling restart of processes that were fine.

/ready may touch everything it needs, because its job is to tell a load
balancer whether to send this process traffic.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..jobs import heartbeat
from ..state.redis_client import redis_is_reachable

health_router = APIRouter(tags=["health"])


def _mongo_is_reachable(timeout: float = 0.5) -> bool:
    """Ping Mongo without importing main, and without leaking a connection.

    main imports this module's router, so importing main back would be a
    circular import held apart only by call timing. backend/db/models.py
    already owns database configuration and imports main nowhere.

    Builds its own short-lived client rather than reusing create_db_engine()'s
    long-lived one: this runs on every /ready poll, and every other caller of
    create_db_engine builds one at startup and holds it for the process
    lifetime. A fresh, never-closed client per call leaks three background
    threads (server monitor, kill-cursors, RTT) per call, forever. The
    timeout is threaded into the client for the same reason redis_is_reachable
    threads it into its probe: pymongo's default serverSelectionTimeoutMS is
    30 seconds, and a readiness probe that takes 30 seconds to say "no" reads
    to an orchestrator as a hung process.
    """
    from pymongo import MongoClient

    from ..db.models import mongo_url

    client = None
    try:
        client = MongoClient(
            mongo_url(),
            serverSelectionTimeoutMS=int(timeout * 1000),
            connectTimeoutMS=int(timeout * 1000),
        )
        client.admin.command("ping")
        return True
    except Exception:
        return False
    finally:
        if client is not None:
            client.close()


def _live_worker_count() -> int:
    """How many workers have a live heartbeat. Never raises.

    Redis being unreachable is already reported by its own check, and a
    second exception from the same cause must not turn /ready into a 500 --
    a readiness probe that crashes tells an orchestrator nothing about
    readiness.
    """
    try:
        return len(heartbeat.live_workers())
    except Exception:
        return 0


def check_dependencies() -> dict:
    return {"redis": redis_is_reachable(), "mongo": _mongo_is_reachable()}


@health_router.get("/health")
def health() -> dict:
    """Liveness. Touches no dependency, by design."""
    return {"status": "alive"}


@health_router.get("/ready")
def ready() -> JSONResponse:
    """Readiness. 503 names the dependency that failed.

    `workers` is reported but deliberately does NOT affect the status code.
    Twice now, matches have quietly stopped finishing while this endpoint
    stayed green -- a vanished consumer group, and a poison job at the head
    of the queue -- and in both cases the missing fact was that no worker
    was making progress. That fact now has a number here (see
    jobs/heartbeat.py), so it can be alerted on.

    Failing readiness on it would be worse than not reporting it: an API
    replica with no worker behind it still serves every read endpoint
    correctly, and 503-ing every replica because the worker fleet is down
    turns a worker outage into a total outage. This endpoint answers "should
    this process receive traffic", and the answer does not change.
    """
    checks = check_dependencies()
    workers = _live_worker_count()
    if all(checks.values()):
        return JSONResponse(
            {"status": "ready", **checks, "workers": workers}, status_code=200
        )
    return JSONResponse(
        {"status": "not ready", **checks, "workers": workers}, status_code=503
    )
