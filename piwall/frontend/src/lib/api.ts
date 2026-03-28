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
  register: (username: string, teamName = "Independent") =>
    apiFetch("/api/register", {
      method: "POST",
      body: JSON.stringify({ username, team_name: teamName }),
    }),

  createRace: (track: string, speed = 5) =>
    apiFetch("/api/race/create", {
      method: "POST",
      body: JSON.stringify({ track, speed }),
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
  getLeaderboard: () => apiFetch("/api/leaderboard"),
  getPlayer: (username: string) => apiFetch(`/api/player/${username}`),
  getTrack: (name: string) => apiFetch(`/api/track/${name}`),
  listTracks: () => apiFetch("/api/tracks"),
  getTemplate: () => apiFetch("/api/strategy/template"),

  testBot: (code: string, track = "bahrain", laps = 20) =>
    apiFetch("/api/test-bot", {
      method: "POST",
      body: JSON.stringify({ code, track, laps }),
    }),
};
