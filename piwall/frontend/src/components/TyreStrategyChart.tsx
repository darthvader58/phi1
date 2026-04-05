"use client";

import { CarState, getCarColor, COMPOUND_COLORS } from "@/lib/types";

interface Props {
  cars: CarState[];
  totalLaps: number;
}

interface StintInfo {
  compound: string;
  startLap: number;
  endLap: number;
}

function parseStints(car: CarState, totalLaps: number): StintInfo[] {
  const stints: StintInfo[] = [];
  const pitLaps = car.pit_laps || [];
  const compounds = car.compounds_used || [car.compound];

  if (compounds.length === 0) return stints;

  const boundaries = [0, ...pitLaps, totalLaps];
  for (let i = 0; i < boundaries.length - 1; i++) {
    stints.push({
      compound: compounds[i] || compounds[compounds.length - 1],
      startLap: boundaries[i],
      endLap: boundaries[i + 1],
    });
  }
  return stints;
}

export default function TyreStrategyChart({ cars, totalLaps }: Props) {
  const sorted = [...cars].sort((a, b) => {
    if (a.retired && !b.retired) return 1;
    if (!a.retired && b.retired) return -1;
    return a.position - b.position;
  });

  return (
    <div className="card overflow-hidden">
      <div className="px-4 py-3 border-b border-pit-border flex items-center justify-between">
        <span className="section-label">Tyre Strategy</span>
        <div className="flex items-center gap-3">
          {["SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"].map((c) => (
            <div key={c} className="flex items-center gap-1">
              <span className="compound-dot" style={{ backgroundColor: COMPOUND_COLORS[c] }} />
              <span className="text-[9px] text-pit-muted">{c[0]}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="p-4 space-y-1.5">
        {sorted.map((car, idx) => {
          const stints = parseStints(car, totalLaps);
          const carColor = getCarColor(idx);
          return (
            <div key={car.car_id} className="flex items-center gap-2">
              {/* Position + Name */}
              <span className="w-6 text-right text-[11px] font-bold tabular-nums text-pit-muted">
                {car.retired ? "R" : car.position}
              </span>
              <div className="w-0.5 h-4 rounded-full flex-shrink-0" style={{ backgroundColor: carColor }} />
              <span className="w-14 text-xs font-semibold text-white truncate">{car.car_id}</span>

              {/* Stint bars */}
              <div className="flex-1 flex h-5 rounded overflow-hidden bg-pit-surface">
                {stints.map((stint, si) => {
                  const widthPct = ((stint.endLap - stint.startLap) / totalLaps) * 100;
                  const color = COMPOUND_COLORS[stint.compound] || "#888";
                  return (
                    <div
                      key={si}
                      className="h-full relative group"
                      style={{
                        width: `${widthPct}%`,
                        backgroundColor: color,
                        opacity: 0.7,
                        borderRight: si < stints.length - 1 ? "1px solid #161616" : "none",
                      }}
                      title={`${stint.compound}: Lap ${stint.startLap + 1}-${stint.endLap}`}
                    >
                      {widthPct > 12 && (
                        <span className="absolute inset-0 flex items-center justify-center text-[8px] font-bold text-black/70">
                          {stint.endLap - stint.startLap}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Total stops */}
              <span className="w-6 text-right text-[10px] text-pit-muted tabular-nums">
                {car.pit_count || 0}s
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
