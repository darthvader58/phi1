/** API client for PIT WALL backend. */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function getApiKey(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("piwall_api_key");
}

async function apiFetch(path: string, options: RequestInit = {}): Promise<any> {
  const apiKey = getApiKey();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (apiKey) headers["x-api-key"] = apiKey;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error: ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Auth
  register: (username: string, teamName = "Independent") =>
    apiFetch("/api/register", {
      method: "POST",
      body: JSON.stringify({ username, team_name: teamName }),
    }),

  // Races
  createRace: (track: string, speed = 5, raceType = "quick") =>
    apiFetch("/api/race/create", {
      method: "POST",
      body: JSON.stringify({ track, speed, race_type: raceType }),
    }),

  joinRace: (raceId: string, compound = "MEDIUM") =>
    apiFetch(`/api/race/${raceId}/join`, {
      method: "POST",
      body: JSON.stringify({ starting_compound: compound }),
    }),

  submitBot: (raceId: string, code: string) =>
    apiFetch(`/api/race/${raceId}/submit-bot`, {
      method: "POST",
      body: JSON.stringify({ code }),
    }),

  startRace: (raceId: string) =>
    apiFetch(`/api/race/${raceId}/start`, { method: "POST" }),

  getRace: (raceId: string) => apiFetch(`/api/race/${raceId}`),
  listRaces: () => apiFetch("/api/races"),

  // Leaderboard
  getLeaderboard: () => apiFetch("/api/leaderboard"),

  // Player
  getPlayer: (username: string) => apiFetch(`/api/player/${username}`),
  getPlayerEloHistory: (username: string) => apiFetch(`/api/player/${username}/elo-history`),
  getPlayerRaces: (username: string) => apiFetch(`/api/player/${username}/races`),

  // Tracks
  getTrack: (name: string) => apiFetch(`/api/track/${name}`),
  listTracks: () => apiFetch("/api/tracks"),

  // Strategy
  getTemplate: () => apiFetch("/api/strategy/template"),
  testBot: (code: string, track = "bahrain", laps = 20) =>
    apiFetch("/api/test-bot", {
      method: "POST",
      body: JSON.stringify({ code, track, laps }),
    }),

  // Seasons
  createSeason: (name: string, tracks: string[]) =>
    apiFetch("/api/season", {
      method: "POST",
      body: JSON.stringify({ name, tracks }),
    }),

  listSeasons: () => apiFetch("/api/seasons"),
  getActiveSeason: () => apiFetch("/api/season/active"),
  getSeasonStandings: (seasonId: string) => apiFetch(`/api/season/${seasonId}/standings`),
  endSeason: (seasonId: string) =>
    apiFetch(`/api/season/${seasonId}/end`, { method: "POST" }),

  // Matchmaking
  getSuggestedMatches: () => apiFetch("/api/matchmaking/suggest"),
};
