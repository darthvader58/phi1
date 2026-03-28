"""Built-in strategy bots for PIT WALL.

Five bot personalities that always fill the field:

VEL-01: Greedy threshold — pits when tyre deg exceeds a fixed threshold
NXS-07: Undercut hunter — monitors gap + rival tyre age for undercut timing
WXP-23: Weather prophet — holds tyres for weather/SC windows
EQL-44: Nash equilibrium — integrates full deg curve, pits when dEV > pit_delta
AGR-33: Aggressive 2-stop — fixed pit windows regardless of conditions
"""

from .race import RaceState, CarState, Decision


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
            # Pick the compound that will last the remaining laps
            if remaining > 25:
                new_compound = "HARD"
            elif remaining > 15:
                new_compound = "MEDIUM"
            else:
                new_compound = "SOFT"
            # Must use a different compound if this is the first stop
            if my_car.pit_count == 0 and new_compound == my_car.compound:
                new_compound = "MEDIUM" if my_car.compound != "MEDIUM" else "HARD"
            return Decision(pit=True, compound=new_compound)

    return Decision(pit=False, compound=my_car.compound)


def nxs_07_strategy(state: RaceState, my_car: CarState) -> Decision:
    """NXS-07: Undercut hunter.

    Monitors rivals ahead — if a rival has old tyres and the gap is small,
    pits early to attempt the undercut.
    """
    remaining = state.total_laps - state.lap
    if remaining <= 5 or my_car.tyre_age < 5:
        return Decision(pit=False, compound=my_car.compound)

    # Look at rivals ahead
    for rival in state.cars:
        if rival.car_id == my_car.car_id or rival.retired:
            continue
        if rival.position < my_car.position:
            gap = my_car.gap_to_leader - rival.gap_to_leader
            # Check belief about rival tyre age
            rival_belief = my_car.beliefs.get(rival.car_id, {})
            rival_tyre_age = rival_belief.get("estimated_tyre_age", rival.tyre_age)
            rival_pit_prob = rival_belief.get("pit_probability_next_5_laps", 0)

            # Undercut opportunity: rival has old tyres and we're close
            if (gap < 5.0 and rival_tyre_age > 12 and
                    my_car.tyre_age > 10 and rival_pit_prob > 0.3):
                # Pit one lap before we think they will
                if remaining > 10:
                    new_compound = "HARD" if remaining > 20 else "MEDIUM"
                else:
                    new_compound = "SOFT"
                if my_car.pit_count == 0 and new_compound == my_car.compound:
                    new_compound = "MEDIUM" if my_car.compound != "MEDIUM" else "HARD"
                return Decision(pit=True, compound=new_compound)

    # Fallback: pit on general degradation
    if my_car.tyre_age > 25:
        new_compound = "SOFT" if remaining <= 12 else "MEDIUM"
        if my_car.pit_count == 0 and new_compound == my_car.compound:
            new_compound = "HARD"
        return Decision(pit=True, compound=new_compound)

    return Decision(pit=False, compound=my_car.compound)


def wxp_23_strategy(state: RaceState, my_car: CarState) -> Decision:
    """WXP-23: Weather prophet.

    Tries to capitalize on safety car periods and weather changes.
    Delays pit stops to coincide with SC (free pit stop) or weather transitions.
    """
    remaining = state.total_laps - state.lap

    # If safety car is out and we haven't pitted recently, pit now (cheap stop)
    if state.safety_car and my_car.tyre_age >= 8 and remaining > 5:
        if remaining > 20:
            new_compound = "HARD"
        elif remaining > 12:
            new_compound = "MEDIUM"
        else:
            new_compound = "SOFT"
        if my_car.pit_count == 0 and new_compound == my_car.compound:
            new_compound = "MEDIUM" if my_car.compound != "MEDIUM" else "HARD"
        return Decision(pit=True, compound=new_compound)

    # Weather change: switch to appropriate tyres
    if state.weather in ("wet", "damp") and my_car.compound in ("SOFT", "MEDIUM", "HARD"):
        if my_car.tyre_age >= 3:  # Don't pit immediately on fresh tyres
            return Decision(pit=True, compound="INTERMEDIATE")

    if state.weather == "dry" and my_car.compound == "INTERMEDIATE":
        return Decision(pit=True, compound="MEDIUM" if remaining > 15 else "SOFT")

    # Otherwise, hold out longer than normal (waiting for SC opportunity)
    hold_threshold = 30 if state.weather == "dry" else 20
    if my_car.tyre_age > hold_threshold and remaining > 5:
        new_compound = "HARD" if remaining > 20 else "MEDIUM"
        if my_car.pit_count == 0 and new_compound == my_car.compound:
            new_compound = "SOFT" if remaining <= 15 else "HARD"
        return Decision(pit=True, compound=new_compound)

    return Decision(pit=False, compound=my_car.compound)


def eql_44_strategy(state: RaceState, my_car: CarState) -> Decision:
    """EQL-44: Nash equilibrium bot.

    Integrates the full degradation curve and pits when the expected
    value of pitting exceeds the pit stop delta.

    Uses a simplified integral: sum of projected deg over remaining laps
    on current tyres vs fresh tyres + pit loss.
    """
    remaining = state.total_laps - state.lap
    if remaining <= 3 or my_car.tyre_age < 5:
        return Decision(pit=False, compound=my_car.compound)

    # Estimate degradation rates
    deg_rates = {"SOFT": 0.10, "MEDIUM": 0.065, "HARD": 0.045}
    current_rate = deg_rates.get(my_car.compound, 0.06)

    # Pit loss estimate (from track data, roughly 22s)
    pit_delta = 22.0

    # Integrate remaining deg on current tyres (power law approx)
    current_cost = sum(
        current_rate * (my_car.tyre_age + k) ** 1.1
        for k in range(1, remaining + 1)
    )

    # Find best alternative compound
    best_alt = None
    best_alt_cost = float("inf")
    for comp, rate in deg_rates.items():
        if comp == my_car.compound and my_car.pit_count == 0:
            continue  # Must use different compound on first stop
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
        # Check if waiting 1-2 more laps would be even better
        future_cost = sum(
            current_rate * (my_car.tyre_age + 2 + k) ** 1.1
            for k in range(1, remaining - 1)
        )
        future_rate = deg_rates.get(best_alt, 0.06)
        future_alt = pit_delta + sum(
            future_rate * k ** 1.1 for k in range(1, remaining - 1)
        )
        future_delta = future_cost - future_alt

        # Pit now if current delta_ev is >= future (we've hit the sweet spot)
        if delta_ev >= future_delta:
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
        "description": "Undercut hunter (monitors gap + rival tyre age)",
        "starting_compound": "SOFT",
    },
    "WXP-23": {
        "strategy": wxp_23_strategy,
        "description": "Weather prophet (holds tyres, capitalizes on SC)",
        "starting_compound": "MEDIUM",
    },
    "EQL-44": {
        "strategy": eql_44_strategy,
        "description": "Nash equilibrium (integrates full deg curve)",
        "starting_compound": "MEDIUM",
    },
    "AGR-33": {
        "strategy": agr_33_strategy,
        "description": "Aggressive 2-stop (lap 18%/47% regardless)",
        "starting_compound": "SOFT",
    },
}
