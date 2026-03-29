"""Bayesian belief model for rival estimation.

Each car maintains beliefs about rival tyre age and compound,
updated each lap based on observed lap time deltas. Uses compound-specific
degradation rates and weather-aware observation correction.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# Compound-specific degradation estimates (s/lap) for belief inference
COMPOUND_DEG_RATES = {
    "SOFT": 0.10,
    "MEDIUM": 0.065,
    "HARD": 0.045,
    "INTERMEDIATE": 0.05,
    "WET": 0.04,
}

# Weather correction: wet laps are naturally slower, don't confuse with tyre deg
WEATHER_LAP_CORRECTION = {
    "dry": 0.0,
    "damp": 3.0,
    "wet": 8.0,
    "drying": 1.5,
}

# Typical stint lengths by compound (used for pit probability)
DEFAULT_TYPICAL_STINTS = {
    "SOFT": 15, "MEDIUM": 25, "HARD": 35,
    "INTERMEDIATE": 20, "WET": 15,
}


@dataclass
class RivalBelief:
    """Belief state about a single rival."""
    estimated_tyre_age: float = 1.0
    estimated_compound: str = "MEDIUM"
    pit_probability_next_5_laps: float = 0.1
    confidence: float = 0.5
    # Enhanced fields
    estimated_deg_rate: float = 0.065  # Learned degradation rate for this rival
    undercut_viable: bool = False
    undercut_gain: float = 0.0
    optimal_pit_in: int = 0  # Estimated laps until rival pits
    observation_count: int = 0

    def reset(self, compound: str):
        """Reset beliefs after observing a rival pit stop."""
        self.estimated_tyre_age = 0.0
        self.estimated_compound = compound
        self.pit_probability_next_5_laps = 0.0
        # After a pit: we know the compound but haven't observed pace yet
        self.confidence = 0.4
        self.estimated_deg_rate = COMPOUND_DEG_RATES.get(compound, 0.065)
        self.undercut_viable = False
        self.undercut_gain = 0.0
        self.optimal_pit_in = 0
        self.observation_count = 0


class BeliefModel:
    """Bayesian belief tracker for one car's view of all rivals."""

    def __init__(self, typical_stints: Optional[Dict[str, int]] = None):
        self.typical_stints = typical_stints or dict(DEFAULT_TYPICAL_STINTS)
        self.beliefs: Dict[str, RivalBelief] = {}
        # Track observed deltas for compound identification
        self._delta_history: Dict[str, List[float]] = {}

    def get_belief(self, rival_id: str) -> RivalBelief:
        """Get current belief about a rival, creating if needed."""
        if rival_id not in self.beliefs:
            self.beliefs[rival_id] = RivalBelief()
            self._delta_history[rival_id] = []
        return self.beliefs[rival_id]

    def update(
        self,
        rival_id: str,
        observed_lap_time: float,
        expected_fresh_pace: float,
        rival_pitted: bool = False,
        pit_compound: Optional[str] = None,
        weather: str = "dry",
        safety_car: bool = False,
    ):
        """Update beliefs about a rival based on observed lap time.

        Args:
            rival_id: The rival car ID
            observed_lap_time: Their observed lap time this lap
            expected_fresh_pace: Expected lap time on fresh tyres
            rival_pitted: Whether they pitted this lap
            pit_compound: If they pitted, what compound they took
            weather: Current weather condition
            safety_car: Whether safety car is out
        """
        belief = self.get_belief(rival_id)

        if rival_pitted and pit_compound:
            belief.reset(pit_compound)
            self._delta_history[rival_id] = []
            return

        # Increment estimated tyre age
        belief.estimated_tyre_age += 1.0
        belief.observation_count += 1

        # Skip lap time inference under safety car (laps are artificially slow)
        if safety_car:
            self._update_pit_probability(belief)
            return

        # Weather-corrected delta
        weather_correction = WEATHER_LAP_CORRECTION.get(weather, 0.0)
        delta = observed_lap_time - expected_fresh_pace - weather_correction

        if delta > 0:
            # Use compound-specific deg rate for more accurate age inference
            deg_rate = COMPOUND_DEG_RATES.get(belief.estimated_compound, 0.065)
            implied_age = delta / max(deg_rate, 0.02)

            # Bayesian blend with adaptive learning rate
            # More observations → trust observations more
            alpha = min(0.5, 0.2 + belief.observation_count * 0.02)
            belief.estimated_tyre_age = (
                (1 - alpha) * belief.estimated_tyre_age + alpha * implied_age
            )
            # Confidence grows faster early, slows as it approaches cap
            gain = 0.04 / (1.0 + belief.observation_count * 0.1)
            belief.confidence = min(0.98, belief.confidence + gain)

            # Track delta for compound identification
            history = self._delta_history.setdefault(rival_id, [])
            history.append(delta)

            # Update estimated degradation rate from recent observations
            if len(history) >= 3:
                recent = history[-5:]
                # Degradation rate ≈ how much delta increases per lap
                if len(recent) >= 2:
                    slope = (recent[-1] - recent[0]) / max(len(recent) - 1, 1)
                    # Blend with compound prior
                    belief.estimated_deg_rate = (
                        0.7 * belief.estimated_deg_rate + 0.3 * max(0.01, slope)
                    )

                # Compound inference from deg rate
                self._infer_compound(belief)
        else:
            # Faster than expected — maybe fresher tyres than estimated
            belief.confidence = max(0.1, belief.confidence - 0.05)

        # Update pit probability
        self._update_pit_probability(belief)

    def _infer_compound(self, belief: RivalBelief):
        """Infer likely compound from observed degradation rate."""
        rate = belief.estimated_deg_rate
        best_compound = belief.estimated_compound
        best_diff = float("inf")

        for compound, expected_rate in COMPOUND_DEG_RATES.items():
            if compound in ("INTERMEDIATE", "WET"):
                continue  # Skip wet compounds for dry-weather inference
            diff = abs(rate - expected_rate)
            if diff < best_diff:
                best_diff = diff
                best_compound = compound

        # Only update if confident enough
        if belief.confidence > 0.4:
            belief.estimated_compound = best_compound

    def _update_pit_probability(self, belief: RivalBelief):
        """Update pit probability using logistic model."""
        typical = self.typical_stints.get(belief.estimated_compound, 20)
        age = belief.estimated_tyre_age

        # Logistic: P(pit) = 1 / (1 + exp(-(age - typical) / scale))
        scale = 3.0
        logistic_input = (age - typical) / scale
        logistic_input = max(-10, min(10, logistic_input))
        single_lap_prob = 1.0 / (1.0 + math.exp(-logistic_input))

        # Probability of pitting in next 5 laps
        belief.pit_probability_next_5_laps = 1.0 - (1.0 - single_lap_prob) ** 5

        # Estimate laps until they pit (expected value)
        if single_lap_prob > 0.01:
            belief.optimal_pit_in = max(0, int(round(1.0 / single_lap_prob)))
        else:
            belief.optimal_pit_in = 99

    def update_undercut_info(
        self,
        rival_id: str,
        undercut_viable: bool,
        undercut_gain: float,
    ):
        """Update undercut opportunity info for a rival."""
        belief = self.get_belief(rival_id)
        belief.undercut_viable = undercut_viable
        belief.undercut_gain = round(undercut_gain, 2)

    def to_dict(self) -> Dict[str, Dict]:
        """Export beliefs as a plain dict for inclusion in RaceState."""
        return {
            rival_id: {
                "estimated_tyre_age": round(b.estimated_tyre_age, 1),
                "estimated_compound": b.estimated_compound,
                "pit_probability_next_5_laps": round(b.pit_probability_next_5_laps, 3),
                "confidence": round(b.confidence, 3),
                "estimated_deg_rate": round(b.estimated_deg_rate, 4),
                "undercut_viable": b.undercut_viable,
                "undercut_gain": b.undercut_gain,
                "optimal_pit_in": b.optimal_pit_in,
            }
            for rival_id, b in self.beliefs.items()
        }
