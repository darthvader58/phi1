"""Weather Markov chain system for PIT WALL.

Handles weather state transitions each lap using track-specific
transition probabilities.
"""

import random
from typing import Dict, List, Optional, Tuple


# Default transition matrix if track doesn't specify one
DEFAULT_TRANSITIONS = {
    "dry": {"dry": 0.92, "damp": 0.07, "wet": 0.01},
    "damp": {"dry": 0.20, "damp": 0.52, "wet": 0.28},
    "wet": {"dry": 0.02, "damp": 0.18, "wet": 0.80},
    "drying": {"dry": 0.35, "damp": 0.45, "wet": 0.05, "drying": 0.15},
}

# Track temperature ranges by weather
TRACK_TEMP_RANGES = {
    "dry": (28.0, 50.0),
    "damp": (18.0, 30.0),
    "wet": (14.0, 25.0),
    "drying": (20.0, 35.0),
}


class WeatherEngine:
    """Manages weather state transitions using a Markov chain."""

    def __init__(
        self,
        transitions: Optional[Dict[str, Dict[str, float]]] = None,
        initial_weather: str = "dry",
        initial_temp: float = 35.0,
        rng: Optional[random.Random] = None,
    ):
        self.transitions = transitions or DEFAULT_TRANSITIONS
        self.weather = initial_weather
        self.track_temp = initial_temp
        self.rng = rng or random.Random()
        self.history: List[str] = [initial_weather]

    def step(self) -> str:
        """Advance weather by one lap. Returns new weather state."""
        probs = self.transitions.get(self.weather, DEFAULT_TRANSITIONS["dry"])

        states = list(probs.keys())
        weights = list(probs.values())

        # Normalize weights (safety)
        total = sum(weights)
        if total > 0:
            weights = [w / total for w in weights]

        # Transition
        r = self.rng.random()
        cumulative = 0.0
        new_weather = self.weather
        for state, weight in zip(states, weights):
            cumulative += weight
            if r <= cumulative:
                new_weather = state
                break

        # Special: when transitioning from wet to dry, go through drying
        if self.weather == "wet" and new_weather == "dry":
            new_weather = "drying"

        self.weather = new_weather
        self.history.append(new_weather)

        # Update track temperature
        self._update_temp()

        return self.weather

    def _update_temp(self):
        """Gradually adjust track temperature based on weather."""
        lo, hi = TRACK_TEMP_RANGES.get(self.weather, (25.0, 40.0))
        target = (lo + hi) / 2

        # Smooth transition (exponential moving average)
        self.track_temp += 0.15 * (target - self.track_temp)
        # Add small noise
        self.track_temp += self.rng.gauss(0, 0.3)
        self.track_temp = max(lo - 5, min(hi + 5, self.track_temp))

    def get_safety_car_probability(
        self,
        base_dry: float = 0.07,
        base_wet: float = 0.15,
    ) -> float:
        """Get per-lap safety car probability based on current weather."""
        if self.weather in ("wet",):
            return base_wet
        elif self.weather in ("damp", "drying"):
            return (base_dry + base_wet) / 2
        return base_dry

    def check_safety_car(
        self,
        base_dry: float,
        base_wet: float,
    ) -> bool:
        """Roll for safety car this lap."""
        prob = self.get_safety_car_probability(base_dry, base_wet)
        return self.rng.random() < prob
