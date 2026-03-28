"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

export default function LobbyPage() {
  const [races, setRaces] = useState<any[]>([]);
  const [leaderboard, setLeaderboard] = useState<any[]>([]);
  const [suggestions, setSuggestions] = useState<any[]>([]);
  const [track, setTrack] = useState("bahrain");
  const [speed, setSpeed] = useState(5);
  const [raceType, setRaceType] = useState("quick");
  const [username, setUsername] = useState("");
  const [registered, setRegistered] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (typeof window !== "undefined" && localStorage.getItem("piwall_api_key")) {
      setRegistered(true);
      setUsername(localStorage.getItem("piwall_username") || "");
    }
    loadData();
    const interval = setInterval(loadData, 5000);
    return () => clearInterval(interval);
  }, []);

  async function loadData() {
    try {
      const [r, lb] = await Promise.all([api.listRaces(), api.getLeaderboard()]);
      setRaces(r);
      setLeaderboard(lb);
    } catch {}
    // Matchmaking (only if registered)
    if (typeof window !== "undefined" && localStorage.getItem("piwall_api_key")) {
      try {
        const s = await api.getSuggestedMatches();
        setSuggestions(s.suggestions || []);
      } catch {}
    }
  }

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const res = await api.register(username);
      localStorage.setItem("piwall_api_key", res.api_key);
      localStorage.setItem("piwall_username", res.username);
      setRegistered(true);
    } catch (err: any) {
      setError(err.message);
    }
  }

  async function handleCreateRace() {
    setError("");
    try {
      const res = await api.createRace(track, speed, raceType);
      window.location.href = `/race/${res.race_id}`;
    } catch (err: any) {
      setError(err.message);
    }
  }

  return (
    <div className="max-w-6xl mx-auto px-4 py-10">
      <div className="mb-8">
        <h1 className="text-3xl font-extrabold text-white tracking-tight">Race Lobby</h1>
        <p className="text-sm text-pit-text mt-1">Join or create a race session</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left column */}
        <div className="lg:col-span-2 space-y-5">
          {!registered ? (
            <div className="card p-6">
              <div className="flex items-center gap-2 mb-4">
                <div className="accent-line" />
                <span className="section-label">Register</span>
              </div>
              <form onSubmit={handleRegister} className="flex gap-3">
                <input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="Choose a username"
                  className="input flex-1"
                  required
                />
                <button type="submit" className="btn-primary">
                  Register
                </button>
              </form>
              {error && <p className="text-f1-red text-xs mt-3">{error}</p>}
            </div>
          ) : (
            <div className="card p-6">
              <div className="flex items-center justify-between mb-5">
                <div className="flex items-center gap-2">
                  <div className="accent-line" />
                  <span className="section-label">Create Race</span>
                </div>
                <span className="text-[11px] text-pit-muted font-mono">
                  Logged in as <span className="text-white font-semibold">{username}</span>
                </span>
              </div>
              <div className="flex gap-4 items-end flex-wrap">
                <div>
                  <label className="text-[10px] text-pit-muted uppercase tracking-wider block mb-1.5">Track</label>
                  <select value={track} onChange={(e) => setTrack(e.target.value)} className="input">
                    {["bahrain", "monaco", "monza", "spa", "silverstone", "suzuka"].map((t) => (
                      <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-pit-muted uppercase tracking-wider block mb-1.5">Speed</label>
                  <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} className="input">
                    <option value={1}>1x (Real-time)</option>
                    <option value={5}>5x</option>
                    <option value={20}>20x (Fast)</option>
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-pit-muted uppercase tracking-wider block mb-1.5">Type</label>
                  <select value={raceType} onChange={(e) => setRaceType(e.target.value)} className="input">
                    <option value="quick">Quick Race</option>
                    <option value="season">Season Race</option>
                  </select>
                </div>
                <button onClick={handleCreateRace} className="btn-primary">
                  Create Race
                </button>
              </div>
              {error && <p className="text-f1-red text-xs mt-3">{error}</p>}
            </div>
          )}

          {/* Matchmaking suggestions */}
          {suggestions.length > 0 && (
            <div className="card overflow-hidden">
              <div className="px-5 py-3.5 border-b border-pit-border flex items-center gap-2">
                <div className="accent-line" />
                <span className="section-label">Suggested for You</span>
                <span className="text-[10px] text-pit-muted font-mono ml-auto">by ELO</span>
              </div>
              <div className="p-3 space-y-1.5">
                {suggestions.map((s) => (
                  <Link key={s.race_id} href={`/race/${s.race_id}`}
                        className="flex items-center justify-between rounded-lg px-4 py-3
                                   hover:bg-white/[0.03] transition-colors duration-150">
                    <div className="flex items-center gap-3">
                      <div className="w-1 h-8 rounded-full bg-f1-blue/60" />
                      <div>
                        <span className="text-white font-bold text-sm">
                          {s.track.charAt(0).toUpperCase() + s.track.slice(1)}
                        </span>
                        <span className="text-pit-muted text-xs ml-2 font-mono">{s.player_count}/8</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-[10px] text-pit-muted">
                        Avg ELO: <span className="text-pit-text font-bold">{s.avg_elo}</span>
                      </span>
                      <span className={`badge text-[10px] font-bold ${
                        s.race_type === "season" ? "bg-f1-red/10 text-f1-red" : "bg-pit-surface text-pit-muted"
                      }`}>
                        {s.race_type.toUpperCase()}
                      </span>
                    </div>
                  </Link>
                ))}
              </div>
            </div>
          )}

          {/* Active races */}
          <div className="card overflow-hidden">
            <div className="px-5 py-3.5 border-b border-pit-border flex items-center justify-between">
              <span className="section-label">Active Races</span>
              <span className="text-[10px] text-pit-muted font-mono">{races.length} sessions</span>
            </div>
            <div className="p-3">
              {races.length === 0 ? (
                <p className="text-pit-muted text-sm text-center py-8">No active races. Create one above!</p>
              ) : (
                <div className="space-y-1.5">
                  {races.map((race) => (
                    <Link
                      key={race.race_id}
                      href={`/race/${race.race_id}`}
                      className="flex items-center justify-between rounded-lg px-4 py-3
                                 hover:bg-white/[0.03] transition-colors duration-150"
                    >
                      <div className="flex items-center gap-3">
                        <div className={`w-1 h-8 rounded-full ${
                          race.race_type === "season" ? "bg-f1-red/60" : "bg-pit-border"
                        }`} />
                        <div>
                          <span className="text-white font-bold text-sm">
                            {race.track.charAt(0).toUpperCase() + race.track.slice(1)}
                          </span>
                          <span className="text-pit-muted text-xs ml-2 font-mono">
                            {race.player_count}/8
                          </span>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className={`badge text-[10px] font-bold ${
                          race.race_type === "season" ? "bg-f1-red/10 text-f1-red" : "bg-pit-surface text-pit-muted"
                        }`}>
                          {race.race_type.toUpperCase()}
                        </span>
                        <span className={`badge text-[10px] font-bold ${
                          race.status === "lobby" ? "bg-green-500/10 text-green-400" :
                          race.status === "running" ? "bg-yellow-500/10 text-yellow-400" :
                          "bg-pit-surface text-pit-muted"
                        }`}>
                          {race.status.toUpperCase()}
                        </span>
                      </div>
                    </Link>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Right: ELO Leaderboard */}
        <div className="card overflow-hidden">
          <div className="px-5 py-3.5 border-b border-pit-border flex items-center justify-between">
            <span className="section-label">ELO Leaderboard</span>
            <span className="text-[10px] text-pit-muted font-mono">Top 20</span>
          </div>
          <div className="p-3">
            {leaderboard.length === 0 ? (
              <p className="text-pit-muted text-sm text-center py-8">No players yet.</p>
            ) : (
              <div className="space-y-0.5">
                {leaderboard.slice(0, 20).map((p, idx) => (
                  <Link key={p.username} href={`/player/${p.username}`}
                        className="flex items-center gap-2 text-xs px-2 py-2 rounded-md
                                   hover:bg-white/[0.02] transition-colors">
                    <span className={`w-6 text-right font-extrabold tabular-nums ${
                      idx === 0 ? "text-f1-red" : idx < 3 ? "text-white" : "text-pit-muted"
                    }`}>
                      {idx + 1}
                    </span>
                    <span className="flex-1 text-white font-semibold truncate">{p.username}</span>
                    <span className="text-pit-muted text-[10px]">{p.team}</span>
                    <span className="w-14 text-right font-bold text-white tabular-nums font-mono">
                      {p.elo}
                    </span>
                  </Link>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
