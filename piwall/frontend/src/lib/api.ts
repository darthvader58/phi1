/** API client for PIT WALL backend. */

const BASE = "/api/game";

function headers(): HeadersInit {
  return { "Content-Type": "application/json" };
}

async function apiFetch(path: string, options: RequestInit = {}): Promise<any> {
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: { ...headers(), ...(options.headers as Record<string, string>) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || body.error || `API error: ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Auth
  register: (username: string, teamName = "Independent") =>
    apiFetch("/register", {
      method: "POST",
      body: JSON.stringify({ username, team_name: teamName }),
    }),

  // Races
  createRace: (track: string, speed = 5, raceType = "quick") =>
    apiFetch("/race/create", {
      method: "POST",
      body: JSON.stringify({ track, speed, race_type: raceType }),
    }),

  joinRace: (raceId: string, compound = "MEDIUM") =>
    apiFetch(`/race/${raceId}/join`, {
      method: "POST",
      body: JSON.stringify({ starting_compound: compound }),
    }),

  submitBot: (raceId: string, code: string) =>
    apiFetch(`/race/${raceId}/submit-bot`, {
      method: "POST",
      body: JSON.stringify({ code }),
    }),

  startRace: (raceId: string) => apiFetch(`/race/${raceId}/start`, { method: "POST" }),

  getRace: (raceId: string) => apiFetch(`/race/${raceId}`),
  listRaces: () => apiFetch("/races"),

  // Leaderboard
  getLeaderboard: () => apiFetch("/leaderboard"),

  // Player
  getPlayer: (username: string) => apiFetch(`/player/${username}`),
  getPlayerEloHistory: (username: string) => apiFetch(`/player/${username}/elo-history`),
  getPlayerRaces: (username: string) => apiFetch(`/player/${username}/races`),

  // Tracks
  getTrack: (name: string) => apiFetch(`/track/${name}`),
  listTracks: () => apiFetch("/tracks"),

  // Strategy
  getTemplate: () => apiFetch("/strategy/template"),
  testBot: (code: string, track = "bahrain", laps = 0) =>
    apiFetch("/test-bot", {
      method: "POST",
      body: JSON.stringify({ code, track, laps }),
    }),

  // Seasons
  createSeason: (name: string, tracks: string[]) =>
    apiFetch("/season", {
      method: "POST",
      body: JSON.stringify({ name, tracks }),
    }),

  listSeasons: () => apiFetch("/seasons"),
  getActiveSeason: () => apiFetch("/season/active"),
  getSeasonStandings: (seasonId: string) => apiFetch(`/season/${seasonId}/standings`),
  endSeason: (seasonId: string) => apiFetch(`/season/${seasonId}/end`, { method: "POST" }),

  // Matchmaking
  getSuggestedMatches: () => apiFetch("/matchmaking/suggest"),
};
