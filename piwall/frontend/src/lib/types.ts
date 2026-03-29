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

export interface RaceResult {
  track: string;
  total_laps: number;
  standings: CarState[];
  events: RaceEvent[];
  weather_history: string[];
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
  type: "countdown" | "lights_out" | "lap" | "finished" | "ping" | "error";
  lap?: number;
  total_laps?: number;
  data?: LapSnapshot;
  events?: RaceEvent[];
  result?: RaceResult;
  seconds?: number;
  error?: string;
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
