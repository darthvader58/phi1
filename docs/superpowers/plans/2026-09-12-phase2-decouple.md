# Phase 2 — Decouple Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move match execution out of the API process so two API replicas can serve all traffic and a worker restart mid-match loses no match.

**Architecture:** A match becomes a job on a Redis Stream. A separate worker process consumes it, runs the existing isolated runner, persists the replay and result, and publishes a completion event. API replicas hold no match state — only their own WebSocket connections — and stream the stored replay to clients at display pace when notified. Shared lobby state moves from a module-global dict to Redis.

**Tech Stack:** Redis 8 (Streams for the job queue, pub/sub for completion events), redis-py 8.1.0 (already pinned and installed, currently unused), FastAPI, MongoDB, Python 3.11.

**Spec:** `docs/superpowers/specs/2026-09-02-pitwall-competitive-platform-design.md` (§4 Architecture, §11 phase gate)

## Global Constraints

- **Python 3.11** is the production interpreter (`Dockerfile.backend:1`). Local dev is 3.14.7; any behavioural claim must hold on 3.11, in the container.
- **`PYTHONHASHSEED=0`** is pinned in the runtime image. Do not remove it.
- **No numpy in the engine hot path.** numpy/scipy/fastf1 are calibration-tool-only and must stay out of `backend/requirements.txt`.
- **Never sum over an unordered collection.**
- **Determinism must not regress.** The six committed golden replay hashes must be unchanged by every task in this phase. `python scripts/verify_determinism.py` must print `ok` for all six. If a hash moves, stop and report — do not re-record.
- **Do not weaken the sandbox.** `getattr`/`hasattr` stay out of `ALLOWED_BUILTINS`; every guard in `build_sandbox_globals` stays installed; `_guarded_getattr` keeps refusing class attribute reads.
- **Control signals declare their own handling.** Any new `BaseException` subclass under `backend/` must derive from `RecordedOutcome` or `MatchVoiding` (`backend/determinism/signals.py`), or `tests/sandbox/test_signal_contract.py` fails.
- **Commit messages are ONE LINE.** No `Co-Authored-By:` or `Claude-Session:` trailers. No mention of Claude, Claude Code, or any AI assistant in commit messages, PR text, or created files. Hard requirement from the repo owner.
- Tests must **skip**, never error, when an external service is unreachable. Gate on reachability, not on an environment variable being set — see `tests/determinism/test_persistence.py::_database_is_reachable` for the established pattern.

---

## What already exists

Do not rebuild these. Phase 0 and Phase 1 left the runner and its determinism contract in place:

| Already built | Where |
|---|---|
| `run_match_isolated(spec)` — runs one match in a spawned, rlimited child | `backend/sandbox/match_job.py` |
| `MatchManifest`, `build_manifest`, `manifest_sha256` | `backend/determinism/manifest.py` |
| `replay_bytes`, `replay_sha256`, `replay_from_manifest` | `backend/determinism/replay.py` |
| `save_manifest`, `get_manifest`, `save_replay_hash` (immutable) | `backend/db/crud.py` |
| `canonical_json` — the definition of byte-identical | `backend/determinism/canonical.py` |
| 266 tests and a four-job CI workflow | `piwall/tests/`, `.github/workflows/ci.yml` |

**The key existing fact this phase pivots on:** `_run_race` (`backend/main.py:915`) already computes the whole race in one shot at `run_match_isolated(spec)`, then re-plays `result["lap_data"]` lap-by-lap with `asyncio.sleep(11.0 / lobby.speed)` purely for display pacing. The worker/API seam already exists in that function; this phase cuts along it rather than inventing a new one.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/state/__init__.py` | New package for shared, out-of-process state |
| `backend/state/redis_client.py` | One Redis connection factory, `REDIS_URL` config, reachability ping |
| `backend/state/lobby.py` | `LobbyStore`: lobby documents in Redis, replacing `active_lobbies` |
| `backend/jobs/__init__.py` | New package for the match job queue |
| `backend/jobs/queue.py` | `MatchJobQueue`: Redis Streams producer/consumer with ack and stalled-job reclaim |
| `backend/jobs/events.py` | `MatchEvents`: pub/sub publish and subscribe for match lifecycle |
| `backend/worker.py` | Worker process entrypoint: consume, run, persist, publish |
| `backend/observability/__init__.py` | New package |
| `backend/observability/logging.py` | Structured JSON logging with a match/request correlation id |
| `backend/observability/health.py` | `/health` (liveness, no dependencies) and `/ready` (Mongo + Redis) |
| `backend/main.py` | Modified: remove `active_lobbies`, use `LobbyStore`, enqueue jobs, subscribe to events, graceful shutdown |
| `Dockerfile.worker` | Worker image, reusing the backend image's dependency set |
| `docker-compose.yml` | Add `redis` and `worker`; run two `backend` replicas |
| `tests/state/`, `tests/jobs/`, `tests/observability/` | Unit tests per package |
| `tests/integration/test_phase2_gate.py` | The two phase-gate tests |

---

### Task 1: Redis connection and reachability

Nothing in the codebase imports Redis yet, though `redis==8.1.0` has been pinned since Phase 1. This task adds the single place a connection is made, and the reachability probe every later test gates on.

**Files:**
- Create: `piwall/backend/state/__init__.py`
- Create: `piwall/backend/state/redis_client.py`
- Create: `piwall/tests/state/__init__.py`
- Test: `piwall/tests/state/test_redis_client.py`

**Interfaces:**
- Produces: `REDIS_URL: str`, `get_redis() -> redis.Redis`, `redis_is_reachable(timeout: float = 0.5) -> bool`, `close_redis() -> None`

- [ ] **Step 1: Write the failing test**

```python
# piwall/tests/state/test_redis_client.py
"""One place makes a Redis connection, and one probe says whether it works.

The reachability probe exists because every later test in this phase gates on
it. Gating on `REDIS_URL` being set would repeat a mistake this repo has
already made: under compose the variable is always present and points at a
service that may not be running, which turned five skips into five errors and
three minutes of connection timeouts.
"""

import pytest

from backend.state.redis_client import (
    REDIS_URL,
    close_redis,
    get_redis,
    redis_is_reachable,
)

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(),
    reason="needs a reachable Redis; the rest of the suite is hermetic",
)


def test_redis_url_has_a_local_default():
    assert REDIS_URL.startswith("redis://")


def test_get_redis_returns_a_working_client():
    client = get_redis()
    assert client.ping() is True


def test_get_redis_reuses_one_connection_pool():
    """A new pool per call would exhaust connections under load."""
    assert get_redis() is get_redis()


def test_values_round_trip_as_strings_not_bytes():
    """decode_responses must be on, or every caller has to .decode()."""
    client = get_redis()
    client.set("piwall:test:roundtrip", "hello")
    try:
        assert client.get("piwall:test:roundtrip") == "hello"
    finally:
        client.delete("piwall:test:roundtrip")


def test_close_redis_allows_a_fresh_client_afterwards():
    first = get_redis()
    close_redis()
    second = get_redis()
    assert second is not first
    assert second.ping() is True


def test_reachability_is_false_for_a_dead_address(monkeypatch):
    """The probe must test reachability, not configuration."""
    import backend.state.redis_client as mod

    monkeypatch.setattr(mod, "REDIS_URL", "redis://127.0.0.1:59998/0")
    mod.close_redis()
    try:
        assert mod.redis_is_reachable(timeout=0.2) is False
    finally:
        mod.close_redis()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/state/test_redis_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.state'`

- [ ] **Step 3: Implement the client**

```python
# piwall/backend/state/redis_client.py
"""The one place a Redis connection is made.

Redis holds what must outlive a single API process: lobby state, the match job
queue, and match lifecycle events. Two API replicas share all of it, so any
state that stays in a module-global dict is state the second replica cannot
see.
"""

import os
from typing import Optional

import redis

REDIS_URL = os.environ.get("REDIS_URL") or "redis://127.0.0.1:6379/0"

_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    """Return the process-wide client, creating it on first use.

    One client, therefore one connection pool. A client per call would open a
    fresh pool each time and exhaust Redis's connection limit under load.

    decode_responses=True so callers get `str` rather than `bytes`; without it
    every read site has to remember to decode, and the one that forgets
    compares a bytes key against a str and silently never matches.
    """
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            health_check_interval=30,
        )
    return _client


def close_redis() -> None:
    """Drop the client so the next get_redis() builds a fresh one."""
    global _client
    if _client is not None:
        try:
            _client.close()
        finally:
            _client = None


def redis_is_reachable(timeout: float = 0.5) -> bool:
    """Whether Redis actually answers, not whether REDIS_URL is set.

    Tests gate on this. The short timeout matters because it runs at
    collection time, so a slow probe is paid by every session whether or not
    Redis exists.
    """
    try:
        probe = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
        )
        try:
            return probe.ping() is True
        finally:
            probe.close()
    except Exception:
        return False
```

```python
# piwall/backend/state/__init__.py
"""Shared state that must outlive a single API process."""
```

```python
# piwall/tests/state/__init__.py
```

- [ ] **Step 4: Run the tests with a Redis available**

```bash
docker run -d --name piwall-redis-dev -p 6379:6379 redis:8-alpine
cd piwall && PYTHONPATH=. python -m pytest tests/state/test_redis_client.py -v
```
Expected: PASS (6 tests)

- [ ] **Step 5: Confirm the suite still skips cleanly with no Redis**

```bash
docker stop piwall-redis-dev
cd piwall && PYTHONPATH=. python -m pytest tests/state -q
docker start piwall-redis-dev
```
Expected: 6 skipped, no errors. If they error, the reachability gate is wrong — fix it before moving on.

- [ ] **Step 6: Commit**

```bash
git add piwall/backend/state/ piwall/tests/state/
git commit -m "feat: add the single Redis connection factory and a reachability probe"
```

---

### Task 2: Structured logging

There is no logging configuration in `backend/main.py` at all — no `basicConfig`, no formatter. Two replicas and a worker interleaving plain `print` output is unreadable, and correlating a match across processes is impossible without a shared id in every line.

**Files:**
- Create: `piwall/backend/observability/__init__.py`
- Create: `piwall/backend/observability/logging.py`
- Create: `piwall/tests/observability/__init__.py`
- Test: `piwall/tests/observability/test_logging.py`

**Interfaces:**
- Produces: `configure_logging(service: str) -> None`, `bind_match(match_id: str) -> None`, `clear_match() -> None`, `get_logger(name: str) -> logging.Logger`

- [ ] **Step 1: Write the failing test**

```python
# piwall/tests/observability/test_logging.py
"""Logs must be machine-readable and carry a match id across processes.

With two API replicas and a worker, the question asked of logs is always "what
happened to match X" — and that cannot be answered by grepping interleaved
plain text from three processes.
"""

import json
import logging

from backend.observability.logging import (
    bind_match,
    clear_match,
    configure_logging,
    get_logger,
)


def _emit(caplog, fn):
    """Run fn and return the single formatted record as a dict."""
    configure_logging("test")
    logger = get_logger("piwall.test")
    handler = logger.handlers[0] if logger.handlers else logging.getLogger().handlers[0]
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(handler.format(record))

    root = logging.getLogger()
    cap = Capture()
    root.addHandler(cap)
    try:
        fn(logger)
    finally:
        root.removeHandler(cap)
    assert records, "nothing was logged"
    return json.loads(records[-1])


def test_records_are_json(caplog):
    payload = _emit(caplog, lambda log: log.info("hello"))
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"


def test_records_name_the_service(caplog):
    payload = _emit(caplog, lambda log: log.info("hello"))
    assert payload["service"] == "test"


def test_records_carry_a_timestamp(caplog):
    payload = _emit(caplog, lambda log: log.info("hello"))
    assert payload["timestamp"]


def test_a_bound_match_id_appears_on_every_record(caplog):
    def emit(log):
        bind_match("m_abc")
        log.info("running")

    try:
        payload = _emit(caplog, emit)
        assert payload["match_id"] == "m_abc"
    finally:
        clear_match()


def test_match_id_is_absent_when_nothing_is_bound(caplog):
    clear_match()
    payload = _emit(caplog, lambda log: log.info("no match here"))
    assert "match_id" not in payload


def test_clear_match_removes_the_binding(caplog):
    bind_match("m_abc")
    clear_match()
    payload = _emit(caplog, lambda log: log.info("after clear"))
    assert "match_id" not in payload


def test_exceptions_are_serialized_not_dropped(caplog):
    def emit(log):
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("failed")

    payload = _emit(caplog, emit)
    assert "ValueError" in payload["exception"]
    assert "boom" in payload["exception"]


def test_configure_logging_is_idempotent(caplog):
    """Called once per process, but a re-import must not double every line."""
    configure_logging("test")
    configure_logging("test")
    root = logging.getLogger()
    json_handlers = [h for h in root.handlers if h.formatter.__class__.__name__ == "JsonFormatter"]
    assert len(json_handlers) == 1
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/observability/test_logging.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.observability'`

- [ ] **Step 3: Implement it**

```python
# piwall/backend/observability/logging.py
"""JSON logs with a match id that follows a match across processes.

A match is now created by an API replica, executed by a worker, and streamed
by (possibly) a different API replica. The only question anyone asks of these
logs is "what happened to match X", and answering it requires the id on every
line rather than in a heading someone has to scroll back to find.
"""

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

_match_id: contextvars.ContextVar[str] = contextvars.ContextVar("match_id", default="")

# A ContextVar rather than a thread-local: the API is async, so one thread
# serves many concurrent requests and a thread-local would leak one match's id
# onto another's log lines.


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        match_id = _match_id.get()
        if match_id:
            payload["match_id"] = match_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(service: str) -> None:
    """Install the JSON formatter on the root logger, once.

    Idempotent because a module re-import would otherwise add a second handler
    and every line would appear twice — which looks like a retry loop in logs.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler.formatter, JsonFormatter):
            handler.formatter.service = service
            return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def bind_match(match_id: str) -> None:
    _match_id.set(match_id)


def clear_match() -> None:
    _match_id.set("")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
```

```python
# piwall/backend/observability/__init__.py
"""Logging and health surfaces."""
```

```python
# piwall/tests/observability/__init__.py
```

- [ ] **Step 4: Run the tests**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/observability/test_logging.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add piwall/backend/observability/ piwall/tests/observability/
git commit -m "feat: emit structured JSON logs carrying a match id across processes"
```

---

### Task 3: Health and readiness endpoints

`backend/main.py` has no health endpoint. A load balancer in front of two replicas needs one that does not touch a database (or a database blip restarts healthy containers), and an orchestrator needs a separate readiness signal that does.

**Files:**
- Create: `piwall/backend/observability/health.py`
- Modify: `piwall/backend/main.py` (register the router)
- Test: `piwall/tests/observability/test_health.py`

**Interfaces:**
- Consumes: `redis_is_reachable` (Task 1)
- Produces: `health_router: fastapi.APIRouter` exposing `GET /health` and `GET /ready`; `check_dependencies() -> dict`

- [ ] **Step 1: Write the failing test**

```python
# piwall/tests/observability/test_health.py
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
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/observability/test_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.observability.health'`

- [ ] **Step 3: Implement it**

```python
# piwall/backend/observability/health.py
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
    """Ping Mongo without importing main and its whole dependency graph."""
    try:
        from ..db.models import create_db_engine
        from ..main import DB_URL

        engine = create_db_engine(DB_URL)
        engine.admin.command("ping")
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
```

- [ ] **Step 4: Register the router in `main.py`**

Immediately after the `app = FastAPI(...)` line, add:

```python
from .observability.health import health_router
from .observability.logging import configure_logging

configure_logging("api")
app.include_router(health_router)
```

- [ ] **Step 5: Run the tests**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/observability -v`
Expected: PASS (15 tests across both files)

- [ ] **Step 6: Confirm the endpoints exist on the real app**

```bash
cd piwall && PYTHONPATH=. python -c "
from backend.main import app
paths = {r.path for r in app.routes}
assert '/health' in paths, 'health not registered'
assert '/ready' in paths, 'ready not registered'
print('both registered')"
```
Expected: `both registered`

- [ ] **Step 7: Commit**

```bash
git add piwall/backend/observability/health.py piwall/backend/main.py \
        piwall/tests/observability/test_health.py
git commit -m "feat: separate liveness from readiness so a database blip cannot restart healthy replicas"
```

---

### Task 4: Redis-backed lobby store

`active_lobbies: Dict[str, RaceLobby]` (`backend/main.py:69`) is a module-global with ten reference sites. With two replicas, a lobby created on replica A is invisible to replica B: a player joining through the load balancer half the time gets "Race not found".

**The one thing that must NOT move to Redis** is `RaceLobby.websockets: Set[WebSocket]`. Socket objects are per-process by nature. That set stays local and becomes this replica's subscriber registry; everything else about a lobby becomes a Redis hash.

**Files:**
- Create: `piwall/backend/state/lobby.py`
- Test: `piwall/tests/state/test_lobby.py`

**Interfaces:**
- Consumes: `get_redis` (Task 1)
- Produces: `LobbyStore` with `create(race_id, track, race_type) -> dict`, `get(race_id) -> Optional[dict]`, `set_status(race_id, status) -> None`, `add_player(race_id, player_id, data) -> None`, `players(race_id) -> dict`, `set_speed(race_id, speed) -> None`, `list_open() -> list[dict]`, `delete(race_id) -> None`, `KEY_PREFIX = "piwall:lobby:"`

- [ ] **Step 1: Write the failing test**

```python
# piwall/tests/state/test_lobby.py
"""Lobby state has to be visible to a second API replica.

active_lobbies was a module-global dict, so a lobby created on replica A did
not exist on replica B and a player routed there got "Race not found" — half
the time, depending on the load balancer.

Two separate LobbyStore instances stand in for two replicas throughout: if a
test only ever uses one instance it proves nothing about sharing.
"""

import pytest

from backend.state.lobby import LobbyStore
from backend.state.redis_client import redis_is_reachable

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)

RACE = "r_test_lobby"


@pytest.fixture
def replica_a():
    store = LobbyStore()
    store.delete(RACE)
    yield store
    store.delete(RACE)


@pytest.fixture
def replica_b():
    """A second instance, standing in for the other API replica."""
    return LobbyStore()


def test_a_lobby_created_on_one_replica_is_visible_on_the_other(replica_a, replica_b):
    replica_a.create(RACE, track="bahrain", race_type="quick")
    assert replica_b.get(RACE) is not None


def test_an_absent_lobby_reads_as_none(replica_a):
    assert replica_a.get("r_does_not_exist") is None


def test_created_fields_round_trip(replica_a):
    replica_a.create(RACE, track="monaco", race_type="ranked")
    lobby = replica_a.get(RACE)
    assert lobby["race_id"] == RACE
    assert lobby["track"] == "monaco"
    assert lobby["race_type"] == "ranked"
    assert lobby["status"] == "lobby"


def test_status_changes_are_seen_by_the_other_replica(replica_a, replica_b):
    replica_a.create(RACE, track="bahrain")
    replica_a.set_status(RACE, "running")
    assert replica_b.get(RACE)["status"] == "running"


def test_players_added_on_one_replica_are_seen_on_the_other(replica_a, replica_b):
    replica_a.create(RACE, track="bahrain")
    replica_a.add_player(RACE, "p1", {"username": "alex", "car_id": "VEL-01"})
    assert replica_b.players(RACE)["p1"]["username"] == "alex"


def test_players_is_empty_for_a_new_lobby(replica_a):
    replica_a.create(RACE, track="bahrain")
    assert replica_a.players(RACE) == {}


def test_speed_round_trips_as_a_float(replica_a, replica_b):
    """Redis stores strings; a speed read back as '5.0' breaks arithmetic."""
    replica_a.create(RACE, track="bahrain")
    replica_a.set_speed(RACE, 5.0)
    assert replica_b.get(RACE)["speed"] == 5.0
    assert isinstance(replica_b.get(RACE)["speed"], float)


def test_list_open_includes_a_waiting_lobby(replica_a):
    replica_a.create(RACE, track="bahrain")
    assert any(l["race_id"] == RACE for l in replica_a.list_open())


def test_list_open_excludes_a_finished_lobby(replica_a):
    replica_a.create(RACE, track="bahrain")
    replica_a.set_status(RACE, "finished")
    assert not any(l["race_id"] == RACE for l in replica_a.list_open())


def test_delete_removes_the_lobby_for_both_replicas(replica_a, replica_b):
    replica_a.create(RACE, track="bahrain")
    replica_a.delete(RACE)
    assert replica_b.get(RACE) is None


def test_add_player_to_a_missing_lobby_raises(replica_a):
    with pytest.raises(KeyError):
        replica_a.add_player("r_nope", "p1", {"username": "x"})
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/state/test_lobby.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.state.lobby'`

- [ ] **Step 3: Implement it**

```python
# piwall/backend/state/lobby.py
"""Lobby state in Redis, so every API replica sees the same lobby.

This replaces `active_lobbies`, a module-global dict. With two replicas that
dict meant a lobby existed on whichever process happened to serve the create
call and nowhere else, so a join routed to the other replica returned "Race
not found".

What deliberately does NOT live here: the set of WebSocket connections. A
socket is a live object owned by one process and cannot be shared through
Redis. Each replica keeps its own set and learns when to use it from the
pub/sub events in backend/jobs/events.py.

Fields are stored as a JSON document under one key rather than as a Redis hash
of loose fields, because a lobby is read as a whole and a hash would invite
partial reads that see a half-updated lobby.
"""

import json
from typing import Optional

from .redis_client import get_redis

KEY_PREFIX = "piwall:lobby:"
OPEN_STATUSES = ("lobby", "countdown", "running")

# Lobbies expire so an abandoned one does not occupy Redis forever. Six hours
# is far longer than any match and short enough that a leak is self-healing.
TTL_SECONDS = 6 * 60 * 60


class LobbyStore:
    """Reads and writes lobby documents. Safe to instantiate per request."""

    def __init__(self, redis_client=None):
        self._redis = redis_client or get_redis()

    def _key(self, race_id: str) -> str:
        return f"{KEY_PREFIX}{race_id}"

    def create(self, race_id: str, track: str, race_type: str = "quick") -> dict:
        lobby = {
            "race_id": race_id,
            "track": track,
            "race_type": race_type,
            "status": "lobby",
            "speed": 1.0,
            "players": {},
        }
        self._write(lobby)
        return lobby

    def get(self, race_id: str) -> Optional[dict]:
        raw = self._redis.get(self._key(race_id))
        return json.loads(raw) if raw else None

    def _write(self, lobby: dict) -> None:
        self._redis.set(
            self._key(lobby["race_id"]), json.dumps(lobby), ex=TTL_SECONDS
        )

    def _require(self, race_id: str) -> dict:
        lobby = self.get(race_id)
        if lobby is None:
            raise KeyError(f"no lobby {race_id!r}")
        return lobby

    def set_status(self, race_id: str, status: str) -> None:
        lobby = self._require(race_id)
        lobby["status"] = status
        self._write(lobby)

    def set_speed(self, race_id: str, speed: float) -> None:
        lobby = self._require(race_id)
        lobby["speed"] = float(speed)
        self._write(lobby)

    def add_player(self, race_id: str, player_id: str, data: dict) -> None:
        lobby = self._require(race_id)
        lobby["players"][player_id] = data
        self._write(lobby)

    def players(self, race_id: str) -> dict:
        return self._require(race_id)["players"]

    def list_open(self) -> list:
        """Every lobby not yet finished or aborted.

        SCAN rather than KEYS: KEYS blocks Redis for the whole keyspace, which
        is fine with three lobbies and an outage with thirty thousand.
        """
        out = []
        for key in self._redis.scan_iter(match=f"{KEY_PREFIX}*", count=100):
            raw = self._redis.get(key)
            if not raw:
                continue
            lobby = json.loads(raw)
            if lobby.get("status") in OPEN_STATUSES:
                out.append(lobby)
        return sorted(out, key=lambda l: l["race_id"])

    def delete(self, race_id: str) -> None:
        self._redis.delete(self._key(race_id))
```

- [ ] **Step 4: Run the tests**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/state/test_lobby.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add piwall/backend/state/lobby.py piwall/tests/state/test_lobby.py
git commit -m "feat: keep lobby state in Redis so every replica sees the same lobby"
```

---

### Task 5: The match job queue

A worker restart mid-match must lose no match. That rules out a plain Redis list with `LPOP`, which deletes the job before the work is done: kill the worker after the pop and the match is gone with no trace. Redis Streams with a consumer group give at-least-once delivery, an explicit ack, and a way to find work abandoned by a dead consumer.

**At-least-once means a match may run twice, and that is safe here specifically because of Phase 1.** The same manifest produces a byte-identical replay, so a duplicate execution writes the same result under the same `match_id`. Determinism is what makes the cheap queue semantics correct.

**Files:**
- Create: `piwall/backend/jobs/__init__.py`
- Create: `piwall/backend/jobs/queue.py`
- Create: `piwall/tests/jobs/__init__.py`
- Test: `piwall/tests/jobs/test_queue.py`

**Interfaces:**
- Consumes: `get_redis` (Task 1)
- Produces: `MatchJobQueue` with `enqueue(job: dict) -> str`, `claim(consumer: str, block_ms: int = 2000) -> Optional[tuple[str, dict]]`, `ack(entry_id: str) -> None`, `reclaim_stalled(consumer: str, min_idle_ms: int = 30000) -> list[tuple[str, dict]]`, `pending_count() -> int`, `depth() -> int`; `STREAM = "piwall:jobs:match"`, `GROUP = "match-workers"`

- [ ] **Step 1: Write the failing test**

```python
# piwall/tests/jobs/test_queue.py
"""The queue must not lose a job when a worker dies holding it.

A Redis list with LPOP deletes the job at the moment it is handed out, so a
worker killed one instruction later takes the match with it and nothing
anywhere records that a match was lost. A Stream consumer group keeps the
entry in a pending list until it is acked, which is what makes
reclaim_stalled possible.
"""

import pytest

from backend.jobs.queue import GROUP, STREAM, MatchJobQueue
from backend.state.redis_client import get_redis, redis_is_reachable

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)

JOB = {"match_id": "m_test_1", "track": "bahrain", "seed": "1000"}


@pytest.fixture
def queue():
    client = get_redis()
    client.delete(STREAM)
    q = MatchJobQueue()
    yield q
    client.delete(STREAM)


def test_enqueue_returns_an_entry_id(queue):
    assert queue.enqueue(JOB)


def test_a_claimed_job_round_trips_its_payload(queue):
    queue.enqueue(JOB)
    entry_id, job = queue.claim("worker-1", block_ms=500)
    assert job["match_id"] == "m_test_1"
    assert job["track"] == "bahrain"


def test_claim_returns_none_on_an_empty_queue(queue):
    assert queue.claim("worker-1", block_ms=200) is None


def test_a_claimed_job_is_not_handed_to_a_second_worker(queue):
    queue.enqueue(JOB)
    queue.claim("worker-1", block_ms=500)
    assert queue.claim("worker-2", block_ms=200) is None


def test_an_unacked_job_stays_pending(queue):
    """This is the property that makes a lost worker recoverable."""
    queue.enqueue(JOB)
    queue.claim("worker-1", block_ms=500)
    assert queue.pending_count() == 1


def test_acking_clears_the_pending_entry(queue):
    queue.enqueue(JOB)
    entry_id, _ = queue.claim("worker-1", block_ms=500)
    queue.ack(entry_id)
    assert queue.pending_count() == 0


def test_a_job_abandoned_by_a_dead_worker_can_be_reclaimed(queue):
    """The phase gate in miniature: worker-1 dies holding the job."""
    queue.enqueue(JOB)
    queue.claim("worker-1", block_ms=500)  # worker-1 now dies, never acks
    reclaimed = queue.reclaim_stalled("worker-2", min_idle_ms=0)
    assert len(reclaimed) == 1
    assert reclaimed[0][1]["match_id"] == "m_test_1"


def test_reclaim_returns_nothing_when_no_job_is_stalled(queue):
    queue.enqueue(JOB)
    entry_id, _ = queue.claim("worker-1", block_ms=500)
    queue.ack(entry_id)
    assert queue.reclaim_stalled("worker-2", min_idle_ms=0) == []


def test_reclaim_respects_the_idle_threshold(queue):
    """A job claimed a moment ago is being worked on, not abandoned."""
    queue.enqueue(JOB)
    queue.claim("worker-1", block_ms=500)
    assert queue.reclaim_stalled("worker-2", min_idle_ms=60000) == []


def test_a_reclaimed_job_can_be_acked_by_its_new_owner(queue):
    queue.enqueue(JOB)
    queue.claim("worker-1", block_ms=500)
    reclaimed = queue.reclaim_stalled("worker-2", min_idle_ms=0)
    queue.ack(reclaimed[0][0])
    assert queue.pending_count() == 0


def test_depth_counts_enqueued_jobs(queue):
    queue.enqueue(JOB)
    queue.enqueue({**JOB, "match_id": "m_test_2"})
    assert queue.depth() == 2


def test_jobs_are_delivered_in_enqueue_order(queue):
    queue.enqueue({**JOB, "match_id": "m_first"})
    queue.enqueue({**JOB, "match_id": "m_second"})
    first = queue.claim("worker-1", block_ms=500)[1]["match_id"]
    second = queue.claim("worker-1", block_ms=500)[1]["match_id"]
    assert (first, second) == ("m_first", "m_second")


def test_creating_the_group_twice_is_harmless(queue):
    """Every worker calls this on startup; only the first one creates it."""
    MatchJobQueue()
    MatchJobQueue()
    assert get_redis().xinfo_groups(STREAM)[0]["name"] == GROUP
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/jobs/test_queue.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.jobs'`

- [ ] **Step 3: Implement it**

```python
# piwall/backend/jobs/queue.py
"""The match job queue, on a Redis Stream with a consumer group.

Why not a list. `LPOP` removes the job at the instant it is handed to a
worker, so a worker killed immediately afterwards takes the match with it and
nothing records that a match existed. The phase gate for this work is "a
worker restart mid-match loses no match", which a list cannot satisfy.

A consumer group keeps every delivered entry in a per-consumer pending list
until `XACK`. A worker that dies leaves its entry there, and `XAUTOCLAIM`
hands it to a live worker after an idle threshold.

Delivery is therefore at-least-once: a match can run twice. That is safe here
because of Phase 1, not by luck — the same manifest produces a byte-identical
replay, so a second execution writes the same bytes under the same match id.
Determinism is what lets this queue be simple.
"""

import json
from typing import Optional, Tuple

from redis.exceptions import ResponseError

from ..state.redis_client import get_redis

STREAM = "piwall:jobs:match"
GROUP = "match-workers"


class MatchJobQueue:
    def __init__(self, redis_client=None):
        self._redis = redis_client or get_redis()
        self._ensure_group()

    def _ensure_group(self) -> None:
        """Create the group, tolerating the race between two workers starting.

        mkstream=True so the group can be created before any job exists;
        otherwise the first worker to start crashes on a missing stream.
        """
        try:
            self._redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def enqueue(self, job: dict) -> str:
        """Add a job. Values are JSON-encoded because streams store strings."""
        return self._redis.xadd(STREAM, {"payload": json.dumps(job)})

    def claim(self, consumer: str, block_ms: int = 2000) -> Optional[Tuple[str, dict]]:
        """Take the next undelivered job, blocking briefly.

        ">" means "entries never delivered to this group". Blocking rather than
        polling keeps an idle worker from spinning on Redis.
        """
        response = self._redis.xreadgroup(
            GROUP, consumer, {STREAM: ">"}, count=1, block=block_ms
        )
        if not response:
            return None
        _stream, entries = response[0]
        if not entries:
            return None
        entry_id, fields = entries[0]
        return entry_id, json.loads(fields["payload"])

    def ack(self, entry_id: str) -> None:
        """Mark a job done. Until this runs, the job is recoverable."""
        self._redis.xack(STREAM, GROUP, entry_id)

    def reclaim_stalled(self, consumer: str, min_idle_ms: int = 30000):
        """Take over jobs whose consumer has been silent too long.

        The idle threshold is the whole safety margin: too low and a live
        worker's job gets stolen mid-match, too high and a crashed worker's
        match waits. 30s default is well above a match's runtime.
        """
        _next, entries, _deleted = self._redis.xautoclaim(
            STREAM, GROUP, consumer, min_idle_time=min_idle_ms, count=10
        )
        return [(eid, json.loads(f["payload"])) for eid, f in entries]

    def pending_count(self) -> int:
        """Jobs delivered but not yet acked — i.e. in flight or abandoned."""
        return int(self._redis.xpending(STREAM, GROUP)["pending"])

    def depth(self) -> int:
        return int(self._redis.xlen(STREAM))
```

```python
# piwall/backend/jobs/__init__.py
"""The match job queue and its lifecycle events."""
```

```python
# piwall/tests/jobs/__init__.py
```

- [ ] **Step 4: Run the tests**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/jobs/test_queue.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add piwall/backend/jobs/ piwall/tests/jobs/
git commit -m "feat: queue match jobs on a Redis stream so a dead worker's match can be reclaimed"
```

---

### Task 6: Match lifecycle events

The worker runs the match; an API replica holding the client's WebSocket must learn it finished. That replica may not be the one that enqueued it, so the notification has to go through Redis pub/sub rather than a return value.

**Files:**
- Create: `piwall/backend/jobs/events.py`
- Test: `piwall/tests/jobs/test_events.py`

**Interfaces:**
- Consumes: `get_redis` (Task 1)
- Produces: `MatchEvents` with `publish(event: dict) -> int`, `subscribe() -> redis.client.PubSub`, `listen(pubsub, timeout: float = 1.0) -> Optional[dict]`; `CHANNEL = "piwall:events:match"`

- [ ] **Step 1: Write the failing test**

```python
# piwall/tests/jobs/test_events.py
"""The replica holding a client's socket is not necessarily the one that
enqueued the match, and it is never the process that ran it.

So "this match finished" has to travel between processes. Pub/sub is the right
shape because delivery is to every subscriber (each replica has its own
sockets to serve) rather than to one consumer.
"""

import pytest

from backend.jobs.events import CHANNEL, MatchEvents
from backend.state.redis_client import redis_is_reachable

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)


@pytest.fixture
def events():
    return MatchEvents()


def test_a_subscriber_receives_a_published_event(events):
    pubsub = events.subscribe()
    try:
        events.publish({"type": "match_finished", "match_id": "m_1"})
        received = events.listen(pubsub, timeout=2.0)
        assert received == {"type": "match_finished", "match_id": "m_1"}
    finally:
        pubsub.close()


def test_listen_returns_none_when_nothing_is_published(events):
    pubsub = events.subscribe()
    try:
        assert events.listen(pubsub, timeout=0.3) is None
    finally:
        pubsub.close()


def test_every_subscriber_receives_the_same_event(events):
    """Both replicas must learn a match finished, not just one of them."""
    replica_a = events.subscribe()
    replica_b = events.subscribe()
    try:
        events.publish({"type": "match_finished", "match_id": "m_2"})
        assert events.listen(replica_a, timeout=2.0)["match_id"] == "m_2"
        assert events.listen(replica_b, timeout=2.0)["match_id"] == "m_2"
    finally:
        replica_a.close()
        replica_b.close()


def test_publish_reports_how_many_subscribers_received_it(events):
    pubsub = events.subscribe()
    try:
        assert events.publish({"type": "ping"}) >= 1
    finally:
        pubsub.close()


def test_publishing_with_no_subscribers_is_not_an_error(events):
    """A match can finish while no client is watching."""
    assert events.publish({"type": "match_finished", "match_id": "m_3"}) == 0


def test_events_round_trip_nested_payloads(events):
    pubsub = events.subscribe()
    try:
        payload = {"type": "match_finished", "match_id": "m_4",
                   "standings": [{"car_id": "VEL-01", "position": 1}]}
        events.publish(payload)
        assert events.listen(pubsub, timeout=2.0) == payload
    finally:
        pubsub.close()


def test_the_channel_name_is_stable(events):
    """Workers and replicas are deployed separately; a rename splits them."""
    assert CHANNEL == "piwall:events:match"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/jobs/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.jobs.events'`

- [ ] **Step 3: Implement it**

```python
# piwall/backend/jobs/events.py
"""Match lifecycle notifications, over Redis pub/sub.

A worker finishes a match. The client watching it holds a WebSocket on some
API replica — possibly not the one that enqueued the job, and never the worker
itself. Pub/sub fits because the message must reach EVERY replica: each one
has its own socket set and only it can write to those sockets.

This is deliberately not a queue. A queue would deliver the notification to
one subscriber, and the wrong replica would receive it.

Pub/sub is fire-and-forget: a replica that is down misses the event. That is
acceptable because the replay is already persisted before publishing, so a
client can always fetch the finished match over HTTP. The event is an
optimisation for live viewers, not the source of truth.
"""

import json
from typing import Optional

from ..state.redis_client import get_redis

CHANNEL = "piwall:events:match"


class MatchEvents:
    def __init__(self, redis_client=None):
        self._redis = redis_client or get_redis()

    def publish(self, event: dict) -> int:
        """Publish one event. Returns the number of subscribers that got it."""
        return int(self._redis.publish(CHANNEL, json.dumps(event)))

    def subscribe(self):
        """Subscribe to the channel. Caller owns closing the returned pubsub."""
        pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(CHANNEL)
        return pubsub

    def listen(self, pubsub, timeout: float = 1.0) -> Optional[dict]:
        """Next event, or None if none arrives within the timeout."""
        message = pubsub.get_message(timeout=timeout)
        if not message or message.get("type") != "message":
            return None
        return json.loads(message["data"])
```

- [ ] **Step 4: Run the tests**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/jobs/test_events.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add piwall/backend/jobs/events.py piwall/tests/jobs/test_events.py
git commit -m "feat: publish match lifecycle events so any replica can serve a finished match"
```

---

### Task 7: The worker process

The worker is the process that takes a match off the queue, runs it through the existing isolated runner, persists the replay and result, and publishes the completion event. It must ack only after persistence, or a crash between running and saving loses the match.

**Files:**
- Create: `piwall/backend/worker.py`
- Test: `piwall/tests/jobs/test_worker.py`

**Interfaces:**
- Consumes: `MatchJobQueue` (Task 5), `MatchEvents` (Task 6), `configure_logging`/`bind_match` (Task 2), `run_match_isolated` (existing, `backend/sandbox/match_job.py`)
- Produces: `process_one(queue, events, persist, consumer) -> Optional[str]`, `run_forever(consumer: str) -> None`, `WORKER_NAME: str`

- [ ] **Step 1: Write the failing test**

```python
# piwall/tests/jobs/test_worker.py
"""The worker must ack only after the result is durable.

Acking first is the tempting order — the job is "done", after all — but a
crash between ack and save loses the match with no record. Acking last means
the worst case is running the match twice, which Phase 1's determinism makes
harmless: the same manifest yields byte-identical output.
"""

import pytest

from backend.jobs.queue import STREAM, MatchJobQueue
from backend.jobs.events import MatchEvents
from backend.state.redis_client import get_redis, redis_is_reachable
from backend.worker import process_one

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)

PLAYER_CODE = (
    "def my_strategy(state, my_car):\n"
    "    return {'pit': my_car.tyre_age > 20, 'compound': 'MEDIUM'}\n"
)

# A player bot in slot 0 on purpose. A job of house bots only would pass even
# if the worker used replay_from_manifest, which cannot run player code at all
# — so an all-house-bot fixture would hide the defect this test exists to catch.
JOB = {"match_id": "m_worker_1", "track": "bahrain", "seed": 1000,
       "participants": [
           {"slot": 0, "player_id": "p1", "car_id": "USR-01",
            "code": PLAYER_CODE},
           {"slot": 1, "house_bot": "NXS-07"}]}


@pytest.fixture
def wiring():
    client = get_redis()
    client.delete(STREAM)
    queue = MatchJobQueue()
    events = MatchEvents()
    yield queue, events
    client.delete(STREAM)


def test_a_job_is_processed_and_acked(wiring):
    queue, events = wiring
    queue.enqueue(JOB)
    saved = []
    match_id = process_one(queue, events, persist=saved.append, consumer="w1")
    assert match_id == "m_worker_1"
    assert queue.pending_count() == 0


def test_the_result_is_persisted_before_the_ack(wiring):
    """Ordering is the point. Persist raising must leave the job recoverable."""
    queue, events = wiring
    queue.enqueue(JOB)

    def exploding_persist(_result):
        raise RuntimeError("database down")

    with pytest.raises(RuntimeError):
        process_one(queue, events, persist=exploding_persist, consumer="w1")
    assert queue.pending_count() == 1, "a failed save must leave the job pending"


def test_persist_receives_the_match_id_and_a_replay_hash(wiring):
    queue, events = wiring
    queue.enqueue(JOB)
    saved = []
    process_one(queue, events, persist=saved.append, consumer="w1")
    assert saved[0]["match_id"] == "m_worker_1"
    assert saved[0]["replay_sha256"].startswith("sha256:")


def test_a_completion_event_is_published(wiring):
    queue, events = wiring
    pubsub = events.subscribe()
    try:
        queue.enqueue(JOB)
        process_one(queue, events, persist=lambda r: None, consumer="w1")
        received = events.listen(pubsub, timeout=3.0)
        assert received["type"] == "match_finished"
        assert received["match_id"] == "m_worker_1"
    finally:
        pubsub.close()


def test_process_one_returns_none_on_an_empty_queue(wiring):
    queue, events = wiring
    assert process_one(queue, events, persist=lambda r: None,
                       consumer="w1", block_ms=200) is None


def test_running_the_same_job_twice_produces_the_same_replay_hash(wiring):
    """At-least-once delivery is only safe if a repeat is identical."""
    queue, events = wiring
    saved = []
    queue.enqueue(JOB)
    process_one(queue, events, persist=saved.append, consumer="w1")
    queue.enqueue(JOB)
    process_one(queue, events, persist=saved.append, consumer="w1")
    assert saved[0]["replay_sha256"] == saved[1]["replay_sha256"]


def test_a_player_bots_code_is_actually_executed(wiring):
    """The worker must run submitted source, not just house bots.

    replay_from_manifest raises NotImplementedError for a participant with no
    house_bot, so a worker built on it would fail every real match. This test
    fails loudly in that case instead of passing on a house-bot-only fixture.
    """
    queue, events = wiring
    queue.enqueue(JOB)
    saved = []
    match_id = process_one(queue, events, persist=saved.append, consumer="w1")
    assert match_id == "m_worker_1", "a match containing player code did not run"


def test_a_stalled_job_is_reclaimed_and_completed(wiring):
    """The phase gate: worker-1 dies holding a job, worker-2 finishes it."""
    queue, events = wiring
    queue.enqueue(JOB)
    queue.claim("worker-1", block_ms=500)  # dies without acking
    saved = []
    match_id = process_one(queue, events, persist=saved.append,
                           consumer="worker-2", min_idle_ms=0)
    assert match_id == "m_worker_1"
    assert queue.pending_count() == 0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/jobs/test_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.worker'`

- [ ] **Step 3: Implement it**

```python
# piwall/backend/worker.py
"""The process that runs matches.

Moving execution here is what lets the API scale: an API replica no longer
holds a match in memory for the length of a race, so a second replica is just
another stateless reader.

Ordering rule, and the reason for it: run, persist, publish, THEN ack. Acking
earlier would make a crash lose the match silently. Acking last means the
worst case is a match running twice — harmless, because Phase 1 made a
manifest produce a byte-identical replay, so the second run writes the same
bytes under the same id.
"""

import os
import signal
import socket
from typing import Callable, Optional

from .determinism.manifest import Participant, build_manifest
from .determinism.replay import replay_bytes
from .sandbox.match_job import run_match_isolated
from .jobs.events import MatchEvents
from .jobs.queue import MatchJobQueue
from .observability.logging import bind_match, clear_match, configure_logging, get_logger

log = get_logger("piwall.worker")

WORKER_NAME = f"{socket.gethostname()}-{os.getpid()}"

_shutting_down = False


def _request_shutdown(_signum, _frame) -> None:
    """Finish the job in hand, then stop. Never abandon a claimed match."""
    global _shutting_down
    _shutting_down = True


def _manifest_from_job(job: dict):
    participants = [
        Participant(
            slot=int(p["slot"]),
            player_id=p.get("player_id"),
            bot_version_id=p.get("bot_version_id"),
            code_sha256=p.get("code_sha256"),
            house_bot=p.get("house_bot"),
        )
        for p in job["participants"]
    ]
    return build_manifest(
        match_id=job["match_id"],
        seed=int(job["seed"]),
        track=job["track"],
        participants=participants,
    )


def _spec_from_job(job: dict) -> dict:
    """Build the runnable spec run_match_isolated expects.

    track_physics is built HERE, in the worker, rather than shipped in the job:
    build_track_physics reads the frozen calibration artifact from disk, and a
    TrackPhysics object in a Redis job payload would have to be serialized for
    no benefit. The worker image carries the same committed calibration as the
    API, so both produce identical physics.
    """
    from .engine.cli_runner import build_track_physics

    spec = {
        "track": job["track"],
        "track_physics": build_track_physics(job["track"]),
        "seed": int(job["seed"]),
        "cars": [],
    }
    for position, participant in enumerate(
        sorted(job["participants"], key=lambda p: int(p["slot"])), start=1
    ):
        car = {
            "car_id": participant.get("car_id") or participant.get("house_bot"),
            "player_id": participant.get("player_id") or participant.get("house_bot"),
            "start_position": position,
            "starting_compound": participant.get("starting_compound", "MEDIUM"),
        }
        if participant.get("code"):
            car["code"] = participant["code"]
        elif participant.get("house_bot"):
            car["bot_id"] = participant["house_bot"]
        spec["cars"].append(car)
    return spec


def process_one(
    queue: MatchJobQueue,
    events: MatchEvents,
    persist: Callable[[dict], None],
    consumer: str = WORKER_NAME,
    block_ms: int = 2000,
    min_idle_ms: int = 30000,
) -> Optional[str]:
    """Run at most one match. Returns its match id, or None if idle.

    Stalled jobs are checked first: a match abandoned by a dead worker has
    already made a client wait, so it goes ahead of new work.
    """
    stalled = queue.reclaim_stalled(consumer, min_idle_ms=min_idle_ms)
    if stalled:
        entry_id, job = stalled[0]
    else:
        claimed = queue.claim(consumer, block_ms=block_ms)
        if claimed is None:
            return None
        entry_id, job = claimed

    match_id = job["match_id"]
    bind_match(match_id)
    try:
        log.info("running match")
        manifest = _manifest_from_job(job)
        # run_match_isolated, NOT replay_from_manifest. The latter raises
        # NotImplementedError for any participant without a house_bot, because
        # replaying a player bot needs its source and Phase 1 deliberately left
        # that to this phase. run_match_isolated takes the source in the spec,
        # which is exactly what a real match has.
        result = run_match_isolated(_spec_from_job(job))
        replay = replay_bytes(result, manifest)
        digest = replay_sha256_of(replay)

        persist({"match_id": match_id, "replay_sha256": digest,
                 "replay_bytes": replay, "manifest": manifest})
        events.publish({"type": "match_finished", "match_id": match_id,
                        "replay_sha256": digest})
        queue.ack(entry_id)
        log.info("match complete")
        return match_id
    finally:
        clear_match()


def replay_sha256_of(replay: bytes) -> str:
    """Hash already-serialized replay bytes.

    replay_sha256() in the determinism package takes a result and a manifest;
    here the bytes are already in hand and re-running the race to hash them
    would be absurd.
    """
    import hashlib

    return "sha256:" + hashlib.sha256(replay).hexdigest()


def run_forever(consumer: str = WORKER_NAME) -> None:
    configure_logging("worker")
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    from .db.crud import save_replay_hash
    from .db.models import create_db_engine, init_db
    from .main import DB_URL

    session_factory = init_db(create_db_engine(DB_URL))

    def persist(result: dict) -> None:
        db = session_factory()
        try:
            save_replay_hash(db, result["match_id"], result["replay_sha256"])
        finally:
            db.close()

    queue, events = MatchJobQueue(), MatchEvents()
    log.info("worker ready")
    while not _shutting_down:
        try:
            process_one(queue, events, persist, consumer=consumer)
        except Exception:
            # An unacked job stays pending and is reclaimed; a crashed worker
            # loop would stop draining the queue entirely.
            log.exception("job failed, leaving it pending for reclaim")
    log.info("worker stopped")


if __name__ == "__main__":
    run_forever()
```

- [ ] **Step 4: Run the tests**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/jobs/test_worker.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Confirm determinism did not move**

Run: `cd piwall && PYTHONPATH=. python scripts/verify_determinism.py`
Expected: `ok` for all six tracks.

- [ ] **Step 6: Commit**

```bash
git add piwall/backend/worker.py piwall/tests/jobs/test_worker.py
git commit -m "feat: add the match worker, acking only once the result is durable"
```

---

### Task 8: Wire the API to Redis and the queue

This is the task that deletes `active_lobbies`. Ten call sites move to `LobbyStore`; `_run_race` splits so the API enqueues a job and streams the stored replay rather than computing the race itself.

**Files:**
- Modify: `piwall/backend/main.py` (the ten `active_lobbies` sites, `RaceLobby`, `_run_race`, the WebSocket endpoint, lifespan)
- Test: `piwall/tests/api/test_lobby_integration.py`

**Interfaces:**
- Consumes: `LobbyStore` (Task 4), `MatchJobQueue` (Task 5), `MatchEvents` (Task 6), `configure_logging` (Task 2)
- Produces: `SOCKETS: dict[str, set[WebSocket]]` (this replica's own sockets, keyed by race id)

- [ ] **Step 1: Write the failing test**

```python
# piwall/tests/api/test_lobby_integration.py
"""active_lobbies must be gone, and the socket registry must stay local.

Both halves matter. Shared state in a module-global dict breaks the second
replica; socket objects in Redis is impossible. The split is the design.
"""

import pytest

from backend.state.redis_client import redis_is_reachable

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)


def test_active_lobbies_no_longer_exists():
    """A module-global lobby dict is invisible to the other replica."""
    import backend.main as main

    assert not hasattr(main, "active_lobbies"), (
        "active_lobbies still exists; lobby state is not shared between replicas"
    )


def test_the_socket_registry_is_keyed_by_race_and_holds_sets():
    """Sockets stay per-process; only their bookkeeping is local."""
    import backend.main as main

    assert isinstance(main.SOCKETS, dict)


def test_main_uses_a_lobby_store():
    import backend.main as main
    from backend.state.lobby import LobbyStore

    assert isinstance(main.LOBBIES, LobbyStore)


def test_main_holds_a_job_queue_and_an_event_bus():
    import backend.main as main
    from backend.jobs.events import MatchEvents
    from backend.jobs.queue import MatchJobQueue

    assert isinstance(main.JOBS, MatchJobQueue)
    assert isinstance(main.EVENTS, MatchEvents)


def test_no_module_global_holds_a_race_engine():
    """An engine in the API process is the thing this phase removes."""
    import backend.main as main
    from backend.engine.race import RaceEngine

    engines = [n for n, v in vars(main).items() if isinstance(v, RaceEngine)]
    assert engines == [], f"RaceEngine instances still live in the API: {engines}"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/api/test_lobby_integration.py -v`
Expected: FAIL on `test_active_lobbies_no_longer_exists`

- [ ] **Step 3: Replace the module globals**

Delete `class RaceLobby` and `active_lobbies` (`backend/main.py:53-69`). In their place:

```python
from .jobs.events import MatchEvents
from .jobs.queue import MatchJobQueue
from .state.lobby import LobbyStore

# Lobby state lives in Redis so both API replicas see the same lobby.
LOBBIES = LobbyStore()
JOBS = MatchJobQueue()
EVENTS = MatchEvents()

# This replica's own WebSocket connections, keyed by race id. Deliberately
# NOT in Redis: a socket is a live object owned by one process. Each replica
# serves the clients attached to it and learns when to do so from EVENTS.
SOCKETS: Dict[str, Set[WebSocket]] = {}
```

- [ ] **Step 4: Convert the ten call sites**

Each `active_lobbies` site becomes a `LOBBIES` call. The mapping, from the current line numbers:

| Was | Becomes |
|---|---|
| `active_lobbies[race.id] = lobby` (`:358`) | `LOBBIES.create(race.id, track=req.track, race_type=req.race_type)` |
| `lobby = active_lobbies.get(race_id)` (`:368`, `:389`, `:415`, `:446`, `:875`, `:917`) | `lobby = LOBBIES.get(race_id)` |
| `for rid, lobby in active_lobbies.items()` (`:491`, `:838`) | `for lobby in LOBBIES.list_open()` |
| `lobby.players[pid] = {...}` | `LOBBIES.add_player(race_id, pid, {...})` |
| `lobby.status = "running"` | `LOBBIES.set_status(race_id, "running")` |
| `lobby.speed = speed` | `LOBBIES.set_speed(race_id, speed)` |
| `lobby.websockets.add(websocket)` | `SOCKETS.setdefault(race_id, set()).add(websocket)` |

A lobby is now a plain dict, so `lobby.track` becomes `lobby["track"]` throughout.

- [ ] **Step 5: Split `_run_race` into enqueue and stream**

`_run_race` currently does countdown, then `run_match_isolated`, then paces `result["lap_data"]` for display. Replace the middle: the API enqueues a job and waits for the completion event instead of running the match.

```python
async def _run_race(race_id: str):
    """Countdown, hand the match to a worker, then stream the stored replay.

    The API no longer simulates. It never did so incrementally — the previous
    code computed the whole race in one shot and then re-played lap_data for
    display pacing — so nothing about the client experience changes. What
    changes is which process holds the race while it runs.
    """
    lobby = LOBBIES.get(race_id)
    if lobby is None:
        return

    for seconds in (5, 4, 3, 2, 1):
        await _broadcast(race_id, {"type": "countdown", "seconds": seconds})
        await asyncio.sleep(1.0)
    await _broadcast(race_id, {"type": "lights_out"})

    LOBBIES.set_status(race_id, "running")
    JOBS.enqueue({
        "match_id": race_id,
        "track": lobby["track"],
        "seed": int(lobby.get("seed") or 42),
        "participants": [
            {"slot": i, "player_id": pid, "house_bot": p.get("house_bot")}
            for i, (pid, p) in enumerate(sorted(LOBBIES.players(race_id).items()))
        ],
    })
```

`_broadcast` takes a race id rather than a lobby object, and reads `SOCKETS`:

```python
async def _broadcast(race_id: str, message: dict) -> None:
    """Send to this replica's sockets for a race, dropping dead ones."""
    dead = set()
    for ws in SOCKETS.get(race_id, set()):
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)
    for ws in dead:
        SOCKETS.get(race_id, set()).discard(ws)
```

- [ ] **Step 6: Subscribe to completion events in lifespan**

In `lifespan`, after `init_db`, start a task that relays completion events to this replica's sockets, and cancel it on shutdown:

```python
    event_task = asyncio.create_task(_relay_match_events())
    yield
    event_task.cancel()
    try:
        await event_task
    except asyncio.CancelledError:
        pass
```

```python
async def _relay_match_events() -> None:
    """Stream finished matches to whichever sockets this replica holds.

    Runs on every replica. A replica with no sockets for a match does nothing,
    which is why publishing to all of them is correct rather than wasteful.
    """
    pubsub = EVENTS.subscribe()
    try:
        while True:
            event = await asyncio.get_running_loop().run_in_executor(
                None, EVENTS.listen, pubsub, 1.0
            )
            if not event or event.get("type") != "match_finished":
                continue
            race_id = event["match_id"]
            if race_id not in SOCKETS:
                continue
            await _stream_stored_replay(race_id)
    finally:
        pubsub.close()
```

- [ ] **Step 7: Run the tests**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/api tests/state tests/jobs -v`
Expected: PASS. Existing API tests must not regress.

- [ ] **Step 8: Confirm determinism and the full suite**

```bash
cd piwall && PYTHONPATH=. python scripts/verify_determinism.py
cd piwall && PYTHONPATH=. python -m pytest -q
```
Expected: six `ok`, and the whole suite green.

- [ ] **Step 9: Commit**

```bash
git add piwall/backend/main.py piwall/tests/api/test_lobby_integration.py
git commit -m "refactor: move lobby state to Redis and hand match execution to the worker"
```

---

### Task 9: Graceful shutdown

A replica killed mid-broadcast leaves clients hanging, and a worker killed mid-match must not abandon a claimed job. Both need to finish what they hold and then stop.

**Files:**
- Modify: `piwall/backend/main.py` (lifespan shutdown)
- Modify: `piwall/backend/worker.py` (already has the signal handler; add the drain assertion)
- Test: `piwall/tests/jobs/test_shutdown.py`

**Interfaces:**
- Consumes: `process_one`, `run_forever` (Task 7)
- Produces: `drain_sockets() -> int`

- [ ] **Step 1: Write the failing test**

```python
# piwall/tests/jobs/test_shutdown.py
"""Shutdown must not abandon work that has been claimed.

A worker that exits holding an unacked job is recoverable — that is what
reclaim_stalled is for — but only after an idle timeout during which a player
waits. Finishing the job in hand costs seconds and saves that wait.
"""

import pytest

from backend.state.redis_client import redis_is_reachable

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)


def test_a_shutdown_signal_stops_the_loop_rather_than_the_job():
    """SIGTERM sets a flag checked between jobs, never mid-job."""
    import backend.worker as worker

    worker._shutting_down = False
    worker._request_shutdown(15, None)
    assert worker._shutting_down is True
    worker._shutting_down = False


def test_the_worker_loop_exits_when_shutdown_is_requested(monkeypatch):
    import backend.worker as worker

    calls = []

    def fake_process_one(*a, **kw):
        calls.append(1)
        worker._request_shutdown(15, None)
        return None

    monkeypatch.setattr(worker, "process_one", fake_process_one)
    monkeypatch.setattr(worker, "configure_logging", lambda s: None)
    monkeypatch.setattr(worker, "MatchJobQueue", lambda: object())
    monkeypatch.setattr(worker, "MatchEvents", lambda: object())
    monkeypatch.setattr(worker, "init_db", lambda e: (lambda: None), raising=False)

    worker._shutting_down = False
    try:
        worker.run_forever(consumer="test")
    finally:
        worker._shutting_down = False
    assert len(calls) == 1, "the loop ran more than once after shutdown was requested"


def test_drain_sockets_closes_every_socket_and_reports_the_count():
    import asyncio

    import backend.main as main

    class FakeSocket:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    a, b = FakeSocket(), FakeSocket()
    main.SOCKETS["r_drain"] = {a, b}
    try:
        closed = asyncio.run(main.drain_sockets())
        assert closed == 2
        assert a.closed and b.closed
        assert "r_drain" not in main.SOCKETS
    finally:
        main.SOCKETS.pop("r_drain", None)


def test_drain_sockets_tolerates_a_socket_that_fails_to_close():
    """One bad socket must not strand the rest."""
    import asyncio

    import backend.main as main

    class Exploding:
        async def close(self):
            raise RuntimeError("already gone")

    class Fine:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    good = Fine()
    main.SOCKETS["r_drain2"] = {Exploding(), good}
    try:
        asyncio.run(main.drain_sockets())
        assert good.closed
    finally:
        main.SOCKETS.pop("r_drain2", None)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/jobs/test_shutdown.py -v`
Expected: FAIL — `drain_sockets` does not exist.

- [ ] **Step 3: Implement `drain_sockets` and call it on shutdown**

```python
async def drain_sockets() -> int:
    """Close every socket this replica holds. Returns how many were closed.

    Called on shutdown so clients get a clean close frame and reconnect to a
    live replica, instead of waiting for a TCP timeout on a process that is
    already gone.
    """
    closed = 0
    for race_id in list(SOCKETS.keys()):
        for ws in list(SOCKETS.pop(race_id, set())):
            try:
                await ws.close()
            except Exception:
                pass
            closed += 1
    return closed
```

In `lifespan`, after cancelling the event task:

```python
    drained = await drain_sockets()
    logger.info("closed %d websocket(s) on shutdown", drained)
```

- [ ] **Step 4: Run the tests**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/jobs/test_shutdown.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add piwall/backend/main.py piwall/backend/worker.py \
        piwall/tests/jobs/test_shutdown.py
git commit -m "feat: drain sockets and finish the job in hand before shutting down"
```

---

### Task 10: The phase gate

Two assertions, both from spec §11: two API replicas serve all traffic correctly, and a worker restart mid-match loses no match. Everything before this task is machinery; this is the proof.

**Files:**
- Create: `piwall/Dockerfile.worker`
- Modify: `piwall/docker-compose.yml` (add `redis`, add `worker`, scale `backend` to 2)
- Create: `piwall/tests/integration/__init__.py`
- Test: `piwall/tests/integration/test_phase2_gate.py`
- Modify: `.github/workflows/ci.yml` (add a Redis service to the backend job)

**Interfaces:**
- Consumes: everything from Tasks 1-9

- [ ] **Step 1: Write the gate tests**

```python
# piwall/tests/integration/test_phase2_gate.py
"""The Phase 2 gate, from spec section 11.

Two properties, both about processes rather than functions, so both are
written against real Redis with separate store instances standing in for
separate replicas.
"""

import pytest

from backend.jobs.events import MatchEvents
from backend.jobs.queue import STREAM, MatchJobQueue
from backend.state.lobby import LobbyStore
from backend.state.redis_client import get_redis, redis_is_reachable
from backend.worker import process_one

pytestmark = pytest.mark.skipif(
    not redis_is_reachable(), reason="needs a reachable Redis"
)

JOB = {"match_id": "m_gate", "track": "bahrain", "seed": 1000,
       "participants": [{"slot": 0, "house_bot": "VEL-01"},
                        {"slot": 1, "house_bot": "NXS-07"}]}


@pytest.fixture
def clean():
    client = get_redis()
    client.delete(STREAM)
    LobbyStore().delete("m_gate")
    yield
    client.delete(STREAM)
    LobbyStore().delete("m_gate")


def test_two_replicas_serve_the_same_lobby(clean):
    """Gate part 1: a lobby created on one replica is complete on the other."""
    replica_a, replica_b = LobbyStore(), LobbyStore()

    replica_a.create("m_gate", track="bahrain", race_type="quick")
    replica_a.add_player("m_gate", "p1", {"username": "alex", "car_id": "VEL-01"})
    replica_a.set_status("m_gate", "countdown")
    replica_a.set_speed("m_gate", 5.0)

    seen = replica_b.get("m_gate")
    assert seen["track"] == "bahrain"
    assert seen["status"] == "countdown"
    assert seen["speed"] == 5.0
    assert replica_b.players("m_gate")["p1"]["username"] == "alex"

    # And writes flow the other way too, or only one replica can accept joins.
    replica_b.add_player("m_gate", "p2", {"username": "sam", "car_id": "NXS-07"})
    assert set(replica_a.players("m_gate")) == {"p1", "p2"}


def test_a_worker_restart_mid_match_loses_no_match(clean):
    """Gate part 2, as a sequence rather than an assertion about config.

    worker-1 claims the job and dies. The match must still complete, and its
    result must be identical to one produced without any interruption —
    otherwise "lost no match" would be satisfied by finishing a different race.
    """
    queue, events = MatchJobQueue(), MatchEvents()

    # A clean baseline, to compare the recovered result against.
    queue.enqueue(JOB)
    baseline = []
    process_one(queue, events, persist=baseline.append, consumer="baseline")

    # Now the failure: worker-1 claims and never acks.
    queue.enqueue(JOB)
    queue.claim("worker-1", block_ms=500)
    assert queue.pending_count() == 1, "the job should be held, not consumed"

    # worker-2 takes over.
    recovered = []
    match_id = process_one(queue, events, persist=recovered.append,
                           consumer="worker-2", min_idle_ms=0)

    assert match_id == "m_gate", "the abandoned match was not recovered"
    assert queue.pending_count() == 0, "the recovered job was never acked"
    assert recovered[0]["replay_sha256"] == baseline[0]["replay_sha256"], (
        "the recovered match produced a different race"
    )


def test_nothing_is_lost_when_the_worker_dies_before_persisting(clean):
    """A crash between running and saving must leave the job recoverable."""
    queue, events = MatchJobQueue(), MatchEvents()
    queue.enqueue(JOB)

    with pytest.raises(RuntimeError):
        process_one(queue, events,
                    persist=lambda r: (_ for _ in ()).throw(RuntimeError("crash")),
                    consumer="worker-1")

    assert queue.pending_count() == 1
    saved = []
    process_one(queue, events, persist=saved.append,
                consumer="worker-2", min_idle_ms=0)
    assert saved[0]["match_id"] == "m_gate"
```

```python
# piwall/tests/integration/__init__.py
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/integration -v`
Expected: PASS if Tasks 1-9 are complete. If any fail, the failure is the finding — report it rather than adjusting the test.

- [ ] **Step 3: Add the worker image**

```dockerfile
# piwall/Dockerfile.worker
# The worker runs untrusted player code, so it is the same image as the API:
# same pinned dependencies, same frozen calibration, same PYTHONHASHSEED. A
# separate dependency set here would mean the sandbox and the determinism
# guarantees were verified against something the worker does not run.
FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY calibration/ calibration/
RUN mkdir -p fastf1_cache processed_cache

ENV PYTHONPATH=/app
ENV PYTHONHASHSEED=0

CMD ["python", "-m", "backend.worker"]
```

- [ ] **Step 4: Add Redis and the worker to compose, and run two API replicas**

In `piwall/docker-compose.yml`, add:

```yaml
  redis:
    image: redis:8-alpine
    # Not published: only the backend and worker need it, and an exposed Redis
    # with no auth is the classic way a dev stack becomes an incident.
    expose:
      - "6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 10
    restart: unless-stopped

  worker:
    build:
      context: .
      dockerfile: Dockerfile.worker
    environment:
      MONGODB_URI: mongodb://${MONGO_ROOT_USERNAME}:${MONGO_ROOT_PASSWORD}@mongodb:27017/${MONGODB_DB}?authSource=admin
      MONGODB_DB: ${MONGODB_DB}
      REDIS_URL: redis://redis:6379/0
    depends_on:
      mongodb:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped
```

On the `backend` service add `REDIS_URL: redis://redis:6379/0`, add `redis` to its `depends_on` with `condition: service_healthy`, and add:

```yaml
    deploy:
      replicas: 2
```

Then remove the `ports:` mapping from `backend` — two replicas cannot both bind host port 8000. The frontend reaches them by service name over the compose network, which load-balances across replicas.

- [ ] **Step 5: Bring the stack up and verify two replicas**

```bash
cd piwall && docker compose up -d --build
docker compose ps
docker compose exec -T redis redis-cli ping
```
Expected: two `backend` containers running, `worker` running, `redis` healthy, `PONG`.

- [ ] **Step 6: Add Redis to CI**

In `.github/workflows/ci.yml`, add to the `backend` job's `services:` block:

```yaml
      redis:
        image: redis:8-alpine
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 10
```

and add `REDIS_URL: redis://localhost:6379/0` to that job's `env:`. Then add a step asserting the Redis-dependent tests actually ran rather than skipped:

```yaml
      - name: The Redis-backed tests must have run, not skipped
        run: |
          out=$(python -m pytest tests/state tests/jobs tests/integration -q 2>&1 | tail -3)
          echo "$out"
          echo "$out" | grep -qE '[0-9]+ passed' || exit 1
          echo "$out" | grep -qE 'skipped' && {
            echo "::error::Redis-backed tests skipped in CI; Redis is not reachable"
            exit 1
          }
          true
```

- [ ] **Step 7: Confirm the whole suite on both interpreters**

```bash
cd piwall && PYTHONPATH=. python -m pytest -q
docker compose run --rm --no-deps -e REDIS_URL=redis://redis:6379/0 backend sh -c "PYTHONPATH=. python -m pytest -q"
cd piwall && PYTHONPATH=. python scripts/verify_determinism.py
```
Expected: green locally and in the 3.11 container; six `ok`.

- [ ] **Step 8: Commit**

```bash
git add piwall/Dockerfile.worker piwall/docker-compose.yml \
        piwall/tests/integration/ .github/workflows/ci.yml
git commit -m "test: prove two replicas share state and a worker restart loses no match"
```

---

## Deferred beyond this phase

- **The production sampling job** (spec §5.6) — periodically re-run recent matches from their manifests and page on a hash mismatch. The queue and worker this phase builds are its prerequisites; the job itself belongs with Phase 3's scheduling work. **When it is written, it must compare `manifest.python_version` against the running interpreter before calling a mismatch a contract break** — the operation counter measurably differs across versions (2411 ops on 3.14.7 vs 2711 on 3.11.16, from PEP 709 inlined comprehensions), so a version difference is a different question from a determinism break.
- **Player-bot replay from a manifest.** `replay_from_manifest` still raises `NotImplementedError` for a participant with no `house_bot`, because replaying player code requires resolving `code_sha256` back to its source. The worker does not need it — `run_match_isolated` takes the source directly from the job — but the sampling job and Phase 4's replay player both do. It needs a bot-source store keyed by `code_sha256` plus a resolver in `replay_from_manifest`, and it is its own reviewable piece of work rather than a line in this phase.

- **Object storage for replay bodies.** This phase persists the replay *hash* against the manifest, which is what detects divergence. Storing the bodies needs a bucket and a retention policy, and belongs with Phase 4's replay player.
- **Matchmaking** — the queue, banded pairing and bot fallback in spec §4.1 are Phase 3. This phase builds the job queue those jobs will land on.
- **Railway deployment of the worker.** The compose stack runs it; adding a third Railway service and pointing `REDIS_URL` at a managed Redis is a deployment step, not a code one.
