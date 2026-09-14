/** Mirror of Python dataclasses for the PIT WALL frontend. */

export interface CarState {
  car_id: string;
  player_id: string;
  position: number;
  gap_to_leader: number;
  compound: string;
  tyre_age: number;
  fuel_kg: number;
  pit_count: number;
  pit_laps: number[];
  last_lap_time: number;
  total_time: number;
  retired: boolean;
  drs_available: boolean;
  compounds_used: string[];
  beliefs: Record<string, RivalBelief>;
}

/**
 * A car as the live-timing panels (TrackMap, Leaderboard, BeliefPanel)
 * see it.
 *
 * Identical to CarState except that the fields only a running race can
 * supply are optional, because after the race they are genuinely absent:
 * a finished result row is rebuilt from race_results and has no tyre age,
 * fuel load, last lap time, DRS state or beliefs. Marking them optional
 * rather than zero-filling them means every component that reads one has
 * to say what it shows when it is missing, instead of silently rendering
 * a 0 that reads as real (round 5, NEW-11). CarState is assignable to
 * this, so a live snapshot still flows through unchanged.
 */
export type DisplayCar = Omit<
  CarState,
  "player_id" | "tyre_age" | "fuel_kg" | "last_lap_time" | "drs_available" | "beliefs"
> &
  Partial<
    Pick<
      CarState,
      "player_id" | "tyre_age" | "fuel_kg" | "last_lap_time" | "drs_available" | "beliefs"
    >
  >;

/** The fields TyreStrategyChart needs, which both a live snapshot and a
 *  finished result row carry. */
export type TyreStrategyCar = Pick<
  CarState,
  "car_id" | "position" | "retired" | "pit_laps" | "compounds_used" | "pit_count"
> & { compound?: string | null };

export interface RivalBelief {
  estimated_tyre_age: number;
  estimated_compound: string;
  pit_probability_next_5_laps: number;
  confidence: number;
  estimated_deg_rate: number;
  undercut_viable: boolean;
  undercut_gain: number;
  optimal_pit_in: number;
  // Compact WebSocket format aliases
  age?: number;
  compound?: string;
  pit_prob?: number;
  undercut?: boolean;
  uc_gain?: number;
}

export interface LapSnapshot {
  lap: number;
  weather: string;
  safety_car: boolean;
  track_temp: number;
  cars: CarState[];
}

export interface RaceEvent {
  lap: number;
  type: string;
  car_id: string;
  detail: string;
}

/**
 * One car's row in the "finished" event that backend/main.py's
 * _stream_stored_replay broadcasts.
 *
 * Deliberately NOT CarState. These rows are rebuilt from the persisted
 * race_results documents after the race is over, so they carry only what
 * survives it. The live-timing fields -- tyre_age, fuel_kg, last_lap_time,
 * drs_available and beliefs -- are per-lap state that exists only while a
 * race is running; the decoupled worker runs a match to completion in one
 * shot and does not stream them, and getting them back means replay
 * bodies, which are Phase 4 work. Declaring these rows as CarState told
 * the reader they carried beliefs and tyre ages when they never do.
 */
export interface FinishedStandingsRow {
  car_id: string;
  position: number;
  retired: boolean;
  points: number;
  total_time: number | null;
  gap_to_leader: number;
  pit_laps: number[];
  pit_count: number;
  compounds_used: string[];
  /** The tyre the car finished on: the last stint in compounds_used. */
  compound: string | null;
}

export interface RaceResult {
  race_id: string;
  replay_sha256: string | null;
  track: string | null;
  total_laps: number;
  standings: FinishedStandingsRow[];
  events: RaceEvent[];
}

export interface TrackInfo {
  name: string;
  display_name: string;
  country: string;
  total_laps: number;
  pit_loss_seconds: number;
  drs_zones: number;
  overtake_difficulty: number;
  safety_car_prob_dry: number;
  safety_car_prob_wet: number;
  typical_stint: Record<string, number>;
}

export interface PlayerInfo {
  username: string;
  elo: number;
  team: string;
  created_at: string;
  stats: {
    total_races: number;
    wins: number;
    podiums: number;
    dnfs: number;
    win_rate: number;
  };
  elo_history: EloEntry[];
  recent_races: RaceResultEntry[];
  bot_history: { code_hash: string; submitted_at: string }[];
}

export interface EloEntry {
  race_id: string;
  elo_before: number;
  elo_after: number;
  delta: number;
}

export interface RaceResultEntry {
  race_id: string;
  track: string;
  race_type: string;
  position: number;
  points: number;
  pit_count: number;
  compounds_used: string[];
  retired: boolean;
  finished_at: string | null;
}

export interface SeasonInfo {
  id: string;
  name: string;
  tracks: string[];
  active: boolean;
  start_date: string | null;
  end_date: string | null;
  race_count: number;
}

export interface SeasonStanding {
  player_id: string;
  username: string;
  team: string;
  elo: number;
  total_points: number;
  races: number;
  wins: number;
  podiums: number;
  best_finish: number;
  per_race: { race_id: string; position: number; points: number; retired: boolean }[];
}

export interface ActiveSeasonData {
  active: boolean;
  season: {
    id: string;
    name: string;
    tracks: string[];
    start_date: string | null;
    races: { id: string; track: string; status: string; finished_at: string | null }[];
    standings: SeasonStanding[];
    next_track: string | null;
    completed_tracks: string[];
  } | null;
}

export interface WsMessage {
  type: "countdown" | "lights_out" | "lap" | "finished" | "aborted" | "ping" | "error";
  lap?: number;
  total_laps?: number;
  data?: LapSnapshot;
  events?: RaceEvent[];
  result?: RaceResult;
  seconds?: number;
  error?: string;
  reason?: string;
}

// Compound colors (matching F1 standard)
export const COMPOUND_COLORS: Record<string, string> = {
  SOFT: "#FF3333",
  MEDIUM: "#FFD700",
  HARD: "#CCCCCC",
  INTERMEDIATE: "#43B02A",
  WET: "#0067B1",
};

// Fixed colors for cars in the field
export const CAR_COLORS = [
  "#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7",
  "#DDA0DD", "#98D8C8", "#F7DC6F", "#BB8FCE", "#85C1E9",
];

export function getCarColor(index: number): string {
  return CAR_COLORS[index % CAR_COLORS.length];
}

export function getCompoundColor(compound: string): string {
  return COMPOUND_COLORS[compound] || "#888888";
}
