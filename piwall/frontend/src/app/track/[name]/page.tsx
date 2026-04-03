"use client";

import { useParams } from "next/navigation";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import { COMPOUND_COLORS } from "@/lib/types";

export default function TrackPage() {
  const params = useParams();
  const name = params.name as string;
  const [track, setTrack] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.getTrack(name).then(setTrack).catch((e) => setError(e.message));
  }, [name]);

  if (error) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-10">
        <p className="text-f1-red text-sm">Error: {error}</p>
      </div>
    );
  }

  if (!track) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-10">
        <div className="text-pit-muted text-sm animate-pulse-slow">Loading track data...</div>
      </div>
    );
  }

  const stats = [
    ["Total Laps", track.total_laps],
    ["Pit Loss", `${track.pit_loss_seconds}s`],
    ["DRS Zones", track.drs_zones],
    ["Overtake Difficulty", `${(track.overtake_difficulty * 100).toFixed(0)}%`],
    ["SC Prob (dry)", `${(track.safety_car_prob_dry * 100).toFixed(0)}%/lap`],
    ["SC Prob (wet)", `${(track.safety_car_prob_wet * 100).toFixed(0)}%/lap`],
  ];

  return (
    <div className="max-w-4xl mx-auto px-4 py-10">
      <div className="mb-8">
        <div className="flex items-center gap-3 mb-1">
          <div className="w-1.5 h-8 rounded-full bg-f1-red" />
          <h1 className="text-3xl font-extrabold text-white tracking-tight">{track.display_name}</h1>
        </div>
        <p className="text-sm text-pit-text ml-5">{track.country}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-6">
        {/* Circuit data */}
        <div className="card overflow-hidden">
          <div className="px-5 py-3.5 border-b border-pit-border">
            <span className="section-label">Circuit Data</span>
          </div>
          <div className="p-5 space-y-0">
            {stats.map(([label, value]) => (
              <div key={label as string}
                   className="flex justify-between items-center py-2.5 border-b border-pit-border/30 last:border-0">
                <span className="text-pit-text text-sm">{label}</span>
                <span className="stat-value text-base">{value}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Typical stints */}
        <div className="card overflow-hidden">
          <div className="px-5 py-3.5 border-b border-pit-border">
            <span className="section-label">Typical Stint Length</span>
          </div>
          <div className="p-5 space-y-4">
            {Object.entries(track.typical_stint || {}).map(([compound, laps]) => {
              const color = COMPOUND_COLORS[compound] || "#888";
              const maxLaps = track.total_laps;
              const pct = ((laps as number) / maxLaps) * 100;
              return (
                <div key={compound}>
                  <div className="flex justify-between text-sm mb-2">
                    <span style={{ color }} className="font-bold text-xs uppercase tracking-wider">{compound}</span>
                    <span className="text-pit-text font-mono text-xs tabular-nums">{laps as number} laps</span>
                  </div>
                  <div className="h-2 bg-pit-surface rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{ width: `${pct}%`, backgroundColor: color }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Strategy notes */}
      <div className="card overflow-hidden">
        <div className="px-5 py-3.5 border-b border-pit-border flex items-center gap-2">
          <div className="accent-line" />
          <span className="section-label">Strategy Notes</span>
        </div>
        <div className="p-5 text-sm text-pit-text space-y-3 leading-relaxed">
          {track.overtake_difficulty > 0.7 && (
            <p>
              High overtake difficulty — track position is crucial. Undercuts are
              the primary overtaking tool. Consider pitting early.
            </p>
          )}
          {track.overtake_difficulty < 0.35 && (
            <p>
              Easy overtaking circuit — DRS zones allow natural passing.
              Focus on tyre management over track position.
            </p>
          )}
          {track.safety_car_prob_dry > 0.08 && (
            <p>
              High safety car probability — consider holding pit stops for
              free pit windows under SC.
            </p>
          )}
          <p>
            Pit loss of <span className="text-white font-bold">{track.pit_loss_seconds}s</span> means you need{" "}
            {track.pit_loss_seconds > 23 ? "significant" : "moderate"} tyre
            degradation benefit to justify an extra stop.
          </p>
        </div>
      </div>
    </div>
  );
}
