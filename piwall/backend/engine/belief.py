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
        """Update beliefs about a rival based on observed lap time."""
        belief = self.get_belief(rival_id)

        if rival_pitted and pit_compound:
            belief.reset(pit_compound)
            self._delta_history[rival_id] = []
            return

        # Age is purely a counter — increment each lap, reset on pit
        belief.estimated_tyre_age += 1.0
        belief.observation_count += 1

        # Skip lap time inference under safety car (laps are artificially slow)
        if safety_car:
            self._update_pit_probability(belief)
            return

        # Weather-corrected delta (fuel already included in expected_fresh_pace)
        weather_correction = WEATHER_LAP_CORRECTION.get(weather, 0.0)
        delta = observed_lap_time - expected_fresh_pace - weather_correction

        if delta > 0:
            # Track delta for compound identification
            history = self._delta_history.setdefault(rival_id, [])
            history.append(delta)

            # Infer compound from degradation trend (need 5+ observations)
            if len(history) >= 5:
                self._infer_compound_from_trend(belief, history)

            # Confidence grows with observations
            gain = 0.04 / (1.0 + belief.observation_count * 0.1)
            belief.confidence = min(0.98, belief.confidence + gain)
        else:
            # Faster than expected — could indicate a pit we missed
            # If consistently faster, reduce confidence
            belief.confidence = max(0.1, belief.confidence - 0.03)

            # Sudden large improvement may indicate undetected pit stop
            if delta < -1.5 and belief.estimated_tyre_age > 5:
                belief.estimated_tyre_age = 1.0
                belief.confidence = 0.3
                self._delta_history[rival_id] = []

        # Update pit probability
        self._update_pit_probability(belief)

    def _infer_compound_from_trend(self, belief: RivalBelief, history: List[float]):
        """Infer compound from the per-lap increase in lap time delta.

        COMPOUND_DEG_RATES represent how much the delta grows per lap.
        SOFT degrades ~0.10s more each lap, MEDIUM ~0.065s, HARD ~0.045s.
        We measure the actual per-lap increase and match to the closest compound.
        """
        if len(history) < 5:
            return

        # Calculate per-lap delta differences (how much worse each lap vs previous)
        recent = history[-10:]
        if len(recent) < 3:
            return

        lap_diffs = [recent[i] - recent[i - 1] for i in range(1, len(recent))]

        # Median filter to reduce noise from DRS/traffic/etc
        lap_diffs_sorted = sorted(lap_diffs)
        n = len(lap_diffs_sorted)
        median_diff = lap_diffs_sorted[n // 2] if n % 2 == 1 else (
            lap_diffs_sorted[n // 2 - 1] + lap_diffs_sorted[n // 2]) / 2

        # Clamp to reasonable range (negative means improving, cap at 0)
        effective_rate = max(0.01, min(0.20, median_diff))

        # Blend with prior
        belief.estimated_deg_rate = 0.6 * belief.estimated_deg_rate + 0.4 * effective_rate

        # Match deg rate to closest compound
        rate = belief.estimated_deg_rate
        best_compound = belief.estimated_compound
        best_diff = float("inf")

        for compound, expected_rate in COMPOUND_DEG_RATES.items():
            if compound in ("INTERMEDIATE", "WET"):
                continue
            diff = abs(rate - expected_rate)
            if diff < best_diff:
                best_diff = diff
                best_compound = compound

        if belief.confidence > 0.5:
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
