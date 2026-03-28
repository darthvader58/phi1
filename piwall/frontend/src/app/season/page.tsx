"use client";

import { useState, useEffect } from "react";
import { api } from "@/lib/api";

export default function SeasonPage() {
  const [leaderboard, setLeaderboard] = useState<any[]>([]);

  useEffect(() => {
    api.getLeaderboard().then(setLeaderboard).catch(() => {});
  }, []);

  const tracks = ["bahrain", "monaco", "monza", "spa", "silverstone", "suzuka"];

  return (
    <div className="max-w-5xl mx-auto px-4 py-10">
      <div className="mb-8">
        <h1 className="text-3xl font-extrabold text-white tracking-tight">Season Championship</h1>
        <p className="text-sm text-pit-text mt-1">
          6 races across the circuit rotation. Points: 25/18/15/12/10/8/6/4/2/1
        </p>
      </div>

      {/* Circuit rotation */}
      <div className="card overflow-hidden mb-6">
        <div className="px-5 py-3.5 border-b border-pit-border">
          <span className="section-label">Circuit Rotation</span>
        </div>
        <div className="px-5 py-4">
          <div className="flex gap-2 items-center overflow-x-auto">
            {tracks.map((t, idx) => (
              <div key={t} className="flex items-center gap-2">
                <div className="flex items-center gap-2.5 bg-pit-surface rounded-lg px-4 py-2.5 flex-shrink-0">
                  <span className={`w-6 h-6 rounded-full flex items-center justify-center
                                   text-[10px] font-extrabold ${
                    idx === 0 ? "bg-f1-red/20 text-f1-red" : "bg-pit-border text-pit-muted"
                  }`}>
                    {idx + 1}
                  </span>
                  <span className="text-white text-sm font-semibold whitespace-nowrap">
                    {t.charAt(0).toUpperCase() + t.slice(1)}
                  </span>
                </div>
                {idx < tracks.length - 1 && (
                  <span className="text-pit-border text-xs flex-shrink-0">→</span>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ELO Standings */}
      <div className="card overflow-hidden">
        <div className="px-5 py-3.5 border-b border-pit-border flex items-center justify-between">
          <span className="section-label">ELO Standings</span>
          <span className="text-[10px] text-pit-muted font-mono">{leaderboard.length} drivers</span>
        </div>

        {/* Column headers */}
        <div className="px-5 py-2 flex items-center gap-3 text-[10px] text-pit-muted font-medium border-b border-pit-border/50">
          <span className="w-8 text-right">#</span>
          <span className="flex-1">DRIVER</span>
          <span className="w-20 text-right">TEAM</span>
          <span className="w-16 text-right">ELO</span>
        </div>

        <div>
          {leaderboard.length === 0 ? (
            <p className="text-pit-muted text-sm text-center py-10">
              No race results yet. Complete races to appear on the leaderboard.
            </p>
          ) : (
            leaderboard.map((p, idx) => (
              <div key={p.username}
                   className={`px-5 py-3 flex items-center gap-3 text-sm border-b border-pit-border/30
                              hover:bg-white/[0.02] transition-colors duration-150
                              ${idx === 0 ? "bg-f1-red/5" : ""}`}>
                <span className={`w-8 text-right font-extrabold tabular-nums ${
                  idx === 0 ? "text-f1-red" :
                  idx === 1 ? "text-white" :
                  idx === 2 ? "text-orange-400" : "text-pit-muted"
                }`}>
                  {idx + 1}
                </span>
                <span className="flex-1 text-white font-bold">{p.username}</span>
                <span className="w-20 text-right text-pit-muted text-xs">{p.team}</span>
                <span className="w-16 text-right font-bold text-white tabular-nums font-mono">{p.elo}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
