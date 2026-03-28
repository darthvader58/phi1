"""RaceEngine — core simulation for PIT WALL.

Runs a complete F1 race with multiple cars, each controlled by a
strategy function. Handles weather, safety cars, pit stops, DRS,
overtaking, DNFs, and belief updates.
"""

import random
import json
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, List, Optional, Tuple

from .physics import (
    TrackPhysics, TyreModel, compute_lap_time, compute_overtake_probability,
    check_dnf, compute_pit_stop_time, SC_GAP_COMPRESSION,
)
from .weather import WeatherEngine
from .belief import BeliefModel, RivalBelief
from .game_theory import compute_nash_pit_window, detect_undercut


# ─── Data classes matching the spec ───────────────────────────────────

@dataclass
class CarState:
    car_id: str
    player_id: str
    position: int
    gap_to_leader: float
    compound: str
    tyre_age: int
    fuel_kg: float
    pit_count: int
    pit_laps: List[int]
    last_lap_time: float
    total_time: float
    retired: bool
    drs_available: bool
    # Extended fields
    compounds_used: List[str] = field(default_factory=list)
    beliefs: Dict[str, dict] = field(default_factory=dict)


@dataclass
class RaceState:
    lap: int
    total_laps: int
    track: str
    weather: str
    safety_car: bool
    safety_car_laps_left: int
    track_temp: float
    cars: List[CarState]


@dataclass
class Decision:
    pit: bool
    compound: str  # If pit=True, which tyre to fit


@dataclass
class RaceEvent:
    lap: int
    event_type: str   # "pit", "sc_start", "sc_end", "dnf", "overtake", "weather", "undercut"
    car_id: str
    detail: str


@dataclass
class RaceResult:
    track: str
    total_laps: int
    final_standings: List[CarState]
    events: List[RaceEvent]
    lap_data: List[dict]  # Per-lap snapshots
    weather_history: List[str]


# Strategy function type
StrategyFn = Callable[[RaceState, CarState], Decision]


class RaceEngine:
    """Simulates a complete F1 race."""

    def __init__(
        self,
        track: TrackPhysics,
        weather_transitions: Optional[dict] = None,
        initial_weather: str = "dry",
        seed: Optional[int] = None,
        sc_prob_dry: float = 0.07,
        sc_prob_wet: float = 0.15,
    ):
        self.track = track
        self.rng = random.Random(seed)
        self.weather = WeatherEngine(
            transitions=weather_transitions,
            initial_weather=initial_weather,
            initial_temp=35.0,
            rng=self.rng,
        )
        self.sc_prob_dry = sc_prob_dry
        self.sc_prob_wet = sc_prob_wet

        # Race state
        self.cars: List[CarState] = []
        self.strategies: Dict[str, StrategyFn] = {}
        self.belief_models: Dict[str, BeliefModel] = {}
        self.events: List[RaceEvent] = []
        self.lap_data: List[dict] = []

        # Safety car state
        self.safety_car = False
        self.safety_car_laps_left = 0

    def add_car(
        self,
        car_id: str,
        player_id: str,
        strategy: StrategyFn,
        starting_position: int,
        starting_compound: str = "MEDIUM",
        typical_stints: Optional[Dict[str, int]] = None,
    ):
        """Add a car to the race."""
        car = CarState(
            car_id=car_id,
            player_id=player_id,
            position=starting_position,
            gap_to_leader=0.0,
            compound=starting_compound,
            tyre_age=0,
            fuel_kg=self.track.fuel_load_kg,
            pit_count=0,
            pit_laps=[],
            last_lap_time=0.0,
            total_time=float(starting_position) * 0.5,  # Grid gap
            retired=False,
            drs_available=False,
            compounds_used=[starting_compound],
        )
        self.cars.append(car)
        self.strategies[car_id] = strategy
        self.belief_models[car_id] = BeliefModel(typical_stints)

    def _build_race_state(self, lap: int) -> RaceState:
        """Build the current RaceState for strategy functions."""
        # Update beliefs dict on each car
        for car in self.cars:
            if not car.retired:
                car.beliefs = self.belief_models[car.car_id].to_dict()

        return RaceState(
            lap=lap,
            total_laps=self.track.total_laps,
            track=self.track.name,
            weather=self.weather.weather,
            safety_car=self.safety_car,
            safety_car_laps_left=self.safety_car_laps_left,
            track_temp=round(self.weather.track_temp, 1),
            cars=[deepcopy(c) for c in self.cars],
        )

    def _get_car_ahead(self, car: CarState) -> Optional[CarState]:
        """Get the car directly ahead in race order."""
        if car.position <= 1:
            return None
        for c in self.cars:
            if c.position == car.position - 1 and not c.retired:
                return c
        return None

    def _get_gap_to_car_ahead(self, car: CarState) -> Optional[float]:
        """Get gap in seconds to car directly ahead."""
        ahead = self._get_car_ahead(car)
        if ahead is None:
            return None
        return car.total_time - ahead.total_time

    def _update_positions(self):
        """Recalculate positions based on total_time."""
        active = [c for c in self.cars if not c.retired]
        active.sort(key=lambda c: c.total_time)
        leader_time = active[0].total_time if active else 0

        for pos, car in enumerate(active, 1):
            car.position = pos
            car.gap_to_leader = round(car.total_time - leader_time, 3)

        # Retired cars get positions after active
        retired = [c for c in self.cars if c.retired]
        for i, car in enumerate(retired):
            car.position = len(active) + i + 1

    def _update_drs(self):
        """Update DRS availability: within 1.0s of car ahead, not under SC."""
        for car in self.cars:
            if car.retired or self.safety_car:
                car.drs_available = False
                continue
            gap = self._get_gap_to_car_ahead(car)
            car.drs_available = gap is not None and 0 < gap < 1.0

    def _handle_safety_car(self, lap: int):
        """Handle safety car deployment and ending."""
        if self.safety_car:
            self.safety_car_laps_left -= 1
            if self.safety_car_laps_left <= 0:
                self.safety_car = False
                self.events.append(RaceEvent(lap, "sc_end", "", "Safety car in"))
                # Compress gaps on SC restart
                active = [c for c in self.cars if not c.retired]
                if active:
                    leader_time = min(c.total_time for c in active)
                    for car in active:
                        gap = car.total_time - leader_time
                        car.total_time = leader_time + gap * SC_GAP_COMPRESSION
        else:
            # Check for new safety car
            if self.weather.check_safety_car(self.sc_prob_dry, self.sc_prob_wet):
                duration = self.rng.randint(2, 5)
                self.safety_car = True
                self.safety_car_laps_left = duration
                self.events.append(RaceEvent(
                    lap, "sc_start", "",
                    f"Safety car deployed for {duration} laps",
                ))
                # Compress gaps when SC comes out
                active = [c for c in self.cars if not c.retired]
                if active:
                    leader_time = min(c.total_time for c in active)
                    for car in active:
                        gap = car.total_time - leader_time
                        car.total_time = leader_time + gap * SC_GAP_COMPRESSION

    def _handle_overtakes(self, lap: int):
        """Process overtake attempts between close cars."""
        active = [c for c in self.cars if not c.retired]
        active.sort(key=lambda c: c.total_time)

        for i in range(1, len(active)):
            attacker = active[i]
            defender = active[i - 1]
            gap = attacker.total_time - defender.total_time

            if gap < 1.5 and gap > 0:
                prob = compute_overtake_probability(
                    gap=gap,
                    track_overtake_difficulty=self.track.overtake_difficulty,
                    drs_available=attacker.drs_available,
                    weather=self.weather.weather,
                    tyre_age_diff=attacker.tyre_age - defender.tyre_age,
                )
                if self.rng.random() < prob:
                    # Overtake succeeds: swap times (attacker gains ~0.3s)
                    time_gain = 0.3 + self.rng.uniform(0, 0.2)
                    attacker.total_time = defender.total_time - time_gain
                    self.events.append(RaceEvent(
                        lap, "overtake", attacker.car_id,
                        f"{attacker.car_id} overtakes {defender.car_id} "
                        f"(gap was {gap:.2f}s, prob {prob:.1%})",
                    ))

    def _execute_pit_stop(self, car: CarState, new_compound: str, lap: int):
        """Execute a pit stop for a car."""
        pit_loss = compute_pit_stop_time(self.track, self.weather.weather)
        car.total_time += pit_loss
        car.compound = new_compound
        car.tyre_age = 0
        car.pit_count += 1
        car.pit_laps.append(lap)
        if new_compound not in car.compounds_used:
            car.compounds_used.append(new_compound)

        self.events.append(RaceEvent(
            lap, "pit", car.car_id,
            f"{car.car_id} pits for {new_compound} "
            f"(stop #{car.pit_count}, loss={pit_loss:.1f}s)",
        ))

    def _update_beliefs(self, lap: int):
        """Update each car's beliefs about rivals."""
        for car in self.cars:
            if car.retired:
                continue
            bm = self.belief_models[car.car_id]
            for rival in self.cars:
                if rival.car_id == car.car_id or rival.retired:
                    continue
                # Check if rival just pitted
                rival_pitted = lap in rival.pit_laps
                # Expected fresh pace
                best_model = None
                for m in self.track.tyre_models.values():
                    if best_model is None or m.base_lap_time < best_model.base_lap_time:
                        best_model = m
                expected_fresh = best_model.base_lap_time if best_model else 90.0

                bm.update(
                    rival_id=rival.car_id,
                    observed_lap_time=rival.last_lap_time,
                    expected_fresh_pace=expected_fresh,
                    rival_pitted=rival_pitted,
                    pit_compound=rival.compound if rival_pitted else None,
                )

    def run(self, lap_callback: Optional[Callable[[RaceState], None]] = None) -> RaceResult:
        """Run the complete race.

        Args:
            lap_callback: Optional function called after each lap with current state

        Returns:
            RaceResult with final standings, events, and lap data
        """
        total_laps = self.track.total_laps

        for lap in range(1, total_laps + 1):
            # 1. Weather transition
            old_weather = self.weather.weather
            self.weather.step()
            if self.weather.weather != old_weather:
                self.events.append(RaceEvent(
                    lap, "weather", "",
                    f"Weather: {old_weather} → {self.weather.weather}",
                ))

            # 2. Safety car handling
            self._handle_safety_car(lap)

            # 3. Build state for strategy functions
            state = self._build_race_state(lap)

            # 4. Collect decisions from all cars
            decisions: Dict[str, Decision] = {}
            for car in self.cars:
                if car.retired:
                    continue
                try:
                    my_state = next(c for c in state.cars if c.car_id == car.car_id)
                    decision = self.strategies[car.car_id](state, my_state)
                    if not isinstance(decision, Decision):
                        decision = Decision(pit=False, compound=car.compound)
                except Exception:
                    decision = Decision(pit=False, compound=car.compound)
                decisions[car.car_id] = decision

            # 5. Execute pit stops
            for car in self.cars:
                if car.retired:
                    continue
                decision = decisions.get(car.car_id)
                if decision and decision.pit:
                    # Validate: can't pit on last lap, must have valid compound
                    if lap < total_laps:
                        compound = decision.compound or "MEDIUM"
                        self._execute_pit_stop(car, compound, lap)

            # 6. Simulate lap times
            for car in self.cars:
                if car.retired:
                    continue

                # Check DNF
                if check_dnf(self.weather.weather, self.rng):
                    car.retired = True
                    self.events.append(RaceEvent(
                        lap, "dnf", car.car_id,
                        f"{car.car_id} retired (mechanical/incident)",
                    ))
                    continue

                car.tyre_age += 1
                car.fuel_kg = max(0, self.track.fuel_load_kg - lap * self.track.fuel_per_lap)

                gap_ahead = self._get_gap_to_car_ahead(car)
                lap_time = compute_lap_time(
                    track=self.track,
                    compound=car.compound,
                    tyre_age=car.tyre_age,
                    lap_number=lap,
                    weather=self.weather.weather,
                    is_safety_car=self.safety_car,
                    gap_to_car_ahead=gap_ahead,
                    drs_available=car.drs_available,
                    rng=self.rng,
                )
                car.last_lap_time = round(lap_time, 3)
                car.total_time += lap_time

            # 7. Update positions
            self._update_positions()

            # 8. Handle overtakes (only when no SC)
            if not self.safety_car:
                self._handle_overtakes(lap)
                self._update_positions()  # Re-sort after overtakes

            # 9. Update DRS
            self._update_drs()

            # 10. Update beliefs
            self._update_beliefs(lap)

            # 11. Snapshot lap data
            self.lap_data.append({
                "lap": lap,
                "weather": self.weather.weather,
                "safety_car": self.safety_car,
                "track_temp": round(self.weather.track_temp, 1),
                "cars": [
                    {
                        "car_id": c.car_id,
                        "position": c.position,
                        "gap_to_leader": round(c.gap_to_leader, 3),
                        "compound": c.compound,
                        "tyre_age": c.tyre_age,
                        "last_lap_time": c.last_lap_time,
                        "total_time": round(c.total_time, 3),
                        "retired": c.retired,
                        "drs": c.drs_available,
                    }
                    for c in self.cars
                ],
            })

            # 12. Lap callback
            if lap_callback:
                lap_callback(self._build_race_state(lap))

        # Validate compound rule: must have used 2+ compounds (penalize if not)
        for car in self.cars:
            if not car.retired and len(car.compounds_used) < 2:
                car.total_time += 30.0  # 30s time penalty
                self.events.append(RaceEvent(
                    total_laps, "penalty", car.car_id,
                    f"{car.car_id} +30s penalty: did not use 2 different compounds",
                ))

        self._update_positions()

        return RaceResult(
            track=self.track.name,
            total_laps=total_laps,
            final_standings=sorted(
                [deepcopy(c) for c in self.cars],
                key=lambda c: (c.retired, c.total_time),
            ),
            events=self.events,
            lap_data=self.lap_data,
            weather_history=self.weather.history,
        )
