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
      <div className="max-w-3xl mx-auto px-4 py-8">
        <p className="text-red-400">Error: {error}</p>
      </div>
    );
  }

  if (!track) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-8">
        <p className="text-gray-500">Loading track data...</p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold mb-1 text-gray-200">{track.display_name}</h1>
      <p className="text-sm text-gray-500 mb-6">{track.country}</p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        {/* Track stats */}
        <div className="bg-gray-900 border border-gray-700 rounded-lg p-5">
          <h2 className="text-sm font-bold text-gray-300 mb-3">CIRCUIT DATA</h2>
          <div className="space-y-2 text-sm">
            {[
              ["Total Laps", track.total_laps],
              ["Pit Loss", `${track.pit_loss_seconds}s`],
              ["DRS Zones", track.drs_zones],
              ["Overtake Difficulty", `${(track.overtake_difficulty * 100).toFixed(0)}%`],
              ["SC Prob (dry)", `${(track.safety_car_prob_dry * 100).toFixed(0)}%/lap`],
              ["SC Prob (wet)", `${(track.safety_car_prob_wet * 100).toFixed(0)}%/lap`],
            ].map(([label, value]) => (
              <div key={label as string} className="flex justify-between">
                <span className="text-gray-500">{label}</span>
                <span className="text-gray-200 font-bold">{value}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Typical stints */}
        <div className="bg-gray-900 border border-gray-700 rounded-lg p-5">
          <h2 className="text-sm font-bold text-gray-300 mb-3">TYPICAL STINT LENGTH</h2>
          <div className="space-y-3">
            {Object.entries(track.typical_stint || {}).map(([compound, laps]) => {
              const color = COMPOUND_COLORS[compound] || "#888";
              const maxLaps = track.total_laps;
              const pct = ((laps as number) / maxLaps) * 100;
              return (
                <div key={compound}>
                  <div className="flex justify-between text-sm mb-1">
                    <span style={{ color }} className="font-bold">{compound}</span>
                    <span className="text-gray-400">{laps as number} laps</span>
                  </div>
                  <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${pct}%`, backgroundColor: color }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Strategy hints */}
      <div className="bg-gray-900 border border-gray-700 rounded-lg p-5">
        <h2 className="text-sm font-bold text-gray-300 mb-3">STRATEGY NOTES</h2>
        <div className="text-sm text-gray-400 space-y-2">
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
            Pit loss of {track.pit_loss_seconds}s means you need{" "}
            {track.pit_loss_seconds > 23 ? "significant" : "moderate"} tyre
            degradation benefit to justify an extra stop.
          </p>
        </div>
      </div>
    </div>
  );
}
