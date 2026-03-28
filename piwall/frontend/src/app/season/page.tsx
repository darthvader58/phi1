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
    <div className="max-w-5xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold mb-2 text-gray-200">Season Championship</h1>
      <p className="text-sm text-gray-500 mb-6">
        6 races across the circuit rotation. Points: 25/18/15/12/10/8/6/4/2/1
      </p>

      {/* Track rotation */}
      <div className="bg-gray-900 border border-gray-700 rounded-lg p-5 mb-6">
        <h2 className="text-sm font-bold text-gray-300 mb-3">CIRCUIT ROTATION</h2>
        <div className="flex gap-3 overflow-x-auto">
          {tracks.map((t, idx) => (
            <div key={t} className="flex items-center gap-2 text-sm">
              <span className="w-6 h-6 rounded-full bg-gray-800 flex items-center justify-center
                             text-xs font-bold text-gray-400">
                {idx + 1}
              </span>
              <span className="text-gray-300 whitespace-nowrap">
                {t.charAt(0).toUpperCase() + t.slice(1)}
              </span>
              {idx < tracks.length - 1 && (
                <span className="text-gray-700 mx-1">→</span>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ELO Leaderboard */}
      <div className="bg-gray-900 border border-gray-700 rounded-lg p-5">
        <h2 className="text-sm font-bold text-gray-300 mb-3">ELO STANDINGS</h2>
        {leaderboard.length === 0 ? (
          <p className="text-gray-600 text-sm">
            No race results yet. Complete races to appear on the leaderboard.
          </p>
        ) : (
          <div className="space-y-1">
            {leaderboard.map((p, idx) => (
              <div key={p.username}
                   className="flex items-center gap-3 text-sm py-1.5 border-b border-gray-800 last:border-0">
                <span className={`w-8 text-right font-bold ${
                  idx === 0 ? "text-yellow-400" : idx === 1 ? "text-gray-300" :
                  idx === 2 ? "text-orange-400" : "text-gray-500"
                }`}>
                  #{idx + 1}
                </span>
                <span className="flex-1 text-gray-200 font-bold">{p.username}</span>
                <span className="text-gray-500 text-xs">{p.team}</span>
                <span className="w-16 text-right font-bold text-gray-200">{p.elo}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
