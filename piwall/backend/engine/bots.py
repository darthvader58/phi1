"""Built-in strategy bots for PIT WALL.

Five bot personalities that always fill the field:

VEL-01: Greedy threshold — pits when tyre deg exceeds a fixed threshold
NXS-07: Undercut hunter — uses belief system undercut detection
WXP-23: Weather prophet — holds tyres for weather/SC windows
EQL-44: Nash equilibrium — uses game theory optimal pit window
AGR-33: Aggressive 2-stop — fixed pit windows regardless of conditions
"""

from .race import RaceState, CarState, Decision


def _pick_compound(remaining: int, current: str, pit_count: int) -> str:
    """Helper to pick a compound based on remaining laps and compound rule."""
    if remaining > 25:
        new = "HARD"
    elif remaining > 15:
        new = "MEDIUM"
    else:
        new = "SOFT"
    # Must use different compound on first stop
    if pit_count == 0 and new == current:
        new = "MEDIUM" if current != "MEDIUM" else "HARD"
    return new


def vel_01_strategy(state: RaceState, my_car: CarState) -> Decision:
    """VEL-01: Greedy threshold bot.

    Simple rule: pit when estimated tyre degradation exceeds 2.2s.
    Uses a rough deg model: ~0.08s/lap for soft, ~0.05s/lap for hard.
    """
    deg_rates = {"SOFT": 0.10, "MEDIUM": 0.065, "HARD": 0.045}
    rate = deg_rates.get(my_car.compound, 0.06)
    estimated_deg = rate * my_car.tyre_age

    if estimated_deg > 2.2 and my_car.tyre_age >= 8:
        remaining = state.total_laps - state.lap
        if remaining > 5:
            return Decision(pit=True, compound=_pick_compound(remaining, my_car.compound, my_car.pit_count))

    return Decision(pit=False, compound=my_car.compound)


def nxs_07_strategy(state: RaceState, my_car: CarState) -> Decision:
    """NXS-07: Undercut hunter.

    Uses the belief system's undercut detection to find and exploit
    undercut windows on rivals ahead. Falls back to belief-based
    pit probability monitoring.
    """
    remaining = state.total_laps - state.lap
    if remaining <= 5 or my_car.tyre_age < 5:
        return Decision(pit=False, compound=my_car.compound)

    # Check for viable undercuts from belief data
    best_undercut_gain = 0.0
    has_undercut = False
    for rival in state.cars:
        if rival.car_id == my_car.car_id or rival.retired:
            continue
        if rival.position < my_car.position:
            belief = my_car.beliefs.get(rival.car_id, {})

            # Use the integrated undercut detection
            undercut_viable = belief.get("undercut_viable", False)
            undercut_gain = belief.get("undercut_gain", belief.get("uc_gain", 0))

            if undercut_viable and undercut_gain > best_undercut_gain:
                best_undercut_gain = undercut_gain
                has_undercut = True

            # Fallback: belief-based heuristic
            rival_tyre_age = belief.get("estimated_tyre_age", belief.get("age", 0))
            rival_pit_prob = belief.get("pit_probability_next_5_laps", belief.get("pit_prob", 0))
            gap = my_car.gap_to_leader - rival.gap_to_leader

            if (gap < 5.0 and rival_tyre_age > 12 and
                    my_car.tyre_age > 10 and rival_pit_prob > 0.4):
                has_undercut = True

    if has_undercut and remaining > 8:
        return Decision(pit=True, compound=_pick_compound(remaining, my_car.compound, my_car.pit_count))

    # Fallback: pit on heavy degradation
    if my_car.tyre_age > 25:
        return Decision(pit=True, compound=_pick_compound(remaining, my_car.compound, my_car.pit_count))

    return Decision(pit=False, compound=my_car.compound)


def wxp_23_strategy(state: RaceState, my_car: CarState) -> Decision:
    """WXP-23: Weather prophet.

    Capitalizes on safety car periods and weather changes.
    Delays pit stops to coincide with SC (free pit stop) or weather transitions.
    Also monitors rival beliefs to avoid pitting into traffic.
    """
    remaining = state.total_laps - state.lap

    # Safety car: pit now for cheap stop
    if state.safety_car and my_car.tyre_age >= 8 and remaining > 5:
        return Decision(pit=True, compound=_pick_compound(remaining, my_car.compound, my_car.pit_count))

    # Weather change: switch to appropriate tyres
    if state.weather in ("wet", "damp") and my_car.compound in ("SOFT", "MEDIUM", "HARD"):
        if my_car.tyre_age >= 3:
            return Decision(pit=True, compound="INTERMEDIATE")

    if state.weather == "dry" and my_car.compound == "INTERMEDIATE":
        return Decision(pit=True, compound="MEDIUM" if remaining > 15 else "SOFT")

    # Check if many rivals are about to pit (avoid pitting into traffic)
    rivals_pitting_soon = sum(
        1 for rival in state.cars
        if rival.car_id != my_car.car_id and not rival.retired
        and my_car.beliefs.get(rival.car_id, {}).get("pit_probability_next_5_laps",
             my_car.beliefs.get(rival.car_id, {}).get("pit_prob", 0)) > 0.6
    )

    # If many rivals pitting soon, hold out a bit longer to benefit from clear air
    hold_threshold = 30 if rivals_pitting_soon >= 2 else 25
    if state.weather != "dry":
        hold_threshold = 20

    if my_car.tyre_age > hold_threshold and remaining > 5:
        return Decision(pit=True, compound=_pick_compound(remaining, my_car.compound, my_car.pit_count))

    return Decision(pit=False, compound=my_car.compound)


def eql_44_strategy(state: RaceState, my_car: CarState) -> Decision:
    """EQL-44: Nash equilibrium bot.

    Uses the full game theory pit window calculation:
    Integrates degradation curves to compute expected value of pitting
    now vs later. Also considers rival positions and undercut threats.
    """
    remaining = state.total_laps - state.lap
    if remaining <= 3 or my_car.tyre_age < 5:
        return Decision(pit=False, compound=my_car.compound)

    # Degradation parameters
    deg_rates = {"SOFT": 0.10, "MEDIUM": 0.065, "HARD": 0.045}
    current_rate = deg_rates.get(my_car.compound, 0.06)
    pit_delta = 22.0

    # Integrate remaining deg on current tyres (power law)
    current_cost = sum(
        current_rate * (my_car.tyre_age + k) ** 1.1
        for k in range(1, remaining + 1)
    )

    # Evaluate each alternative compound
    best_alt = None
    best_alt_cost = float("inf")
    for comp, rate in deg_rates.items():
        if comp == my_car.compound and my_car.pit_count == 0:
            continue
        alt_cost = pit_delta + sum(
            rate * k ** 1.1 for k in range(1, remaining + 1)
        )
        if alt_cost < best_alt_cost:
            best_alt_cost = alt_cost
            best_alt = comp

    if best_alt is None:
        return Decision(pit=False, compound=my_car.compound)

    delta_ev = current_cost - best_alt_cost

    if delta_ev > 0:
        # Check if waiting 1-3 laps yields better EV (Nash timing)
        should_pit_now = True
        for offset in range(1, min(4, remaining)):
            future_remaining = remaining - offset
            if future_remaining <= 0:
                break
            future_cost = sum(
                current_rate * (my_car.tyre_age + offset + k) ** 1.1
                for k in range(1, future_remaining + 1)
            )
            future_rate = deg_rates.get(best_alt, 0.06)
            future_alt = pit_delta + sum(
                future_rate * k ** 1.1 for k in range(1, future_remaining + 1)
            )
            future_delta = future_cost - future_alt
            if future_delta > delta_ev * 1.05:
                # Future is significantly better - wait
                should_pit_now = False
                break

        # Also check: is a rival about to undercut us?
        for rival in state.cars:
            if rival.car_id == my_car.car_id or rival.retired:
                continue
            if rival.position > my_car.position:
                belief = my_car.beliefs.get(rival.car_id, {})
                rival_undercut = belief.get("undercut_viable", False)
                if rival_undercut:
                    # Rival may undercut - defensive pit
                    should_pit_now = True
                    break

        if should_pit_now:
            return Decision(pit=True, compound=best_alt)

    return Decision(pit=False, compound=my_car.compound)


def agr_33_strategy(state: RaceState, my_car: CarState) -> Decision:
    """AGR-33: Aggressive 2-stop.

    Fixed strategy: pit at lap ~18% and ~47% of race distance regardless.
    Soft → Medium → Soft (or Hard if few laps remain).
    """
    total = state.total_laps
    remaining = total - state.lap

    pit1_lap = int(total * 0.18)
    pit2_lap = int(total * 0.47)

    if remaining <= 3:
        return Decision(pit=False, compound=my_car.compound)

    if my_car.pit_count == 0 and state.lap >= pit1_lap:
        return Decision(pit=True, compound="MEDIUM")

    if my_car.pit_count == 1 and state.lap >= pit2_lap:
        new_compound = "SOFT" if remaining <= 20 else "HARD"
        return Decision(pit=True, compound=new_compound)

    return Decision(pit=False, compound=my_car.compound)


# Registry of all built-in bots
BUILTIN_BOTS = {
    "VEL-01": {
        "strategy": vel_01_strategy,
        "description": "Greedy threshold (pit when deg > 2.2s)",
        "starting_compound": "SOFT",
    },
    "NXS-07": {
        "strategy": nxs_07_strategy,
        "description": "Undercut hunter (belief-driven undercut detection)",
        "starting_compound": "SOFT",
    },
    "WXP-23": {
        "strategy": wxp_23_strategy,
        "description": "Weather prophet (holds tyres, capitalizes on SC)",
        "starting_compound": "MEDIUM",
    },
    "EQL-44": {
        "strategy": eql_44_strategy,
        "description": "Nash equilibrium (integral-based optimal pit timing)",
        "starting_compound": "MEDIUM",
    },
    "AGR-33": {
        "strategy": agr_33_strategy,
        "description": "Aggressive 2-stop (lap 18%/47% regardless)",
        "starting_compound": "SOFT",
    },
}
