# PIT WALL — F1 Algorithmic Race Strategy Game

Write strategy bot functions that race each other in a physics-accurate F1 simulation powered by real telemetry data from FastF1.

## Quick Start

### Backend

```bash
cd piwall
python3 -m venv ../.venv
source ../.venv/bin/activate
pip install -r backend/requirements.txt

export MONGODB_URI='mongodb://127.0.0.1:27017/phi1'
export MONGODB_DB='phi1'

# Required for player registration. The backend reads only real environment
# variables — it loads no .env file — so this must be exported here even
# though the Next.js server picks the same value up from frontend/.env
# automatically. The two must be identical; without it, /api/register
# returns 404 to every caller and no new player can be provisioned.
export PROVISIONING_SECRET='<same value as frontend/.env>'   # openssl rand -hex 32

# Run a CLI race with built-in bots
PYTHONPATH=. python backend/engine/cli_runner.py bahrain 42

# Start the API server
PYTHONPATH=. uvicorn backend.main:app --reload --port 8000
```

### Frontend

```bash
cd piwall/frontend
npm install
cp .env.example .env.local
npm run dev
# Open http://localhost:3000
```

### Docker (full stack with MongoDB)

```bash
cd piwall
cp .env.example .env      # fill in secrets before starting
docker compose up --build
# Backend: http://localhost:8000
# Frontend: http://localhost:3000
```

## Upgrading an existing database — required before first start

API keys are stored hashed. A database created before that change holds
plaintext `api_key` fields, and nothing migrates them automatically: startup
drops the stale `api_key_1` index and lookups query only `api_key_hash`, so
**every existing player gets 401 until the migration has run**. Run it once,
against the same database the backend will use, before starting the new
backend:

```bash
cd piwall
PYTHONPATH=. MONGODB_URI='mongodb://…' python scripts/migrate_hash_api_keys.py
# migrated N player(s), 0 failure(s)
```

Then rotate. Every key that existed before this change was stored in plaintext
in MongoDB *and* left in each player's browser localStorage, so it has to be
treated as disclosed — hashing it at rest does not un-leak it:

```bash
PYTHONPATH=. MONGODB_URI='mongodb://…' python scripts/migrate_hash_api_keys.py --rotate
# rotated N player(s), 0 failure(s)
# re-linked M web profile(s); K player(s) have no profile in this database …
```

Rotation rewrites `playerProfiles.backendApiKey` for each web account, so
signed-in users are carried across with no action from them. Any player
reported as having no profile — an API-only account, or one whose profile
lives in a different database — must be issued a new key by hand.

Both commands are re-runnable and exit non-zero if any document failed. A
fresh database needs neither.

## Architecture

- **Backend**: Python (FastAPI), FastF1 for real F1 telemetry, MongoDB Atlas or local MongoDB
- **Frontend**: Next.js 14 (App Router), Tailwind CSS, Monaco Editor, NextAuth
- **Realtime**: WebSockets for live race broadcasting
- **Accounts**: Google OAuth or email/password, Mongo-backed submissions and race history
- **Simulation**: Physics-accurate lap time model calibrated from 2024 F1 data

## Tracks

| Track | Laps | Pit Loss | Character |
|-------|------|----------|-----------|
| Bahrain | 57 | 22.7s | High degradation, 2-stop viable |
| Monaco | 78 | 18.7s | No overtaking, strategy is everything |
| Monza | 53 | 24.7s | Low deg, 1-stop dominant |
| Spa | 44 | 17.3s | Variable weather, SC prone |
| Silverstone | 52 | 20.1s | Medium deg, tyre sensitive |
| Suzuka | 53 | 20.3s | Technical, compound choice critical |

## Built-in Bots

- **VEL-01**: Greedy threshold (pits when degradation > 2.2s)
- **NXS-07**: Undercut hunter (monitors gap + rival tyre age)
- **WXP-23**: Weather prophet (holds tyres for SC windows)
- **EQL-44**: Nash equilibrium (integrates full deg curve)
- **AGR-33**: Aggressive 2-stop (fixed pit windows)

## Writing a Strategy Bot

```python
def my_strategy(state, my_car):
    remaining = state.total_laps - state.lap

    if my_car.tyre_age > 20 and remaining > 5:
        return {"pit": True, "compound": "HARD"}

    return {"pit": False, "compound": my_car.compound}
```

See `/strategy` in the web app for full type definitions and a live test environment.
