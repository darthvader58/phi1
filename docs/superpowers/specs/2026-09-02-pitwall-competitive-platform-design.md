# PIT WALL — Competitive Programming Platform Design

**Date:** 2026-09-02
**Status:** Draft for review
**Scope:** Convert PIT WALL from a single-process prototype into a hosted, Battlecode-style competitive programming game with deterministic simulation, isolated execution of untrusted player code, ELO matchmaking, and bot fallback.

---

## 1. Current State (audited 2026-09-02)

All findings below were verified directly against source.

### 1.1 Security — the sandbox is not a boundary

| Finding | Evidence |
|---|---|
| Raw `getattr`/`hasattr` exposed as builtins, nullifying the attribute guard | `backend/sandbox/runner.py:57-58` |
| `_write_` set to a no-op passthrough, disabling the write guard | `backend/sandbox/runner.py:128` |
| `_getitem_` is an unguarded lambda; `_getiter_` is the raw `iter` builtin | `backend/sandbox/runner.py:123-124` |
| CPU/wall-clock timeout never arms — `signal.signal()` raises `ValueError` off the main thread and is swallowed by a bare `except` | `backend/sandbox/runner.py:161-167` |
| Both execution paths run off the main thread, so the timeout is structurally dead | `backend/main.py:805-806` (executor), `backend/main.py:426` (sync `def` → Starlette threadpool) |
| No memory, recursion, iteration, or allocation limit | no `resource.setrlimit` anywhere |
| Execution is in-process `exec()`; no subprocess, container, or VM | `backend/sandbox/runner.py:170` |
| API keys stored in plaintext in MongoDB and returned in plaintext | `backend/db/crud.py:35`, `backend/main.py:169-174` |
| API key persisted in browser `localStorage`, readable by any page script | `frontend/src/components/BackendPlayerSync.tsx:55` |
| `start_race` authenticates but never checks race ownership — any player can start any race | `backend/main.py:252-271` |
| `create_season`/`end_season` have no admin role check | `backend/main.py:492-512`, `:599-609` |
| No rate limiting anywhere; `/api/test-bot` runs a full simulation per call | repo-wide; `backend/main.py:425` |
| MongoDB published on host port 27017 with no authentication configured | `docker-compose.yml:2-12` |

**Assessment:** for a determined adversary this is equivalent to no sandbox. The language-level guards are compromised by configuration, and there is no OS-level backstop behind them.

### 1.2 Determinism — no match is reproducible

| Finding | Evidence |
|---|---|
| Seed generated from the unseeded global `random` and never persisted | `backend/main.py:443`, `:769` |
| The process-global `random` module is injected into every bot's namespace | `backend/sandbox/runner.py:132` |
| That global RNG is shared across concurrently-running matches in the thread pool | `backend/main.py:805-806` |
| `scipy.optimize.curve_fit` recalibrates tyre models on every race build | `backend/data/calibration.py:61-129` |
| numpy/scipy pinned only by `>=`, so a version bump silently changes every lap time | `backend/requirements.txt` |
| Bot code that actually raced is not snapshotted 1:1 with the race record | `backend/db/crud.py:105-117` vs `:144-154` |
| Engine RNG itself is correctly single-instance and seeded (the one thing that is right) | `backend/engine/race.py:98` |

**Assessment:** the ladder cannot be trusted and no result can be audited or re-derived.

### 1.3 Architecture — cannot scale past one process

| Finding | Evidence |
|---|---|
| Match state lives in a module-level dict in API process memory | `backend/main.py:63` |
| Redis is a declared dependency with zero usage; a comment concedes the gap | `backend/requirements.txt`, `backend/main.py:45` |
| Simulation runs inline via fire-and-forget `asyncio.create_task`, untracked | `backend/main.py:270` |
| `_run_race` has no exception handling at all; a failure strands the lobby in `running` forever | `backend/main.py:741-872` |
| A fresh unbounded `ThreadPoolExecutor` is created per race | `backend/main.py:805` |
| All pymongo calls are synchronous inside async handlers, blocking the event loop | throughout `backend/main.py` |
| No graceful shutdown, health check, structured logging, migrations, or CI | `backend/main.py:96-101`; repo-wide |
| **Zero test files in the repository** | repo-wide `find` |

### 1.4 Matchmaking — does not exist

`/api/matchmaking/suggest` (`backend/main.py:657-693`) browses open lobbies sorted by ELO proximity, with an N+1 query per candidate. There is no queue, no pairing, no bot fallback.

### 1.5 Frontend — the strongest part of the system

Real and working: a live WebSocket race view with a lobby→countdown→lights-out→finish state machine (`race/[id]/page.tsx`), a hand-rolled Canvas2D animated track using genuine FastF1-derived circuit geometry (`TrackMap.tsx`, `lib/trackData.ts`), ELO leaderboard, season standings, and a Mongo-backed account dashboard.

Absent: any replay system or scrubbing, queue UI, spectator mode, bot versioning, tournaments, onboarding. The Monaco editor has no autocomplete, type hints, or linting for the bot API — the "type hints" are a static comment block pasted into the buffer (`StrategyEditor.tsx:24-56`). Editor content is `useState` only and is lost on refresh. PixiJS 8 is a declared dependency with zero imports.

---

## 2. Goals and Non-Goals

### Goals

1. **Bit-reproducible matches.** A stored manifest re-runs to a byte-identical replay. Enforced by automated verification, not convention.
2. **Untrusted code cannot harm the platform or other players.** Defense in depth ending in OS/VM-level isolation.
3. **Matchmaking with bot fallback.** A player never waits indefinitely; after a bounded wait they are matched with a house bot near their rating.
4. **Horizontal scalability.** No match state in API process memory.
5. **Replays are first-class.** Every match produces a durable, scrubable artifact.
6. **The ladder is honest.** ELO updates are atomic, provisional ratings are handled, and bot-assisted matches are marked.

### Non-Goals (this cycle)

- Languages other than Python for bots.
- Real-time human input during a race. Competition is between *programs*; the race is computed, then watched.
- Live head-to-head streaming of an in-progress simulation. Playback of a completed match replaces it (see §4.1).
- Mobile-native clients.

---

## 3. Infrastructure

| Layer | Choice | Rationale |
|---|---|---|
| Frontend | Vercel | Already Next.js 14 App Router |
| API | Single container host (Fly.io / Railway) | FastAPI needs a long-lived process for WebSockets |
| Match execution | Managed microVM sandbox, per-invocation | ~1–3 s of isolated compute per match; no kernel-level ops burden for a solo operator |
| Queue / ephemeral state | Redis (Upstash) | Matchmaking queue, job queue, pub/sub fan-out, locks |
| Database | MongoDB Atlas | Already on pymongo |
| Replay storage | Object storage (R2 / Blob) | Immutable, cheap, CDN-frontable |

Rejected: **self-hosted gVisor on a VPS** — cheapest, but makes kernel-level isolation a solo-operator responsibility, the highest-consequence and lowest-visibility failure mode available. **AWS Batch fleet (Battlecode's design)** — correct at their scale, overbuilt here.

---

## 4. Architecture

### 4.1 The central pivot: a match is a batch job, not a live session

Today a match is a long-lived object in the API process calling `exec()` once per car per lap — recompiling user source every lap (`backend/sandbox/runner.py:109-117`), sharing process globals, with no isolation boundary.

The new unit is **one match = one headless job = one sandbox invocation**, producing a replay artifact.

This is not a large behavioural change, because `_run_race` **already computes the entire race in one shot** at `backend/main.py:805` and then artificially re-plays it lap-by-lap with `asyncio.sleep(11.0 / speed)` purely for display pacing. The system is already replay-based; it just discards the replay. Extracting it is less work than maintaining the current path.

Security, determinism, and scalability all resolve at this single seam.

```
 submit bot ──► validate + version ──► store (source + sha256)
                                            │
 "Find Match" ──► Redis queue (ELO-scored, timestamped)
                                            │
                 pairing worker (~1s tick, leader-locked)
                 ELO band widens with wait time, jittered
                        │
                        ├── real opponents found ─────────┐
                        └── T+25s: fill slots w/ house bot ┤
                                                           ▼
                                              match job ──► Redis job queue
                                                           │
                                    match worker ──► ONE sandbox invocation
                                    (engine + all bots, seeded RNG, no net/fs)
                                                           │
                                  replay artifact ──► object storage
                                  result ──► Mongo (atomic ELO) ──► pub/sub notify
                                                           │
                                  client fetches replay ──► scrub / play / spectate
```

### 4.2 Components

Each component is independently testable with an explicit interface.

| Component | Responsibility | Depends on |
|---|---|---|
| `api` | HTTP/WS surface, auth, submissions, queue tickets, read models | Mongo, Redis |
| `matchmaker` | Pairing loop, band widening, bot fallback, job emission | Redis, Mongo (ELO reads) |
| `runner` | Executes one match from a manifest; emits replay + result | sandbox platform only |
| `worker` | Pulls jobs, invokes runner, persists replay + result, updates ELO | Redis, Mongo, object storage |
| `engine` | Pure simulation (existing `backend/engine/`) | none (no I/O) |
| `sandbox` | Language-level guards + subprocess limits | none |
| `web` | Next.js client: editor, queue, replay player, ladder | api |

**Key constraint on `runner`:** it must be a pure function of its manifest. No network, no clock, no filesystem writes, no database. This is what makes verification possible.

---

## 5. Determinism Contract

### 5.1 The match manifest

Immutable, persisted per match. Given this document alone, the runner must reproduce the match byte-for-byte.

```json
{
  "match_id": "m_01J...",
  "seed": 1234567890,
  "engine_version": "2.1.0",
  "ruleset_version": "2026.1",
  "calibration_id": "sha256:9f2a...",
  "track": "bahrain",
  "python_version": "3.11.9",
  "dep_lock_sha256": "sha256:41c8...",
  "participants": [
    {"slot": 0, "player_id": "p_...", "bot_version_id": "bv_...", "code_sha256": "sha256:...", "house_bot": null},
    {"slot": 1, "player_id": null,    "bot_version_id": null,     "code_sha256": null,        "house_bot": "vel_01"}
  ]
}
```

### 5.2 RNG discipline

- **Engine stream:** `random.Random(seed)`, single shared instance. Already correct at `backend/engine/race.py:98`.
- **Per-bot streams:** each slot receives its own `random.Random((seed << 8) ^ slot)` bound to the name `random` inside the sandbox globals, replacing the global module injection at `backend/sandbox/runner.py:132`. Independent of the engine stream and of every other bot, so one bot's draws cannot perturb another's.
- **Forbidden entirely:** `time`, `datetime`, `os`, `uuid`, `secrets`. None are reachable today once the `getattr` bypass is closed; keep it that way with an explicit denylist test.
- `PYTHONHASHSEED=0` fixed in the runner image.

### 5.3 Frozen calibration

`calibrate_track` currently runs `scipy.optimize.curve_fit` at every race build. `curve_fit` results can shift across numpy/scipy versions (different LAPACK/BLAS backends), and dependencies are floor-pinned — so a routine dependency bump silently changes every tyre model and therefore every lap time in the game.

- Calibration becomes an **offline build step** producing `calibration/<track>.<calibration_id>.json`, committed to the repo.
- The runner loads the artifact and never calls scipy.
- scipy/numpy become dev-only dependencies of the calibration tool, shrinking the runner image.
- `calibration_id` is recorded in the manifest, so a recalibration creates a new id rather than invalidating history.

### 5.4 Float and ordering hygiene

- Pinned CPython version and platform via the runner container image; IEEE-754 arithmetic is then reproducible.
- Remove the dead `import numpy as np` at `backend/engine/physics.py:18` and forbid numpy in the hot path.
- Keep summation order fixed and source-literal; never sum over an unordered collection.
- `backend/engine/weather.py:48-49` walks a dict's key order. It is stable today only because the transition tables are source literals in `backend/data/tracks.py`. Add a test asserting the ordering contract so a future refactor to dynamic construction fails loudly rather than silently.

### 5.5 Deterministic resource budgets — a subtlety that matters

**Wall-clock timeouts are nondeterministic and must never influence simulation outcome.** If a bot times out on lap 30 in one run and lap 31 in another, replays diverge and the contract is broken.

- **Budget enforcement is by counted interpreter operations, not elapsed time.** Each bot gets a fixed operation budget per decision. Exceeding it yields a recorded no-op decision (a "budget forfeit" event written into the replay). Because the count is reproducible, so is the penalty.
- **Wall-clock limits remain only as an outer safety net** at the process and sandbox level. If they fire, the match is **voided and requeued**, never silently completed with divergent state.

### 5.6 Verification

- **Golden tests in CI:** committed manifests re-run and must produce replays matching committed hashes. This is the single most valuable test in the codebase.
- **Production sampling job:** periodically re-runs a random sample of recent matches from their manifests and asserts `sha256(replay)` matches the stored value. A mismatch pages, because it means the determinism contract has broken in production.

---

## 6. Security Model

Five layers, no single one trusted.

1. **Submission-time static validation.** `compile_restricted` must succeed; an AST pass rejects dunder attribute access and references to `getattr`/`hasattr`/`eval`/`exec`; source size capped.
2. **Language layer (defense in depth, never the boundary).** Remove `getattr` and `hasattr` from `ALLOWED_BUILTINS`; restore a real write guard in place of the `_write_` passthrough; use RestrictedPython's `guarded_getitem` and guarded iteration rather than raw lambdas and `iter`.
3. **Process layer.** The match runs in a subprocess with `resource.setrlimit(RLIMIT_AS)` for memory and `RLIMIT_CPU` for CPU, and the alarm armed **in that subprocess's own main thread** — which is precisely what is broken today. The parent hard-`terminate()`s on wall-clock overrun.
4. **Platform layer.** The whole match executes inside a managed microVM sandbox: no network, read-only rootfs, no host filesystem access, ephemeral, discarded after the run.
5. **Budget layer.** Deterministic per-decision operation budgets as described in §5.5.

### 6.1 Identity and authorization

- **Hash API keys at rest.** Store `sha256(key)` and look up by hash; display the raw key exactly once at creation. Note the codebase already hashes bot code (`backend/db/crud.py:144-154`) — the primitive exists, it just isn't applied to the credential.
- **Remove the game API key from the browser.** The Next.js server proxies backend calls, so the key never reaches page JavaScript. This reuses the existing server-side pattern in `app/api/backend-player/route.ts` and eliminates the `localStorage` exposure.
- **Unify identity.** Add `auth_user_id` to the backend `players` collection, closing the one-directional bridge (the frontend records `backendPlayerId`, but the backend cannot resolve the NextAuth user). Required for match ownership and audit.
- **Ownership and roles.** Add `owner_id` to races and enforce it in `start_race`; add a `role` field and require admin for season lifecycle endpoints.
- **Rate limits** per key on registration, match-queue joins, and especially `/api/test-bot`, which runs a full simulation per call and is currently an unthrottled compute amplifier.
- **Infrastructure:** MongoDB authentication enabled and the port unpublished; secrets injected, never in compose literals.

---

## 7. Matchmaking and Bot Fallback

### 7.1 Redis structures

- `mm:queue:<mode>` — sorted set, score = player ELO, member = player id.
- `mm:ticket:<player_id>` — hash: `enqueued_at`, `mode`, `bot_version_id`, `state`.
- `mm:leader` — lock ensuring exactly one pairing worker acts per tick.

### 7.2 Pairing loop (~1 s tick)

1. For each waiting ticket, search the band `[elo − w, elo + w]` where `w = base_band + growth × waited_seconds`, capped at `max_band`.
2. Apply jitter to candidate ordering so identical ratings do not deterministically pair the same opponents repeatedly (Battlecode does the same).
3. Fill up to the mode's grid size (heads-up = 2; grand prix = up to 10).
4. **At `waited_seconds > bot_fallback_seconds` (default 25 s), fill remaining slots with house bots.** This is the colonist.io behaviour requested.

### 7.3 House bot ladder

`backend/engine/bots.py` already ships built-in strategies. Rate them by running an offline round-robin to assign each a calibrated ELO, then select the bot nearest the waiting player's rating.

Bot-assisted matches are **ranked at a reduced K-factor** and flagged in both the replay and the UI, so the ladder stays meaningful without punishing players for queueing at quiet hours. (Tunable: set the factor to zero to make them unranked.)

### 7.4 ELO

Extend the existing `backend/season/elo.py`:

- Provisional period: elevated K for a player's first 10 rated matches.
- Multi-car races resolve to a rating update over finishing order rather than a single pairwise comparison.
- Updates applied atomically via `findOneAndUpdate` with optimistic versioning, so concurrent match completions cannot interleave and lose an update. (Battlecode achieves this with row-level locking; this is the Mongo equivalent.)

---

## 8. Replay Format

Current lap snapshots are stored as raw JSON on the race document (`backend/db/crud.py:105-117`) with no compression. Cost is dominated by belief cross-terms, which scale as cars² × laps: roughly 854 scalars per lap at 10 cars, ~48,700 for a 57-lap race.

**Design:**

- Replay = header (the manifest) + ordered per-lap frames, encoded with msgpack, compressed with zstd.
- Stored in object storage keyed by `match_id`; Mongo keeps only the manifest, result summary, replay pointer, and replay sha256.
- Frames carry full state initially. Belief fields are derivable and are the dominant cost, so delta-encoding or on-demand recomputation is the first optimization if measurements justify it — **a task in Phase 1 measures real compressed sizes before optimizing.**
- The format is versioned by `ruleset_version` so the client can refuse to render frames it does not understand.

---

## 9. Client

- **`/replay/[matchId]`** — fetches and decodes the replay, with scrub bar, play/pause, speed control, and a per-lap inspector. `TrackMap.tsx` already interpolates car positions from discrete lap snapshots, which is exactly the replay frame shape, so it is reused rather than rewritten.
- **Queue UI** — "Find Match" → ticket state → live search feedback ("widening… 23 s") → resolution showing either the matched opponent or the house bot substituted in.
- **Editor intelligence** — ship a `.pyi` type stub for the bot API and register a Monaco completion provider against it, replacing the pasted comment block at `StrategyEditor.tsx:24-56`. Map runner tracebacks back to editor line numbers as inline markers.
- **Persistence** — localStorage draft autosave plus server-side bot versioning, so refreshing no longer discards work.
- **Spectating** — any completed match is publicly replayable by URL; live spectating becomes "watch the replay as it lands."
- **Housekeeping** — remove the unused PixiJS dependency.

---

## 10. Testing Strategy

The repository currently contains zero tests. In priority order:

1. **Determinism golden tests** — fixed manifests must produce byte-identical replays. Highest value in the suite.
2. **Sandbox containment tests** — a corpus of malicious patterns (attribute traversal, allocation bombs, infinite loops, recursion bombs) must each be rejected or contained, asserted at every layer.
3. **Unit tests** — physics, ELO, belief modelling, weather transitions, including the dict-ordering contract from §5.4.
4. **Integration** — a full match through queue → pairing → worker → replay → ELO update.
5. **Load** — N concurrent matches, verifying no cross-match interference (the failure mode that the shared global RNG causes today).

CI runs 1–4 on every push.

---

## 11. Phases and Acceptance Criteria

| Phase | Scope | Done when |
|---|---|---|
| **0 — Contain** | Fix sandbox builtins and write guard; out-of-process execution with real rlimits; hash API keys; remove key from browser; ownership + admin checks; rate limiting; Mongo auth; secrets | Containment test corpus passes at every layer; no credential readable by page JS |
| **1 — Determinism** | Per-bot seeded RNG; persist manifests; freeze calibration; pin dependencies; replay format + storage; verification job | The same manifest produces byte-identical replays across 100 runs and two machines |
| **2 — Decouple** | Redis-backed state; match job queue; worker process; remove `active_lobbies`; health checks, structured logging, graceful shutdown; first test suite + CI | Two API replicas serve all traffic correctly; a worker restart mid-match loses no match |
| **3 — Matchmaking** | Queue, banded pairing with jitter, house bot ladder + fallback, ranked/casual, atomic ELO | A solo player is always racing within 25 s; ratings survive concurrent completions |
| **4 — Client** | Replay player with scrubbing, queue UI, Monaco stubs + autocomplete, draft autosave, bot versioning, spectating | A match can be found, watched, scrubbed, and shared by URL |
| **5 — Ship** | Deploy, observability, onboarding, tournaments | Public launch |

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Sandbox escape despite layering | Platform-level microVM isolation means an interpreter escape yields an ephemeral VM, not the API host |
| Determinism silently regresses | Production sampling job re-verifies real matches continuously, not just CI |
| Per-match sandbox cost at scale | One invocation per match (not per lap per car) keeps cost proportional to matches; measure in Phase 1 |
| Replay size growth | Measure before optimizing; delta-encoding of belief fields held in reserve |
| Migrating live data | Existing races predate manifests and cannot be replayed; mark them `legacy` and exclude from verification |
| Scope | Phases gate each other; 0 and 1 are prerequisites for any public exposure |

---

## 13. Open Questions

1. **Grid size for ranked play** — heads-up (2 cars) is simplest to rate and reason about; larger grids are more faithful to F1. Recommend launching heads-up ranked with larger grids as casual.
2. **Bot-assisted match K-factor** — recommend K/2; needs a value.
3. **Replay retention** — indefinite for ranked, or time-boxed for casual to bound storage cost.
