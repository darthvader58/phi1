"""Lap time physics model for PIT WALL.

Core formula:
    T(lap) = T_base(track) + delta_tyre(c, age) + delta_fuel(fuel_kg)
           + delta_weather(wx) + delta_traffic + delta_drs + epsilon

Where:
    delta_tyre = alpha + k * age^e  (from calibration)
    delta_fuel = 0.032 * fuel_kg
    epsilon ~ N(0, 0.15) truncated to [-0.5, 0.5]
"""

import math
import random
from dataclasses import dataclass
from typing import Optional

import numpy as np


# Real F1 constant: seconds per kg of fuel
FUEL_EFFECT_PER_KG = 0.032

# Lap time noise
LAP_NOISE_STDDEV = 0.15
LAP_NOISE_CLAMP = 0.5

# Traffic penalty when stuck behind a slower car
TRAFFIC_PENALTY = 0.3  # seconds per lap

# DRS time gain when available and activated
DRS_GAIN_PER_ZONE = 0.22  # seconds per DRS zone

# DNF probability per lap per car
DNF_PROB_DRY = 0.004
DNF_PROB_WET = 0.006

# Safety car field compression factor
SC_GAP_COMPRESSION = 0.82

# Wet weather lap time penalties
WEATHER_PENALTIES = {
    "dry": 0.0,
    "damp": 2.5,
    "wet": 7.0,
    "drying": 1.5,
}

# Wrong tyre penalty (dry tyres in wet, wet tyres in dry)
WRONG_TYRE_PENALTY = {
    # (weather, compound) -> additional penalty
    ("wet", "SOFT"): 15.0,
    ("wet", "MEDIUM"): 15.0,
    ("wet", "HARD"): 15.0,
    ("damp", "SOFT"): 5.0,
    ("damp", "MEDIUM"): 4.0,
    ("damp", "HARD"): 3.5,
    ("dry", "INTERMEDIATE"): 4.0,
    ("dry", "WET"): 8.0,
    ("drying", "WET"): 3.0,
}


@dataclass
class TyreModel:
    """Tyre degradation parameters for one compound at one track."""
    compound: str
    alpha: float
    k: float
    e: float
    base_lap_time: float

    def degradation(self, age: int) -> float:
        """Calculate tyre degradation delta at given age."""
        return self.alpha + self.k * math.pow(max(age, 1), self.e)

    def lap_time_tyre_only(self, age: int) -> float:
        """Base + tyre deg (no fuel, no weather)."""
        return self.base_lap_time + self.degradation(age)


@dataclass
class TrackPhysics:
    """Physics constants for a specific track."""
    name: str
    base_lap_time: float
    pit_loss_seconds: float
    total_laps: int
    drs_zones: int
    overtake_difficulty: float  # 0-1
    fuel_load_kg: float
    tyre_models: dict  # compound -> TyreModel

    @property
    def fuel_per_lap(self) -> float:
        return self.fuel_load_kg / self.total_laps


def compute_lap_time(
    track: TrackPhysics,
    compound: str,
    tyre_age: int,
    lap_number: int,
    weather: str,
    is_safety_car: bool,
    gap_to_car_ahead: Optional[float],
    drs_available: bool,
    rng: random.Random,
) -> float:
    """Compute a single lap time with all physics effects.

    Returns the lap time in seconds.
    """
    # Get tyre model (fall back to base if compound not calibrated)
    tyre_model = track.tyre_models.get(compound)
    if tyre_model is None:
        # Fallback: use base lap time + generic degradation
        delta_tyre = 0.5 + 0.04 * tyre_age
        base = track.base_lap_time
    else:
        delta_tyre = tyre_model.degradation(tyre_age)
        base = tyre_model.base_lap_time

    # Fuel effect: fuel decreases linearly over the race
    fuel_kg = max(0, track.fuel_load_kg - (lap_number - 1) * track.fuel_per_lap)
    delta_fuel = FUEL_EFFECT_PER_KG * fuel_kg

    # Weather effect
    delta_weather = WEATHER_PENALTIES.get(weather, 0.0)

    # Wrong tyre penalty
    wrong_penalty = WRONG_TYRE_PENALTY.get((weather, compound), 0.0)

    # DRS effect
    delta_drs = 0.0
    if drs_available and weather == "dry" and not is_safety_car:
        delta_drs = -DRS_GAIN_PER_ZONE * track.drs_zones

    # Traffic effect
    delta_traffic = 0.0
    if gap_to_car_ahead is not None and 0.0 < gap_to_car_ahead < 1.5 and not is_safety_car:
        # Dirty air effect, scaled by how close
        delta_traffic = TRAFFIC_PENALTY * (1.0 - gap_to_car_ahead / 1.5)

    # Random noise (truncated normal)
    noise = rng.gauss(0, LAP_NOISE_STDDEV)
    noise = max(-LAP_NOISE_CLAMP, min(LAP_NOISE_CLAMP, noise))

    lap_time = base + delta_tyre + delta_fuel + delta_weather + wrong_penalty + delta_drs + delta_traffic + noise

    # Safety car: everyone laps at SC pace (base + ~10s)
    if is_safety_car:
        sc_pace = base + delta_fuel + 10.0 + rng.uniform(-0.1, 0.1)
        lap_time = max(lap_time, sc_pace)

    return lap_time


def compute_overtake_probability(
    gap: float,
    track_overtake_difficulty: float,
    drs_available: bool,
    weather: str,
    tyre_age_diff: int,
) -> float:
    """Compute probability of an overtake attempt succeeding.

    Args:
        gap: Time gap to car ahead (seconds, should be < ~1.5s)
        track_overtake_difficulty: 0 (easy) to 1 (very hard)
        drs_available: Whether DRS is available
        weather: Current weather
        tyre_age_diff: attacker_tyre_age - defender_tyre_age (negative = attacker has fresher tyres)
    """
    if gap > 1.5:
        return 0.0

    # Base probability from gap (closer = higher chance)
    base_prob = max(0, 0.4 * (1.0 - gap / 1.5))

    # Track difficulty modifier
    track_mod = 1.0 - 0.7 * track_overtake_difficulty

    # DRS boost
    drs_mod = 1.4 if drs_available else 1.0

    # Weather: wet conditions make overtaking harder
    weather_mod = {"dry": 1.0, "damp": 0.7, "wet": 0.5, "drying": 0.8}.get(weather, 1.0)

    # Tyre advantage: fresher tyres help overtaking
    tyre_mod = 1.0
    if tyre_age_diff < -5:
        tyre_mod = 1.3  # Attacker has much fresher tyres
    elif tyre_age_diff < 0:
        tyre_mod = 1.1

    prob = base_prob * track_mod * drs_mod * weather_mod * tyre_mod
    return min(0.85, max(0.0, prob))


def check_dnf(weather: str, rng: random.Random) -> bool:
    """Check if a car DNFs this lap."""
    prob = DNF_PROB_WET if weather in ("wet", "damp") else DNF_PROB_DRY
    return rng.random() < prob


def compute_pit_stop_time(
    track: TrackPhysics,
    weather: str,
) -> float:
    """Total time lost during a pit stop (pit in + pit out vs normal lap)."""
    base_loss = track.pit_loss_seconds
    # Pit stops slightly slower in wet
    if weather == "wet":
        base_loss += 2.0
    elif weather == "damp":
        base_loss += 1.0
    return base_loss
