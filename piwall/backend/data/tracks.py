"""Per-track constants for the PIT WALL simulation."""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class TrackConfig:
    name: str
    display_name: str
    country: str
    total_laps: int
    pit_loss_seconds: float  # Time lost entering/exiting pit lane
    drs_zones: int
    base_lap_time: float  # Approximate base lap time in seconds (dry, fresh tyres, mid-fuel)
    safety_car_prob_dry: float  # Per-lap SC probability in dry conditions
    safety_car_prob_wet: float  # Per-lap SC probability in wet conditions
    overtake_difficulty: float  # 0.0 (easy) to 1.0 (very hard)
    fuel_load_kg: float  # Starting fuel load
    # Weather transition probabilities (Markov chain) — track-specific
    weather_transitions: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # Typical stint lengths per compound
    typical_stint: Dict[str, int] = field(default_factory=dict)
    # FastF1 event identifiers
    fastf1_year: int = 2024
    fastf1_event: str = ""


TRACKS: Dict[str, TrackConfig] = {
    "bahrain": TrackConfig(
        name="bahrain",
        display_name="Bahrain International Circuit",
        country="Bahrain",
        total_laps=57,
        pit_loss_seconds=22.0,
        drs_zones=3,
        base_lap_time=92.0,
        safety_car_prob_dry=0.018,
        safety_car_prob_wet=0.045,
        overtake_difficulty=0.3,
        fuel_load_kg=110.0,
        weather_transitions={
            "dry": {"dry": 0.92, "damp": 0.07, "wet": 0.01},
            "damp": {"dry": 0.20, "damp": 0.52, "wet": 0.28},
            "wet": {"dry": 0.02, "damp": 0.18, "wet": 0.80},
            "drying": {"dry": 0.35, "damp": 0.45, "wet": 0.05, "drying": 0.15},
        },
        typical_stint={"SOFT": 15, "MEDIUM": 25, "HARD": 35},
        fastf1_year=2024,
        fastf1_event="Bahrain",
    ),
    "monaco": TrackConfig(
        name="monaco",
        display_name="Circuit de Monaco",
        country="Monaco",
        total_laps=78,
        pit_loss_seconds=24.0,
        drs_zones=1,
        base_lap_time=73.0,
        safety_car_prob_dry=0.02,
        safety_car_prob_wet=0.055,
        overtake_difficulty=0.95,
        fuel_load_kg=110.0,
        weather_transitions={
            "dry": {"dry": 0.90, "damp": 0.08, "wet": 0.02},
            "damp": {"dry": 0.15, "damp": 0.55, "wet": 0.30},
            "wet": {"dry": 0.03, "damp": 0.17, "wet": 0.80},
            "drying": {"dry": 0.30, "damp": 0.50, "wet": 0.05, "drying": 0.15},
        },
        typical_stint={"SOFT": 25, "MEDIUM": 35, "HARD": 50},
        fastf1_year=2024,
        fastf1_event="Monaco",
    ),
    "monza": TrackConfig(
        name="monza",
        display_name="Autodromo Nazionale Monza",
        country="Italy",
        total_laps=53,
        pit_loss_seconds=25.0,
        drs_zones=2,
        base_lap_time=81.0,
        safety_car_prob_dry=0.015,
        safety_car_prob_wet=0.04,
        overtake_difficulty=0.2,
        fuel_load_kg=110.0,
        weather_transitions={
            "dry": {"dry": 0.91, "damp": 0.07, "wet": 0.02},
            "damp": {"dry": 0.18, "damp": 0.52, "wet": 0.30},
            "wet": {"dry": 0.03, "damp": 0.17, "wet": 0.80},
            "drying": {"dry": 0.32, "damp": 0.48, "wet": 0.05, "drying": 0.15},
        },
        typical_stint={"SOFT": 20, "MEDIUM": 30, "HARD": 40},
        fastf1_year=2024,
        fastf1_event="Monza",
    ),
    "spa": TrackConfig(
        name="spa",
        display_name="Circuit de Spa-Francorchamps",
        country="Belgium",
        total_laps=44,
        pit_loss_seconds=21.0,
        drs_zones=2,
        base_lap_time=106.0,
        safety_car_prob_dry=0.02,
        safety_car_prob_wet=0.05,
        overtake_difficulty=0.3,
        fuel_load_kg=110.0,
        weather_transitions={
            "dry": {"dry": 0.85, "damp": 0.12, "wet": 0.03},
            "damp": {"dry": 0.12, "damp": 0.55, "wet": 0.33},
            "wet": {"dry": 0.02, "damp": 0.15, "wet": 0.83},
            "drying": {"dry": 0.25, "damp": 0.50, "wet": 0.08, "drying": 0.17},
        },
        typical_stint={"SOFT": 12, "MEDIUM": 22, "HARD": 32},
        fastf1_year=2024,
        fastf1_event="Belgium",
    ),
    "silverstone": TrackConfig(
        name="silverstone",
        display_name="Silverstone Circuit",
        country="United Kingdom",
        total_laps=52,
        pit_loss_seconds=21.5,
        drs_zones=2,
        base_lap_time=88.0,
        safety_car_prob_dry=0.018,
        safety_car_prob_wet=0.045,
        overtake_difficulty=0.4,
        fuel_load_kg=110.0,
        weather_transitions={
            "dry": {"dry": 0.87, "damp": 0.10, "wet": 0.03},
            "damp": {"dry": 0.15, "damp": 0.55, "wet": 0.30},
            "wet": {"dry": 0.03, "damp": 0.17, "wet": 0.80},
            "drying": {"dry": 0.30, "damp": 0.48, "wet": 0.07, "drying": 0.15},
        },
        typical_stint={"SOFT": 16, "MEDIUM": 24, "HARD": 34},
        fastf1_year=2024,
        fastf1_event="British Grand Prix",
    ),
    "suzuka": TrackConfig(
        name="suzuka",
        display_name="Suzuka International Racing Course",
        country="Japan",
        total_laps=53,
        pit_loss_seconds=22.5,
        drs_zones=1,
        base_lap_time=91.0,
        safety_car_prob_dry=0.02,
        safety_car_prob_wet=0.05,
        overtake_difficulty=0.55,
        fuel_load_kg=110.0,
        weather_transitions={
            "dry": {"dry": 0.88, "damp": 0.09, "wet": 0.03},
            "damp": {"dry": 0.14, "damp": 0.54, "wet": 0.32},
            "wet": {"dry": 0.02, "damp": 0.16, "wet": 0.82},
            "drying": {"dry": 0.28, "damp": 0.50, "wet": 0.07, "drying": 0.15},
        },
        typical_stint={"SOFT": 14, "MEDIUM": 22, "HARD": 32},
        fastf1_year=2024,
        fastf1_event="Japan",
    ),
}
