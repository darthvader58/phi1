"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

export default function LobbyPage() {
  const [races, setRaces] = useState<any[]>([]);
  const [leaderboard, setLeaderboard] = useState<any[]>([]);
  const [track, setTrack] = useState("bahrain");
  const [speed, setSpeed] = useState(5);
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
      const res = await api.createRace(track, speed);
      window.location.href = `/race/${res.race_id}`;
    } catch (err: any) {
      setError(err.message);
    }
  }

  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold mb-6 text-gray-200">Race Lobby</h1>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Registration + Create Race */}
        <div className="lg:col-span-2 space-y-4">
          {!registered ? (
            <div className="bg-gray-900 border border-gray-700 rounded-lg p-5">
              <h2 className="text-sm font-bold text-gray-300 mb-3">REGISTER</h2>
              <form onSubmit={handleRegister} className="flex gap-2">
                <input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="Choose a username"
                  className="flex-1 bg-gray-800 border border-gray-700 rounded px-3 py-2
                             text-sm text-gray-200 placeholder-gray-600 focus:border-red-500
                             focus:outline-none"
                  required
                />
                <button
                  type="submit"
                  className="px-4 py-2 bg-red-700 hover:bg-red-600 text-white rounded
                             text-sm font-bold transition-colors"
                >
                  Register
                </button>
              </form>
              {error && <p className="text-red-400 text-xs mt-2">{error}</p>}
            </div>
          ) : (
            <div className="bg-gray-900 border border-gray-700 rounded-lg p-5">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-bold text-gray-300">CREATE RACE</h2>
                <span className="text-xs text-gray-500">
                  Logged in as <span className="text-gray-300">{username}</span>
                </span>
              </div>
              <div className="flex gap-3 items-end">
                <div>
                  <label className="text-xs text-gray-500 block mb-1">Track</label>
                  <select
                    value={track}
                    onChange={(e) => setTrack(e.target.value)}
                    className="bg-gray-800 border border-gray-700 rounded px-3 py-2
                               text-sm text-gray-200 focus:border-red-500 focus:outline-none"
                  >
                    {["bahrain", "monaco", "monza", "spa", "silverstone", "suzuka"].map((t) => (
                      <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-gray-500 block mb-1">Speed</label>
                  <select
                    value={speed}
                    onChange={(e) => setSpeed(Number(e.target.value))}
                    className="bg-gray-800 border border-gray-700 rounded px-3 py-2
                               text-sm text-gray-200 focus:border-red-500 focus:outline-none"
                  >
                    <option value={1}>1x (Real-time)</option>
                    <option value={5}>5x</option>
                    <option value={20}>20x (Fast)</option>
                  </select>
                </div>
                <button
                  onClick={handleCreateRace}
                  className="px-5 py-2 bg-red-700 hover:bg-red-600 text-white rounded
                             text-sm font-bold transition-colors"
                >
                  Create Race
                </button>
              </div>
              {error && <p className="text-red-400 text-xs mt-2">{error}</p>}
            </div>
          )}

          {/* Active races */}
          <div className="bg-gray-900 border border-gray-700 rounded-lg p-5">
            <h2 className="text-sm font-bold text-gray-300 mb-3">ACTIVE RACES</h2>
            {races.length === 0 ? (
              <p className="text-gray-600 text-sm">No active races. Create one above!</p>
            ) : (
              <div className="space-y-2">
                {races.map((race) => (
                  <Link
                    key={race.race_id}
                    href={`/race/${race.race_id}`}
                    className="flex items-center justify-between bg-gray-800/50 rounded p-3
                               hover:bg-gray-800 transition-colors"
                  >
                    <div>
                      <span className="text-gray-200 font-bold text-sm">
                        {race.track.charAt(0).toUpperCase() + race.track.slice(1)}
                      </span>
                      <span className="text-gray-500 text-xs ml-2">
                        {race.player_count}/8 players
                      </span>
                    </div>
                    <span className={`text-xs font-bold px-2 py-0.5 rounded ${
                      race.status === "lobby" ? "bg-green-900 text-green-400" :
                      race.status === "running" ? "bg-yellow-900 text-yellow-400" :
                      "bg-gray-800 text-gray-500"
                    }`}>
                      {race.status.toUpperCase()}
                    </span>
                  </Link>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right: Leaderboard */}
        <div className="bg-gray-900 border border-gray-700 rounded-lg p-5">
          <h2 className="text-sm font-bold text-gray-300 mb-3">ELO LEADERBOARD</h2>
          {leaderboard.length === 0 ? (
            <p className="text-gray-600 text-sm">No players yet.</p>
          ) : (
            <div className="space-y-1">
              {leaderboard.slice(0, 20).map((p, idx) => (
                <div key={p.username} className="flex items-center gap-2 text-xs">
                  <span className="w-6 text-right text-gray-600 font-bold">
                    #{idx + 1}
                  </span>
                  <span className="flex-1 text-gray-300 truncate">{p.username}</span>
                  <span className="text-gray-500">{p.team}</span>
                  <span className="w-14 text-right font-bold text-gray-200">
                    {p.elo}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
