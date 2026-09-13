"""Liveness and readiness, deliberately separate.

/health must touch nothing. A liveness probe that queries a database turns a
database incident into a rolling restart of processes that were fine.

/ready may touch everything it needs, because its job is to tell a load
balancer whether to send this process traffic.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..state.redis_client import redis_is_reachable

health_router = APIRouter(tags=["health"])


def _mongo_is_reachable(timeout: float = 0.5) -> bool:
    """Ping Mongo without importing main.

    main imports this module's router, so importing main back would be a
    circular import held apart only by call timing. backend/db/models.py
    already owns database configuration and imports main nowhere.
    """
    try:
        from ..db.models import create_db_engine, mongo_url

        engine = create_db_engine(mongo_url())
        engine.command("ping")
        return True
    except Exception:
        return False


def check_dependencies() -> dict:
    return {"redis": redis_is_reachable(), "mongo": _mongo_is_reachable()}


@health_router.get("/health")
def health() -> dict:
    """Liveness. Touches no dependency, by design."""
    return {"status": "alive"}


@health_router.get("/ready")
def ready() -> JSONResponse:
    """Readiness. 503 names the dependency that failed."""
    checks = check_dependencies()
    if all(checks.values()):
        return JSONResponse({"status": "ready", **checks}, status_code=200)
    return JSONResponse({"status": "not ready", **checks}, status_code=503)
