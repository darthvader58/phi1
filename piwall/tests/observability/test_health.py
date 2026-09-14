"""Liveness and readiness answer different questions and must not be merged.

/health says "this process is running" and must touch no dependency: if it
queried Mongo, a database blip would make a load balancer kill and restart
containers that were working perfectly.

/ready says "this process can serve traffic", which does depend on Mongo and
Redis. Conflating them means either restarting healthy processes or routing
traffic to ones that cannot answer.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.observability.health import check_dependencies, health_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(health_router)
    return TestClient(app)


def test_health_is_200_with_no_dependencies_available(monkeypatch):
    """The whole point: liveness must not depend on Mongo or Redis."""
    import backend.observability.health as mod

    monkeypatch.setattr(mod, "redis_is_reachable", lambda **kw: False)
    monkeypatch.setattr(mod, "_mongo_is_reachable", lambda **kw: False)
    response = _client().get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_health_names_the_service_and_does_not_claim_readiness():
    body = _client().get("/health").json()
    assert body["status"] == "alive"
    assert "ready" not in body


def test_ready_is_200_when_both_dependencies_answer(monkeypatch):
    import backend.observability.health as mod

    monkeypatch.setattr(mod, "redis_is_reachable", lambda **kw: True)
    monkeypatch.setattr(mod, "_mongo_is_reachable", lambda **kw: True)
    response = _client().get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "redis": True, "mongo": True}


def test_ready_is_503_when_redis_is_down(monkeypatch):
    import backend.observability.health as mod

    monkeypatch.setattr(mod, "redis_is_reachable", lambda **kw: False)
    monkeypatch.setattr(mod, "_mongo_is_reachable", lambda **kw: True)
    response = _client().get("/ready")
    assert response.status_code == 503
    assert response.json()["redis"] is False


def test_ready_is_503_when_mongo_is_down(monkeypatch):
    import backend.observability.health as mod

    monkeypatch.setattr(mod, "redis_is_reachable", lambda **kw: True)
    monkeypatch.setattr(mod, "_mongo_is_reachable", lambda **kw: False)
    response = _client().get("/ready")
    assert response.status_code == 503
    assert response.json()["mongo"] is False


def test_ready_names_which_dependency_failed(monkeypatch):
    """A 503 that does not say what is down costs an on-call engineer time."""
    import backend.observability.health as mod

    monkeypatch.setattr(mod, "redis_is_reachable", lambda **kw: False)
    monkeypatch.setattr(mod, "_mongo_is_reachable", lambda **kw: False)
    body = _client().get("/ready").json()
    assert body["redis"] is False
    assert body["mongo"] is False


def test_check_dependencies_returns_both_flags(monkeypatch):
    import backend.observability.health as mod

    monkeypatch.setattr(mod, "redis_is_reachable", lambda **kw: True)
    monkeypatch.setattr(mod, "_mongo_is_reachable", lambda **kw: False)
    assert check_dependencies() == {"redis": True, "mongo": False}


def test_mongo_probe_is_false_and_fast_for_a_dead_address(monkeypatch):
    """The Mongo probe must not reuse the long-lived client or its defaults.

    Mirrors tests/state/test_redis_client.py::test_reachability_is_false_for_a_dead_address.
    pymongo's default serverSelectionTimeoutMS is 30 seconds; a readiness
    probe that takes 30 seconds to say "no" reads to an orchestrator as a
    hung process, not a fast, cheap check that failed.
    """
    import time

    import backend.db.models as models
    from backend.observability.health import _mongo_is_reachable

    monkeypatch.setattr(models, "mongo_url", lambda: "mongodb://127.0.0.1:59999/dead")

    start = time.monotonic()
    result = _mongo_is_reachable(timeout=0.2)
    elapsed = time.monotonic() - start

    assert result is False
    assert elapsed < 5, f"probe took {elapsed:.2f}s -- the timeout is not being honored"
