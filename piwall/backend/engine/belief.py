"""Bayesian belief model for rival estimation.

Each car maintains beliefs about rival tyre age and compound,
updated each lap based on observed lap time deltas.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class RivalBelief:
    """Belief state about a single rival."""
    estimated_tyre_age: float = 1.0
    estimated_compound: str = "MEDIUM"
    pit_probability_next_5_laps: float = 0.1
    confidence: float = 0.5

    def reset(self, compound: str):
        """Reset beliefs after observing a rival pit stop."""
        self.estimated_tyre_age = 0.0
        self.estimated_compound = compound
        self.pit_probability_next_5_laps = 0.0
        self.confidence = 0.95


class BeliefModel:
    """Bayesian belief tracker for one car's view of all rivals."""

    def __init__(self, typical_stints: Optional[Dict[str, int]] = None):
        self.typical_stints = typical_stints or {
            "SOFT": 15, "MEDIUM": 25, "HARD": 35,
            "INTERMEDIATE": 20, "WET": 15,
        }
        self.beliefs: Dict[str, RivalBelief] = {}

    def get_belief(self, rival_id: str) -> RivalBelief:
        """Get current belief about a rival, creating if needed."""
        if rival_id not in self.beliefs:
            self.beliefs[rival_id] = RivalBelief()
        return self.beliefs[rival_id]

    def update(
        self,
        rival_id: str,
        observed_lap_time: float,
        expected_fresh_pace: float,
        rival_pitted: bool = False,
        pit_compound: Optional[str] = None,
    ):
        """Update beliefs about a rival based on observed lap time.

        Args:
            rival_id: The rival car ID
            observed_lap_time: Their observed lap time this lap
            expected_fresh_pace: Expected lap time on fresh tyres
            rival_pitted: Whether they pitted this lap
            pit_compound: If they pitted, what compound they took
        """
        belief = self.get_belief(rival_id)

        if rival_pitted and pit_compound:
            belief.reset(pit_compound)
            return

        # Increment estimated tyre age
        belief.estimated_tyre_age += 1.0

        # Update confidence based on lap time observation
        # If lap time is much slower than fresh pace, they're likely on old tyres
        delta = observed_lap_time - expected_fresh_pace
        if delta > 0:
            # Estimate age from delta using simple linear model (~0.06s/lap)
            implied_age = delta / 0.06
            # Bayesian update: blend prior with observation
            alpha = 0.3  # Learning rate
            belief.estimated_tyre_age = (
                (1 - alpha) * belief.estimated_tyre_age + alpha * implied_age
            )
            belief.confidence = min(0.95, belief.confidence + 0.02)
        else:
            # Faster than expected - maybe fresher tyres than we thought
            belief.confidence = max(0.1, belief.confidence - 0.05)

        # Update pit probability using logistic model
        typical = self.typical_stints.get(belief.estimated_compound, 20)
        age = belief.estimated_tyre_age
        # logistic: P(pit) = 1 / (1 + exp(-(age - typical) / scale))
        scale = 3.0
        logistic_input = (age - typical) / scale
        logistic_input = max(-10, min(10, logistic_input))  # Clamp for numerical stability
        single_lap_prob = 1.0 / (1.0 + math.exp(-logistic_input))

        # Probability of pitting in next 5 laps
        belief.pit_probability_next_5_laps = 1.0 - (1.0 - single_lap_prob) ** 5

    def to_dict(self) -> Dict[str, Dict]:
        """Export beliefs as a plain dict for inclusion in RaceState."""
        return {
            rival_id: {
                "estimated_tyre_age": round(b.estimated_tyre_age, 1),
                "estimated_compound": b.estimated_compound,
                "pit_probability_next_5_laps": round(b.pit_probability_next_5_laps, 3),
                "confidence": round(b.confidence, 3),
            }
            for rival_id, b in self.beliefs.items()
        }
