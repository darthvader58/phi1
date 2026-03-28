"""Game theory layer for PIT WALL.

Implements:
1. Nash pit window: integral-based optimal pit timing
2. Undercut detector: identifies undercut opportunities
3. Strategy evaluation helpers
"""

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .physics import TyreModel, TrackPhysics


@dataclass
class PitWindowResult:
    """Result of Nash pit window calculation."""
    should_pit: bool
    delta_ev: float       # Expected value gain from pitting now
    pit_delta: float      # Time cost of pitting
    remaining_deg_cost: float  # Projected deg cost if staying out
    optimal_lap: int      # Estimated optimal pit lap


@dataclass
class UndercutOpportunity:
    """An identified undercut opportunity against a specific rival."""
    rival_id: str
    gap_to_rival: float
    rival_estimated_tyre_age: float
    undercut_gain: float  # Estimated time gain from undercutting
    is_viable: bool


def compute_nash_pit_window(
    current_tyre: TyreModel,
    new_tyre: TyreModel,
    current_age: int,
    remaining_laps: int,
    pit_delta: float,
) -> PitWindowResult:
    """Compute whether pitting now is Nash-optimal.

    Compares the integrated degradation cost of staying out vs pitting:
    Sum over k=1..remaining_laps of [deg(current, age+k) - deg(new, k)]
    If this sum > pit_delta, pitting is beneficial.
    """
    if remaining_laps <= 1:
        return PitWindowResult(
            should_pit=False, delta_ev=0, pit_delta=pit_delta,
            remaining_deg_cost=0, optimal_lap=0,
        )

    # Cost of staying out on current tyres
    stay_cost = sum(
        current_tyre.degradation(current_age + k)
        for k in range(1, remaining_laps + 1)
    )

    # Cost on fresh tyres after pitting
    pit_cost = pit_delta + sum(
        new_tyre.degradation(k)
        for k in range(1, remaining_laps + 1)
    )

    delta_ev = stay_cost - pit_cost

    # Find optimal pit lap (scan forward to find when pitting becomes beneficial)
    optimal_lap = 0
    best_delta = delta_ev
    for future_offset in range(1, min(remaining_laps, 20)):
        future_remaining = remaining_laps - future_offset
        if future_remaining <= 0:
            break
        future_stay = sum(
            current_tyre.degradation(current_age + future_offset + k)
            for k in range(1, future_remaining + 1)
        )
        future_pit = pit_delta + sum(
            new_tyre.degradation(k)
            for k in range(1, future_remaining + 1)
        )
        future_delta = future_stay - future_pit
        if future_delta > best_delta:
            best_delta = future_delta
            optimal_lap = future_offset

    return PitWindowResult(
        should_pit=delta_ev > 0,
        delta_ev=round(delta_ev, 2),
        pit_delta=pit_delta,
        remaining_deg_cost=round(stay_cost, 2),
        optimal_lap=optimal_lap,
    )


def detect_undercut(
    my_gap_to_rival: float,
    my_tyre: TyreModel,
    my_tyre_age: int,
    rival_estimated_tyre_age: float,
    rival_estimated_compound: str,
    new_tyre: TyreModel,
    pit_delta: float,
    rival_id: str,
) -> UndercutOpportunity:
    """Detect if an undercut opportunity exists against a rival.

    An undercut works when:
    1. We're close behind a rival (gap < pit_delta)
    2. We can pit and come out with enough pace advantage
       to overcome the pit loss before they respond
    3. Fresh tyre pace gain over 2-3 laps > gap + pit_delta_diff

    Args:
        my_gap_to_rival: Gap to the rival (positive = we're behind)
        my_tyre: Our current tyre model
        my_tyre_age: Our current tyre age
        rival_estimated_tyre_age: Estimated rival tyre age
        rival_estimated_compound: Estimated rival compound
        new_tyre: Tyre model we'd switch to
        pit_delta: Pit stop time loss
        rival_id: ID of the rival
    """
    # Undercut window: 3 laps of fresh-tyre advantage
    undercut_laps = 3

    # Our pace on old tyres for next 3 laps
    old_pace = sum(my_tyre.degradation(my_tyre_age + k) for k in range(1, undercut_laps + 1))

    # Our pace on fresh tyres for next 3 laps
    fresh_pace = sum(new_tyre.degradation(k) for k in range(1, undercut_laps + 1))

    # Pace gain from pitting
    pace_gain = old_pace - fresh_pace

    # Net undercut gain: pace advantage minus the gap we need to overcome
    # When we pit, we lose pit_delta but gain pace_gain over the undercut window
    # We emerge ahead if pace_gain > (gap + extra time lost in pit)
    # But the rival also loses time from their degrading tyres
    rival_deg_next_3 = sum(
        my_tyre.degradation(int(rival_estimated_tyre_age) + k)
        for k in range(1, undercut_laps + 1)
    )

    undercut_gain = pace_gain - pit_delta + rival_deg_next_3 - my_gap_to_rival

    is_viable = (
        undercut_gain > 0
        and my_gap_to_rival < pit_delta + 3.0  # Must be close enough
        and my_gap_to_rival > 0  # Must be behind
    )

    return UndercutOpportunity(
        rival_id=rival_id,
        gap_to_rival=round(my_gap_to_rival, 2),
        rival_estimated_tyre_age=rival_estimated_tyre_age,
        undercut_gain=round(undercut_gain, 2),
        is_viable=is_viable,
    )


def evaluate_compound_choice(
    track: TrackPhysics,
    remaining_laps: int,
    compounds_used: List[str],
) -> str:
    """Suggest the best compound for a pit stop.

    Considers remaining laps and compound rule (must use 2 different).
    """
    available = list(track.tyre_models.keys())
    if not available:
        return "MEDIUM"

    best_compound = available[0]
    best_cost = float("inf")

    for compound in available:
        # Skip wet compounds in this evaluation
        if compound in ("INTERMEDIATE", "WET"):
            continue

        model = track.tyre_models[compound]
        # Total degradation cost over remaining laps
        cost = sum(model.degradation(k) for k in range(1, remaining_laps + 1))

        # Bonus for satisfying compound rule
        if compound not in compounds_used and len(compounds_used) < 2:
            cost -= 5.0  # Incentivize using a different compound

        if cost < best_cost:
            best_cost = cost
            best_compound = compound

    return best_compound
