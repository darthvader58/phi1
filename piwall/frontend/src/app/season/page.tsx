"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { ActiveSeasonData, SeasonStanding } from "@/lib/types";

export default function SeasonPage() {
  const [leaderboard, setLeaderboard] = useState<any[]>([]);
  const [seasonData, setSeasonData] = useState<ActiveSeasonData | null>(null);
  const [seasonName, setSeasonName] = useState("Season 1");
  const [creating, setCreating] = useState(false);
  const [registered, setRegistered] = useState(false);
  const [tab, setTab] = useState<"championship" | "elo">("championship");

  useEffect(() => {
    if (typeof window !== "undefined" && localStorage.getItem("piwall_api_key")) {
      setRegistered(true);
    }
    loadData();
  }, []);

  async function loadData() {
    try {
      const [lb, sd] = await Promise.all([api.getLeaderboard(), api.getActiveSeason()]);
      setLeaderboard(lb);
      setSeasonData(sd);
    } catch {}
  }

  async function handleCreateSeason() {
    setCreating(true);
    try {
      await api.createSeason(seasonName, ["bahrain", "monaco", "monza", "spa", "silverstone", "suzuka"]);
      await loadData();
    } catch {}
    setCreating(false);
  }

  const season = seasonData?.season;
  const standings = season?.standings || [];
  const tracks = season?.tracks || ["bahrain", "monaco", "monza", "spa", "silverstone", "suzuka"];
  const completedTracks = season?.completed_tracks || [];

  return (
    <div className="max-w-6xl mx-auto px-4 py-10">
      <div className="mb-8 flex items-end justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-3xl font-extrabold text-white tracking-tight">
            {season ? season.name : "Season Championship"}
          </h1>
          <p className="text-sm text-pit-text mt-1">
            {season
              ? `${completedTracks.length}/${tracks.length} races completed`
              : "6 races across the circuit rotation. Points: 25/18/15/12/10/8/6/4/2/1"}
          </p>
          <p className="text-xs text-pit-muted mt-2">
            Signed-in account data such as race positions and submissions is available on{" "}
            <Link href="/account" className="text-f1-red hover:text-f1-redHover">
              /account
            </Link>
            .
          </p>
        </div>
        {registered && !season && (
          <div className="flex items-center gap-3">
            <input
              value={seasonName}
              onChange={(e) => setSeasonName(e.target.value)}
              className="input text-sm"
              placeholder="Season name"
            />
            <button onClick={handleCreateSeason} disabled={creating} className="btn-primary text-sm" type="button">
              {creating ? "Creating..." : "New Season"}
            </button>
          </div>
        )}
      </div>

      <div className="card overflow-hidden mb-6">
        <div className="px-5 py-3.5 border-b border-pit-border flex items-center justify-between">
          <span className="section-label">Circuit Rotation</span>
          {season?.next_track && (
            <span className="text-[10px] text-f1-red font-bold uppercase tracking-wider">
              Next: {season.next_track.charAt(0).toUpperCase() + season.next_track.slice(1)}
            </span>
          )}
        </div>
        <div className="px-5 py-4">
          <div className="flex gap-2 items-center overflow-x-auto">
            {tracks.map((t, idx) => {
              const isCompleted = completedTracks.includes(t);
              const isNext = season?.next_track === t;
              return (
                <div key={t} className="flex items-center gap-2">
                  <Link
                    href={`/track/${t}`}
                    className={`flex items-center gap-2.5 rounded-lg px-4 py-2.5 flex-shrink-0
                               transition-colors duration-150
                               ${
                                 isCompleted
                                   ? "bg-green-500/10"
                                   : isNext
                                     ? "bg-f1-red/10 ring-1 ring-f1-red/30"
                                     : "bg-pit-surface"
                               }`}
                  >
                    <span
                      className={`w-6 h-6 rounded-full flex items-center justify-center
                                 text-[10px] font-extrabold ${
                                   isCompleted
                                     ? "bg-green-500/20 text-green-400"
                                     : isNext
                                       ? "bg-f1-red/20 text-f1-red"
                                       : "bg-pit-border text-pit-muted"
                                 }`}
                    >
                      {isCompleted ? "✓" : idx + 1}
                    </span>
                    <span
                      className={`text-sm font-semibold whitespace-nowrap ${
                        isCompleted ? "text-green-400" : isNext ? "text-white" : "text-pit-text"
                      }`}
                    >
                      {t.charAt(0).toUpperCase() + t.slice(1)}
                    </span>
                  </Link>
                  {idx < tracks.length - 1 && <span className="text-pit-border text-xs flex-shrink-0">→</span>}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-1 mb-5">
        <button
          onClick={() => setTab("championship")}
          className={`px-4 py-2 rounded-lg text-sm font-bold transition-colors
                     ${tab === "championship" ? "bg-f1-red/10 text-f1-red" : "text-pit-muted hover:text-pit-text"}`}
          type="button"
        >
          Championship Points
        </button>
        <button
          onClick={() => setTab("elo")}
          className={`px-4 py-2 rounded-lg text-sm font-bold transition-colors
                     ${tab === "elo" ? "bg-f1-red/10 text-f1-red" : "text-pit-muted hover:text-pit-text"}`}
          type="button"
        >
          ELO Ratings
        </button>
      </div>

      {tab === "championship" ? (
        <ChampionshipTable standings={standings} />
      ) : (
        <EloTable leaderboard={leaderboard} />
      )}
    </div>
  );
}

function ChampionshipTable({ standings }: { standings: SeasonStanding[] }) {
  if (standings.length === 0) {
    return (
      <div className="card p-10 text-center">
        <p className="text-pit-muted text-sm">No championship results yet. Start a season and race!</p>
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <div className="px-5 py-3.5 border-b border-pit-border flex items-center justify-between">
        <span className="section-label">Driver Standings</span>
        <span className="text-[10px] text-pit-muted font-mono">{standings.length} drivers</span>
      </div>

      <div className="px-5 py-2 flex items-center gap-3 text-[10px] text-pit-muted font-medium border-b border-pit-border/50">
        <span className="w-8 text-right">#</span>
        <span className="flex-1">DRIVER</span>
        <span className="w-14 text-right">PTS</span>
        <span className="w-10 text-right">W</span>
        <span className="w-10 text-right">POD</span>
        <span className="w-10 text-right">RACES</span>
        <span className="w-14 text-right">ELO</span>
      </div>

      <div>
        {standings.map((s, idx) => (
          <Link
            key={s.player_id}
            href={`/player/${s.username}`}
            className={`px-5 py-3 flex items-center gap-3 text-sm border-b border-pit-border/30
                       hover:bg-white/[0.02] transition-colors duration-150 block
                       ${idx === 0 ? "bg-f1-red/5" : ""}`}
          >
            <span
              className={`w-8 text-right font-extrabold tabular-nums ${
                idx === 0 ? "text-f1-red" : idx === 1 ? "text-white" : idx === 2 ? "text-orange-400" : "text-pit-muted"
              }`}
            >
              {idx + 1}
            </span>
            <div className="flex-1 min-w-0">
              <span className="text-white font-bold">{s.username}</span>
              <span className="text-pit-muted text-xs ml-2">{s.team}</span>
            </div>
            <span className="w-14 text-right font-extrabold text-white tabular-nums">{s.total_points}</span>
            <span className="w-10 text-right text-pit-text tabular-nums font-mono text-xs">{s.wins}</span>
            <span className="w-10 text-right text-pit-text tabular-nums font-mono text-xs">{s.podiums}</span>
            <span className="w-10 text-right text-pit-muted tabular-nums font-mono text-xs">{s.races}</span>
            <span className="w-14 text-right text-pit-muted tabular-nums font-mono text-xs">{Math.round(s.elo)}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}

function EloTable({ leaderboard }: { leaderboard: any[] }) {
  if (leaderboard.length === 0) {
    return (
      <div className="card p-10 text-center">
        <p className="text-pit-muted text-sm">No players yet. Register and race to appear here.</p>
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <div className="px-5 py-3.5 border-b border-pit-border flex items-center justify-between">
        <span className="section-label">ELO Standings</span>
        <span className="text-[10px] text-pit-muted font-mono">{leaderboard.length} drivers</span>
      </div>

      <div className="px-5 py-2 flex items-center gap-3 text-[10px] text-pit-muted font-medium border-b border-pit-border/50">
        <span className="w-8 text-right">#</span>
        <span className="flex-1">DRIVER</span>
        <span className="w-20 text-right">TEAM</span>
        <span className="w-16 text-right">ELO</span>
      </div>

      <div>
        {leaderboard.map((p, idx) => (
          <Link
            key={p.username}
            href={`/player/${p.username}`}
            className={`px-5 py-3 flex items-center gap-3 text-sm border-b border-pit-border/30
                       hover:bg-white/[0.02] transition-colors duration-150 block
                       ${idx === 0 ? "bg-f1-red/5" : ""}`}
          >
            <span
              className={`w-8 text-right font-extrabold tabular-nums ${
                idx === 0 ? "text-f1-red" : idx === 1 ? "text-white" : idx === 2 ? "text-orange-400" : "text-pit-muted"
              }`}
            >
              {idx + 1}
            </span>
            <span className="flex-1 text-white font-bold">{p.username}</span>
            <span className="w-20 text-right text-pit-muted text-xs">{p.team}</span>
            <span className="w-16 text-right font-bold text-white tabular-nums font-mono">{p.elo}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
