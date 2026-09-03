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
