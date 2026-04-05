# PIT WALL — F1 Algorithmic Race Strategy Game

Write strategy bot functions that race each other in a physics-accurate F1 simulation powered by real telemetry data from FastF1.

## Quick Start

### Backend (standalone, SQLite)

```bash
cd piwall
pip install -r backend/requirements.txt

# Run a CLI race with built-in bots
PYTHONPATH=. python backend/engine/cli_runner.py bahrain 42

# Start the API server
PYTHONPATH=. uvicorn backend.main:app --reload --port 8000
```

### Frontend

```bash
cd piwall/frontend
npm install
npm run dev
# Open http://localhost:3000
```

### Docker (full stack with Postgres + Redis)

```bash
cd piwall
docker-compose up --build
# Backend: http://localhost:8000
# Frontend: http://localhost:3000
```

## Architecture

- **Backend**: Python (FastAPI), FastF1 for real F1 telemetry, SQLAlchemy (Postgres/SQLite)
- **Frontend**: Next.js 14 (App Router), Tailwind CSS, Canvas API, Monaco Editor
- **Realtime**: WebSockets for live race broadcasting
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
