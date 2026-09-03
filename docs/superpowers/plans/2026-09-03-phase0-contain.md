# PIT WALL Phase 0 — Contain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PIT WALL safe to expose publicly — untrusted player code cannot escape, exhaust, or read the host, and no credential is forgeable or readable by page JavaScript.

**Architecture:** Five defensive layers replace the current single compromised one. Submission-time AST validation rejects bad code with a clear error; corrected RestrictedPython guards remove the attribute and write bypasses; the whole match moves into a `spawn`-ed subprocess where `resource.setrlimit` and signals actually work; credentials move to hashed-at-rest with a server-side proxy so the browser never holds a game key; and authorization gates the endpoints that currently have none.

**Tech Stack:** Python 3.11, FastAPI, RestrictedPython, `multiprocessing` (spawn context), `resource`, pymongo, pytest, slowapi, Next.js 14 (App Router).

**Spec:** `docs/superpowers/specs/2026-09-02-pitwall-competitive-platform-design.md` (§1.1, §6, §11)

## Global Constraints

- Python 3.11; all backend commands run from `piwall/` with `PYTHONPATH=.`.
- The default `STRATEGY_TEMPLATE` (`backend/sandbox/runner.py:211-287`) MUST keep working after every task. It is the code every new player starts from. Verified working under the Task 2–3 guards: returns `{'pit': True, 'compound': 'HARD'}` for the fixture state.
- No task may change simulation output for a fixed seed. Phase 0 is containment only; determinism work is Phase 1.
- Subprocess isolation uses the **spawn** start method, never fork — fork would inherit the parent's MongoDB connections and memory into untrusted code.
- Never log, return, or persist a raw API key after the single moment of issue.
- Every task ends green: `pytest` passes before the commit.

---

## File Structure

| Path | Responsibility | Task |
|---|---|---|
| `piwall/pytest.ini` | Test discovery + `pythonpath` | 1 |
| `piwall/tests/conftest.py` | Shared fixtures: sample race state and car dicts | 1 |
| `piwall/tests/sandbox/test_containment.py` | Escape/exhaustion corpus — the security regression suite | 2, 3, 5 |
| `piwall/tests/sandbox/test_template.py` | Guards the default template against regression | 2 |
| `piwall/tests/sandbox/test_validation.py` | Submission-time rejection rules | 4 |
| `piwall/tests/api/test_authz.py` | Ownership and role enforcement | 9 |
| `piwall/tests/db/test_credentials.py` | API key hashing | 7 |
| `backend/sandbox/runner.py` | Language-layer guards (modified) | 2, 3 |
| `backend/sandbox/validation.py` | **New.** AST validation at submission time | 4 |
| `backend/sandbox/isolation.py` | **New.** Generic `run_isolated()` subprocess+rlimit wrapper | 5 |
| `backend/engine/serialize.py` | **New.** Import-safe state serializers (moved out of `main.py`) | 6 |
| `backend/sandbox/match_job.py` | **New.** Picklable `run_match(spec)` entry point for the child | 6 |
| `backend/db/crud.py` | Key hashing, `owner_id`, `role` (modified) | 7, 9 |
| `backend/main.py` | Wire isolation, authz, rate limits (modified) | 6, 9, 10 |
| `frontend/src/app/api/game/[...path]/route.ts` | **New.** Server-side proxy holding the key | 8 |
| `piwall/docker-compose.yml` | Mongo auth, no published DB port (modified) | 11 |

---

### Task 1: Test infrastructure

The repository has zero tests. Everything downstream needs a harness first.

**Files:**
- Create: `piwall/pytest.ini`
- Create: `piwall/tests/__init__.py`, `piwall/tests/sandbox/__init__.py`
- Create: `piwall/tests/conftest.py`
- Modify: `piwall/backend/requirements.txt`

**Interfaces:**
- Consumes: nothing.
- Produces: pytest fixtures `sample_car` (dict) and `sample_state` (dict), used by every sandbox test in Tasks 2–6.

- [ ] **Step 1: Add pytest to requirements**

Append to `piwall/backend/requirements.txt`:

```
pytest>=8.0.0
```

- [ ] **Step 2: Create pytest config**

Create `piwall/pytest.ini`:

```ini
[pytest]
pythonpath = .
testpaths = tests
python_files = test_*.py
```

- [ ] **Step 3: Create the fixtures**

Create `piwall/tests/__init__.py` and `piwall/tests/sandbox/__init__.py` as empty files. Create `piwall/tests/conftest.py`:

```python
import pytest


@pytest.fixture
def sample_car():
    return {
        "car_id": "c1", "player_id": "p1", "position": 3,
        "gap_to_leader": 4.2, "compound": "MEDIUM", "tyre_age": 22,
        "fuel_kg": 40.0, "pit_count": 0, "pit_laps": [],
        "last_lap_time": 94.1, "total_time": 900.0, "retired": False,
        "drs_available": False, "compounds_used": ["MEDIUM"],
        "beliefs": {"c2": {"undercut_viable": True, "undercut_gain": 3.1}},
    }


@pytest.fixture
def sample_state(sample_car):
    return {
        "lap": 25, "total_laps": 57, "track": "bahrain", "weather": "dry",
        "safety_car": False, "safety_car_laps_left": 0,
        "track_temp": 32.0, "cars": [sample_car],
    }
```

- [ ] **Step 4: Write a test proving the harness imports backend code**

Create `piwall/tests/sandbox/test_template.py`:

```python
from backend.sandbox.runner import STRATEGY_TEMPLATE, execute_strategy


def test_default_template_returns_a_valid_decision(sample_state, sample_car):
    result = execute_strategy(STRATEGY_TEMPLATE, sample_state, sample_car)
    assert "error" not in result, result
    assert isinstance(result["pit"], bool)
    assert result["compound"] in {"SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"}
```

- [ ] **Step 5: Run the test**

Run: `cd piwall && pip install -r backend/requirements.txt && python -m pytest -v`
Expected: PASS (1 test).

- [ ] **Step 6: Commit**

```bash
git add piwall/pytest.ini piwall/tests piwall/backend/requirements.txt
git commit -m "test: add pytest harness and shared race-state fixtures"
```

---

### Task 2: Close the attribute-guard bypass

`ALLOWED_BUILTINS` exposes raw `getattr` and `hasattr` (`backend/sandbox/runner.py:57-58`). RestrictedPython rewrites `.attr` *syntax* into guarded `_getattr_` calls but does not intercept a direct call to the `getattr` *function*, so exposing it nullifies `safer_getattr` entirely.

**Files:**
- Modify: `backend/sandbox/runner.py:57-58`
- Modify: `piwall/tests/sandbox/test_containment.py` (create)

**Interfaces:**
- Consumes: `execute_strategy(code, state_dict, my_car_dict) -> dict` from Task 1's fixtures.
- Produces: no signature change. `execute_strategy` returns `{"error": str}` for code calling `getattr`/`hasattr`.

- [ ] **Step 1: Write the failing tests**

Create `piwall/tests/sandbox/test_containment.py`:

```python
from backend.sandbox.runner import execute_strategy

GETATTR = (
    "def my_strategy(state, my_car):\n"
    "    getattr(state, 'lap')\n"
    "    return {'pit': False, 'compound': 'SOFT'}\n"
)
HASATTR = (
    "def my_strategy(state, my_car):\n"
    "    hasattr(state, 'lap')\n"
    "    return {'pit': False, 'compound': 'SOFT'}\n"
)


def test_getattr_builtin_is_unreachable(sample_state, sample_car):
    result = execute_strategy(GETATTR, sample_state, sample_car)
    assert "error" in result


def test_hasattr_builtin_is_unreachable(sample_state, sample_car):
    result = execute_strategy(HASATTR, sample_state, sample_car)
    assert "error" in result
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd piwall && python -m pytest tests/sandbox/test_containment.py -v`
Expected: FAIL — both tests, because the calls currently succeed and no `error` key is returned.

- [ ] **Step 3: Remove the two builtins**

In `backend/sandbox/runner.py`, delete these two lines from `ALLOWED_BUILTINS` (currently lines 57-58):

```python
    "getattr": getattr,
    "hasattr": hasattr,
```

The dict must now end:

```python
    "isinstance": isinstance,
}
```

- [ ] **Step 4: Run the tests**

Run: `cd piwall && python -m pytest tests/sandbox/ -v`
Expected: PASS — 3 tests. `test_default_template_returns_a_valid_decision` must still pass; the template does not call `getattr`.

- [ ] **Step 5: Commit**

```bash
git add backend/sandbox/runner.py piwall/tests/sandbox/test_containment.py
git commit -m "fix(security): remove getattr/hasattr builtins that bypassed safer_getattr"
```

---

### Task 3: Restore the write and iteration guards

`_write_` is a no-op passthrough (`runner.py:128`), `_getitem_` an unguarded lambda (`:124`), and `_getiter_` the raw `iter` builtin (`:123`). Together these disable RestrictedPython's mutation and iteration protection.

**Files:**
- Modify: `backend/sandbox/runner.py:18-23` (imports), `:123-128` (guards)
- Modify: `piwall/tests/sandbox/test_containment.py`

**Interfaces:**
- Consumes: `execute_strategy` as in Task 2.
- Produces: no signature change. Attribute assignment on `state`/`my_car` now returns `{"error": ...}`.

- [ ] **Step 1: Write the failing test**

Append to `piwall/tests/sandbox/test_containment.py`:

```python
WRITE = (
    "def my_strategy(state, my_car):\n"
    "    my_car.position = 1\n"
    "    return {'pit': False, 'compound': 'SOFT'}\n"
)


def test_user_code_cannot_mutate_passed_state(sample_state, sample_car):
    result = execute_strategy(WRITE, sample_state, sample_car)
    assert "error" in result
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd piwall && python -m pytest tests/sandbox/test_containment.py::test_user_code_cannot_mutate_passed_state -v`
Expected: FAIL — the assignment currently succeeds.

- [ ] **Step 3: Import the real guards**

In `backend/sandbox/runner.py`, replace the import block at lines 20-23:

```python
from RestrictedPython.Guards import (
    full_write_guard,
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
    safer_getattr,
)
```

Also delete the unused import at line 19 (`from RestrictedPython.Eval import default_guarded_getattr`) — it is dead and misleading.

- [ ] **Step 4: Replace the guards**

In `execute_strategy`, replace lines 126-128:

```python
    restricted_globals["_unpack_sequence_"] = guarded_unpack_sequence
    restricted_globals["_iter_unpack_sequence_"] = guarded_iter_unpack_sequence
    restricted_globals["_write_"] = full_write_guard
```

- [ ] **Step 5: Run the full sandbox suite**

Run: `cd piwall && python -m pytest tests/sandbox/ -v`
Expected: PASS — 4 tests. The template test is the critical one: it exercises `my_car.beliefs.get(...)` and iteration over `state.cars`, both of which must still work. Verified: the template returns `{'pit': True, 'compound': 'HARD'}` under these guards.

- [ ] **Step 6: Commit**

```bash
git add backend/sandbox/runner.py piwall/tests/sandbox/test_containment.py
git commit -m "fix(security): restore write and iteration guards in the sandbox"
```

---

### Task 4: Reject dangerous code at submission time

With Tasks 2–3 done, forbidden names raise `NameError` at runtime — which `race.py:438-441` swallows into a silent no-op decision, so the player sees their bot doing nothing with no explanation. Rejecting at submission gives a real error message instead.

**Files:**
- Create: `backend/sandbox/validation.py`
- Create: `piwall/tests/sandbox/test_validation.py`
- Modify: `backend/main.py:236` (submit_bot), `backend/main.py:430` (test_bot)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `validate_submission(code: str) -> Optional[str]` — returns an error message, or `None` when the code is acceptable. Called by `submit_bot` and `test_bot`.

- [ ] **Step 1: Write the failing tests**

Create `piwall/tests/sandbox/test_validation.py`:

```python
from backend.sandbox.validation import MAX_SOURCE_BYTES, validate_submission


def test_accepts_the_default_template():
    from backend.sandbox.runner import STRATEGY_TEMPLATE
    assert validate_submission(STRATEGY_TEMPLATE) is None


def test_rejects_forbidden_names():
    code = "def my_strategy(state, my_car):\n    return getattr(state, 'lap')\n"
    error = validate_submission(code)
    assert error is not None and "getattr" in error


def test_rejects_oversized_source():
    code = "def my_strategy(state, my_car):\n    return {}\n" + ("# pad\n" * 200000)
    error = validate_submission(code)
    assert error is not None and "too large" in error


def test_rejects_code_without_my_strategy():
    code = "def other(state, my_car):\n    return {}\n"
    error = validate_submission(code)
    assert error is not None and "my_strategy" in error


def test_rejects_syntax_errors():
    assert validate_submission("def my_strategy(:\n") is not None


def test_max_source_bytes_is_reasonable():
    assert 10_000 <= MAX_SOURCE_BYTES <= 200_000
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd piwall && python -m pytest tests/sandbox/test_validation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.sandbox.validation'`.

- [ ] **Step 3: Implement the validator**

Create `backend/sandbox/validation.py`:

```python
"""Submission-time validation for user strategy code.

Runs before a bot is ever stored or executed. Rejection here produces a clear
error for the player; without it, forbidden names fail at runtime and are
swallowed into a silent no-op decision by the race loop.
"""

import ast
from typing import Optional

from RestrictedPython import compile_restricted

MAX_SOURCE_BYTES = 64_000

FORBIDDEN_NAMES = frozenset({
    "getattr", "hasattr", "setattr", "delattr",
    "eval", "exec", "compile", "open", "input",
    "globals", "locals", "vars", "dir",
    "__import__", "breakpoint", "memoryview",
})


def validate_submission(code: str) -> Optional[str]:
    """Return an error message, or None when the code is acceptable."""
    if len(code.encode("utf-8")) > MAX_SOURCE_BYTES:
        return f"Source is too large (limit {MAX_SOURCE_BYTES} bytes)"

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"Syntax error: {exc}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            return f"Use of '{node.id}' is not allowed in strategy code"
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return f"Access to dunder attribute '{node.attr}' is not allowed"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return "Imports are not allowed; 'math' and 'random' are provided"

    if not any(
        isinstance(node, ast.FunctionDef) and node.name == "my_strategy"
        for node in tree.body
    ):
        return "Code must define a top-level function called 'my_strategy'"

    try:
        if compile_restricted(code, filename="<user_strategy>", mode="exec") is None:
            return "Code was rejected by the sandbox compiler"
    except SyntaxError as exc:
        return f"Sandbox rejected the code: {exc}"

    return None
```

- [ ] **Step 4: Run the tests**

Run: `cd piwall && python -m pytest tests/sandbox/test_validation.py -v`
Expected: PASS — 6 tests.

- [ ] **Step 5: Wire it into both submission endpoints**

In `backend/main.py`, add to the imports near the other sandbox imports:

```python
from backend.sandbox.validation import validate_submission
```

In `submit_bot`, replace the existing `compile_strategy(req.code)` validation call at line 236 with:

```python
    error = validate_submission(req.code)
    if error:
        raise HTTPException(400, error)
```

In `test_bot`, replace the `compile_strategy(req.code)` call at line 430 with the same two-line block.

- [ ] **Step 6: Verify the whole suite still passes**

Run: `cd piwall && python -m pytest -v`
Expected: PASS — 10 tests.

- [ ] **Step 7: Commit**

```bash
git add backend/sandbox/validation.py backend/main.py piwall/tests/sandbox/test_validation.py
git commit -m "feat(security): validate strategy submissions before storing or running them"
```

---

### Task 5: Isolated subprocess execution with real resource limits

The existing timeout never arms: `signal.signal()` raises `ValueError` off the main thread and is swallowed (`runner.py:161-167`), while both execution paths run in threadpools (`main.py:805-806`, `main.py:426`). A subprocess has its own main thread, so limits work there.

Note this wrapper is deliberately generic — Phase 1 reuses it as the seam where the managed microVM sandbox replaces the local subprocess.

**Files:**
- Create: `backend/sandbox/isolation.py`
- Create: `piwall/tests/sandbox/test_isolation.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `run_isolated(fn, args=(), memory_mb=512, cpu_seconds=30, wall_seconds=60) -> Any`
  - `probe_supported_limits(memory_mb=512, cpu_seconds=30) -> dict[str, bool]` — which rlimits this platform accepts.
  - `class MatchAborted(Exception)` — raised on timeout, memory exhaustion, or child crash.
  - **Constraint:** `fn` must be a module-level (picklable) function, because the spawn context re-imports it in the child.

- [ ] **Step 1: Write the failing tests**

Create `piwall/tests/sandbox/test_isolation.py`:

```python
import pytest

from backend.sandbox.isolation import (
    MatchAborted,
    probe_supported_limits,
    run_isolated,
)


def _add(a, b):
    return a + b


def _spin_forever():
    while True:
        pass


def _eat_memory():
    blob = []
    while True:
        blob.append(bytearray(10_000_000))


def _write_a_file():
    with open("/tmp/piwall_should_not_exist", "w") as handle:
        handle.write("x" * 1000)
    return "wrote"


def test_returns_the_child_result():
    assert run_isolated(_add, (2, 3)) == 5


def test_infinite_loop_is_killed():
    with pytest.raises(MatchAborted):
        run_isolated(_spin_forever, (), cpu_seconds=1, wall_seconds=5)


def test_memory_exhaustion_is_contained():
    with pytest.raises(MatchAborted):
        run_isolated(_eat_memory, (), memory_mb=128, cpu_seconds=10, wall_seconds=20)


def test_child_cannot_write_files():
    with pytest.raises(MatchAborted):
        run_isolated(_write_a_file, (), wall_seconds=10)


def test_the_platform_limit_mechanism_is_visible():
    """Assert what this platform genuinely enforces, rather than assuming.

    RLIMIT_CPU and RLIMIT_FSIZE work everywhere we run. RLIMIT_AS works on
    Linux but is rejected by macOS, so it is reported rather than required:
    on macOS memory is bounded by the CPU and wall-clock limits instead.
    """
    supported = probe_supported_limits()
    assert supported["RLIMIT_CPU"] is True
    assert supported["RLIMIT_FSIZE"] is True
    assert "RLIMIT_AS" in supported
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd piwall && python -m pytest tests/sandbox/test_isolation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.sandbox.isolation'`.

- [ ] **Step 3: Implement the isolation wrapper**

Create `backend/sandbox/isolation.py`:

```python
"""Out-of-process execution with enforced resource limits.

The in-process signal timeout in runner.py cannot arm, because signal.signal()
only works on the main thread of the main interpreter and every call site runs
in a threadpool. A child process has its own main thread, so limits apply.

The spawn start method is mandatory: fork would inherit the parent's MongoDB
connections and memory into untrusted code.
"""

import multiprocessing as mp
import resource
from typing import Any, Callable, Tuple

DEFAULT_MEMORY_MB = 512
DEFAULT_CPU_SECONDS = 30
DEFAULT_WALL_SECONDS = 60


class MatchAborted(Exception):
    """The isolated child exceeded its limits or failed."""


def _limit_plan(memory_mb: int, cpu_seconds: int):
    return [
        ("RLIMIT_AS", memory_mb * 1024 * 1024),
        ("RLIMIT_CPU", cpu_seconds),
        ("RLIMIT_NOFILE", 64),
        ("RLIMIT_FSIZE", 0),
        ("RLIMIT_NPROC", 0),
    ]


def _apply_limits(memory_mb: int, cpu_seconds: int) -> dict:
    """Apply each limit independently; return which ones actually took effect.

    Not every rlimit exists on every platform. macOS rejects RLIMIT_AS and
    RLIMIT_DATA outright ("ValueError: current limit exceeds maximum limit"),
    so applying the set as one block would abort every match on a developer
    machine. Each limit is applied on its own and the outcome recorded, so
    callers can assert on the mechanism rather than assume it.
    """
    applied = {}
    for name, value in _limit_plan(memory_mb, cpu_seconds):
        limit = getattr(resource, name, None)
        if limit is None:
            applied[name] = False
            continue
        try:
            resource.setrlimit(limit, (value, value))
            applied[name] = True
        except (ValueError, OSError):
            applied[name] = False
    return applied


def probe_supported_limits(memory_mb: int = 512, cpu_seconds: int = 30) -> dict:
    """Report which limits this platform accepts, without running user code.

    Linux enforces RLIMIT_AS, so memory is bounded directly. macOS does not,
    and there memory exhaustion is bounded only by the CPU and wall-clock
    limits. Production runs on Linux (spec 3, managed microVM); this function
    exists so the gap is visible in tests rather than assumed away.
    """
    return _child_probe_limits(memory_mb, cpu_seconds)


def _child_probe_limits(memory_mb: int, cpu_seconds: int) -> dict:
    return run_isolated(_apply_limits, (memory_mb, cpu_seconds),
                        memory_mb=memory_mb, cpu_seconds=cpu_seconds,
                        wall_seconds=15)


def _child_entrypoint(fn, args, memory_mb, cpu_seconds, conn) -> None:
    try:
        applied = _apply_limits(memory_mb, cpu_seconds)
        if fn is _apply_limits:
            conn.send(("ok", applied))
        else:
            conn.send(("ok", fn(*args)))
    except BaseException as exc:
        conn.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


def run_isolated(
    fn: Callable[..., Any],
    args: Tuple = (),
    memory_mb: int = DEFAULT_MEMORY_MB,
    cpu_seconds: int = DEFAULT_CPU_SECONDS,
    wall_seconds: int = DEFAULT_WALL_SECONDS,
) -> Any:
    """Run fn(*args) in a limited child process and return its result.

    fn must be a module-level function: the spawn context re-imports it.
    Raises MatchAborted on timeout, limit breach, or child failure.
    """
    ctx = mp.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_child_entrypoint,
        args=(fn, args, memory_mb, cpu_seconds, sender),
    )
    process.start()
    sender.close()

    try:
        if receiver.poll(wall_seconds):
            status, payload = receiver.recv()
        else:
            status, payload = "error", f"exceeded {wall_seconds}s wall clock"
    except EOFError:
        status, payload = "error", "child died without reporting"
    finally:
        receiver.close()
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join()

    if status == "ok":
        return payload
    raise MatchAborted(payload)
```

- [ ] **Step 4: Run the tests**

Run: `cd piwall && python -m pytest tests/sandbox/test_isolation.py -v`
Expected: PASS — 5 tests. The infinite-loop test takes ~1s (killed by RLIMIT_CPU, which reaches the
parent as `EOFError` — the child dies on SIGXCPU before it can send, and `run_isolated` converts that
to `MatchAborted`). On Linux the memory test trips `RLIMIT_AS`; on macOS, where that limit is
rejected by the kernel, it trips the CPU/wall-clock limit instead — verified behaviour on
Darwin arm64 / CPython 3.14.7.

- [ ] **Step 5: Commit**

```bash
git add backend/sandbox/isolation.py piwall/tests/sandbox/test_isolation.py
git commit -m "feat(security): add subprocess isolation with enforced CPU, memory and file limits"
```

---

### Task 6: Run the match inside the isolated subprocess

`engine.run()` is called with no `lap_callback` (`main.py:805`), so the whole race is computed in one shot and only replayed afterwards for display pacing. That makes the subprocess boundary clean — nothing needs to stream across it.

Per-call isolation is not an option: user code executes once per car per lap (`race.py:437`), which would mean several hundred process spawns per race. The match is the correct unit.

**Files:**
- Create: `backend/sandbox/match_job.py`
- Create: `piwall/tests/sandbox/test_match_job.py`
- Modify: `backend/main.py:763-806`

**Interfaces:**
- Consumes: `run_isolated`, `MatchAborted` from Task 5.
- Produces: `run_match(spec: dict) -> dict` — a module-level, picklable function.
  - `spec` keys: `track` (str), `seed` (int), `cars` (list of `{car_id, player_id, code|bot_id, start_position, starting_compound}`).
  - Returns `{"standings": [...], "events": [...], "lap_data": [...], "weather_history": [...]}`.

- [ ] **Step 1: Write the failing test**

Create `piwall/tests/sandbox/test_match_job.py`:

```python
import pytest

from backend.sandbox.isolation import MatchAborted
from backend.sandbox.match_job import run_match_isolated

SPEC = {
    "track": "bahrain",
    "seed": 42,
    "cars": [
        {"car_id": "c1", "player_id": "p1", "bot_id": "VEL-01",
         "start_position": 1, "starting_compound": "MEDIUM"},
        {"car_id": "c2", "player_id": "p2", "bot_id": "NXS-07",
         "start_position": 2, "starting_compound": "MEDIUM"},
    ],
}

HANGS = (
    "def my_strategy(state, my_car):\n"
    "    while True:\n"
    "        pass\n"
)


def test_runs_a_match_and_returns_standings():
    result = run_match_isolated(SPEC)
    assert len(result["standings"]) == 2
    assert result["lap_data"]


def test_a_hanging_bot_cannot_hang_the_server():
    spec = dict(SPEC)
    spec["cars"] = [
        {"car_id": "c1", "player_id": "p1", "code": HANGS,
         "start_position": 1, "starting_compound": "MEDIUM"},
        SPEC["cars"][1],
    ]
    with pytest.raises(MatchAborted):
        run_match_isolated(spec, cpu_seconds=3, wall_seconds=10)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd piwall && python -m pytest tests/sandbox/test_match_job.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.sandbox.match_job'`.

- [ ] **Step 3: Extract the state serializers into an import-safe module**

`_race_state_to_dict` and `_car_state_to_dict` currently live in `backend/main.py:877-907`. The
isolated child must not import `backend.main` — that would construct the FastAPI app and a MongoDB
client inside the untrusted process, and creates a circular import. Move them to a module that pulls
in nothing but the engine dataclasses.

Create `backend/engine/serialize.py`:

```python
"""Plain-dict serialization of simulation state.

Lives outside main.py so the isolated match child can import it without
constructing the FastAPI application or a database connection.
"""

from backend.engine.race import CarState, RaceState


def car_state_to_dict(car: CarState) -> dict:
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


def race_state_to_dict(state: RaceState) -> dict:
    return {
        "lap": state.lap,
        "total_laps": state.total_laps,
        "track": state.track,
        "weather": state.weather,
        "safety_car": state.safety_car,
        "safety_car_laps_left": state.safety_car_laps_left,
        "track_temp": state.track_temp,
        "cars": [car_state_to_dict(c) for c in state.cars],
    }
```

Then in `backend/main.py`, delete the two function definitions at lines 877-907 and add the import
alongside the other backend imports:

```python
from backend.engine.serialize import car_state_to_dict, race_state_to_dict
```

Replace every remaining call to `_race_state_to_dict(` with `race_state_to_dict(` and
`_car_state_to_dict(` with `car_state_to_dict(` in `backend/main.py`.

- [ ] **Step 4: Verify nothing still imports the old names**

Run: `cd piwall && grep -rn "_race_state_to_dict\|_car_state_to_dict" backend/`
Expected: no matches.

- [ ] **Step 5: Implement the match job**

Create `backend/sandbox/match_job.py`:

```python
"""Picklable match entry point executed inside an isolated child process.

Engine construction lives here rather than in main.py so the child can rebuild
the whole match from a plain dict, with no reference to server state.
"""

from typing import Any, Dict

from backend.data.tracks import TRACKS
from backend.engine.bots import BUILTIN_BOTS
from backend.engine.cli_runner import build_track_physics
from backend.engine.race import Decision, RaceEngine
from backend.sandbox.isolation import (
    DEFAULT_CPU_SECONDS,
    DEFAULT_MEMORY_MB,
    DEFAULT_WALL_SECONDS,
    run_isolated,
)
from backend.engine.serialize import car_state_to_dict, race_state_to_dict
from backend.sandbox.runner import execute_strategy


def _make_user_strategy(code: str):
    def strategy(state, my_car):
        result = execute_strategy(code, race_state_to_dict(state),
                                  car_state_to_dict(my_car))
        if "error" in result:
            return Decision(pit=False, compound=my_car.compound)
        return Decision(pit=result["pit"], compound=result["compound"])

    return strategy


def run_match(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Run one full match. Module-level and picklable for the spawn context."""
    track = build_track_physics(spec["track"])
    config = TRACKS[spec["track"]]

    engine = RaceEngine(
        track=track,
        weather_transitions=config.weather_transitions,
        seed=spec["seed"],
        sc_prob_dry=config.safety_car_prob_dry,
        sc_prob_wet=config.safety_car_prob_wet,
    )

    for car in spec["cars"]:
        if car.get("code"):
            strategy = _make_user_strategy(car["code"])
        else:
            strategy = BUILTIN_BOTS[car["bot_id"]]["strategy"]
        engine.add_car(
            car["car_id"], car["player_id"], strategy,
            car["start_position"], car.get("starting_compound", "MEDIUM"),
        )

    result = engine.run()
    return {
        "standings": [
            {"car_id": c.car_id, "player_id": c.player_id,
             "position": c.position, "total_time": c.total_time,
             "retired": c.retired, "pit_count": c.pit_count}
            for c in result.final_standings
        ],
        "events": [
            {"lap": e.lap, "event_type": e.event_type,
             "car_id": e.car_id, "detail": e.detail}
            for e in result.events
        ],
        "lap_data": result.lap_data,
        "weather_history": result.weather_history,
    }


def run_match_isolated(
    spec: Dict[str, Any],
    memory_mb: int = DEFAULT_MEMORY_MB,
    cpu_seconds: int = DEFAULT_CPU_SECONDS,
    wall_seconds: int = DEFAULT_WALL_SECONDS,
) -> Dict[str, Any]:
    """Run a match in a resource-limited child process."""
    return run_isolated(
        run_match, (spec,),
        memory_mb=memory_mb, cpu_seconds=cpu_seconds, wall_seconds=wall_seconds,
    )
```

- [ ] **Step 6: Run the tests**

Run: `cd piwall && python -m pytest tests/sandbox/test_match_job.py -v`
Expected: PASS — 2 tests. The hanging-bot test proves the previously-unarmed timeout now works.

- [ ] **Step 7: Replace the inline execution in main.py**

In `backend/main.py`, add the import:

```python
from backend.sandbox.match_job import run_match_isolated
from backend.sandbox.isolation import MatchAborted
```

Replace the block at lines 763-806 (from `track = build_track_physics(lobby.track)` through the `run_in_executor` call) with:

```python
    track = build_track_physics(lobby.track)
    seed = random.randint(0, 99999)

    spec = {"track": lobby.track, "seed": seed, "cars": []}
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

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, run_match_isolated, spec)
    except MatchAborted as exc:
        lobby.status = "aborted"
        await _broadcast(lobby, {"type": "aborted", "reason": str(exc)})
        db = SessionLocal()
        try:
            crud.update_race_status(db, race_id, "aborted")
        finally:
            db.close()
        return
```

Downstream code in `_run_race` reads `result.lap_data`, `result.final_standings` and `result.events` as attributes; `run_match` returns a dict, so update those accesses to `result["lap_data"]`, `result["standings"]` and `result["events"]`, and read standings entries with `entry["car_id"]` rather than `entry.car_id`.

- [ ] **Step 8: Verify the full suite**

Run: `cd piwall && python -m pytest -v`
Expected: PASS — 17 tests.

- [ ] **Step 9: Commit**

```bash
git add backend/sandbox/match_job.py backend/engine/serialize.py backend/main.py piwall/tests/sandbox/test_match_job.py
git commit -m "feat(security): execute matches in an isolated child process"
```

---

### Task 7: Hash API keys at rest

Keys are stored and matched in plaintext (`crud.py:35`, `crud.py:44-45`) and returned plaintext (`main.py:169-174`). The codebase already hashes bot code with sha256 (`crud.py:144-154`); the primitive exists, it just is not applied to the credential.

**Files:**
- Modify: `backend/db/crud.py:20-52`
- Modify: `backend/main.py:117-125` (authenticate), `:158-178` (register)
- Create: `piwall/tests/db/__init__.py`, `piwall/tests/db/test_credentials.py`
- Create: `piwall/scripts/migrate_hash_api_keys.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `hash_api_key(raw: str) -> str` in `backend/db/crud.py`
  - `create_player(db, username, team_name) -> namespace` — now carries a transient `.api_key` (raw, for the response) while persisting only `api_key_hash`.
  - `get_player_by_api_key(db, raw_key)` — unchanged signature, hashes before lookup.

- [ ] **Step 1: Write the failing tests**

Create `piwall/tests/db/__init__.py` (empty) and `piwall/tests/db/test_credentials.py`:

```python
from backend.db.crud import hash_api_key


def test_hash_is_deterministic():
    assert hash_api_key("pw_abc") == hash_api_key("pw_abc")


def test_hash_differs_per_key():
    assert hash_api_key("pw_abc") != hash_api_key("pw_abd")


def test_hash_is_not_the_raw_key():
    raw = "pw_secret"
    assert hash_api_key(raw) != raw
    assert raw not in hash_api_key(raw)


def test_hash_is_hex_sha256():
    digest = hash_api_key("pw_abc")
    assert len(digest) == 64
    int(digest, 16)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd piwall && python -m pytest tests/db/test_credentials.py -v`
Expected: FAIL — `ImportError: cannot import name 'hash_api_key'`.

- [ ] **Step 3: Implement hashing in crud**

In `backend/db/crud.py`, add near the top (after the existing imports, which already include `hashlib` and `secrets`):

```python
def hash_api_key(raw: str) -> str:
    """Hash an API key for storage. Raw keys are never persisted."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

Replace `_player_doc` (lines 20-28) so it stores the hash rather than the key:

```python
def _player_doc(player):
    return {
        "id": player.id,
        "username": player.username,
        "api_key_hash": player.api_key_hash,
        "elo": player.elo,
        "team_name": player.team_name,
        "created_at": player.created_at,
    }
```

Replace `create_player` (lines 31-41):

```python
def create_player(db, username: str, team_name: str = "Independent"):
    raw_key = f"pw_{secrets.token_hex(24)}"
    player = to_namespace({
        "id": _id(),
        "username": username,
        "api_key_hash": hash_api_key(raw_key),
        "elo": 1200.0,
        "team_name": team_name,
        "created_at": _now(),
    })
    db.db.players.insert_one(_player_doc(player))
    player.api_key = raw_key  # transient: returned once, never persisted
    return player
```

Replace `get_player_by_api_key` (lines 44-45):

```python
def get_player_by_api_key(db, api_key: str):
    return to_namespace(
        db.db.players.find_one({"api_key_hash": hash_api_key(api_key)})
    )
```

- [ ] **Step 4: Update the index**

In `backend/db/models.py`, replace the `api_key` index (line 41):

```python
    db.players.create_index([("api_key_hash", ASCENDING)], unique=True)
```

- [ ] **Step 5: Run the tests**

Run: `cd piwall && python -m pytest tests/db/ -v`
Expected: PASS — 4 tests.

- [ ] **Step 6: Write the migration for existing plaintext keys**

Create `piwall/scripts/migrate_hash_api_keys.py`:

```python
"""One-shot migration: replace plaintext api_key with api_key_hash.

Existing keys keep working — the same raw key hashes to the stored digest.
Run once, from piwall/, with MONGODB_URI set:
    PYTHONPATH=. python scripts/migrate_hash_api_keys.py
"""

from backend.db.crud import hash_api_key
from backend.db.models import create_db_engine

if __name__ == "__main__":
    db = create_db_engine()
    migrated = 0
    for doc in db.players.find({"api_key": {"$exists": True}}):
        db.players.update_one(
            {"_id": doc["_id"]},
            {
                "$set": {"api_key_hash": hash_api_key(doc["api_key"])},
                "$unset": {"api_key": ""},
            },
        )
        migrated += 1
    print(f"migrated {migrated} player(s)")
```

- [ ] **Step 7: Remove the frontend fallback that queries the renamed field**

`frontend/src/lib/repositories.ts` has a third-tier fallback in `resolveBackendPlayer` that looks a
player up by the plaintext key:

```typescript
  if (backendApiKey) {
    const backendPlayer = await db.collection("players").findOne({ api_key: backendApiKey });
    if (backendPlayer) {
      return backendPlayer;
    }
  }
```

After this task that field no longer exists, so the branch can never match. Delete the whole `if
(backendApiKey) { ... }` block. The two fallbacks above it — by `backendPlayerId`, then by
`backendUsername` — are unaffected and remain the resolution path. Also delete the now-unused
`backendApiKey` const at the top of the function if nothing else in it references the variable.

- [ ] **Step 8: Verify the full suite and the frontend build**

Run: `cd piwall && python -m pytest -v`
Expected: PASS — 21 tests.

Run: `cd piwall/frontend && npm run build`
Expected: build succeeds (no unused-variable or type error from the deletion).

- [ ] **Step 9: Commit**

```bash
git add backend/db/crud.py backend/db/models.py piwall/tests/db piwall/scripts/migrate_hash_api_keys.py frontend/src/lib/repositories.ts
git commit -m "fix(security): hash API keys at rest and migrate existing plaintext keys"
```

---

### Task 8: Remove the game API key from the browser

`BackendPlayerSync.tsx:55` writes the key to `localStorage`, where any page script can read it, and `lib/api.ts:7-10` reads it back for every call. The Next.js server already holds these credentials in Mongo (`repositories.ts:265-296`), so it can proxy instead.

**Files:**
- Create: `frontend/src/app/api/game/[...path]/route.ts`
- Modify: `frontend/src/lib/api.ts:1-20`
- Modify: `frontend/src/components/BackendPlayerSync.tsx`
- Modify: `frontend/src/app/lobby/page.tsx` (lines 35, 50, 73, 89)
- Modify: `frontend/src/app/season/page.tsx` (lines 19, 31)
- Modify: `frontend/src/components/UserMenu.tsx` (line 24)

**Interfaces:**
- Consumes: `getPlayerProfileByUserId` from `frontend/src/lib/repositories.ts`.
- Produces: every backend call goes to `/api/game/<path>` on the same origin; the browser never sees an api_key.

- [ ] **Step 1: Create the server-side proxy**

Create `frontend/src/app/api/game/[...path]/route.ts`:

```typescript
import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getPlayerProfileByUserId } from "@/lib/repositories";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function proxy(request: Request, path: string[]) {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "You must be signed in." }, { status: 401 });
  }

  const profile = await getPlayerProfileByUserId(session.user.id);
  const apiKey = profile?.backendApiKey;
  if (!apiKey) {
    return NextResponse.json({ error: "No backend player provisioned." }, { status: 409 });
  }

  const body = request.method === "GET" || request.method === "HEAD"
    ? undefined
    : await request.text();

  const upstream = await fetch(`${API_BASE}/api/${path.join("/")}`, {
    method: request.method,
    headers: { "Content-Type": "application/json", "x-api-key": String(apiKey) },
    body,
    cache: "no-store",
  });

  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}

export async function GET(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await ctx.params).path);
}

export async function POST(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await ctx.params).path);
}
```

- [ ] **Step 2: Point the client at the proxy**

In `frontend/src/lib/api.ts`, replace the base URL and the header-building helper (lines 1-20) so requests target the same origin and carry no key:

```typescript
const BASE = "/api/game";

function headers(): HeadersInit {
  return { "Content-Type": "application/json" };
}
```

Remove every read of `localStorage.getItem("piwall_api_key")` in this file, and change each `fetch(`${API_BASE}/api/...`)` call to `fetch(`${BASE}/...`)`.

- [ ] **Step 3: Stop storing the key client-side**

In `frontend/src/components/BackendPlayerSync.tsx`, delete the `API_KEY_STORAGE` constant and every `localStorage.setItem`/`getItem`/`removeItem` call referencing it. Keep the username and session-user entries — they are not secrets. The component still POSTs to `/api/backend-player` to provision the player; it just no longer receives or stores the key.

In `frontend/src/app/api/backend-player/route.ts`, remove `apiKey` from both success responses (the object returned when stored credentials validate, and the object returned after registration), returning only `{ username }`.

**Every remaining reader of `piwall_api_key` must be migrated, not just the ones above.** Several pages
use the presence of that key as the "is this player registered?" signal. If it stops being written and
those checks are left alone, every user appears permanently unregistered. Keep `piwall_username` in
localStorage — it is not a secret — and use it as the registration marker instead.

Change each of these to read `piwall_username` rather than `piwall_api_key`:

| File | Lines | What it does |
|---|---|---|
| `frontend/src/app/lobby/page.tsx` | 35, 50, 73 | gates the registered view and request paths |
| `frontend/src/app/season/page.tsx` | 19, 31 | gates the registered view |
| `frontend/src/components/UserMenu.tsx` | 24 | clears credentials on sign-out |

In `frontend/src/app/lobby/page.tsx` around line 89, `api.register()`'s response is stored with
`localStorage.setItem("piwall_api_key", res.api_key)`. Delete that line and keep the
`piwall_username` write beside it. `UserMenu.tsx:24` should drop its `removeItem("piwall_api_key")`
call and keep removing `piwall_username`.

Line numbers are indicative — grep for `piwall_api_key` and confirm you have caught every
occurrence outside `src/app/api/` before you finish.

- [ ] **Step 4: Verify no key reaches the browser**

Run: `cd piwall/frontend && npm run build`
Expected: build succeeds.

Run: `cd piwall/frontend && grep -rn "piwall_api_key" src/ | grep -v "app/api/"`
Expected: **no matches at all** outside server-side route handlers.

Run: `cd piwall/frontend && grep -rn "api_key" src/ | grep -v "app/api/"`
Expected: no matches. (`src/app/api/**` is server-side and may legitimately reference the key.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "fix(security): proxy backend calls server-side so the browser never holds a game key"
```

---

### Task 9: Race ownership and admin roles

`start_race` (`main.py:252-271`) authenticates but never checks who owns the race — any player can start anyone's lobby. `create_season`/`end_season` (`:492-512`, `:599-609`) have no role check, so any registered player can end the active season.

**Files:**
- Modify: `backend/db/crud.py:64-84` (create_race), `:20-28` (`_player_doc`)
- Modify: `backend/main.py:179-203` (create_race), `:252-271` (start_race), `:492-512`, `:599-609`
- Create: `piwall/tests/api/__init__.py`, `piwall/tests/api/test_authz.py`

**Interfaces:**
- Consumes: `hash_api_key` from Task 7.
- Produces:
  - `crud.create_race(db, track, race_type, season_id, weather_seed, owner_id=None)` — new trailing `owner_id` parameter.
  - `require_admin(player: dict) -> None` in `backend/main.py` — raises `HTTPException(403)` for non-admins.
  - Players gain a `role` field defaulting to `"player"`.

- [ ] **Step 1: Write the failing tests**

Create `piwall/tests/api/__init__.py` (empty) and `piwall/tests/api/test_authz.py`:

```python
import pytest
from fastapi import HTTPException

from backend.main import require_admin


def test_admin_passes():
    require_admin({"id": "p1", "role": "admin"})


def test_non_admin_is_rejected():
    with pytest.raises(HTTPException) as exc:
        require_admin({"id": "p1", "role": "player"})
    assert exc.value.status_code == 403


def test_missing_role_is_rejected():
    with pytest.raises(HTTPException) as exc:
        require_admin({"id": "p1"})
    assert exc.value.status_code == 403
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd piwall && python -m pytest tests/api/test_authz.py -v`
Expected: FAIL — `ImportError: cannot import name 'require_admin'`.

- [ ] **Step 3: Add the role field**

In `backend/db/crud.py`, add `"role": getattr(player, "role", "player"),` to the dict returned by `_player_doc`, and `"role": "player",` to the dict passed to `to_namespace` in `create_player`.

- [ ] **Step 4: Implement require_admin and return role from authenticate**

In `backend/main.py`, change the return of `authenticate` (line 123) to include the role:

```python
        return {
            "id": player.id,
            "username": player.username,
            "elo": player.elo,
            "role": getattr(player, "role", "player"),
        }
```

Add below `authenticate`:

```python
def require_admin(player: dict) -> None:
    """Raise unless the authenticated player holds the admin role."""
    if player.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
```

- [ ] **Step 5: Run the tests**

Run: `cd piwall && python -m pytest tests/api/test_authz.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 6: Record and enforce race ownership**

In `backend/db/crud.py`, add `owner_id: Optional[str] = None` as the final parameter of `create_race` and include `"owner_id": owner_id,` in the inserted document.

In `backend/main.py`'s `create_race` endpoint, pass the caller through:

```python
    race = crud.create_race(db, req.track, req.race_type, req.season_id,
                            req.weather_seed, owner_id=player["id"])
```

In `start_race`, after the lobby lookup and before the status change, add:

```python
    db = SessionLocal()
    try:
        race = crud.get_race(db, race_id)
    finally:
        db.close()
    if race is None:
        raise HTTPException(404, "Race not found")
    if getattr(race, "owner_id", None) not in (None, player["id"]):
        raise HTTPException(403, "Only the race owner can start this race")
```

- [ ] **Step 7: Gate the season endpoints**

In `create_season` and `end_season`, immediately after the existing `player = authenticate(x_api_key)` line, add:

```python
    require_admin(player)
```

- [ ] **Step 8: Verify the full suite**

Run: `cd piwall && python -m pytest -v`
Expected: PASS — 24 tests.

- [ ] **Step 9: Commit**

```bash
git add backend/main.py backend/db/crud.py piwall/tests/api
git commit -m "fix(security): enforce race ownership and admin role on season endpoints"
```

---

### Task 10: Rate limiting

Nothing is rate limited. `/api/test-bot` (`main.py:425`) runs a full race simulation per call, making it a compute amplifier, and `/api/register` allows unlimited account creation.

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/main.py` (app setup, and three endpoint decorators)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: a module-level `limiter` in `backend/main.py`. Limits are per client IP; Phase 2 moves them to Redis so they hold across replicas.

- [ ] **Step 1: Add the dependency**

Append to `piwall/backend/requirements.txt`:

```
slowapi>=0.1.9
```

Run: `cd piwall && pip install -r backend/requirements.txt`

- [ ] **Step 2: Wire the limiter into the app**

In `backend/main.py`, add to the imports:

```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
```

Immediately after the `app = FastAPI(...)` construction, add:

```python
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

- [ ] **Step 3: Apply limits to the expensive endpoints**

Each limited endpoint must accept the `Request` object, which slowapi requires.

On `test_bot` (line 425) — the most expensive route:

```python
@app.post("/api/test-bot")
@limiter.limit("10/minute")
def test_bot(request: Request, req: TestBotRequest, x_api_key: str = Header()):
```

On `register` (line 158):

```python
@app.post("/api/register")
@limiter.limit("5/hour")
def register(request: Request, req: RegisterRequest):
```

On `create_race` (line 179):

```python
@app.post("/api/race/create")
@limiter.limit("30/minute")
def create_race(request: Request, req: CreateRaceRequest, x_api_key: str = Header()):
```

Add `Request` to the existing `from fastapi import ...` line if it is not already imported.

- [ ] **Step 4: Verify the limiter engages**

Run: `cd piwall && PYTHONPATH=. python -c "
from fastapi.testclient import TestClient
from backend.main import app
c = TestClient(app)
codes = [c.post('/api/register', json={'username': f'u{i}', 'team_name': 'T'}).status_code for i in range(8)]
print(codes)
assert 429 in codes, 'rate limit did not engage'
print('rate limiting active')
"`
Expected: prints a list ending in `429`s, then `rate limiting active`.

- [ ] **Step 5: Verify the full suite**

Run: `cd piwall && python -m pytest -v`
Expected: PASS — 24 tests.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/requirements.txt
git commit -m "feat(security): rate limit registration, race creation and bot testing"
```

---

### Task 11: Infrastructure hardening

`docker-compose.yml` publishes MongoDB on host port 27017 with no authentication configured (`docker-compose.yml:2-12`), and every credential is a plaintext literal.

**Files:**
- Modify: `piwall/docker-compose.yml`
- Create: `piwall/.env.example`
- Modify: `piwall/.gitignore` (create at that path if absent)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: compose reads all secrets from a git-ignored `.env`; Mongo is reachable only on the internal compose network.

- [ ] **Step 1: Create the environment template**

Create `piwall/.env.example`:

```
MONGO_ROOT_USERNAME=piwall
MONGO_ROOT_PASSWORD=change-me-before-deploying
MONGODB_DB=phi1
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
NEXT_PUBLIC_API_URL=http://localhost:8000
AUTH_SECRET=change-me-before-deploying
```

- [ ] **Step 2: Ignore the real env file**

Append to `piwall/.gitignore` (create the file if it does not exist):

```
.env
```

- [ ] **Step 3: Harden the compose file**

In `piwall/docker-compose.yml`, in the `mongodb` service: delete the `ports:` block entirely (the backend reaches it over the compose network by service name), and add authentication:

```yaml
    environment:
      MONGO_INITDB_ROOT_USERNAME: ${MONGO_ROOT_USERNAME}
      MONGO_INITDB_ROOT_PASSWORD: ${MONGO_ROOT_PASSWORD}
```

In the `backend` service, replace the hardcoded `MONGODB_URI` with the authenticated form and drop the source bind-mounts (`./backend:/app/backend`), which overlay the built image and are a development-only pattern:

```yaml
    environment:
      MONGODB_URI: mongodb://${MONGO_ROOT_USERNAME}:${MONGO_ROOT_PASSWORD}@mongodb:27017/${MONGODB_DB}?authSource=admin
      MONGODB_DB: ${MONGODB_DB}
      CORS_ORIGINS: ${CORS_ORIGINS}
    restart: unless-stopped
```

Add `restart: unless-stopped` to the `frontend` service as well.

- [ ] **Step 4: Verify the stack comes up clean**

Run:
```bash
cd piwall && cp .env.example .env && docker-compose config >/dev/null && docker-compose up -d --build
sleep 20 && docker-compose ps
```
Expected: all services `running`. Then confirm Mongo is not reachable from the host:

Run: `nc -z -w 2 localhost 27017; echo "exit=$?"`
Expected: `exit=1` (connection refused — the port is no longer published).

- [ ] **Step 5: Tear down and commit**

```bash
cd piwall && docker-compose down
git add piwall/docker-compose.yml piwall/.env.example piwall/.gitignore
git commit -m "fix(security): require Mongo auth, unpublish its port, and move secrets to .env"
```

---

## Phase 0 Exit Criteria

Per spec §11, Phase 0 is done when the containment corpus passes at every layer and no credential is readable by page JavaScript. Verify all of it:

- [ ] `cd piwall && python -m pytest -v` — 24 tests pass.
- [ ] `grep -rn "api_key" frontend/src/ | grep -v "app/api/"` returns nothing.
- [ ] `grep -n "getattr\|hasattr" backend/sandbox/runner.py` shows no entries inside `ALLOWED_BUILTINS`.
- [ ] A bot containing `while True: pass` raises `MatchAborted` instead of hanging a worker thread.
- [ ] `nc -z localhost 27017` fails against the compose stack.
- [ ] The default `STRATEGY_TEMPLATE` still returns a valid decision.

**Not in scope, deferred to Phase 1:** deterministic per-decision operation budgets (§5.5), persisted match manifests and seeds, per-bot seeded RNG, frozen calibration artifacts, and the replay format. Phase 0 contains; Phase 1 makes matches reproducible.
