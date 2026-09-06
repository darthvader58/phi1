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
