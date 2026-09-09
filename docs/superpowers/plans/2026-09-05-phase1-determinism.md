# Phase 1 — Determinism Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a match reproducible from its manifest alone — the same manifest must produce a byte-identical replay across 100 runs and two machines.

**Architecture:** Every source of run-to-run variation is either removed or captured in an immutable manifest. Floating-point drift is pinned by exact dependency versions and a frozen calibration artifact; RNG is split into one engine stream and one independent per-bot stream; wall-clock timeouts stop influencing simulation outcome and are replaced by counted interpreter operations. A canonical replay serializer then makes "identical" a byte-level assertion rather than a judgement call.

**Tech Stack:** Python 3.11 (container), CPython `random.Random`, `sys.settrace` for operation counting, RestrictedPython, MongoDB, pytest.

**Spec:** `docs/superpowers/specs/2026-09-02-pitwall-competitive-platform-design.md` (§5 Determinism Contract, §11 phase gate)

## Global Constraints

- **Python 3.11** is the production interpreter (`Dockerfile.backend:1` — `python:3.11-slim`). Local dev runs 3.14.7; any determinism claim must be verified on 3.11, in the container.
- **`PYTHONHASHSEED=0`** fixed in the runner image (spec §5.2).
- **No numpy in the engine hot path** (spec §5.4). numpy/scipy become calibration-tool-only dependencies.
- **Never sum over an unordered collection.** Keep summation order fixed and source-literal (spec §5.4).
- **Wall-clock limits must never influence simulation outcome** (spec §5.5). They remain only as an outer safety net; if they fire the match is voided and requeued, never completed.
- **Commit convention:** one-line commit messages, no `Co-Authored-By:` or `Claude-Session:` trailers, no mention of any AI assistant in commit messages, PR titles, PR bodies, or created files.
- The engine's physics is frozen: no behavioural change to lap-time or degradation maths is in scope. Changes that alter output are defects unless a task explicitly authorises them.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/determinism/__init__.py` | New package for the determinism contract |
| `backend/determinism/canonical.py` | Canonical JSON: the single definition of "byte-identical" |
| `backend/determinism/manifest.py` | `MatchManifest` dataclass, build + hash + round-trip |
| `backend/determinism/replay.py` | `RaceResult` → canonical replay bytes + sha256 |
| `backend/determinism/budget.py` | Counted-operation budget for bot decisions |
| `backend/determinism/lockfile.py` | `dep_lock_sha256` over the pinned requirements |
| `backend/data/calibration_store.py` | Loads frozen calibration artifacts; never imports scipy |
| `scripts/build_calibration.py` | Offline calibration build (the only scipy caller) |
| `calibration/<track>.<id>.json` | Committed calibration artifacts |
| `backend/requirements.txt` | Exact-pinned runtime deps (no numpy/scipy) |
| `backend/requirements-calibration.txt` | numpy/scipy/fastf1/matplotlib, calibration only |
| `tests/determinism/` | Golden manifests + the repeat-run gate |

---

### Task 1: Pin dependencies exactly and hash the lock

Floor pins (`>=`) mean a rebuild can silently change every float in the game. `curve_fit` results shift across numpy/scipy versions because the LAPACK/BLAS backend changes (spec §5.3). This task removes the variability and makes the pinned set addressable so the manifest can record which one produced a match.

**Files:**
- Modify: `piwall/backend/requirements.txt`
- Create: `piwall/backend/requirements-calibration.txt`
- Create: `piwall/backend/determinism/__init__.py`
- Create: `piwall/backend/determinism/lockfile.py`
- Test: `piwall/tests/determinism/test_lockfile.py`

**Interfaces:**
- Produces: `dep_lock_sha256() -> str` returning `"sha256:<64 hex>"`; `REQUIREMENTS_PATH: Path`

- [ ] **Step 1: Capture the versions the container actually resolved**

Run against the real 3.11 image, not the local 3.14 venv — the pins must describe production:

```bash
cd piwall
docker compose build backend
docker compose run --rm --no-deps backend pip freeze > /tmp/frozen.txt
grep -E '^(fastapi|uvicorn|websockets|redis|pymongo|pydantic|RestrictedPython|slowapi|httpx|pytest)==' /tmp/frozen.txt
```

Expected: exact `==` versions for each. Record them; they become the new `requirements.txt`.

- [ ] **Step 2: Write the failing test**

```python
# piwall/tests/determinism/test_lockfile.py
import re
from backend.determinism.lockfile import REQUIREMENTS_PATH, dep_lock_sha256


def test_hash_is_a_prefixed_sha256():
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", dep_lock_sha256())


def test_hash_is_stable_across_calls():
    assert dep_lock_sha256() == dep_lock_sha256()


def test_every_runtime_requirement_is_exactly_pinned():
    """A floor pin lets a rebuild change float results underneath a replay."""
    lines = [
        line.strip()
        for line in REQUIREMENTS_PATH.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines, "requirements.txt is empty"
    unpinned = [line for line in lines if "==" not in line]
    assert unpinned == [], f"not exactly pinned: {unpinned}"


def test_numpy_and_scipy_are_not_runtime_dependencies():
    """They belong to the calibration tool only (spec 5.3)."""
    text = REQUIREMENTS_PATH.read_text().lower()
    assert "numpy" not in text
    assert "scipy" not in text
```

- [ ] **Step 3: Run it and watch it fail**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/determinism/test_lockfile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.determinism'`

- [ ] **Step 4: Create the package and the lockfile hasher**

```python
# piwall/backend/determinism/__init__.py
"""The determinism contract: everything that makes a match reproducible."""
```

```python
# piwall/backend/determinism/lockfile.py
"""Content hash of the pinned runtime dependency set.

Recorded in every match manifest. Two matches sharing a dep_lock_sha256 ran
against byte-identical library versions, so a float difference between them
is a real divergence rather than a dependency bump.
"""

import hashlib
from pathlib import Path

REQUIREMENTS_PATH = Path(__file__).resolve().parent.parent / "requirements.txt"


def dep_lock_sha256() -> str:
    """Hash the pinned requirements, ignoring comments and blank lines.

    Normalising first means a comment edit does not invalidate replay
    history, while any version change does.
    """
    lines = [
        line.strip()
        for line in REQUIREMENTS_PATH.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    payload = "\n".join(sorted(lines)).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()
```

- [ ] **Step 5: Rewrite requirements.txt with the exact versions from Step 1**

Use the versions Step 1 printed. Do not invent them. Move the calibration-only libraries out:

```
# piwall/backend/requirements-calibration.txt
# Calibration tooling only — never installed into the runner image.
# scipy's curve_fit results shift across BLAS/LAPACK backends, which is why
# calibration is an offline build step and its output is committed.
numpy==2.4.6
scipy==1.17.1
fastf1==3.8.3
matplotlib==3.11.1
```

- [ ] **Step 6: Run the tests**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/determinism/test_lockfile.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Verify the runner image still builds without numpy/scipy**

Task 2 removes the last runtime scipy caller, so this is expected to FAIL here and pass after Task 2. Record the failure; do not fix it in this task.

```bash
cd piwall && docker compose build backend && \
  docker compose run --rm --no-deps backend python -c "import backend.main"
```

- [ ] **Step 8: Commit**

```bash
git add piwall/backend/requirements.txt piwall/backend/requirements-calibration.txt \
        piwall/backend/determinism/ piwall/tests/determinism/test_lockfile.py
git commit -m "build: pin runtime dependencies exactly and hash the lock"
```

---

### Task 2: Freeze calibration into committed artifacts

`build_track_physics` calls `calibrate_track`, which runs `scipy.optimize.curve_fit` at every race build (`backend/engine/cli_runner.py:31`). A dependency bump therefore silently changes every tyre model and so every lap time. This task makes calibration an offline step whose output is committed and content-addressed.

**Files:**
- Create: `piwall/scripts/build_calibration.py`
- Create: `piwall/backend/data/calibration_store.py`
- Create: `piwall/calibration/<track>.<calibration_id>.json` (generated, committed)
- Modify: `piwall/backend/engine/cli_runner.py:28-31`
- Test: `piwall/tests/determinism/test_calibration_store.py`

**Interfaces:**
- Consumes: `TrackCalibration`, `TyreDegParams` from `backend/data/calibration.py`
- Produces: `load_calibration(track: str) -> TrackCalibration`, `calibration_id(track: str) -> str`, `ARTIFACT_DIR: Path`

- [ ] **Step 1: Write the failing test**

```python
# piwall/tests/determinism/test_calibration_store.py
import re
import pytest
from backend.data.calibration_store import (
    ARTIFACT_DIR, calibration_id, load_calibration,
)

TRACKS = ["bahrain", "monaco", "monza", "spa", "silverstone", "suzuka"]


@pytest.mark.parametrize("track", TRACKS)
def test_every_track_has_a_committed_artifact(track):
    assert list(ARTIFACT_DIR.glob(f"{track}.sha256-*.json")), f"no artifact for {track}"


@pytest.mark.parametrize("track", TRACKS)
def test_calibration_id_is_a_prefixed_sha256(track):
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", calibration_id(track))


@pytest.mark.parametrize("track", TRACKS)
def test_loading_twice_gives_equal_calibration(track):
    assert load_calibration(track) == load_calibration(track)


def test_loading_does_not_import_scipy():
    """The runner image has no scipy; an accidental import would crash it."""
    import subprocess, sys
    from pathlib import Path
    # Derived, not hardcoded: this same suite runs inside the container,
    # where the tree lives at /app rather than at any developer's path.
    repo = Path(__file__).resolve().parents[2]
    code = (
        "import sys; from backend.data.calibration_store import load_calibration; "
        "load_calibration('bahrain'); "
        "assert 'scipy' not in sys.modules, 'scipy was imported'"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=repo)


def test_unknown_track_raises_rather_than_silently_calibrating():
    with pytest.raises(FileNotFoundError):
        load_calibration("nurburgring")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/determinism/test_calibration_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.data.calibration_store'`

- [ ] **Step 3: Write the store**

```python
# piwall/backend/data/calibration_store.py
"""Loads frozen calibration artifacts. Never imports numpy or scipy.

calibrate_track() fits with scipy.optimize.curve_fit, whose results shift
across BLAS/LAPACK backends. Fitting at race time therefore made every lap
time a function of the installed dependency versions. The fit now happens
offline in scripts/build_calibration.py; this module only reads its output.
"""

import hashlib
import json
from pathlib import Path
from typing import Dict

from .calibration import TrackCalibration, TyreDegParams

ARTIFACT_DIR = Path(__file__).resolve().parent.parent.parent / "calibration"


def _artifact_path(track: str) -> Path:
    matches = sorted(ARTIFACT_DIR.glob(f"{track}.sha256-*.json"))
    if not matches:
        raise FileNotFoundError(
            f"No committed calibration for {track!r}. "
            f"Run: python scripts/build_calibration.py {track}"
        )
    if len(matches) > 1:
        # Two artifacts for one track means an ambiguous history: a match
        # replayed against the wrong one would diverge silently.
        raise RuntimeError(f"Multiple calibrations for {track!r}: {[m.name for m in matches]}")
    return matches[0]


def calibration_id(track: str) -> str:
    """Content address of the artifact, recorded in the match manifest."""
    return "sha256:" + _artifact_path(track).name.split(".sha256-")[1][:-5]


def load_calibration(track: str) -> TrackCalibration:
    raw = json.loads(_artifact_path(track).read_text())
    compounds: Dict[str, TyreDegParams] = {
        name: TyreDegParams(**params) for name, params in sorted(raw["compounds"].items())
    }
    return TrackCalibration(
        track=raw["track"],
        base_lap_time=raw["base_lap_time"],
        pit_loss_seconds=raw["pit_loss_seconds"],
        compounds=compounds,
    )


def canonical_calibration_bytes(cal: TrackCalibration) -> bytes:
    """Byte form the calibration_id is computed over. Shared with the builder."""
    from dataclasses import asdict
    payload = {
        "track": cal.track,
        "base_lap_time": cal.base_lap_time,
        "pit_loss_seconds": cal.pit_loss_seconds,
        "compounds": {name: asdict(p) for name, p in sorted(cal.compounds.items())},
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
```

- [ ] **Step 4: Write the offline builder**

```python
# piwall/scripts/build_calibration.py
"""Offline calibration build. The only place scipy runs.

Usage: python scripts/build_calibration.py [track ...]

Writes calibration/<track>.sha256-<id>.json. Commit the result: a match
manifest records the calibration_id, so recalibrating creates a new artifact
rather than invalidating replay history. Delete the old file only once no
manifest references it.
"""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.data.calibration import calibrate_track
from backend.data.calibration_store import ARTIFACT_DIR, canonical_calibration_bytes
from backend.data.tracks import TRACKS


def build(track: str) -> Path:
    cal = calibrate_track(track)
    payload = canonical_calibration_bytes(cal)
    digest = hashlib.sha256(payload).hexdigest()
    ARTIFACT_DIR.mkdir(exist_ok=True)
    out = ARTIFACT_DIR / f"{track}.sha256-{digest}.json"
    out.write_bytes(payload)
    print(f"{track}: {out.name}")
    return out


if __name__ == "__main__":
    targets = sys.argv[1:] or sorted(TRACKS)
    for name in targets:
        build(name)
```

- [ ] **Step 5: Generate and commit the artifacts**

```bash
cd piwall && PYTHONPATH=. python scripts/build_calibration.py
ls calibration/
```
Expected: one `<track>.sha256-<64 hex>.json` per track in `TRACKS`.

- [ ] **Step 6: Point build_track_physics at the store**

In `piwall/backend/engine/cli_runner.py`, replace the import and the call:

```python
from ..data.calibration_store import load_calibration
```

```python
def build_track_physics(track_name: str) -> TrackPhysics:
    """Build TrackPhysics from the frozen calibration artifact."""
    track_cfg = TRACKS[track_name]
    cal = load_calibration(track_name)
```

Everything below that line is unchanged.

- [ ] **Step 7: Prove the swap changed no output**

The engine is frozen; this must be output-neutral. Compare a fixed-seed race before and after:

```bash
cd piwall && PYTHONPATH=. python -c "
from backend.engine.cli_runner import build_track_physics
from backend.data.tracks import TRACKS
import hashlib, json
p = build_track_physics('bahrain')
print(hashlib.sha256(json.dumps({
    c: [m.alpha, m.k, m.e, m.base_lap_time] for c, m in sorted(p.tyre_models.items())
}, sort_keys=True).encode()).hexdigest())
"
```
Expected: identical hash to the same command run on the previous commit. If it differs, STOP — the calibration artifact does not reproduce the live fit and the cause must be found before proceeding.

- [ ] **Step 8: Run the tests**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/determinism/ -v`
Expected: PASS

- [ ] **Step 9: Verify the runner image no longer needs scipy**

```bash
cd piwall && docker compose build backend && \
  docker compose run --rm --no-deps backend python -c "
import backend.main, sys
assert 'scipy' not in sys.modules and 'numpy' not in sys.modules
print('runner clean')"
```
Expected: `runner clean`. This is Task 1 Step 7's deferred failure now passing.

- [ ] **Step 10: Commit**

```bash
git add piwall/scripts/build_calibration.py piwall/backend/data/calibration_store.py \
        piwall/calibration/ piwall/backend/engine/cli_runner.py \
        piwall/tests/determinism/test_calibration_store.py
git commit -m "feat: freeze track calibration into committed content-addressed artifacts"
```

---

### Task 3: Per-bot seeded RNG streams

`backend/sandbox/runner.py:131` binds the global `random` module into every bot's globals. All bots therefore share one process-global stream: one bot's draws shift every other bot's, and nothing is reproducible from a seed. Each slot gets its own stream instead (spec §5.2).

**Files:**
- Modify: `piwall/backend/sandbox/runner.py:129-131`
- Modify: `piwall/backend/sandbox/match_job.py`
- Test: `piwall/tests/determinism/test_bot_rng.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: `build_sandbox_globals(..., seed: int, slot: int)` — the existing globals builder gains two required keyword arguments

- [ ] **Step 1: Write the failing test**

```python
# piwall/tests/determinism/test_bot_rng.py
import random
import pytest
from backend.sandbox.runner import build_sandbox_globals

FORBIDDEN = ["time", "datetime", "os", "uuid", "secrets", "sys", "socket"]


def _stream(seed, slot):
    return build_sandbox_globals(seed=seed, slot=slot)["random"]


def test_each_slot_gets_its_own_generator():
    assert _stream(42, 0) is not _stream(42, 1)


def test_the_module_itself_is_never_injected():
    """The global module makes every bot share one stream."""
    assert _stream(42, 0) is not random


def test_same_seed_and_slot_reproduce_the_same_draws():
    a = [_stream(42, 0).random() for _ in range(5)]
    b = [_stream(42, 0).random() for _ in range(5)]
    assert a == b


def test_different_slots_draw_independently():
    assert [_stream(42, 0).random() for _ in range(5)] != [_stream(42, 1).random() for _ in range(5)]


def test_different_seeds_draw_differently():
    assert [_stream(42, 0).random() for _ in range(5)] != [_stream(43, 0).random() for _ in range(5)]


def test_one_bot_draining_its_stream_cannot_perturb_another():
    """The failure the shared module caused: draw order leaking across slots."""
    quiet = _stream(42, 1)
    baseline = [quiet.random() for _ in range(3)]

    noisy, quiet_again = _stream(42, 0), _stream(42, 1)
    for _ in range(1000):
        noisy.random()
    assert [quiet_again.random() for _ in range(3)] == baseline


@pytest.mark.parametrize("name", FORBIDDEN)
def test_nondeterministic_modules_are_unreachable(name):
    assert name not in build_sandbox_globals(seed=42, slot=0)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/determinism/test_bot_rng.py -v`
Expected: FAIL — `build_sandbox_globals` does not accept `seed`/`slot`, or does not exist under that name. If the globals builder in `runner.py` is currently inline rather than a function, extract it to `build_sandbox_globals(seed: int, slot: int) -> dict` first, changing nothing else.

- [ ] **Step 3: Replace the module injection with a per-slot stream**

In `piwall/backend/sandbox/runner.py`, replace:

```python
    # Inject math and random modules (safe)
    restricted_globals["math"] = math
    restricted_globals["random"] = random
```

with:

```python
    restricted_globals["math"] = math
    # A private stream per slot, derived from the match seed. Binding the
    # random *module* here gave every bot in the process one shared
    # generator: draw order then depended on which bots raced alongside
    # which, so no match could be reproduced from its seed.
    restricted_globals["random"] = random.Random((seed << 8) ^ slot)
```

- [ ] **Step 4: Thread seed and slot through the match job**

In `piwall/backend/sandbox/match_job.py`, the per-car strategy compilation must pass the car's grid index as `slot` and the match seed as `seed`. The slot must be the car's stable index in `spec["cars"]`, not enumeration order of a dict.

- [ ] **Step 5: Run the tests**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/determinism/test_bot_rng.py tests/sandbox/ -v`
Expected: PASS, including the existing sandbox suite unchanged.

- [ ] **Step 6: Commit**

```bash
git add piwall/backend/sandbox/runner.py piwall/backend/sandbox/match_job.py \
        piwall/tests/determinism/test_bot_rng.py
git commit -m "feat: give each bot slot its own seeded random stream"
```

---

### Task 4: Ordering and float hygiene tests

`backend/engine/weather.py:47-48` builds `states`/`weights` by walking a dict's key order. That is stable today only because `DEFAULT_TRANSITIONS` is a source literal. A future refactor to dynamic construction would silently reorder the cumulative-probability walk and change weather outcomes for the same seed. This task pins the contract with tests rather than changing behaviour.

**Files:**
- Test: `piwall/tests/determinism/test_ordering.py`

**Interfaces:**
- Consumes: `WeatherModel`, `DEFAULT_TRANSITIONS` from `backend/engine/weather.py`
- Produces: nothing

- [ ] **Step 1: Write the test**

```python
# piwall/tests/determinism/test_ordering.py
"""Pins the ordering assumptions the engine's reproducibility rests on.

None of these assert new behaviour. They fail loudly if a refactor removes a
guarantee the determinism contract depends on (spec 5.4).
"""

import random
import pytest
from backend.engine.weather import DEFAULT_TRANSITIONS, WeatherModel


def test_transition_tables_iterate_in_a_fixed_order():
    """step() walks probs.keys() cumulatively, so order changes outcomes."""
    for state, probs in DEFAULT_TRANSITIONS.items():
        assert list(probs.keys()) == list(probs.keys()), f"{state} iterates unstably"


def test_weather_is_reproducible_from_a_seed():
    def run():
        model = WeatherModel(rng=random.Random(7))
        return [model.step() for _ in range(60)]
    assert run() == run()


def test_weather_sequences_differ_across_seeds():
    def run(seed):
        model = WeatherModel(rng=random.Random(seed))
        return [model.step() for _ in range(60)]
    assert run(7) != run(8)


def test_engine_hot_path_does_not_import_numpy():
    """numpy's float paths vary with the BLAS backend (spec 5.4)."""
    import subprocess, sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    code = (
        "import sys; import backend.engine.race, backend.engine.physics, "
        "backend.engine.weather; "
        "assert 'numpy' not in sys.modules, 'numpy reached the hot path'"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=repo)
```

- [ ] **Step 2: Run it**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/determinism/test_ordering.py -v`
Expected: PASS. If `test_engine_hot_path_does_not_import_numpy` fails, a numpy import has been reintroduced into the engine — remove it rather than relaxing the test.

- [ ] **Step 3: Commit**

```bash
git add piwall/tests/determinism/test_ordering.py
git commit -m "test: pin the ordering and float assumptions determinism rests on"
```

---

### Task 5: Counted-operation budgets replace wall-clock in the simulation

This is the subtlety in spec §5.5. A bot that times out on lap 30 in one run and lap 31 in another produces divergent replays, so **elapsed time must not decide anything the replay records**. Budget enforcement becomes a count of executed lines, which is reproducible; wall-clock survives only as an outer safety net that voids the match rather than completing it.

**Files:**
- Create: `piwall/backend/determinism/budget.py`
- Modify: `piwall/backend/sandbox/runner.py` (decision call site)
- Modify: `piwall/backend/engine/race.py` (record the forfeit event)
- Test: `piwall/tests/determinism/test_budget.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: `run_with_budget(fn, args, max_ops: int) -> tuple[object, int]`, `BudgetForfeit`, `DEFAULT_DECISION_OPS: int`

- [ ] **Step 1: Write the failing test**

```python
# piwall/tests/determinism/test_budget.py
import pytest
from backend.determinism.budget import (
    BudgetForfeit, DEFAULT_DECISION_OPS, run_with_budget,
)


def _cheap(n):
    return n * 2


def _expensive(n):
    total = 0
    for i in range(100000):
        total += i
    return total


def test_a_cheap_call_returns_normally():
    result, ops = run_with_budget(_cheap, (21,), max_ops=1000)
    assert result == 42
    assert ops > 0


def test_op_count_is_reproducible():
    """The whole point: the penalty must not depend on machine speed."""
    first = run_with_budget(_cheap, (21,), max_ops=1000)[1]
    for _ in range(20):
        assert run_with_budget(_cheap, (21,), max_ops=1000)[1] == first


def test_exceeding_the_budget_forfeits():
    with pytest.raises(BudgetForfeit):
        run_with_budget(_expensive, (0,), max_ops=500)


def test_the_forfeit_point_is_reproducible():
    """Same code, same budget, same place — every time."""
    counts = []
    for _ in range(10):
        try:
            run_with_budget(_expensive, (0,), max_ops=500)
        except BudgetForfeit as exc:
            counts.append(exc.ops_used)
    assert len(set(counts)) == 1, f"forfeit point varied: {sorted(set(counts))}"


def test_default_budget_is_a_positive_int():
    assert isinstance(DEFAULT_DECISION_OPS, int) and DEFAULT_DECISION_OPS > 0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/determinism/test_budget.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.determinism.budget'`

- [ ] **Step 3: Implement the counter**

```python
# piwall/backend/determinism/budget.py
"""Reproducible cost accounting for a single bot decision.

Wall-clock timeouts cannot bound a bot here: if the same bot times out on
lap 30 on one machine and lap 31 on another, the two replays diverge and the
determinism contract is broken. Counting executed lines instead gives a
budget whose exhaustion point depends only on the code and its inputs, so
the penalty lands in exactly the same place on every machine.

Wall-clock limits still exist one layer out, in sandbox/isolation.py. Those
are a safety net against a hang, and when they fire the match is voided and
requeued rather than completed (spec 5.5).
"""

import sys
from typing import Any, Callable, Tuple

# Roughly two orders of magnitude above the busiest built-in strategy, so a
# genuine bot never approaches it and a runaway loop always trips it.
DEFAULT_DECISION_OPS = 200_000


class BudgetForfeit(Exception):
    """Raised when a decision exhausts its operation budget."""

    def __init__(self, ops_used: int, max_ops: int):
        super().__init__(f"decision exhausted its {max_ops}-operation budget")
        self.ops_used = ops_used
        self.max_ops = max_ops


def run_with_budget(fn: Callable, args: Tuple, max_ops: int = DEFAULT_DECISION_OPS):
    """Run fn(*args) under a counted-line budget.

    Returns (result, ops_used). Raises BudgetForfeit at the exact same line
    on every machine, which is what makes the penalty replay-safe.
    """
    state = {"ops": 0}

    def tracer(frame, event, arg):
        if event == "line":
            state["ops"] += 1
            if state["ops"] > max_ops:
                raise BudgetForfeit(state["ops"], max_ops)
        return tracer

    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        result = fn(*args)
    finally:
        sys.settrace(previous)
    return result, state["ops"]
```

- [ ] **Step 4: Run the tests**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/determinism/test_budget.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Record a forfeit as a replay event, not a crash**

A forfeit must produce a recorded no-op decision so the replay stays complete. At the decision call site in `piwall/backend/sandbox/runner.py`, catch `BudgetForfeit` and return the no-op decision `Decision(pit=False, compound=car.compound)`, and append a `RaceEvent(lap, "budget_forfeit", car_id, "decision budget exhausted")` so the event is visible in the replay.

- [ ] **Step 6: Test the forfeit path end to end**

```python
# append to piwall/tests/determinism/test_budget.py
def test_a_forfeiting_bot_yields_a_no_op_and_the_race_continues():
    """A forfeit must not abort the match — it is a recorded non-decision."""
    from backend.sandbox.runner import run_strategy
    code = "def strategy(state, car):\n    x = 0\n    while True:\n        x += 1\n"
    decision, event = run_strategy(code, seed=42, slot=0, max_ops=500)
    assert decision.pit is False
    assert event is not None and event.event_type == "budget_forfeit"
```

Adjust the call to whatever `runner.py` exposes; the assertions are the contract.

- [ ] **Step 7: Run the whole suite**

Run: `cd piwall && PYTHONPATH=. python -m pytest -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add piwall/backend/determinism/budget.py piwall/backend/sandbox/runner.py \
        piwall/backend/engine/race.py piwall/tests/determinism/test_budget.py
git commit -m "feat: bound bot decisions by counted operations instead of elapsed time"
```

---

### Task 6: The match manifest

The manifest is the determinism contract made concrete: given this document alone, the runner must reproduce the match byte-for-byte (spec §5.1).

**Files:**
- Create: `piwall/backend/determinism/canonical.py`
- Create: `piwall/backend/determinism/manifest.py`
- Test: `piwall/tests/determinism/test_manifest.py`

**Interfaces:**
- Consumes: `dep_lock_sha256()` (Task 1), `calibration_id(track)` (Task 2)
- Produces: `canonical_json(obj) -> bytes`, `MatchManifest`, `Participant`, `build_manifest(...) -> MatchManifest`, `manifest_sha256(m) -> str`

- [ ] **Step 1: Write the failing test**

```python
# piwall/tests/determinism/test_manifest.py
import json
import pytest
from backend.determinism.canonical import canonical_json
from backend.determinism.manifest import (
    MatchManifest, Participant, build_manifest, manifest_sha256,
)


def _manifest():
    return build_manifest(
        match_id="m_01J", seed=1234567890, track="bahrain",
        participants=[
            Participant(slot=0, player_id="p_1", bot_version_id="bv_1",
                        code_sha256="sha256:" + "a" * 64, house_bot=None),
            Participant(slot=1, player_id=None, bot_version_id=None,
                        code_sha256=None, house_bot="VEL-01"),
        ],
    )


def test_canonical_json_sorts_keys_and_omits_whitespace():
    assert canonical_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_canonical_json_is_stable_across_dict_insertion_order():
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})


def test_manifest_carries_every_field_the_spec_requires():
    payload = json.loads(canonical_json(_manifest()))
    for field in ("match_id", "seed", "engine_version", "ruleset_version",
                  "calibration_id", "track", "python_version",
                  "dep_lock_sha256", "participants"):
        assert field in payload, f"manifest is missing {field}"


def test_participants_keep_their_slot_order():
    payload = json.loads(canonical_json(_manifest()))
    assert [p["slot"] for p in payload["participants"]] == [0, 1]


def test_hash_is_stable_and_content_addressed():
    assert manifest_sha256(_manifest()) == manifest_sha256(_manifest())


def test_changing_the_seed_changes_the_hash():
    other = build_manifest(match_id="m_01J", seed=999, track="bahrain",
                           participants=_manifest().participants)
    assert manifest_sha256(other) != manifest_sha256(_manifest())


def test_round_trips_through_json_unchanged():
    m = _manifest()
    assert canonical_json(MatchManifest.from_dict(json.loads(canonical_json(m)))) == canonical_json(m)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/determinism/test_manifest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.determinism.canonical'`

- [ ] **Step 3: Write the canonical encoder**

```python
# piwall/backend/determinism/canonical.py
"""One definition of "byte-identical", shared by manifests and replays.

Every reproducibility claim in this phase is an equality between two byte
strings, so the encoding must be fixed: sorted keys so dict insertion order
cannot leak in, no whitespace so formatting cannot, and no NaN or Infinity
because neither survives a JSON round trip intact.
"""

import json
from dataclasses import asdict, is_dataclass
from typing import Any


def canonical_json(obj: Any) -> bytes:
    if is_dataclass(obj) and not isinstance(obj, type):
        obj = asdict(obj)
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True,
    ).encode("utf-8")
```

- [ ] **Step 4: Write the manifest**

```python
# piwall/backend/determinism/manifest.py
"""The immutable description of a match (spec 5.1)."""

import hashlib
import platform
from dataclasses import dataclass, field
from typing import List, Optional

from .canonical import canonical_json
from .lockfile import dep_lock_sha256
from ..data.calibration_store import calibration_id

ENGINE_VERSION = "2.1.0"
RULESET_VERSION = "2026.1"


@dataclass
class Participant:
    slot: int
    player_id: Optional[str]
    bot_version_id: Optional[str]
    code_sha256: Optional[str]
    house_bot: Optional[str]


@dataclass
class MatchManifest:
    match_id: str
    seed: int
    engine_version: str
    ruleset_version: str
    calibration_id: str
    track: str
    python_version: str
    dep_lock_sha256: str
    participants: List[Participant] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict) -> "MatchManifest":
        return cls(
            **{k: v for k, v in raw.items() if k != "participants"},
            participants=[Participant(**p) for p in raw["participants"]],
        )


def build_manifest(match_id: str, seed: int, track: str,
                   participants: List[Participant]) -> MatchManifest:
    """Capture everything a replay needs, at the moment the match is created."""
    return MatchManifest(
        match_id=match_id,
        seed=seed,
        engine_version=ENGINE_VERSION,
        ruleset_version=RULESET_VERSION,
        calibration_id=calibration_id(track),
        track=track,
        python_version=platform.python_version(),
        dep_lock_sha256=dep_lock_sha256(),
        # Sorted by slot: participant order decides RNG stream assignment, so
        # an unsorted list would silently change the match.
        participants=sorted(participants, key=lambda p: p.slot),
    )


def manifest_sha256(manifest: MatchManifest) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(manifest)).hexdigest()
```

- [ ] **Step 5: Run the tests**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/determinism/test_manifest.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add piwall/backend/determinism/canonical.py piwall/backend/determinism/manifest.py \
        piwall/tests/determinism/test_manifest.py
git commit -m "feat: add the immutable match manifest and its canonical hash"
```

---

### Task 7: Replay serialization

`RaceResult` already carries `final_standings`, `events`, `lap_data` and `weather_history`. The replay is those, canonically encoded and hashed, so "identical" becomes a byte comparison.

**Files:**
- Create: `piwall/backend/determinism/replay.py`
- Test: `piwall/tests/determinism/test_replay.py`

**Interfaces:**
- Consumes: `canonical_json` (Task 6), `RaceResult` from `backend/engine/race.py`
- Produces: `replay_bytes(result, manifest) -> bytes`, `replay_sha256(result, manifest) -> str`, `replay_from_manifest(manifest) -> bytes`, `REPLAY_FORMAT_VERSION: str`

- [ ] **Step 1: Write the failing test**

```python
# piwall/tests/determinism/test_replay.py
import random
from backend.determinism.manifest import Participant, build_manifest
from backend.determinism.replay import (
    REPLAY_FORMAT_VERSION, replay_from_manifest,
)


def _manifest(seed=42):
    return build_manifest(
        match_id="m_test", seed=seed, track="bahrain",
        participants=[
            Participant(0, None, None, None, "VEL-01"),
            Participant(1, None, None, None, "NXS-07"),
        ],
    )


def _run(seed=42):
    """Re-run through the manifest path, which is what the gate exercises."""
    return replay_from_manifest(_manifest(seed))


def test_replay_declares_its_format_version():
    import json
    assert json.loads(_run())["format_version"] == REPLAY_FORMAT_VERSION


def test_the_same_seed_produces_byte_identical_replays():
    assert _run(42) == _run(42)


def test_different_seeds_produce_different_replays():
    assert _run(42) != _run(43)


def test_the_replay_embeds_the_manifest_that_produced_it():
    import json
    payload = json.loads(_run())
    assert payload["manifest"]["seed"] == 42
    assert payload["manifest"]["track"] == "bahrain"


def test_a_global_random_call_between_runs_cannot_change_the_replay():
    """Catches any residual dependence on the process-global stream."""
    first = _run(42)
    for _ in range(1000):
        random.random()
    assert _run(42) == first
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/determinism/test_replay.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.determinism.replay'`

- [ ] **Step 3: Write the serializer**

```python
# piwall/backend/determinism/replay.py
"""Canonical replay artifact: the thing whose sha256 the contract is about.

The manifest is embedded rather than referenced so a replay is verifiable on
its own — re-running the embedded manifest must reproduce these exact bytes.
"""

import hashlib
from dataclasses import asdict

from .canonical import canonical_json
from .manifest import MatchManifest
from ..engine.race import RaceResult
from ..engine.serialize import car_state_to_dict

REPLAY_FORMAT_VERSION = "1.0.0"


def replay_bytes(result: RaceResult, manifest: MatchManifest) -> bytes:
    payload = {
        "format_version": REPLAY_FORMAT_VERSION,
        "manifest": asdict(manifest),
        "track": result.track,
        "total_laps": result.total_laps,
        # List order is the finishing order and is load-bearing; canonical_json
        # sorts dict keys but never reorders a list.
        "final_standings": [car_state_to_dict(c) for c in result.final_standings],
        "events": [asdict(e) for e in result.events],
        "lap_data": result.lap_data,
        "weather_history": result.weather_history,
    }
    return canonical_json(payload)


def replay_sha256(result: RaceResult, manifest: MatchManifest) -> str:
    return "sha256:" + hashlib.sha256(replay_bytes(result, manifest)).hexdigest()


def replay_from_manifest(manifest: MatchManifest) -> bytes:
    """Re-run a match from its manifest alone. This *is* the contract.

    The grid is rebuilt from manifest.participants rather than from any
    ambient state, because anything the manifest does not name must not be
    able to influence the result. Slot order is load-bearing twice over: it
    sets the starting grid and it selects each bot's RNG stream.
    """
    # Imported here, not at module scope: the engine pulls in the track data
    # and calibration store, and replay.py is imported by the manifest tests.
    from ..engine.bots import BUILTIN_BOTS
    from ..engine.cli_runner import build_track_physics
    from ..engine.race import RaceEngine

    engine = RaceEngine(track=build_track_physics(manifest.track), seed=manifest.seed)
    for participant in sorted(manifest.participants, key=lambda p: p.slot):
        if participant.house_bot is None:
            raise NotImplementedError(
                "Replaying a player bot needs its source, which Phase 2 stores "
                "against code_sha256. Golden manifests use house bots only."
            )
        bot = BUILTIN_BOTS[participant.house_bot]
        engine.add_car(
            car_id=participant.house_bot,
            player_id=participant.house_bot,
            strategy=bot["strategy"],
            starting_position=participant.slot + 1,
            starting_compound=bot["starting_compound"],
        )
    return replay_bytes(engine.run(), manifest)
```

- [ ] **Step 4: Run the tests**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/determinism/test_replay.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add piwall/backend/determinism/replay.py piwall/tests/determinism/test_replay.py
git commit -m "feat: add canonical replay serialization and hashing"
```

---

### Task 8: The verification gate — golden manifests and the repeat-run test

This is the phase gate and, per spec §5.6, the single most valuable test in the codebase. It is also the only task that can prove the previous seven worked.

**Files:**
- Create: `piwall/tests/determinism/golden/<track>.manifest.json` (committed)
- Create: `piwall/tests/determinism/golden/<track>.expected.txt` (committed hashes)
- Create: `piwall/scripts/verify_determinism.py`
- Create: `piwall/tests/determinism/test_golden.py`
- Modify: `piwall/Dockerfile.backend`
- Test: as above

**Interfaces:**
- Consumes: everything from Tasks 1-7
- Produces: `replay_from_manifest(manifest) -> bytes`

- [ ] **Step 1: Write the golden runner**

```python
# piwall/scripts/verify_determinism.py
"""Re-run committed manifests and compare replay hashes.

Usage:
  python scripts/verify_determinism.py            # verify against committed hashes
  python scripts/verify_determinism.py --record   # regenerate them

--record is how a deliberate engine change is accepted. A hash that moves
without --record means the determinism contract broke.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.determinism.manifest import MatchManifest
from backend.determinism.replay import replay_from_manifest

GOLDEN = Path(__file__).resolve().parent.parent / "tests" / "determinism" / "golden"


def main(record: bool) -> int:
    failures = 0
    for path in sorted(GOLDEN.glob("*.manifest.json")):
        manifest = MatchManifest.from_dict(json.loads(path.read_text()))
        expected_path = path.with_name(path.name.replace(".manifest.json", ".expected.txt"))
        import hashlib
        actual = "sha256:" + hashlib.sha256(replay_from_manifest(manifest)).hexdigest()
        if record:
            expected_path.write_text(actual + "\n")
            print(f"recorded {path.stem}: {actual}")
            continue
        expected = expected_path.read_text().strip()
        if actual == expected:
            print(f"ok       {path.stem}")
        else:
            print(f"MISMATCH {path.stem}\n  expected {expected}\n  actual   {actual}")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main("--record" in sys.argv))
```

- [ ] **Step 2: Generate one golden manifest per track and record hashes**

```bash
cd piwall && PYTHONPATH=. python -c "
import json
from pathlib import Path
from backend.determinism.manifest import Participant, build_manifest
from backend.determinism.canonical import canonical_json
from backend.data.tracks import TRACKS
out = Path('tests/determinism/golden'); out.mkdir(parents=True, exist_ok=True)
for i, track in enumerate(sorted(TRACKS)):
    m = build_manifest(match_id=f'golden_{track}', seed=1000 + i, track=track,
                       participants=[Participant(s, None, None, None, b) for s, b in
                                     enumerate(['VEL-01','NXS-07','WXP-23','EQL-44','AGR-33'])])
    (out / f'{track}.manifest.json').write_bytes(canonical_json(m))
    print(track)
"
PYTHONPATH=. python scripts/verify_determinism.py --record
```

- [ ] **Step 3: Write the gate test**

```python
# piwall/tests/determinism/test_golden.py
"""The Phase 1 gate: the same manifest must produce byte-identical replays."""

import hashlib
import json
from pathlib import Path

import pytest

from backend.determinism.manifest import MatchManifest
from backend.determinism.replay import replay_from_manifest

GOLDEN = Path(__file__).parent / "golden"
MANIFESTS = sorted(GOLDEN.glob("*.manifest.json"))


def _load(path):
    return MatchManifest.from_dict(json.loads(path.read_text()))


def test_golden_manifests_exist():
    assert MANIFESTS, "no golden manifests committed"


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.stem)
def test_replay_matches_the_committed_hash(path):
    expected = path.with_name(path.name.replace(".manifest.json", ".expected.txt")).read_text().strip()
    actual = "sha256:" + hashlib.sha256(replay_from_manifest(_load(path))).hexdigest()
    assert actual == expected, (
        "Replay diverged from its committed hash. Either a change altered "
        "simulation output, or the determinism contract broke. Do not re-record "
        "without establishing which."
    )


@pytest.mark.parametrize("path", MANIFESTS[:1], ids=lambda p: p.stem)
def test_one_hundred_runs_are_byte_identical(path):
    """The phase gate itself (spec 11)."""
    manifest = _load(path)
    first = replay_from_manifest(manifest)
    for run in range(99):
        assert replay_from_manifest(manifest) == first, f"diverged on run {run + 2}"
```

- [ ] **Step 4: Run the gate**

Run: `cd piwall && PYTHONPATH=. python -m pytest tests/determinism/test_golden.py -v`
Expected: PASS. A failure here is the real finding of this phase — do not re-record to make it pass.

- [ ] **Step 5: Fix PYTHONHASHSEED and ship the tests in the image**

Spec §5.2 requires `PYTHONHASHSEED=0` in the runner image. The image also currently contains no tests, so nothing verifies the sandbox on the interpreter that runs untrusted code — the 3.11-only CPU-limit misclassification found during the Phase 0 deploy gate is exactly the class of bug that hides there.

In `piwall/Dockerfile.backend`, add before the entrypoint:

```dockerfile
ENV PYTHONHASHSEED=0
COPY tests/ ./tests/
COPY pytest.ini ./pytest.ini
```

Task 2 already added `COPY calibration/ calibration/` — do not add it twice.

- [ ] **Step 6: Run the gate on the production interpreter — the second machine**

The gate says "two machines". Local 3.14 and the 3.11 container are two distinct interpreters and are the pairing that matters:

```bash
cd piwall && docker compose build backend
docker compose run --rm --no-deps backend sh -c \
  "PYTHONPATH=. python -m pytest tests/determinism -v"
docker compose run --rm --no-deps backend sh -c \
  "python -c \"import os; assert os.environ['PYTHONHASHSEED']=='0'; print('hashseed pinned')\""
```
Expected: the determinism suite passes inside the container.

**If the golden hashes differ between local and container, that is the expected outcome of this step, not a defect in it** — it means float results genuinely differ across interpreter versions. Record the container hashes as authoritative (production runs 3.11), note the divergence in the report, and pin local development to 3.11 in a follow-up rather than weakening the test.

- [ ] **Step 7: Run the full suite both places**

```bash
cd piwall && PYTHONPATH=. python -m pytest -q
docker compose run --rm --no-deps backend sh -c "PYTHONPATH=. python -m pytest -q"
```
Expected: PASS in both.

- [ ] **Step 8: Commit**

```bash
git add piwall/scripts/verify_determinism.py piwall/tests/determinism/ \
        piwall/Dockerfile.backend
git commit -m "test: add the determinism gate with golden manifests and committed replay hashes"
```

---

### Task 9: Persist manifests and replay hashes

Spec §11 puts "persist manifests" in this phase, and it is what makes the contract enforceable in production rather than only in CI: without a stored `replay_sha256`, a later divergence has nothing to be compared against.

**Files:**
- Modify: `piwall/backend/db/crud.py`
- Modify: `piwall/backend/db/models.py` (indexes)
- Test: `piwall/tests/determinism/test_persistence.py`

**Interfaces:**
- Consumes: `MatchManifest`, `manifest_sha256` (Task 6); `replay_bytes`, `replay_sha256` (Task 7)
- Produces: `save_manifest(db, manifest) -> str`, `get_manifest(db, match_id) -> Optional[MatchManifest]`, `save_replay_hash(db, match_id, replay_sha256) -> None`, `get_replay_hash(db, match_id) -> Optional[str]`

- [ ] **Step 1: Write the failing test**

```python
# piwall/tests/determinism/test_persistence.py
import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("MONGODB_URI"),
    reason="needs a database; the rest of the determinism suite is hermetic",
)

from backend.db import crud
from backend.db.models import create_db_engine, init_db
from backend.determinism.manifest import Participant, build_manifest


@pytest.fixture
def db():
    engine = create_db_engine(os.environ["MONGODB_URI"])
    factory = init_db(engine)
    session = factory()
    yield session
    session.manifests.delete_many({"match_id": {"$regex": "^t_"}})
    session.close()


def _manifest(match_id="t_1", seed=7):
    return build_manifest(match_id=match_id, seed=seed, track="bahrain",
                          participants=[Participant(0, None, None, None, "VEL-01")])


def test_a_manifest_round_trips_through_the_database(db):
    crud.save_manifest(db, _manifest())
    assert crud.get_manifest(db, "t_1") == _manifest()


def test_manifests_are_immutable_once_written(db):
    """Rewriting a manifest would silently invalidate every replay of it."""
    crud.save_manifest(db, _manifest())
    with pytest.raises(ValueError):
        crud.save_manifest(db, _manifest(match_id="t_1", seed=999))


def test_an_absent_manifest_returns_none(db):
    assert crud.get_manifest(db, "t_nonexistent") is None


def test_the_replay_hash_round_trips(db):
    crud.save_manifest(db, _manifest())
    crud.save_replay_hash(db, "t_1", "sha256:" + "b" * 64)
    assert crud.get_replay_hash(db, "t_1") == "sha256:" + "b" * 64
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd piwall && MONGODB_URI=mongodb://127.0.0.1:27017/phi1_test PYTHONPATH=. python -m pytest tests/determinism/test_persistence.py -v`
Expected: FAIL with `AttributeError: module 'backend.db.crud' has no attribute 'save_manifest'`

- [ ] **Step 3: Add the persistence functions to `crud.py`**

```python
def save_manifest(db, manifest) -> str:
    """Write a manifest once. Raises ValueError on any attempt to change it.

    A manifest is the definition of what a match was; rewriting one would
    invalidate every replay recorded against it without leaving a trace.
    """
    from dataclasses import asdict
    from ..determinism.manifest import manifest_sha256

    existing = db.manifests.find_one({"match_id": manifest.match_id})
    digest = manifest_sha256(manifest)
    if existing:
        if existing.get("manifest_sha256") != digest:
            raise ValueError(
                f"manifest {manifest.match_id} already exists with different content"
            )
        return digest
    db.manifests.insert_one({**asdict(manifest), "manifest_sha256": digest})
    return digest


def get_manifest(db, match_id: str):
    from ..determinism.manifest import MatchManifest

    raw = db.manifests.find_one({"match_id": match_id})
    if not raw:
        return None
    raw.pop("_id", None)
    raw.pop("manifest_sha256", None)
    return MatchManifest.from_dict(raw)


def save_replay_hash(db, match_id: str, replay_sha256: str) -> None:
    db.manifests.update_one(
        {"match_id": match_id}, {"$set": {"replay_sha256": replay_sha256}}
    )


def get_replay_hash(db, match_id: str):
    raw = db.manifests.find_one({"match_id": match_id})
    return raw.get("replay_sha256") if raw else None
```

- [ ] **Step 4: Add the index**

In `piwall/backend/db/models.py`, alongside the existing index creation:

```python
    db.manifests.create_index([("match_id", ASCENDING)], unique=True)
```

- [ ] **Step 5: Run the tests**

Run: `cd piwall && MONGODB_URI=mongodb://127.0.0.1:27017/phi1_test PYTHONPATH=. python -m pytest tests/determinism/test_persistence.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Confirm the suite is still collectable with no database**

The Phase 0 whole-branch review found a test that aborted collection without a database, silently running zero tests. Verify this file skips rather than errors:

```bash
cd piwall && env -u MONGODB_URI PYTHONPATH=. python -m pytest tests/determinism -q
```
Expected: the persistence tests report as skipped; everything else passes.

- [ ] **Step 7: Commit**

```bash
git add piwall/backend/db/crud.py piwall/backend/db/models.py \
        piwall/tests/determinism/test_persistence.py
git commit -m "feat: persist match manifests immutably with their replay hashes"
```

---

## Deferred to Phase 2

- **Production sampling job** (spec §5.6): periodically re-runs recent matches from their manifests and pages on hash mismatch. It needs the job queue and worker Phase 2 builds; the golden CI test covers the same contract until then.
- **Replay body storage.** Task 9 persists the manifest and the replay's sha256, which is what the contract needs to detect divergence. Storing the replay *bodies* needs object storage and a retention policy, and belongs with the Phase 2 worker that will own the match lifecycle.
- **A single env file shared by backend and frontend.** Three separate config gaps in Phase 0 (`PROVISIONING_SECRET`, `NEXTAUTH_URL`/`NEXT_PUBLIC_WS_URL`, the Google OAuth pair) each rendered as a silently missing feature rather than an error. A startup assertion naming absent variables belongs with Phase 2's health checks.
