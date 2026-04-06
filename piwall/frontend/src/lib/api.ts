/** API client for PIT WALL backend. */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const API_KEY_STORAGE = "piwall_api_key";
const USERNAME_STORAGE = "piwall_username";

function getApiKey(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(API_KEY_STORAGE);
}

async function provisionBackendPlayer(force = false): Promise<string | null> {
  if (typeof window === "undefined") {
    return null;
  }

  const response = await fetch("/api/backend-player", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force }),
    cache: "no-store"
  });

  if (!response.ok) {
    return null;
  }

  const payload = (await response.json().catch(() => ({}))) as {
    apiKey?: string;
    username?: string;
  };

  if (!payload.apiKey || !payload.username) {
    return null;
  }

  localStorage.setItem(API_KEY_STORAGE, payload.apiKey);
  localStorage.setItem(USERNAME_STORAGE, payload.username);
  window.dispatchEvent(new Event("piwall-backend-auth-changed"));

  return payload.apiKey;
}

async function apiFetch(
  path: string,
  options: RequestInit = {},
  config: { retry?: boolean; requireApiKey?: boolean } = {}
): Promise<any> {
  const { retry = true, requireApiKey = false } = config;
  let apiKey = getApiKey();
  if (requireApiKey && !apiKey) {
    apiKey = await provisionBackendPlayer();
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (apiKey) headers["x-api-key"] = apiKey;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    if (requireApiKey && retry && res.status === 401 && body.detail === "Invalid API key") {
      localStorage.removeItem(API_KEY_STORAGE);
      localStorage.removeItem(USERNAME_STORAGE);
      window.dispatchEvent(new Event("piwall-backend-auth-changed"));
      const refreshedKey = await provisionBackendPlayer();
      if (refreshedKey) {
        return apiFetch(path, options, { retry: false, requireApiKey: true });
      }
    }
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
    }, { requireApiKey: true }),

  joinRace: (raceId: string, compound = "MEDIUM") =>
    apiFetch(`/api/race/${raceId}/join`, {
      method: "POST",
      body: JSON.stringify({ starting_compound: compound }),
    }, { requireApiKey: true }),

  submitBot: (raceId: string, code: string) =>
    apiFetch(`/api/race/${raceId}/submit-bot`, {
      method: "POST",
      body: JSON.stringify({ code }),
    }, { requireApiKey: true }),

  startRace: (raceId: string) =>
    apiFetch(`/api/race/${raceId}/start`, { method: "POST" }, { requireApiKey: true }),

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
  testBot: (code: string, track = "bahrain", laps = 0) =>
    apiFetch("/api/test-bot", {
      method: "POST",
      body: JSON.stringify({ code, track, laps }),
    }),

  // Seasons
  createSeason: (name: string, tracks: string[]) =>
    apiFetch("/api/season", {
      method: "POST",
      body: JSON.stringify({ name, tracks }),
    }, { requireApiKey: true }),

  listSeasons: () => apiFetch("/api/seasons"),
  getActiveSeason: () => apiFetch("/api/season/active"),
  getSeasonStandings: (seasonId: string) => apiFetch(`/api/season/${seasonId}/standings`),
  endSeason: (seasonId: string) =>
    apiFetch(`/api/season/${seasonId}/end`, { method: "POST" }, { requireApiKey: true }),

  // Matchmaking
  getSuggestedMatches: () => apiFetch("/api/matchmaking/suggest", {}, { requireApiKey: true }),
};
