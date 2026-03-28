"use client";

import { CarState, getCompoundColor, getCarColor } from "@/lib/types";

interface Props {
  cars: CarState[];
  highlightCarId?: string;
}

export default function Leaderboard({ cars, highlightCarId }: Props) {
  const sorted = [...cars].sort((a, b) => {
    if (a.retired && !b.retired) return 1;
    if (!a.retired && b.retired) return -1;
    return a.position - b.position;
  });

  return (
    <div className="card overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-pit-border flex items-center justify-between">
        <span className="section-label">Standings</span>
        <span className="text-[10px] text-pit-muted font-mono">
          {sorted.filter((c) => !c.retired).length} active
        </span>
      </div>

      {/* Column headers */}
      <div className="px-4 py-1.5 flex items-center gap-2 text-[10px] text-pit-muted font-medium border-b border-pit-border/50">
        <span className="w-8">POS</span>
        <span className="flex-1">DRIVER</span>
        <span className="w-16 text-right">GAP</span>
        <span className="w-8 text-center">TYRE</span>
        <span className="w-8 text-right">AGE</span>
        <span className="w-6 text-right">PIT</span>
      </div>

      {/* Rows */}
      <div>
        {sorted.map((car, idx) => {
          const isHighlight = car.car_id === highlightCarId;
          const compoundColor = getCompoundColor(car.compound);
          const carColor = getCarColor(idx);

          return (
            <div
              key={car.car_id}
              className={`px-4 py-2 flex items-center gap-2 text-xs border-b border-pit-border/30
                         transition-colors duration-150
                         ${isHighlight ? "bg-f1-red/5" : "hover:bg-white/[0.02]"}
                         ${car.retired ? "opacity-30" : ""}`}
            >
              {/* Position */}
              <span className={`w-8 font-bold tabular-nums ${
                car.position === 1 ? "text-f1-red" :
                car.position <= 3 ? "text-white" : "text-pit-text"
              }`}>
                {car.retired ? "RET" : car.position}
              </span>

              {/* Car color bar + ID */}
              <div className="flex-1 flex items-center gap-2 min-w-0">
                <div className="w-0.5 h-4 rounded-full flex-shrink-0" style={{ backgroundColor: carColor }} />
                <span className="font-semibold text-white truncate">{car.car_id}</span>
                {car.drs_available && (
                  <span className="badge bg-green-500/10 text-green-400 text-[9px] px-1 py-0">
                    DRS
                  </span>
                )}
              </div>

              {/* Gap */}
              <span className="w-16 text-right text-pit-text tabular-nums font-mono text-[11px]">
                {car.position === 1 && !car.retired
                  ? ""
                  : car.retired
                  ? "DNF"
                  : `+${car.gap_to_leader.toFixed(1)}`}
              </span>

              {/* Compound */}
              <div className="w-8 flex justify-center">
                <span
                  className="compound-dot"
                  style={{ backgroundColor: compoundColor }}
                  title={car.compound}
                />
              </div>

              {/* Tyre age */}
              <span className="w-8 text-right text-pit-muted tabular-nums font-mono text-[11px]">
                {car.tyre_age}
              </span>

              {/* Pit count */}
              <span className="w-6 text-right text-pit-muted tabular-nums font-mono text-[11px]">
                {car.pit_count || "-"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
